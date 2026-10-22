from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from firstpr import context as ctx  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class GatherTests(unittest.TestCase):
    def test_detects_ecosystem_and_ci_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "pyproject.toml", "[project]\nname='x'\n")
            _write(root / "CONTRIBUTING.md", "# Contributing\nRun the tests first.\n")
            _write(
                root / ".github/workflows/ci.yml",
                "jobs:\n  test:\n    steps:\n      - run: pytest -q\n      - run: ruff check .\n",
            )

            context = ctx.gather(root)

            self.assertEqual(context.kind, "python")
            self.assertIn("pytest -q", context.commands)
            self.assertIn("ruff check .", context.commands)
            self.assertIn("CONTRIBUTING.md", context.docs)

    def test_falls_back_to_default_command_without_ci(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "Cargo.toml", "[package]\nname='x'\n")

            context = ctx.gather(root)

            self.assertEqual(context.kind, "rust")
            self.assertEqual(context.commands, ["cargo test"])

    def test_skips_templated_ci_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / ".github/workflows/ci.yml",
                "steps:\n  - run: pytest --junitxml=${{ runner.temp }}/r.xml\n",
            )

            context = ctx.gather(root)

            self.assertEqual(context.commands, [])

    def test_reads_block_scalar_runs(self) -> None:
        """`run: |` is the common form; missing it would silently disable this."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / ".github/workflows/ci.yml",
                "jobs:\n"
                "  test:\n"
                "    steps:\n"
                "      - run: |\n"
                "          pytest -q\n"
                "          ruff check .\n"
                "        name: test\n",
            )

            context = ctx.gather(root)

            self.assertIn("pytest -q", context.commands)
            self.assertIn("ruff check .", context.commands)
            self.assertNotIn("name: test", context.commands)

    def test_rejects_shell_chains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / ".github/workflows/ci.yml",
                "steps:\n  - run: pip install -e . && pytest\n",
            )

            self.assertEqual(ctx.gather(root).commands, [])

    def test_packaging_steps_are_not_offered_as_advice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / ".github/workflows/release.yml",
                "steps:\n"
                "  - run: python -m pip install --quiet build\n"
                "  - run: python -m build\n",
            )

            self.assertEqual(ctx.gather(root).commands, [])

    def test_test_commands_rank_before_build_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / ".github/workflows/ci.yml",
                "steps:\n  - run: make build\n  - run: make lint\n  - run: pytest -q\n",
            )

            commands = ctx.gather(root).commands

        self.assertEqual(commands[0], "pytest -q")
        self.assertEqual(commands[1], "make lint")

    def test_case_variant_template_is_read_once(self) -> None:
        """Uppercase and lowercase template paths are one file on Windows/macOS."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / ".github/PULL_REQUEST_TEMPLATE.md", "## What changed\n")

            templates = [
                key for key in ctx.gather(root).docs if "pull_request_template" in key.lower()
            ]

        self.assertEqual(len(templates), 1)

    def test_budget_truncates_long_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "CONTRIBUTING.md", "a" * 20000)

            context = ctx.gather(root)

            body = context.docs["CONTRIBUTING.md"]
            self.assertLessEqual(len(body), ctx.MAX_FILE_CHARS + 80)
            self.assertIn("truncated", body)
            self.assertTrue(any("truncated" in note for note in context.notes))

    def test_empty_repository_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = ctx.gather(Path(tmp))

            self.assertTrue(context.is_empty)
            self.assertIn("no documented rules", context.as_prompt_block().lower())

    def test_missing_directory_raises(self) -> None:
        with self.assertRaises(ctx.ContextError):
            ctx.gather(Path(tempfile.gettempdir()) / "firstpr-does-not-exist-xyz")


class TruncateDiffTests(unittest.TestCase):
    def test_short_diff_untouched(self) -> None:
        diff = "diff --git a/x b/x\n+hello\n"
        self.assertEqual(ctx.truncate_diff(diff), diff)

    def test_cuts_on_file_boundaries(self) -> None:
        chunk = "diff --git a/f{0} b/f{0}\n" + "+line\n" * 50
        diff = "".join(chunk.format(i) for i in range(20))

        trimmed = ctx.truncate_diff(diff, limit=2000)

        self.assertLessEqual(len(trimmed), 2000 + 200)
        self.assertIn("further file(s) not shown", trimmed)
        self.assertTrue(trimmed.startswith("diff --git a/f0"))
        # Every retained chunk is complete: a stray half-hunk would confuse the model.
        self.assertTrue(trimmed.rstrip().endswith("+line") or "not shown" in trimmed)

    def test_single_oversized_file_still_returns_content(self) -> None:
        diff = "diff --git a/big b/big\n" + "+x\n" * 5000

        trimmed = ctx.truncate_diff(diff, limit=1000)

        self.assertLessEqual(len(trimmed), 1000 + 80)
        self.assertIn("single file larger than budget", trimmed)


class ReadDiffTests(unittest.TestCase):
    def test_reads_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "change.diff"
            path.write_text("diff --git a/a b/a\n", encoding="utf-8")

            self.assertIn("diff --git", ctx.read_diff(str(path)))

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(ctx.ContextError):
            ctx.read_diff("/tmp/definitely-not-a-real-diff-file.diff")


if __name__ == "__main__":
    unittest.main()
