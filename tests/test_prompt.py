from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from firstpr import prompt  # noqa: E402

CONTEXT = "Repository: demo\n### CONTRIBUTING.md\nRun `pytest -q` before pushing."
DIFF = "diff --git a/app.py b/app.py\n+print('hi')\n"


class BuildMessagesTests(unittest.TestCase):
    def test_returns_system_and_user(self) -> None:
        messages = prompt.build_messages(CONTEXT, DIFF)

        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertIn(CONTEXT, messages[1]["content"])
        self.assertIn("diff --git", messages[1]["content"])

    def test_system_prompt_has_no_unfilled_placeholder(self) -> None:
        messages = prompt.build_messages(CONTEXT, DIFF)

        self.assertNotIn("{max_words}", messages[0]["content"])
        self.assertIn(str(prompt.MAX_WORDS), messages[0]["content"])

    def test_defaults_to_english_without_a_title(self) -> None:
        messages = prompt.build_messages(CONTEXT, DIFF)

        self.assertIn("Write the comment in English", messages[1]["content"])

    def test_auto_language_follows_title(self) -> None:
        messages = prompt.build_messages(CONTEXT, DIFF, pr_title="修复登录页的空白")

        self.assertIn("same language as the pull request title", messages[1]["content"])
        self.assertIn("修复登录页的空白", messages[1]["content"])

    def test_explicit_language_wins(self) -> None:
        messages = prompt.build_messages(
            CONTEXT, DIFF, language="Japanese", pr_title="Fix the thing"
        )

        self.assertIn("Write the comment in Japanese", messages[1]["content"])

    def test_empty_diff_is_labelled(self) -> None:
        messages = prompt.build_messages(CONTEXT, "   ")

        self.assertIn("(the diff is empty)", messages[1]["content"])

    def test_author_is_mentioned_once(self) -> None:
        messages = prompt.build_messages(CONTEXT, DIFF, author="@octocat")

        self.assertEqual(messages[1]["content"].count("octocat"), 1)


class EstimateTests(unittest.TestCase):
    def test_estimate_scales_with_content(self) -> None:
        small = prompt.build_messages(CONTEXT, DIFF)
        large = prompt.build_messages(CONTEXT, DIFF * 400)

        self.assertGreater(prompt.estimate_tokens(large), prompt.estimate_tokens(small))
        self.assertGreater(prompt.estimate_tokens(small), 100)


class ContractTests(unittest.TestCase):
    def test_system_prompt_states_the_guardrails(self) -> None:
        """The prompt is load-bearing; these rules should not vanish in a refactor."""
        system = prompt.SYSTEM_PROMPT

        for phrase in ("must not invent", "At most three points", "No praise padding"):
            self.assertIn(phrase, system)


if __name__ == "__main__":
    unittest.main()
