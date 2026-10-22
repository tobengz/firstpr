"""A minimal chat-completions client on top of urllib.

No SDK, no transitive dependencies. That is a deliberate product decision: a tool
that runs inside someone else's CI must not be able to break their lockfile, and
``pip install`` of a 30 MB SDK to make one HTTP call is a bad trade.

Any OpenAI-compatible endpoint works — set ``FIRSTPR_BASE_URL`` and the model name.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "https://api.openai.com/v1"
#: Overridable via FIRSTPR_MODEL / OPENAI_MODEL. Any chat-completions model works.
DEFAULT_MODEL = "gpt-4.1-mini"

RETRY_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
_SNIPPET = 400


class LLMError(RuntimeError):
    """The model call could not be completed."""


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def resolve_api_key(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit.strip() or None
    return _first_env("FIRSTPR_API_KEY", "OPENAI_API_KEY")


def resolve_model(explicit: str | None = None) -> str:
    if explicit:
        return explicit.strip()
    return _first_env("FIRSTPR_MODEL", "OPENAI_MODEL") or DEFAULT_MODEL


def resolve_base_url(explicit: str | None = None) -> str:
    if explicit:
        return explicit.strip().rstrip("/")
    return (_first_env("FIRSTPR_BASE_URL", "OPENAI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _SNIPPET else text[:_SNIPPET] + "…"


def _http_post(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
) -> tuple[int, str]:
    """Single HTTP round trip. Returns (status, body); never raises on 4xx/5xx.

    Split out from :func:`complete` so tests can replace it without a network.
    """
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise LLMError(f"network error reaching {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise LLMError(f"timed out after {timeout:.0f}s reaching {url}") from exc


def _backoff(attempt: int, body: str) -> float:
    """Exponential backoff, honouring Retry-After when the server sends one."""
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        parsed = {}
    headers_hint = None
    if isinstance(parsed, dict):
        headers_hint = parsed.get("retry_after")
    if isinstance(headers_hint, (int, float)) and 0 < headers_hint <= 30:
        return float(headers_hint)
    return float(min(2**attempt, 8))


def _extract_content(raw: str) -> str:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError(f"response was not JSON: {_short(raw)}") from exc

    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices:
        message = data.get("error", {}).get("message") if isinstance(data, dict) else None
        raise LLMError(message or f"no choices in response: {_short(raw)}")

    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):  # some providers return content parts
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    if not isinstance(content, str) or not content.strip():
        raise LLMError(f"empty completion: {_short(raw)}")
    return content.strip()


def complete(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float = 90.0,
    temperature: float = 0.2,
    max_retries: int = 3,
) -> str:
    """Return the assistant message content for ``messages``.

    Retries transient failures (429/5xx/network) up to ``max_retries`` times and
    raises :class:`LLMError` with the server's own message on permanent failure.
    """
    key = resolve_api_key(api_key)
    if not key:
        raise LLMError(
            "no API key. Set FIRSTPR_API_KEY (or OPENAI_API_KEY) in the environment."
        )

    url = f"{resolve_base_url(base_url)}/chat/completions"
    payload: dict[str, Any] = {
        "model": resolve_model(model),
        "messages": messages,
        "temperature": temperature,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
        "User-Agent": "firstpr",
    }

    last_error = "unknown error"
    for attempt in range(max_retries + 1):
        status, body = _http_post(url, headers, payload, timeout)
        if status == 200:
            return _extract_content(body)
        last_error = f"HTTP {status}: {_short(body)}"
        if status not in RETRY_STATUS or attempt == max_retries:
            break
        time.sleep(_backoff(attempt, body))

    raise LLMError(last_error)
