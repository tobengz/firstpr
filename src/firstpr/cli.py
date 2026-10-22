"""Command line entry point.

Three commands, on purpose:

``review``  generate the comment, optionally posting it to a pull request
``doctor``  show exactly what context was found, before spending any money
``init``    drop a commented config file

``doctor`` exists because the honest failure mode of a context-dependent tool is
"it found nothing and quietly made things up". One command that prints the
inventory makes that failure visible before a comment goes public.
"""

from __future__ import annotations

import argparse
import platform
import sys
from pathlib import Path

from . import __version__
from . import context as ctx
from . import github, llm, prompt
from .config import CONFIG_FILENAME, TEMPLATE, ConfigError, get_str, read_file_config


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def _mask(secret: str | None) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "(set)"
    return f"({secret[:4]}...{secret[-2:]})"


def _note(message: str) -> None:
    print(f"firstpr: {message}", file=sys.stderr)


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="firstpr",
        description="Write a repo-aware onboarding comment on a first pull request.",
    )
    parser.add_argument("--version", action="version", version=f"firstpr {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    review = sub.add_parser("review", help="generate the comment")
    review.add_argument("--repo", default=".", help="any path inside the repository")
    review.add_argument("--diff", help="diff file, or '-' to read stdin")
    review.add_argument("--base", help="git ref to diff against (default: autodetect)")
    review.add_argument(
        "--head",
        default="HEAD",
        help="git ref holding the proposed change (default: HEAD)",
    )
    review.add_argument("--title", help="pull request title, also sets output language")
    review.add_argument("--author", help="pull request author login")
    review.add_argument("--language", help="output language, or 'auto'")
    review.add_argument("--model", help="model name for the completions endpoint")
    review.add_argument("--base-url", dest="base_url", help="OpenAI-compatible endpoint")
    review.add_argument("--post", action="store_true", help="post to the pull request")
    review.add_argument("--pr", type=int, help="pull request number")
    review.add_argument(
        "--only-first-time",
        action="store_true",
        dest="only_first_time",
        help="exit quietly unless this is the author's first PR here",
    )
    review.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="print the prompt instead of calling the model",
    )
    review.add_argument("--out", help="also write the comment to this file")

    doctor = sub.add_parser("doctor", help="show what firstpr can see")
    doctor.add_argument("--repo", default=".")
    doctor.add_argument("--diff", help="optional diff file to size up")

    init = sub.add_parser("init", help=f"write a {CONFIG_FILENAME} template")
    init.add_argument("--repo", default=".")
    init.add_argument("--force", action="store_true", help="overwrite an existing file")

    return parser


# --------------------------------------------------------------------------- review


def _resolve_diff(args: argparse.Namespace, root: Path) -> str:
    if args.diff:
        return ctx.read_diff(args.diff)
    base = args.base or ctx.autodetect_base(root)
    if not base:
        raise ctx.ContextError(
            "no diff source found. Pass --diff FILE, or --base REF inside a git repo."
        )
    return ctx.diff_against(root, base, args.head)


def cmd_review(args: argparse.Namespace) -> int:
    root = ctx.repo_root(Path(args.repo))
    file_config = read_file_config(root)
    repo_context = ctx.gather(root)

    if repo_context.is_empty:
        _note("no CONTRIBUTING or PR template found; the comment will be cautious")

    diff = _resolve_diff(args, root)
    if not diff.strip():
        _note("the diff is empty, nothing to review")
        return 0

    event = github.pull_request_from_event() or {}
    event_user = event.get("user") or {}
    number = args.pr or _int_or_none(event.get("number"))
    author = args.author or (event_user.get("login") if isinstance(event_user, dict) else None)
    title = args.title or (event.get("title") if isinstance(event.get("title"), str) else None)
    repo_slug = github.resolve_repo()
    token = github.resolve_token()

    only_first = args.only_first_time or bool(file_config.get("only_first_time", False))
    if only_first and _can_check_first_time(repo_slug, author, token):
        first, count = github.is_first_time_contributor(
            repo_slug or "", author or "", token or ""
        )
        if not first:
            if count is None:
                print(
                    "could not read this author's history; skipping rather than risk a "
                    "wrong comment"
                )
            else:
                print(f"@{author} has {count} pull requests here; not a first-time contributor")
            return 0
    elif only_first:
        _note("--only-first-time needs a token, repository and author; ignoring the gate")

    messages = prompt.build_messages(
        repo_context.as_prompt_block(),
        ctx.truncate_diff(diff),
        language=args.language or get_str(file_config, "language") or "auto",
        pr_title=title,
        author=author,
    )

    estimate = prompt.estimate_tokens(messages)
    _note(f"prompt is about {estimate} tokens")

    if args.dry_run:
        print(f"# model: {llm.resolve_model(args.model or get_str(file_config, 'model'))}")
        for message in messages:
            print(f"\n# --- {message['role']} ---")
            print(message["content"])
        return 0

    body = llm.complete(
        messages,
        model=args.model or get_str(file_config, "model"),
        base_url=args.base_url or get_str(file_config, "base_url"),
    )

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(body + "\n", encoding="utf-8")
        _note(f"comment written to {out_path}")

    if args.post:
        if not (token and repo_slug and number):
            raise github.GitHubError(
                "cannot post: need a token (GITHUB_TOKEN), "
                "a repository (GITHUB_REPOSITORY) and a pull request number"
            )
        action, comment_id = github.upsert_comment(repo_slug, number, body, token)
        print(f"comment {action} on {repo_slug}#{number} (id {comment_id})")
        return 0

    print(body)
    return 0


def _can_check_first_time(
    repo_slug: str | None, author: str | None, token: str | None
) -> bool:
    return bool(repo_slug and author and token)


# --------------------------------------------------------------------------- doctor


def cmd_doctor(args: argparse.Namespace) -> int:
    root = ctx.repo_root(Path(args.repo))
    repo_context = ctx.gather(root)

    print(f"firstpr  {__version__}")
    print(f"python   {platform.python_version()}")
    print(f"repo     {root}")
    print(f"git      {'yes' if (root / '.git').exists() else 'no'}")
    print(f"ecosystem {repo_context.kind}")
    print()

    print("conventions found:")
    if repo_context.docs:
        for path, text in repo_context.docs.items():
            print(f"  {path:<38} {len(text):>6} chars")
    else:
        print("  none — the model will have no documented rules to cite")
    if repo_context.notes:
        print()
        print("notes:")
        for note in repo_context.notes:
            print(f"  {note}")
    print()

    print("commands found:")
    if repo_context.commands:
        for command in repo_context.commands:
            print(f"  {command}")
    else:
        print("  none")
    print()

    print("credentials:")
    api_key = llm.resolve_api_key()
    print(f"  FIRSTPR_API_KEY / OPENAI_API_KEY   {'set ' + _mask(api_key) if api_key else 'MISSING'}")
    print(f"  model                              {llm.resolve_model()}")
    print(f"  base url                           {llm.resolve_base_url()}")
    github_token = github.resolve_token()
    print(
        "  GITHUB_TOKEN                       "
        f"{'set ' + _mask(github_token) if github_token else 'missing (local generation only)'}"
    )
    print(f"  repository                         {github.resolve_repo() or 'unknown'}")
    print()

    config_path = root / CONFIG_FILENAME
    print(f"config   {'yes, ' + str(config_path) if config_path.is_file() else 'none'}")

    if args.diff:
        diff = ctx.read_diff(args.diff)
        trimmed = ctx.truncate_diff(diff)
        print()
        print(f"diff     {len(diff)} chars, {len(trimmed)} after truncation")
        messages = prompt.build_messages(repo_context.as_prompt_block(), trimmed)
        print(f"prompt   about {prompt.estimate_tokens(messages)} tokens")

    return 0


# ----------------------------------------------------------------------------- init


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    path = root / CONFIG_FILENAME
    if path.exists() and not args.force:
        _note(f"{path} already exists; use --force to overwrite")
        return 1
    path.write_text(TEMPLATE, encoding="utf-8")
    print(f"wrote {path}")
    return 0


# ----------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)

    handlers = {"review": cmd_review, "doctor": cmd_doctor, "init": cmd_init}
    try:
        return handlers[args.command](args)
    except (ctx.ContextError, llm.LLMError, github.GitHubError, ConfigError) as exc:
        print(f"firstpr: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
