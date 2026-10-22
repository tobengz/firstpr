"""Prompt construction. This module is the product.

Two failure modes matter more than anything else here:

1. **Invented rules.** A model told to review a diff will happily reference a
   CONTRIBUTING.md section that does not exist. The system prompt makes every
   repository claim conditional on the supplied excerpts and forbids commands that
   were not observed.
2. **Helpful-sounding noise.** Generic advice ("consider adding tests") is what
   makes review bots get muted. The prompt caps friction points at three, allows
   zero, and bans praise padding.
"""

from __future__ import annotations

MAX_WORDS = 220

SYSTEM_PROMPT = """\
You write a single pull request comment, on behalf of the maintainers of a \
repository, addressed to a contributor who has never opened a pull request there.

You are given that repository's own convention excerpts and the diff. You are not \
given the maintainers' personal preferences, so you must not invent any.

Hard rules:

1. Never describe how this repository works unless the convention excerpts support \
it. No invented commands, file paths, labels, CI jobs, review processes or policies.
2. Never restate or summarise the diff back to the author. They wrote it.
3. At most three points of friction. One is fine. Zero is also fine: if the change is \
small and correct, say that in one line and stop. Do not manufacture feedback to fill \
a section.
4. If a point is your inference rather than a documented rule, phrase it as a question \
("would this repo want…?") instead of an instruction.
5. Copy commands and paths verbatim from the excerpts. Do not fix their spelling.
6. No praise padding. No "Great work!", no "Thanks for contributing!", no emoji. Be \
direct and warm the way a good reviewer is, not the way a form letter is.
7. Under {max_words} words. Second person. Markdown.

Output exactly this shape, omitting any section you have nothing real to put in it:

### Welcome
One or two sentences naming what they actually changed.

### Before this merges
- Up to three bullets. Each one actionable and specific to this diff.

### Running it locally
The commands this repository actually uses, in a single fenced code block.

Output the comment body only: no preamble, no reasoning, no outer code fence around \
the whole thing.\
"""


def _language_instruction(language: str, pr_title: str | None) -> str:
    if language and language.lower() != "auto":
        return f"Write the comment in {language}."
    if pr_title:
        return (
            "Write the comment in the same language as the pull request title above. "
            "If the title is English, write in English."
        )
    return "Write the comment in English."


def build_messages(
    context_block: str,
    diff: str,
    *,
    language: str = "auto",
    pr_title: str | None = None,
    author: str | None = None,
) -> list[dict[str, str]]:
    """Assemble the request payload for one review comment."""
    system = SYSTEM_PROMPT.replace("{max_words}", str(MAX_WORDS))

    parts: list[str] = [context_block, ""]
    if pr_title:
        parts.append(f"Pull request title: {pr_title.strip()}")
    if author:
        parts.append(f"Author: @{author.lstrip('@')}")
    parts.append("")
    parts.append("--- pull request diff ---")
    parts.append(diff.strip() if diff.strip() else "(the diff is empty)")

    user = "\n".join(parts)
    user += "\n\n" + _language_instruction(language, pr_title)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def estimate_tokens(messages: list[dict[str, str]]) -> int:
    """Rough token estimate (~4 chars/token) for cost previews. Not billing-exact."""
    chars = sum(len(message.get("content", "")) for message in messages)
    return max(1, chars // 4)
