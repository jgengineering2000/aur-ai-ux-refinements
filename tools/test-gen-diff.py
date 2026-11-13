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
            for bad in (["--bogus"], ["--base"], ["--base", "nope"], ["--group"], ["--group", ""], ["stray.py"]):
                r = self.run_script(*bad, cwd=d)
                self.assertNotEqual(r.returncode, 0, f"expected non-zero for {bad}")
                self.assertFalse((Path(d) / "aur-docs").exists(), f"side effect for {bad}")

    def _assert_doc_contract(self, html):
        self.assertIn('<link rel="stylesheet" href="/ann.css">', html)
        self.assertIn('<link rel="stylesheet" href="/doc.css">', html)
        self.assertIn('<script src="/manifest-link.js"></script>', html)
        self.assertIn('<script src="/ann.js"></script>', html)
        self.assertNotIn(':root', html)
        style_m = re.search(r"<style>(.*?)</style>", html, re.S)
        self.assertIsNotNone(style_m)
        self.assertNotRegex(style_m.group(1), r"#[0-9a-fA-F]{3,8}\b")

    def test_clean_repo_also_emits_manifest_and_annotation_scripts(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            git("init", "-q", cwd=repo)
            (repo / "a.txt").write_text("hi\n")
            git("add", "a.txt", cwd=repo)
            git("commit", "-q", "-m", "init", cwd=repo)
            r = self.run_script(cwd=repo)
            self.assertEqual(r.returncode, 0, r.stderr)
            html = (repo / r.stdout.strip().splitlines()[-1]).read_text()
            self._assert_doc_contract(html)
            self.assertIn("No changes.", html)

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

            self._assert_doc_contract(html)
            self.assertEqual(html.count("UNIQUE_STAGED_MARKER"), 1, "staged diff must not duplicate")
            self.assertNotIn('\\"', html)
            self.assertIn("document.querySelectorAll('details.file')", html)
            self.assertRegex(html, re.compile(r"Python work.*Uncategorized", re.S))
            self.assertIn("b.txt", html)

    def test_invalid_base_in_repo_and_unicode_binary_language_buckets(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            git("init", "-q", cwd=repo)
            (repo / "café.py").write_text("x = 1\n")
            (repo / "run.sh").write_text("echo 1\n")
            (repo / "app.ts").write_text("export const x = 1\n")
            (repo / "pyproject.toml").write_text("[project]\n")
            (repo / "blob.bin").write_bytes(b"\x00\x01\x02")
            git("add", "café.py", "run.sh", "app.ts", "pyproject.toml", "blob.bin", cwd=repo)
            git("commit", "-q", "-m", "init", cwd=repo)

            bad = self.run_script("--base", "does-not-exist", cwd=repo)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("unknown revision", bad.stderr)
            self.assertFalse((repo / "aur-docs").exists())

            (repo / "café.py").write_text("x = 2\n")
            (repo / "run.sh").write_text("echo 2\n")
            (repo / "app.ts").write_text("export const x = 2\n")
            (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
            (repo / "blob.bin").write_bytes(b"\x00\xff\xfe")

            r = self.run_script(cwd=repo)
            self.assertEqual(r.returncode, 0, r.stderr)
            html = (repo / r.stdout.strip().splitlines()[-1]).read_text()
            self._assert_doc_contract(html)
            for expected in ("café.py", "Python (1)", "Shell (1)", "JS/TS (1)", "Config (1)", "binary"):
                self.assertIn(expected, html)


if __name__ == "__main__":
    unittest.main()
