from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from firstpr import github  # noqa: E402


class FindMarkerTests(unittest.TestCase):
    def test_returns_id_of_our_comment(self) -> None:
        comments = [
            {"id": 1, "body": "nice work"},
            {"id": 2, "body": f"{github.MARKER}\n### Welcome"},
        ]
        with mock.patch("firstpr.github._request", return_value=comments):
            self.assertEqual(github.find_marker_comment("o/r", 7, "t"), 2)

    def test_returns_none_when_absent(self) -> None:
        with mock.patch("firstpr.github._request", return_value=[{"id": 1, "body": "hi"}]):
            self.assertIsNone(github.find_marker_comment("o/r", 7, "t"))

    def test_returns_none_on_empty_page(self) -> None:
        with mock.patch("firstpr.github._request", return_value=[]):
            self.assertIsNone(github.find_marker_comment("o/r", 7, "t"))


class UpsertTests(unittest.TestCase):
    def test_creates_when_no_previous_comment(self) -> None:
        calls: list[tuple[str, str]] = []

        def fake(method, path, token, payload=None, timeout=30.0):
            calls.append((method, path))
            if method == "GET":
                return []
            return {"id": 99}

        with mock.patch("firstpr.github._request", side_effect=fake):
            action, comment_id = github.upsert_comment("o/r", 7, "### Welcome", "t")

        self.assertEqual(action, "created")
        self.assertEqual(comment_id, 99)
        self.assertEqual(calls[-1], ("POST", "/repos/o/r/issues/7/comments"))

    def test_updates_in_place_when_marker_exists(self) -> None:
        calls: list[tuple[str, str]] = []

        def fake(method, path, token, payload=None, timeout=30.0):
            calls.append((method, path))
            if method == "GET":
                return [{"id": 42, "body": github.MARKER}]
            return {"id": 42}

        with mock.patch("firstpr.github._request", side_effect=fake):
            action, comment_id = github.upsert_comment("o/r", 7, "### Welcome", "t")

        self.assertEqual(action, "updated")
        self.assertEqual(comment_id, 42)
        self.assertEqual(calls[-1], ("PATCH", "/repos/o/r/issues/comments/42"))

    def test_body_carries_the_marker(self) -> None:
        sent: dict = {}

        def fake(method, path, token, payload=None, timeout=30.0):
            if method == "GET":
                return []
            sent.update(payload or {})
            return {"id": 1}

        with mock.patch("firstpr.github._request", side_effect=fake):
            github.upsert_comment("o/r", 7, "### Welcome", "t")

        self.assertTrue(sent["body"].startswith(github.MARKER))
        self.assertIn("### Welcome", sent["body"])


class FirstTimeTests(unittest.TestCase):
    def test_single_pr_counts_as_first_time(self) -> None:
        with mock.patch("firstpr.github._request", return_value={"total_count": 1}):
            first, count = github.is_first_time_contributor("o/r", "newbie", "t")

        self.assertTrue(first)
        self.assertEqual(count, 1)

    def test_repeat_contributor_is_not_first_time(self) -> None:
        with mock.patch("firstpr.github._request", return_value={"total_count": 12}):
            first, count = github.is_first_time_contributor("o/r", "veteran", "t")

        self.assertFalse(first)
        self.assertEqual(count, 12)

    def test_degraded_response_is_not_first_time(self) -> None:
        """A malformed search response must not spam the comment."""
        with mock.patch("firstpr.github._request", return_value=[]):
            first, count = github.is_first_time_contributor("o/r", "x", "t")

        self.assertFalse(first)
        self.assertIsNone(count)


class EventTests(unittest.TestCase):
    def test_reads_pull_request_from_event_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "event.json"
            path.write_text(
                json.dumps({"pull_request": {"number": 5, "title": "Fix", "user": {"login": "a"}}}),
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(path)}):
                event = github.pull_request_from_event()

        self.assertEqual(event["number"], 5)

    def test_missing_event_file_is_not_fatal(self) -> None:
        with mock.patch.dict(os.environ, {"GITHUB_EVENT_PATH": "/nonexistent/event.json"}):
            self.assertIsNone(github.pull_request_from_event())

    def test_issue_comment_event_returns_none(self) -> None:
        """The workflow also fires on other events; those must be ignored."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "event.json"
            path.write_text(json.dumps({"action": "created"}), encoding="utf-8")
            with mock.patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(path)}):
                self.assertIsNone(github.pull_request_from_event())


if __name__ == "__main__":
    unittest.main()
