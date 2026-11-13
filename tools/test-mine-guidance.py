#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""Unit and CLI tests for tools/mine-guidance.py."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MINE_GUIDANCE = ROOT / "tools" / "mine-guidance.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("mine_guidance", MINE_GUIDANCE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mine_guidance = _load_module()


def _write_jsonl(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


class MineGuidanceTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-I", str(MINE_GUIDANCE), *args],
            capture_output=True,
            text=True,
        )

    def test_p14_cli_validation(self):
        for flag in ("-h", "--help"):
            r = self.run_cli(flag)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Mine owner interaction guidance.", r.stdout)

        bad_args = (
            ["--bogus"],
            ["--min-score", "0"],
            ["--min-score", "-2"],
            ["--min-score", "abc"],
            ["--category", "no-such-category"],
        )
        for argv in bad_args:
            r = self.run_cli(*argv)
            self.assertEqual(r.returncode, 2, f"{argv} -> {r.returncode}: {r.stdout}{r.stderr}")
            self.assertIn("usage:", r.stderr)
            self.assertNotIn("Traceback", r.stderr)

    def test_missing_projects_returns_1(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_cli("--projects", str(Path(d) / "empty"))
            self.assertEqual(r.returncode, 1)
            self.assertIn("no transcripts under", r.stderr)

    def test_owner_messages_uses_transcript_owner_items(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "abcdef12-0000-0000-0000-000000000000.jsonl"
            records = [
                # Real owner guidance following an IDE tag (old NOISE regex dropped this)
                {
                    "type": "user",
                    "timestamp": "2026-11-10T10:00:00Z",
                    "message": {
                        "content": (
                            "<ide_opened_file>docs/spec.md</ide_opened_file>\n"
                            "Explain why before changing code so I can follow your reasoning cleanly."
                        )
                    },
                },
                # Bracketed owner emphasis (old NOISE regex dropped `[note]` prefixes)
                {
                    "type": "user",
                    "timestamp": "2026-11-10T10:05:00Z",
                    "message": {
                        "content": "[rule] Don't bury the actual answer in fluff; give me a short summary first."
                    },
                },
                # Queued owner attachment with None timestamp
                {
                    "type": "attachment",
                    "attachment": {
                        "type": "queued_command",
                        "origin": {"kind": "human"},
                        "prompt": "Stop dumping huge tables and always link back to the rationale you used.",
                    },
                },
                # Excluded: tool_result
                {
                    "type": "user",
                    "timestamp": "2026-11-10T10:06:00Z",
                    "message": {
                        "content": [
                            {"type": "tool_result", "tool_use_id": "t1", "content": "explain why"},
                            {"type": "text", "text": "Explain why and provide more context in tables please."},
                        ]
                    },
                },
                # Excluded: isMeta / isCompactSummary / non-human origin / scaffolding only / short / assistant
                {
                    "type": "user",
                    "isMeta": True,
                    "timestamp": "2026-11-10T10:07:00Z",
                    "message": {"content": "Explain why and stop doing walls of text in summaries."},
                },
                {
                    "type": "user",
                    "isCompactSummary": True,
                    "timestamp": "2026-11-10T10:08:00Z",
                    "message": {"content": "Explain why and stop doing walls of text in summaries."},
                },
                {
                    "type": "user",
                    "origin": {"kind": "task-notification"},
                    "timestamp": "2026-11-10T10:09:00Z",
                    "message": {"content": "Explain why and stop doing walls of text in summaries."},
                },
                {
                    "type": "user",
                    "timestamp": "2026-11-10T10:10:00Z",
                    "message": {"content": "<task-notification>explain why stop walls of text</task-notification>"},
                },
                {
                    "type": "user",
                    "timestamp": "2026-11-10T10:11:00Z",
                    "message": {"content": "Short ask explain why"},
                },
                {
                    "type": "assistant",
                    "timestamp": "2026-11-10T10:12:00Z",
                    "message": {"content": [{"type": "text", "text": "Here is the rationale and summary table."}]},
                },
            ]
            p.write_text("\n".join(json.dumps(r) for r in records) + "\n{not valid json\n")

            msgs = mine_guidance.owner_messages([str(p)])
            texts = [t for _date, _sid, t in msgs]
            self.assertEqual(len(msgs), 3, msgs)
            self.assertEqual(msgs[0][0], "")
            self.assertIn("Stop dumping huge tables", texts[0])
            self.assertIn("Explain why before changing code", texts[1])
            self.assertIn("[rule] Don't bury", texts[2])

    def test_scoring_filtering_dedup_and_full_mode(self):
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d) / "proj-a"
            proj.mkdir()
            long_tail = " detail" * 80
            m_multi = (
                "Explain why and give more context so I know the rationale, "
                "and keep it brief without wall of text or burying the lead."
                + long_tail
            )
            m_dup = m_multi[:75] + " extra trailing variant."
            m_single = "Use a single clean table with alignment instead of prose."
            _write_jsonl(
                proj / "11112222-aaaa.jsonl",
                [
                    {"type": "user", "timestamp": "2026-11-11T09:00:00Z", "message": {"content": m_multi}},
                    {"type": "user", "timestamp": "2026-11-11T09:01:00Z", "message": {"content": m_dup}},
                    {"type": "user", "timestamp": "2026-11-11T09:02:00Z", "message": {"content": m_single}},
                ],
            )

            r = self.run_cli("--projects", str(Path(d) / "*"))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("3 owner messages scanned, 1 carry 2+ interaction categories", r.stdout)
            self.assertNotIn("extra trailing variant", r.stdout)
            body_line = [ln for ln in r.stdout.splitlines() if ln.startswith("   ")][0]
            self.assertEqual(len(body_line) - 3, 400)

            r_cat = self.run_cli(
                "--projects", str(Path(d) / "*"),
                "--min-score", "1",
                "--category", "structure",
                "--full",
            )
            self.assertEqual(r_cat.returncode, 0, r_cat.stderr)
            self.assertIn("1 carry 1+ interaction categories", r_cat.stdout)
            self.assertIn("Use a single clean table with alignment", r_cat.stdout)

            r_full = self.run_cli("--projects", str(Path(d) / "*"), "--full")
            self.assertEqual(r_full.returncode, 0, r_full.stderr)
            full_line = [ln for ln in r_full.stdout.splitlines() if ln.startswith("   ")][0]
            self.assertGreater(len(full_line), 450)


if __name__ == "__main__":
    unittest.main()
