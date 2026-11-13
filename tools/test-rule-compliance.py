#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""Hermetic unit tests for tools/rule-compliance.py (inference, filters, CSV, P14)."""

import csv
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE / "rule-compliance.py"


def load_module():
    spec = importlib.util.spec_from_file_location("rule_compliance", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RC = load_module()


def user(text, ts, **extra):
    rec = {"type": "user", "timestamp": ts,
           "message": {"content": [{"type": "text", "text": text}]}}
    rec.update(extra)
    return rec


def assistant(text, ts):
    return {"type": "assistant", "timestamp": ts,
            "message": {"content": [{"type": "text", "text": text}]}}


def write_jsonl(path, records):
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def substantive_reply(prefix=""):
    body = (
        f"{prefix}I understand that you want to verify the pipeline safely.\n\n"
        "Where this sits: inside the CLI verification layer.\n"
        "What is designed vs implemented: both run sequentially.\n"
        "What happens today: unvalidated inputs drifted quietly. "
        "Before: errors went unseen. After: inputs fail fast.\n"
        "- Why: a clear contract prevents silent misattribution across steps.\n\n"
        "## Options\n"
        "- Option A (recommended) vs Option B.\n"
        "## Decisions\n"
        "- I agree that the contract holds; I recommend Option A (the alternative is slower).\n"
        "Should we apply this check across rule-compliance.py?\n"
        "Effort = 5. Model settings are appropriate.\n"
    )
    assert len(body) >= RC.SUBSTANTIVE_CHARS
    return body


class RuleComplianceTests(unittest.TestCase):
    def run_cli(self, *args, cwd=None, home=None):
        env = dict(os.environ)
        if home is not None:
            env["HOME"] = str(home)
        return subprocess.run(
            [sys.executable, "-I", str(TOOL), *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_default_project_dir_infers_git_root_and_falls_back_to_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fake_home = root / "acct"
            fake_home.mkdir()
            repo = root / "work" / "sample-proj"
            sub = repo / "nested" / "dir"
            sub.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
            plain = root / "outside"
            plain.mkdir()

            old_cwd = os.getcwd()
            old_home = os.environ.get("HOME")
            try:
                os.environ["HOME"] = str(fake_home)
                os.chdir(sub)
                inferred_git = RC.default_project_dir()
                self.assertEqual(
                    inferred_git,
                    fake_home / ".claude" / "projects" / str(repo.resolve()).replace("/", "-"),
                )
                os.chdir(plain)
                inferred_cwd = RC.default_project_dir()
                self.assertEqual(
                    inferred_cwd,
                    fake_home / ".claude" / "projects" / str(plain.resolve()).replace("/", "-"),
                )
            finally:
                os.chdir(old_cwd)
                if old_home is not None:
                    os.environ["HOME"] = old_home
                else:
                    os.environ.pop("HOME", None)

    def test_cli_infers_project_dir_from_git_cwd_and_outputs_csv_and_table(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fake_home = root / "acct"
            repo = root / "sample-proj"
            sub = repo / "sub"
            sub.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)

            encoded = str(repo.resolve()).replace("/", "-")
            proj_dir = fake_home / ".claude" / "projects" / encoded
            proj_dir.mkdir(parents=True)

            # Session 1 (old, filtered out by --since): non-owner noise must NOT count as ask
            write_jsonl(proj_dir / "11111111.jsonl", [
                user("old ask", "2026-08-01T10:00:00Z"),
                assistant(substantive_reply(), "2026-08-01T10:00:05Z"),
            ])
            # Session 2 (in window): compaction + peer hand-back + pure confirmation ignored;
            # owner text alongside <ide_opened_file> counts as 1 ask.
            write_jsonl(proj_dir / "22222222.jsonl", [
                user("compaction summary", "2026-09-10T10:00:00Z", isCompactSummary=True),
                assistant(substantive_reply(), "2026-09-10T10:00:05Z"),
                user("subagent done", "2026-09-10T10:01:00Z", origin={"kind": "peer"}),
                assistant(substantive_reply(), "2026-09-10T10:01:05Z"),
                user("yes", "2026-09-10T10:02:00Z"),
                assistant(substantive_reply(), "2026-09-10T10:02:05Z"),
                user("<ide_opened_file>a.py</ide_opened_file>\nplease audit rule-compliance.py",
                     "2026-09-10T10:03:00Z"),
                assistant(substantive_reply(), "2026-09-10T10:03:05Z"),
                assistant("short ok", "2026-09-10T10:03:10Z"),
            ])

            r_csv = self.run_cli("--since", "2026-09-01", "--csv", cwd=sub, home=fake_home)
            self.assertEqual(r_csv.returncode, 0, r_csv.stderr)
            rows = list(csv.DictReader(io.StringIO(r_csv.stdout)))
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["session"], "22222222")
            self.assertEqual(row["turns"], "4")
            self.assertEqual(row["asks"], "1")
            self.assertEqual(row["understand"], "1")
            self.assertEqual(row["ground"], "1")
            self.assertEqual(row["recommend"], "1")
            self.assertEqual(row["asking"], "4")
            self.assertEqual(row["charblock"], "4")

            r_tbl = self.run_cli("--project", str(proj_dir), cwd=root, home=fake_home)
            self.assertEqual(r_tbl.returncode, 0, r_tbl.stderr)
            self.assertIn("22222222", r_tbl.stdout)
            self.assertIn("11111111", r_tbl.stdout)
            self.assertIn("Effort values printed: 5×5", r_tbl.stdout)

    def test_p14_cli_validation_and_empty_error_paths(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_cli("--help", cwd=d).returncode, 0)
            self.assertEqual(self.run_cli("-h", cwd=d).returncode, 0)
            for bad in (["--bogus"], ["--since", "2026/09/01"], ["--project"]):
                r = self.run_cli(*bad, cwd=d)
                self.assertNotEqual(r.returncode, 0, f"expected non-zero for {bad}")
            missing = Path(d) / "missing-proj"
            r_missing = self.run_cli("--project", str(missing), cwd=d)
            self.assertEqual(r_missing.returncode, 1)
            self.assertIn("no transcripts under", r_missing.stderr)

            # Empty directory / only short replies -> "no sessions matched"
            empty_dir = Path(d) / "empty-proj"
            empty_dir.mkdir()
            write_jsonl(empty_dir / "tiny.jsonl", [
                user("hi", "2026-09-10T10:00:00Z"),
                assistant("too short", "2026-09-10T10:00:05Z"),
            ])
            r_empty = self.run_cli("--project", str(empty_dir), cwd=d)
            self.assertEqual(r_empty.returncode, 1)
            self.assertIn("no sessions matched", r_empty.stderr)


if __name__ == "__main__":
    unittest.main()
