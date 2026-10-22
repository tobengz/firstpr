from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from firstpr import llm  # noqa: E402

MESSAGES = [
    {"role": "system", "content": "be helpful"},
    {"role": "user", "content": "diff"},
]


def _ok(content: str = "hello") -> tuple[int, str]:
    return 200, json.dumps({"choices": [{"message": {"content": content}}]})


class CompleteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = mock.patch.dict(os.environ, {"FIRSTPR_API_KEY": "test-key"}, clear=False)
        self.env.start()
        self.sleep = mock.patch("firstpr.llm.time.sleep", return_value=None)
        self.sleep.start()

    def tearDown(self) -> None:
        self.sleep.stop()
        self.env.stop()

    def test_returns_content(self) -> None:
        with mock.patch("firstpr.llm._http_post", return_value=_ok("### Welcome\nhi")) as post:
            result = llm.complete(MESSAGES)

        self.assertEqual(result, "### Welcome\nhi")
        self.assertEqual(post.call_count, 1)

    def test_sends_bearer_token_and_model(self) -> None:
        captured: dict = {}

        def fake(url, headers, payload, timeout):
            captured.update(url=url, headers=headers, payload=payload)
            return _ok()

        with mock.patch("firstpr.llm._http_post", side_effect=fake):
            llm.complete(MESSAGES, model="my-model", base_url="https://example.test/v1/")

        self.assertEqual(captured["url"], "https://example.test/v1/chat/completions")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(captured["payload"]["model"], "my-model")

    def test_retries_then_succeeds(self) -> None:
        responses = [(500, "boom"), (429, "slow down"), _ok("fine")]
        with mock.patch("firstpr.llm._http_post", side_effect=responses) as post:
            result = llm.complete(MESSAGES)

        self.assertEqual(result, "fine")
        self.assertEqual(post.call_count, 3)

    def test_gives_up_after_max_retries(self) -> None:
        with mock.patch("firstpr.llm._http_post", return_value=(503, "unavailable")) as post:
            with self.assertRaises(llm.LLMError) as caught:
                llm.complete(MESSAGES, max_retries=2)

        self.assertEqual(post.call_count, 3)
        self.assertIn("503", str(caught.exception))

    def test_client_error_is_not_retried(self) -> None:
        body = json.dumps({"error": {"message": "invalid api key"}})
        with mock.patch("firstpr.llm._http_post", return_value=(401, body)) as post:
            with self.assertRaises(llm.LLMError) as caught:
                llm.complete(MESSAGES)

        self.assertEqual(post.call_count, 1)
        self.assertIn("invalid api key", str(caught.exception))

    def test_missing_key_raises_before_any_request(self) -> None:
        with mock.patch.dict(os.environ, {"FIRSTPR_API_KEY": "", "OPENAI_API_KEY": ""}):
            with mock.patch("firstpr.llm._http_post") as post:
                with self.assertRaises(llm.LLMError):
                    llm.complete(MESSAGES)

        post.assert_not_called()

    def test_list_content_is_joined(self) -> None:
        payload = {
            "choices": [{"message": {"content": [{"text": "a"}, {"text": "b"}]}}]
        }
        with mock.patch("firstpr.llm._http_post", return_value=(200, json.dumps(payload))):
            self.assertEqual(llm.complete(MESSAGES), "ab")

    def test_empty_completion_is_an_error(self) -> None:
        with mock.patch("firstpr.llm._http_post", return_value=_ok("   ")):
            with self.assertRaises(llm.LLMError):
                llm.complete(MESSAGES)

    def test_network_error_is_wrapped(self) -> None:
        with mock.patch(
            "firstpr.llm._http_post",
            side_effect=llm.LLMError("network error reaching x: refused"),
        ):
            with self.assertRaises(llm.LLMError):
                llm.complete(MESSAGES)


class ResolutionTests(unittest.TestCase):
    def test_env_precedence(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"FIRSTPR_MODEL": "first", "OPENAI_MODEL": "second"},
            clear=False,
        ):
            self.assertEqual(llm.resolve_model(), "first")

    def test_explicit_beats_env(self) -> None:
        with mock.patch.dict(os.environ, {"FIRSTPR_MODEL": "env-model"}, clear=False):
            self.assertEqual(llm.resolve_model("flag-model"), "flag-model")

    def test_default_base_url_has_no_trailing_slash(self) -> None:
        with mock.patch.dict(os.environ, {"FIRSTPR_BASE_URL": "", "OPENAI_BASE_URL": ""}):
            self.assertEqual(llm.resolve_base_url(), llm.DEFAULT_BASE_URL)
            self.assertFalse(llm.resolve_base_url().endswith("/"))


class BackoffTests(unittest.TestCase):
    def test_exponential_until_cap(self) -> None:
        self.assertEqual(llm._backoff(0, "{}"), 1.0)
        self.assertEqual(llm._backoff(1, "{}"), 2.0)
        self.assertEqual(llm._backoff(9, "{}"), 8.0)

    def test_honours_retry_after_hint(self) -> None:
        self.assertEqual(llm._backoff(0, json.dumps({"retry_after": 5})), 5.0)

    def test_absurd_retry_after_is_ignored(self) -> None:
        self.assertEqual(llm._backoff(0, json.dumps({"retry_after": 9999})), 1.0)


if __name__ == "__main__":
    unittest.main()
