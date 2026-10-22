"""Collect the repository facts a reviewer needs: conventions, commands, diff.

The whole premise of this tool is that advice should come from *this* repository,
not from the model's imagination. Everything the model is allowed to say about a
project is assembled here, under an explicit character budget.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: Per-file ceiling. CONTRIBUTING files are usually the most informative, so they
#: get the largest slice; README is often marketing and gets less.
MAX_FILE_CHARS = 4000
#: Ceiling across all convention files combined.
MAX_TOTAL_CHARS = 16000
#: Ceiling for the diff. Long enough for a typical PR, short enough to stay cheap.
MAX_DIFF_CHARS = 20000

CONVENTION_FILES: tuple[tuple[str, int], ...] = (
    ("CONTRIBUTING.md", MAX_FILE_CHARS),
    (".github/CONTRIBUTING.md", MAX_FILE_CHARS),
    ("docs/CONTRIBUTING.md", MAX_FILE_CHARS),
    (".github/PULL_REQUEST_TEMPLATE.md", 3000),
    (".github/pull_request_template.md", 3000),
    ("PULL_REQUEST_TEMPLATE.md", 3000),
    (".github/CODE_OF_CONDUCT.md", 1200),
    ("CODE_OF_CONDUCT.md", 1200),
    ("README.md", 2500),
)

#: (marker file, ecosystem, fallback test command)
PROJECT_MARKERS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("pyproject.toml", "python", ("pytest",)),
    ("setup.py", "python", ("pytest",)),
    ("setup.cfg", "python", ("pytest",)),
    ("package.json", "javascript", ("npm test",)),
    ("Cargo.toml", "rust", ("cargo test",)),
    ("go.mod", "go", ("go test ./...",)),
    ("pom.xml", "java", ("mvn test",)),
    ("build.gradle", "java", ("./gradlew test",)),
    ("build.gradle.kts", "java", ("./gradlew test",)),
    ("Gemfile", "ruby", ("bundle exec rspec",)),
    ("composer.json", "php", ("composer test",)),
)

_RUN_KEY = re.compile(r"^(\s*(?:-\s*)?)run:\s*(.*)$")
_BLOCK_SCALARS = frozenset({"|", ">", "|-", ">-", "|+", ">+"})
_COMMAND_HINT = re.compile(
    r"\b(pytest|tox|nox|unittest|npm|pnpm|yarn|bun|cargo|go\s+test|mvn|gradle|make|"
    r"ruff|flake8|black|isort|mypy|pyright|eslint|prettier|tsc|clippy|rustfmt|"
    r"golangci-lint|test|lint|fmt|build)\b",
    re.IGNORECASE,
)
_DIFF_HEADER = re.compile(r"(?m)^diff --git ")

#: Packaging steps belong in CI, not in advice addressed to a contributor.
_CI_ONLY = re.compile(
    r"\b(pip3? install|uv pip|python -m build|twine|docker (build|push)|"
    r"actions/checkout|npm (ci|install)|yarn install)\b",
    re.IGNORECASE,
)

#: Lower is more useful to a newcomer. Tests first, formatting last.
_COMMAND_RANK: tuple[tuple[int, re.Pattern[str]], ...] = (
    (
        0,
        re.compile(
            r"\b(pytest|unittest|tox|nox|go test|cargo test|npm (run )?test|"
            r"mvn test|gradlew test|rspec|spec)\b",
            re.IGNORECASE,
        ),
    ),
    (
        1,
        re.compile(
            r"\b(lint|ruff|flake8|eslint|clippy|golangci|mypy|pyright|tsc|typecheck)\b",
            re.IGNORECASE,
        ),
    ),
    (
        2,
        re.compile(r"\b(fmt|format|prettier|black|rustfmt|isort)\b", re.IGNORECASE),
    ),
)


def _command_rank(command: str) -> int:
    for rank, pattern in _COMMAND_RANK:
        if pattern.search(command):
            return rank
    return 3


def _is_ci_only(command: str) -> bool:
    return bool(_CI_ONLY.search(command))


class ContextError(RuntimeError):
    """Raised when repository context cannot be read."""


@dataclass
class RepoContext:
    """Everything firstpr knows about a repository. Nothing more is allowed out."""

    root: Path
    kind: str = "unknown"
    commands: list[str] = field(default_factory=list)
    docs: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """True when we have no conventions at all — advice would be invented."""
        return not self.docs

    def as_prompt_block(self) -> str:
        lines = [
            f"Repository: {self.root.name}",
            f"Ecosystem: {self.kind}",
        ]
        if self.commands:
            lines.append("Commands extracted from this repository's config:")
            lines.extend(f"- {command}" for command in self.commands)
        else:
            lines.append(
                "Commands extracted from this repository's config: none found. "
                "Do not invent any."
            )
        if self.notes:
            lines.extend(f"Note: {note}" for note in self.notes)

        if not self.docs:
            lines.append("")
            lines.append(
                "This repository has no contribution documentation. You therefore have "
                "no documented rules to cite. Keep the comment to what the diff itself "
                "shows and ask rather than assert."
            )
            return "\n".join(lines)

        lines.append("")
        lines.append("--- convention excerpts (the only source of truth) ---")
        for path, text in self.docs.items():
            lines.append("")
            lines.append(f"### {path}")
            lines.append(text)
        return "\n".join(lines)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _usable(command: str) -> bool:
    """Reject anything that is not a command a newcomer could paste and run."""
    command = command.strip().strip("'\"")
    if not command or command.startswith("#"):
        return False
    if "${{" in command or "|" in command or "&&" in command:
        return False
    if _is_ci_only(command):
        return False
    return bool(_COMMAND_HINT.search(command))


def _ci_commands(root: Path) -> list[str]:
    """Pull real commands out of GitHub Actions workflows.

    Handles both inline ``run: make test`` and the ``run: |`` block form, which is
    what most workflows actually use. Block indentation is measured from the
    column of the ``run`` key so sibling keys are never mistaken for commands.
    """
    workflows = root / ".github" / "workflows"
    if not workflows.is_dir():
        return []

    found: list[str] = []

    def consider(candidate: str) -> None:
        if _usable(candidate) and candidate.strip() not in found:
            found.append(candidate.strip())

    for path in sorted(workflows.glob("*.y*ml")):
        try:
            lines = _read_text(path).splitlines()
        except OSError:
            continue

        index = 0
        while index < len(lines):
            match = _RUN_KEY.match(lines[index])
            if not match:
                index += 1
                continue

            key_column = lines[index].index("run:")
            inline = match.group(2).strip()
            index += 1
            if inline not in _BLOCK_SCALARS:
                consider(inline)
                continue

            while index < len(lines):
                line = lines[index]
                stripped = line.strip()
                indentation = len(line) - len(line.lstrip())
                if stripped and indentation <= key_column:
                    break
                consider(stripped)
                index += 1

        if len(found) >= 8:
            break

    # Tests before linters before everything else: the model receives an ordered
    # list and tends to reproduce that order in its "run it locally" section.
    ordered = sorted(enumerate(found), key=lambda pair: (_command_rank(pair[1]), pair[0]))
    return [command for _, command in ordered][:4]


def gather(root: Path) -> RepoContext:
    """Read conventions and commands from ``root`` without exceeding the budget."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise ContextError(f"not a directory: {root}")

    kind = "unknown"
    defaults: tuple[str, ...] = ()
    for marker, ecosystem, fallback in PROJECT_MARKERS:
        if (root / marker).exists():
            kind, defaults = ecosystem, fallback
            break

    commands = _ci_commands(root)
    if not commands and defaults:
        commands = list(defaults)

    docs: dict[str, str] = {}
    notes: list[str] = []
    budget = MAX_TOTAL_CHARS
    seen: set[str] = set()

    for rel, per_file_cap in CONVENTION_FILES:
        path = root / rel
        if not path.is_file():
            continue
        # On a case-insensitive filesystem PULL_REQUEST_TEMPLATE.md and
        # pull_request_template.md are one file. Without this, the same template
        # is paid for twice and appears twice in the prompt.
        identity = os.path.normcase(str(path.resolve()))
        if identity in seen:
            continue
        seen.add(identity)
        if budget <= 0:
            notes.append(f"{rel} skipped: overall context budget exhausted")
            continue
        try:
            text = _read_text(path).strip()
        except OSError as exc:
            notes.append(f"{rel} unreadable ({exc.__class__.__name__})")
            continue
        if not text:
            continue
        cap = min(per_file_cap, budget)
        if len(text) > cap:
            text = text[:cap].rstrip() + f"\n[... {rel} truncated at {cap} characters]"
            notes.append(f"{rel} was truncated")
        docs[rel] = text
        budget -= len(text)

    if not (root / ".git").exists():
        notes.append("not a git repository; diff must be supplied explicitly")

    return RepoContext(root=root, kind=kind, commands=commands, docs=docs, notes=notes)


def truncate_diff(diff: str, limit: int = MAX_DIFF_CHARS) -> str:
    """Trim a diff to ``limit`` chars, cutting on file boundaries when possible."""
    if len(diff) <= limit:
        return diff

    chunks = ["diff --git " + chunk for chunk in _DIFF_HEADER.split(diff) if chunk.strip()]
    if not chunks:
        return diff[:limit] + "\n[diff truncated]\n"

    kept: list[str] = []
    used = 0
    for chunk in chunks:
        if used + len(chunk) > limit:
            break
        kept.append(chunk)
        used += len(chunk)

    if not kept:
        return chunks[0][:limit] + "\n[diff truncated: single file larger than budget]\n"

    omitted = len(chunks) - len(kept)
    out = "".join(kept)
    if omitted:
        out += f"\n[diff truncated: {omitted} further file(s) not shown]\n"
    return out


def read_diff(path: str | None) -> str:
    """Read a diff from a file, or stdin when ``path`` is ``-``."""
    if path in (None, "-"):
        return sys.stdin.read()
    file_path = Path(path)
    if not file_path.is_file():
        raise ContextError(f"diff file not found: {file_path}")
    return _read_text(file_path)


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise ContextError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def repo_root(path: Path) -> Path:
    """Resolve the git top level, falling back to the given directory."""
    path = Path(path).resolve()
    try:
        out = git(path, "rev-parse", "--show-toplevel").strip()
    except ContextError:
        return path
    return Path(out) if out else path


def autodetect_base(root: Path) -> str | None:
    """First ref that exists among the usual remote defaults."""
    for ref in ("origin/main", "origin/master", "main", "master", "HEAD~1"):
        try:
            git(root, "rev-parse", "--verify", "--quiet", ref)
        except ContextError:
            continue
        return ref
    return None


def diff_against(root: Path, base: str, head: str = "HEAD") -> str:
    """Three-dot diff, which is what a pull request actually shows: base...head.

    ``head`` is separate from the checkout on purpose. A workflow can keep the
    working tree on the trusted base branch — so the convention files that reach
    the model come from the maintainers — while still diffing the contributor's
    commit, which is untrusted data.
    """
    return git(root, "diff", f"{base}...{head}")
