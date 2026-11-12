#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""Tests for tools/gen-diff.sh (diff grouping, staged deduplication, JS quoting, P14)."""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GEN_DIFF = Path(__file__).resolve().parent / "gen-diff.sh"


def git(*args, cwd):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@localhost",
                 GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@localhost"),
        check=True,
        capture_output=True,
        text=True,
    )


class GenDiffTests(unittest.TestCase):
    def run_script(self, *args, cwd=None):
        return subprocess.run(["bash", str(GEN_DIFF), *args], cwd=cwd, capture_output=True, text=True)

    def test_p14_cli_before_side_effects(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_script("--help", cwd=d).returncode, 0)
            self.assertEqual(self.run_script("-h", cwd=d).returncode, 0)
            for bad in (["--bogus"], ["--base"], ["--group"], ["--group", ""]):
                r = self.run_script(*bad, cwd=d)
                self.assertNotEqual(r.returncode, 0, f"expected non-zero for {bad}")
                self.assertFalse((Path(d) / "aur-docs").exists(), f"side effect for {bad}")

    def test_staged_diff_not_duplicated_and_valid_js_controls(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            git("init", "-q", cwd=repo)
            (repo / "a.py").write_text("x = 1\n")
            (repo / "b.txt").write_text("hello\n")
            git("add", "a.py", "b.txt", cwd=repo)
            git("commit", "-q", "-m", "init", cwd=repo)

            (repo / "a.py").write_text("x = 1\nUNIQUE_STAGED_MARKER = 42\n")
            git("add", "a.py", cwd=repo)
            (repo / "b.txt").write_text("hello\nworld\n")

            r = self.run_script("--group", "Python work", "a.py", cwd=repo)
            self.assertEqual(r.returncode, 0, r.stderr)
            out_path = repo / r.stdout.strip().splitlines()[-1]
            html = out_path.read_text()

            self.assertEqual(html.count("UNIQUE_STAGED_MARKER"), 1, "staged diff must not duplicate")
            self.assertNotIn('\\"', html)
            self.assertIn("document.querySelectorAll('details.file')", html)
            self.assertRegex(html, re.compile(r"Python work.*Uncategorized", re.S))
            self.assertIn("b.txt", html)


if __name__ == "__main__":
    unittest.main()
