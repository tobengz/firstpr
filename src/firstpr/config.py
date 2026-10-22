"""Configuration resolution.

Precedence, highest first: command line flag, then ``.firstpr.toml``, then the
environment variable, then the built-in default. Keeping the file deliberately
small — a tool that needs a config schema to be useful will not be adopted.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

CONFIG_FILENAME = ".firstpr.toml"

TEMPLATE = """\
# firstpr configuration. Every key is optional: flags and environment variables
# take precedence over this file.
[firstpr]
# Any chat-completions-compatible model.
model = "gpt-4.1-mini"

# Point this at another OpenAI-compatible endpoint if you do not use OpenAI.
# base_url = "https://api.openai.com/v1"

# "auto" follows the language of the pull request title.
language = "auto"

# Only comment on an author's first pull request in this repository.
only_first_time = true
"""


class ConfigError(RuntimeError):
    """The configuration file exists but cannot be used."""


def read_file_config(root: Path) -> dict[str, Any]:
    """Parse ``.firstpr.toml`` if present. Returns an empty dict otherwise."""
    path = Path(root) / CONFIG_FILENAME
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        data = tomllib.loads(raw)
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    table = data.get("firstpr", data)
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: expected a [firstpr] table")
    return table


def get_bool(config: dict[str, Any], key: str, default: bool = False) -> bool:
    value = config.get(key, default)
    return bool(value)


def get_str(config: dict[str, Any], key: str) -> str | None:
    value = config.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None
