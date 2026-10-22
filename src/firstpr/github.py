"""GitHub REST calls: post the comment, and stay idempotent about it.

A bot that comments twice on the same push is a bot that gets uninstalled. Every
comment firstpr writes carries an invisible marker; a re-run finds it and edits in
place instead of appending a duplicate.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API_ROOT = "https://api.github.com"
#: Hidden marker so re-runs update rather than duplicate.
MARKER = "<!-- firstpr:comment -->"
_SNIPPET = 400


class GitHubError(RuntimeError):
    """A GitHub API call failed."""


def _request(
    method: str,
    path: str,
    token: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> Any:
    url = path if path.startswith("http") else API_ROOT + path
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "firstpr",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = " ".join(exc.read().decode("utf-8", errors="replace").split())
        raise GitHubError(f"{method} {path} -> {exc.code}: {detail[:_SNIPPET]}") from exc
    except urllib.error.URLError as exc:
        raise GitHubError(f"network error: {exc.reason}") from exc

    return json.loads(body) if body.strip() else {}


def resolve_token(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit.strip() or None
    for name in ("FIRSTPR_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def resolve_repo(explicit: str | None = None) -> str | None:
    """``owner/name`` from an argument or from CI's GITHUB_REPOSITORY."""
    if explicit:
        return explicit.strip()
    value = os.environ.get("GITHUB_REPOSITORY", "").strip()
    return value or None


def find_marker_comment(repo: str, number: int, token: str, marker: str = MARKER) -> int | None:
    """Return the id of our previous comment on this PR, if any."""
    for page in range(1, 6):
        comments = _request(
            "GET",
            f"/repos/{repo}/issues/{number}/comments?per_page=100&page={page}",
            token,
        )
        if not isinstance(comments, list) or not comments:
            return None
        for comment in comments:
            if marker in (comment.get("body") or ""):
                return int(comment["id"])
        if len(comments) < 100:
            return None
    return None


def upsert_comment(
    repo: str,
    number: int,
    body: str,
    token: str,
    marker: str = MARKER,
) -> tuple[str, int]:
    """Create or update our comment. Returns ("created"|"updated", comment_id)."""
    payload_body = f"{marker}\n{body.strip()}\n"
    existing = find_marker_comment(repo, number, token, marker)
    if existing is not None:
        result = _request(
            "PATCH",
            f"/repos/{repo}/issues/comments/{existing}",
            token,
            {"body": payload_body},
        )
        return "updated", int(result.get("id", existing))
    result = _request(
        "POST",
        f"/repos/{repo}/issues/{number}/comments",
        token,
        {"body": payload_body},
    )
    return "created", int(result.get("id", 0))


def contributor_pr_count(repo: str, login: str, token: str) -> int | None:
    """How many PRs ``login`` has opened in ``repo``, including the current one.

    Returns ``None`` when the search API gives an unusable answer. The distinction
    matters: "zero PRs" and "we could not tell" must not be treated alike.
    """
    query = f"repo:{repo} is:pr author:{login}"
    result = _request(
        "GET",
        f"/search/issues?q={urllib.parse.quote(query)}&per_page=1",
        token,
    )
    total = result.get("total_count") if isinstance(result, dict) else None
    return total if isinstance(total, int) else None


def is_first_time_contributor(
    repo: str, login: str, token: str
) -> tuple[bool, int | None]:
    """True when the current PR is this author's first in the repository.

    Fails closed. If the count is unknown we answer ``False`` rather than risk
    posting a comment under a wrong premise, and the second element carries
    ``None`` so callers can say so honestly.
    """
    count = contributor_pr_count(repo, login, token)
    if count is None:
        return False, None
    return count <= 1, count


def pull_request_from_event() -> dict[str, Any] | None:
    """Read the PR payload GitHub Actions drops at GITHUB_EVENT_PATH."""
    event_path = os.environ.get("GITHUB_EVENT_PATH", "").strip()
    if not event_path:
        return None
    path = Path(event_path)
    if not path.is_file():
        return None
    try:
        event = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    pull_request = event.get("pull_request")
    return pull_request if isinstance(pull_request, dict) else None
