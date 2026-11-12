#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""Unit tests for tools/transcript-prompts.py (typed, queued, filters, P14 CLI).

Run: python3 -I tools/test-transcript-prompts.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "transcript-prompts.py")


def user(text, ts, **extra):
    rec = {"type": "user", "timestamp": ts,
           "message": {"content": [{"type": "text", "text": text}]}}
    rec.update(extra)
    return rec


def queued(prompt, ts, origin=None, mode="prompt", inner_ts=True):
    a = {"type": "queued_command", "prompt": prompt, "commandMode": mode}
    if origin is not None:
        a["origin"] = origin
    if inner_ts:
        a["timestamp"] = ts
    rec = {"type": "attachment", "attachment": a}
    if not inner_ts:
        rec["timestamp"] = ts
    return rec


class TranscriptPromptsTests(unittest.TestCase):
    def run_raw(self, *args):
        return subprocess.run([sys.executable, "-I", TOOL, *args],
                              capture_output=True, text=True)

    def run_tool(self, recs, *args):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "abcdef1234.jsonl")
            with open(p, "w") as f:
                for r in recs:
                    f.write(json.dumps(r) + "\n")
            r = self.run_raw(p, *args)
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout

    def test_queued_list_prompt_is_owner_text_with_marker(self):
        out = self.run_tool([
            user("typed alpha", "2026-01-02T03:04:05.000Z"),
            queued([{"type": "text", "text": "queued beta"}],
                   "2026-01-02T03:05:06.000Z", {"kind": "human"}),
        ])
        self.assertIn("typed alpha", out)
        line = [l for l in out.splitlines() if "queued beta" in l]
        self.assertEqual(len(line), 1, out)
        self.assertIn("2026-01-02 03:05", line[0])
        self.assertIn("queued", line[0].split("]")[0])

    def test_queued_string_prompt_and_record_level_timestamp(self):
        out = self.run_tool([
            queued("queued gamma", "2026-01-02T04:00:00.000Z", None, inner_ts=False)
        ])
        self.assertIn("queued gamma", out)
        self.assertIn("2026-01-02 04:00", out)

    def test_queued_non_human_is_not_owner_text(self):
        out = self.run_tool([
            queued("peer words", "2026-01-02T04:00:00.000Z", {"kind": "peer"}),
            queued("<task-notification>x</task-notification>", "2026-01-02T04:01:00.000Z",
                   {"kind": "task-notification"}, mode="task-notification"),
        ])
        self.assertEqual(out.strip(), "")

    def test_metadata_filters_drop_meta_compact_and_peer_user_records(self):
        out = self.run_tool([
            user("real owner prompt", "2026-01-02T04:00:00.000Z"),
            user("image size note", "2026-01-02T04:01:00.000Z", isMeta=True),
            user("compaction summary", "2026-01-02T04:02:00.000Z", isCompactSummary=True),
            user("subagent hand-back", "2026-01-02T04:03:00.000Z", origin={"kind": "peer"}),
        ])
        self.assertEqual(out.strip().splitlines(), ["[abcdef12 #001 2026-01-02 04:00] real owner prompt"])

    def test_queue_operation_duplicates_are_ignored(self):
        out = self.run_tool([
            {"type": "queue-operation", "operation": "enqueue", "content": "dup delta",
             "timestamp": "2026-01-02T05:00:00.000Z"},
            queued([{"type": "text", "text": "dup delta"}],
                   "2026-01-02T05:00:00.000Z", {"kind": "human"}),
        ])
        self.assertEqual(out.count("dup delta"), 1, out)

    def test_grep_applies_to_queued(self):
        recs = [queued([{"type": "text", "text": "queued epsilon"}],
                       "2026-01-02T06:00:00.000Z", {"kind": "human"})]
        self.assertIn("epsilon", self.run_tool(recs, "--grep", "EPSILON"))
        self.assertEqual(self.run_tool(recs, "--grep", "zzz").strip(), "")

    def test_p14_cli_validation(self):
        self.assertEqual(self.run_raw("--help").returncode, 0)
        self.assertEqual(self.run_raw("-h").returncode, 0)
        for bad in ([], ["--bogus"], ["--grep"], ["--max"], ["--max", "abc"], ["--max", "0"]):
            r = self.run_raw(*bad)
            self.assertNotEqual(r.returncode, 0, f"expected non-zero for {bad}")


if __name__ == "__main__":
    unittest.main()
