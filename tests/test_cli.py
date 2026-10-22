from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from firstpr import cli  # noqa: E402
from firstpr.config import CONFIG_FILENAME  # noqa: E402

DIFF = (
    "diff --git a/app.py b/app.py\n"
    "index 111..222 100644\n"
    "--- a/app.py\n"
    "+++ b/app.py\n"
    "@@ -1,2 +1,3 @@\n"
    " import os\n"
    "+print('debug')\n"
)

CLEAR_KEY = {"FIRSTPR_API_KEY": "", "OPENAI_API_KEY": ""}


def _repo(tmp: str) -> Path:
    root = Path(tmp)
    (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (root / "CONTRIBUTING.md").write_text(
        "# Contributing\n\nRun `pytest -q` before opening a pull request.\n",
        encoding="utf-8",
    )
    (root / "change.diff").write_text(DIFF, encoding="utf-8")
    return root


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class ReviewTests(unittest.TestCase):
    def test_dry_run_prints_the_prompt_without_calling_the_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(tmp)
            with mock.patch.dict(os.environ, CLEAR_KEY, clear=False):
                with mock.patch("firstpr.llm.complete") as complete:
                    code, out, _ = _run(
                        "review", "--repo", str(root), "--diff", str(root / "change.diff"), "--dry-run"
                    )

        self.assertEqual(code, 0)
        complete.assert_not_called()
        self.assertIn("convention excerpts", out)
        self.assertIn("pytest -q", out)
        self.assertIn("print('debug')", out)

    def test_prints_the_comment_when_not_posting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(tmp)
            with mock.patch.dict(os.environ, {"FIRSTPR_API_KEY": "k"}, clear=False):
                with mock.patch("firstpr.llm.complete", return_value="### Welcome\nhello"):
                    code, out, _ = _run(
                        "review", "--repo", str(root), "--diff", str(root / "change.diff")
                    )

        self.assertEqual(code, 0)
        self.assertIn("### Welcome", out)

    def test_missing_key_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(tmp)
            with mock.patch.dict(os.environ, CLEAR_KEY, clear=False):
                code, _, err = _run(
                    "review", "--repo", str(root), "--diff", str(root / "change.diff")
                )

        self.assertEqual(code, 1)
        self.assertIn("no API key", err)

    def test_writes_to_out_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(tmp)
            target = root / "out" / "comment.md"
            with mock.patch.dict(os.environ, {"FIRSTPR_API_KEY": "k"}, clear=False):
                with mock.patch("firstpr.llm.complete", return_value="### Welcome"):
                    code, _, _ = _run(
                        "review",
                        "--repo",
                        str(root),
                        "--diff",
                        str(root / "change.diff"),
                        "--out",
                        str(target),
                    )

            self.assertEqual(code, 0)
            self.assertIn("### Welcome", target.read_text(encoding="utf-8"))

    def test_posts_when_asked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(tmp)
            env = {
                "FIRSTPR_API_KEY": "k",
                "GITHUB_TOKEN": "gh",
                "GITHUB_REPOSITORY": "o/r",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch("firstpr.llm.complete", return_value="### Welcome"):
                    with mock.patch(
                        "firstpr.github.upsert_comment", return_value=("created", 11)
                    ) as upsert:
                        code, out, _ = _run(
                            "review",
                            "--repo",
                            str(root),
                            "--diff",
                            str(root / "change.diff"),
                            "--post",
                            "--pr",
                            "5",
                        )

        self.assertEqual(code, 0)
        upsert.assert_called_once()
        self.assertEqual(upsert.call_args.args[:2], ("o/r", 5))
        self.assertIn("created", out)

    def test_first_time_gate_skips_veterans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(tmp)
            env = {
                "FIRSTPR_API_KEY": "k",
                "GITHUB_TOKEN": "gh",
                "GITHUB_REPOSITORY": "o/r",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch(
                    "firstpr.github.is_first_time_contributor", return_value=(False, 9)
                ):
                    with mock.patch("firstpr.llm.complete") as complete:
                        code, out, _ = _run(
                            "review",
                            "--repo",
                            str(root),
                            "--diff",
                            str(root / "change.diff"),
                            "--only-first-time",
                            "--author",
                            "veteran",
                        )

        self.assertEqual(code, 0)
        complete.assert_not_called()
        self.assertIn("not a first-time contributor", out)

    def test_empty_diff_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(tmp)
            empty = root / "empty.diff"
            empty.write_text("", encoding="utf-8")
            code, out, err = _run(
                "review", "--repo", str(root), "--diff", str(empty)
            )

        self.assertEqual(code, 0)
        self.assertIn("empty", err)


class DoctorTests(unittest.TestCase):
    def test_reports_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(tmp)
            with mock.patch.dict(os.environ, CLEAR_KEY, clear=False):
                code, out, _ = _run("doctor", "--repo", str(root))

        self.assertEqual(code, 0)
        self.assertIn("conventions found:", out)
        self.assertIn("CONTRIBUTING.md", out)
        self.assertIn("MISSING", out)

    def test_sizes_a_given_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(tmp)
            with mock.patch.dict(os.environ, CLEAR_KEY, clear=False):
                code, out, _ = _run("doctor", "--repo", str(root), "--diff", str(root / "change.diff"))

        self.assertEqual(code, 0)
        self.assertIn("prompt", out)


class InitTests(unittest.TestCase):
    def test_writes_template_then_refuses_to_clobber(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, _, _ = _run("init", "--repo", str(root))
            self.assertEqual(code, 0)
            self.assertTrue((root / CONFIG_FILENAME).is_file())

            code, _, err = _run("init", "--repo", str(root))
            self.assertEqual(code, 1)
            self.assertIn("already exists", err)

    def test_force_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / CONFIG_FILENAME).write_text("[firstpr]\nmodel='old'\n", encoding="utf-8")
            code, _, _ = _run("init", "--repo", str(root), "--force")

        self.assertEqual(code, 0)

    def test_template_is_valid_toml(self) -> None:
        import tomllib

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _run("init", "--repo", str(root), "--force")
            data = tomllib.loads((root / CONFIG_FILENAME).read_text(encoding="utf-8"))

        self.assertIn("firstpr", data)


class ParserTests(unittest.TestCase):
    def test_version_flag(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            _run("--version")
        self.assertEqual(caught.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
