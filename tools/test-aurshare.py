#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""Unit tests for aurshare.py CLI (P14 argument validation) and shared assets."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AURSHARE = ROOT / "aurshare.py"
sys.path.insert(0, str(ROOT))
import aurshare  # noqa: E402


class AurshareCliTests(unittest.TestCase):
    def run_cli(self, *args, share_root=None):
        env = dict(os.environ)
        if share_root is not None:
            env["AUR_SHARE_ROOT"] = str(share_root)
        return subprocess.run(
            [sys.executable, "-I", str(AURSHARE), *args],
            env=env,
            capture_output=True,
            text=True,
        )

    def test_p14_help_and_bad_or_missing_arguments(self):
        for argv in (["-h"], ["--help"], ["devlink", "--help"], ["verify", "-h"]):
            r = self.run_cli(*argv)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("usage:", r.stdout)

        bad_invocations = (
            ["--bogus"],
            ["--help", "extra"],
            ["devlink", "--bogus"],
            ["devlink", "/nonexistent/aur-root"],
            ["devlink", str(ROOT), "no-such-asset.py"],
            ["install", "/nonexistent/proj"],
            ["link", "/nonexistent/proj"],
            ["discover", "/nonexistent/root"],
            ["verify", "extra"],
            ["list", "extra"],
            ["canonical", "a", "b"],
            ["deploy"],
            ["deploy", str(ROOT)],
            ["devlink"],
            ["install"],
            ["link"],
            ["register"],
            ["discover"],
        )
        for argv in bad_invocations:
            r = self.run_cli(*argv)
            self.assertEqual(r.returncode, 2, f"{argv} returned {r.returncode}: {r.stdout}{r.stderr}")
            self.assertIn("usage:", r.stderr, f"missing usage for {argv}")
            self.assertNotIn("Traceback", r.stderr, f"traceback leaked for {argv}")

    def test_canonical_checks_tracked_assets_hermetically(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "aur"
            repo.mkdir()
            git = ["git", "-c", "user.name=t", "-c", "user.email=t@localhost"]
            subprocess.run(git + ["init", "-q", "--template=", str(repo)], check=True, capture_output=True)
            for rel in aurshare.SHARED_ASSETS.values():
                p = repo / rel.rstrip("/")
                if rel.endswith("/"):
                    p.mkdir(parents=True, exist_ok=True)
                    (p / "keep").write_text("ok\n")
                    subprocess.run(["git", "-C", str(repo), "add", str(p / "keep")],
                                   check=True, capture_output=True)
                else:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text("ok\n")
                    subprocess.run(["git", "-C", str(repo), "add", str(p)],
                                   check=True, capture_output=True)
            subprocess.run(git + ["-C", str(repo), "commit", "-q", "-m", "seed"],
                           check=True, capture_output=True)
            self.assertEqual(self.run_cli("canonical", str(repo)).returncode, 0)

            # Untracked shared asset -> non-zero canonical refusal
            subprocess.run(["git", "-C", str(repo), "rm", "-q", "--cached", "tools/transcript.py"],
                           check=True, capture_output=True)
            r = self.run_cli("canonical", str(repo))
            self.assertEqual(r.returncode, 1)
            self.assertIn("UNTRACKED", r.stdout + r.stderr)

    def test_isolated_devlink_and_verify(self):
        with tempfile.TemporaryDirectory() as d:
            share = Path(d) / "share"
            self.assertEqual(self.run_cli("devlink", str(ROOT), share_root=share).returncode, 0)
            r = self.run_cli("verify", share_root=share)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("clean", r.stdout)


if __name__ == "__main__":
    unittest.main()
