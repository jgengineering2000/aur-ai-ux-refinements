#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Gate for token-usage.py against a synthetic project built in a temp directory.

The fixture is a home directory holding a project and its transcripts: two main
transcripts (one begun before the window), a delegate under the first, a stale
file the scan must not open, a directory masquerading as a transcript, a corrupt
line, streamed duplicate records inside and across files, a zero-usage
synthetic record, records either side of the window, images in tool results
(whose record repeats the image outside the message), a pasted image in an
owner prompt, harness scaffolding that is not a request, and a git history with
commits either side of the window. Every number asserted is worked out by hand
in the comments beside the fixture, not read back from the tool.

Usage:
    tools/test-token-usage.py [--verbose]
"""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
TOOL = HERE / "token-usage.py"

_spec = importlib.util.spec_from_file_location("token_usage", TOOL)
tu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tu)

OPUS, SONNET = "claude-opus-5", "claude-sonnet-5-5"
MAIN1 = "11111111-aaaa-bbbb-cccc-000000000001"
MAIN2 = "22222222-aaaa-bbbb-cccc-000000000002"
STALE = "33333333-aaaa-bbbb-cccc-000000000003"
AGENT = "agent-a0000001"


def assistant(mid, rid, ts, model, inp, cw, cr, out, block="tool_use"):
    return {"type": "assistant", "timestamp": ts, "requestId": rid,
            "message": {"id": mid, "model": model, "role": "assistant",
                        "content": [{"type": block}],
                        "usage": {"input_tokens": inp,
                                  "cache_creation_input_tokens": cw,
                                  "cache_read_input_tokens": cr,
                                  "output_tokens": out}}}


def prompt(ts, text, extra=None):
    content = text if extra is None else [{"type": "text", "text": text}] + extra
    return {"type": "user", "timestamp": ts,
            "message": {"role": "user", "content": content}}


def tool_result(ts, images):
    blocks = [{"type": "image", "source": {"type": "base64", "data": "AAAA"}}] * images
    return {"type": "user", "timestamp": ts,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": [{"type": "text", "text": "ok"}] + blocks}]},
            "toolUseResult": {"type": "image", "file": {"base64": "AAAA"}}}


def boundary(ts, trigger, pre, post):
    """The record Claude Code writes where a conversation is compacted."""
    return {"type": "system", "subtype": "compact_boundary",
            "content": "Conversation compacted", "level": "info", "timestamp": ts,
            "compactMetadata": {"trigger": trigger, "preTokens": pre,
                                "postTokens": post}}


def compact_summary(ts):
    """The harness's summary of what was dropped, typed `user` but not a request."""
    return {"type": "user", "isCompactSummary": True, "isVisibleInTranscriptOnly": True,
            "timestamp": ts,
            "message": {"role": "user", "content":
                        "This session is being continued from a previous conversation "
                        "that ran out of context. The summary below covers it."}}


def call(mid, ts, ctx, model=OPUS):
    """One API call whose context (input + cache write + cache read) is `ctx`."""
    return assistant(mid, "r" + mid, ts, model, 1, 0, ctx - 1, 1)


def write_agent(fx, session, name, records, meta=None, raw_meta=None):
    """A delegate transcript under `session`, with its sibling `.meta.json`
    holding `meta` (a dict), or `raw_meta` verbatim, or nothing."""
    d = fx.root / session / "subagents"
    write_jsonl(d / f"agent-{name}.jsonl", records)
    if raw_meta is not None:
        (d / f"agent-{name}.meta.json").write_text(raw_meta)
    elif meta is not None:
        (d / f"agent-{name}.meta.json").write_text(json.dumps(meta))


def write_jsonl(path, records, compact=True, extra_raw=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    sep = (",", ":") if compact else None
    with open(path, "w") as fh:
        for r in records:
            fh.write(json.dumps(r, separators=sep) + "\n")
        for raw in extra_raw:
            fh.write(raw + "\n")


def git(repo, *args, when=None):
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    if when:
        env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t",
                    "-c", "user.email=t@localhost", *args],
                   check=True, capture_output=True, env=env)


class Fixture:
    """A temp HOME with a project directory, its transcripts and a git log."""

    def __init__(self, with_git=True):
        self._td = tempfile.TemporaryDirectory(prefix="token-usage-test-")
        self.home = Path(self._td.name) / "home"
        self.project = self.home / "work" / "my.proj"
        self.project.mkdir(parents=True)
        self.root = self.home / ".claude" / "projects" / re.sub(
            r"[^A-Za-z0-9]", "-", str(self.project))
        self.root.mkdir(parents=True)
        self.env = {"HOME": str(self.home)}
        if with_git:
            subprocess.run(["git", "init", "-q", "--template=", str(self.project)],
                           check=True, capture_output=True)
            for when in ("2026-10-01T23:59:00+0000", "2026-10-02T12:00:00+0000",
                         "2026-10-04T23:00:00+0000", "2026-10-05T00:30:00+0000"):
                git(self.project, "commit", "-q", "--allow-empty", "-m", "c", when=when)

    def close(self):
        self._td.cleanup()

    def run(self, *argv, progress=None):
        out = io.StringIO()
        with mock.patch.dict(os.environ, self.env):
            rc = tu.run(["--project", str(self.project), *argv], out=out,
                        progress=progress)
        return rc, out.getvalue()

    def result(self, *argv):
        rc, text = self.run("--json", *argv)
        assert rc == 0
        return json.loads(text)

    def cli(self, *argv, cwd=None):
        env = dict(os.environ, **self.env)
        return subprocess.run([sys.executable, str(TOOL), *argv], capture_output=True,
                              text=True, env=env, cwd=cwd or self.project)


def build_standard(fx):
    # main1: one prompt before the window (sets the first prompt, counts
    # nowhere), then calls m5, m1, m2, m3, m4 inside it.
    main1 = [
        {"type": "queue-operation", "operation": "enqueue",
         "timestamp": "2026-10-02T00:00:00.000Z"},
        prompt("2026-10-01T23:59:59.999Z", "First   owner\nprompt about the sharpener"),
        # m5: window opens at 00:00:00.000, inclusive. in 2 + write 800 + read 0.
        assistant("m5", "r5", "2026-10-02T00:00:00.000Z", OPUS, 2, 800, 0, 3),
        # m1 streamed in two chunks: output grows 5 -> 40, context is one call.
        assistant("m1", "r1", "2026-10-02T09:00:01.000Z", OPUS, 3, 1000, 0, 5, "thinking"),
        assistant("m1", "r1", "2026-10-02T09:00:02.000Z", OPUS, 3, 1000, 0, 40),
        tool_result("2026-10-02T09:00:05.000Z", images=2),
        assistant("m2", "r2", "2026-10-02T09:00:10.000Z", OPUS, 2, 500, 1003, 20),
        prompt("2026-10-03T08:00:00.000Z", "go"),
        prompt("2026-10-03T08:00:01.000Z",
               "<system-reminder>scaffolding only</system-reminder>"),
        {"type": "user", "isMeta": True, "timestamp": "2026-10-03T08:00:02.000Z",
         "message": {"role": "user", "content": "meta record"}},
        prompt("2026-10-03T08:00:03.000Z",
               "<task-notification>a job ended</task-notification>"),
        assistant("m3", "r3", "2026-10-03T08:00:05.000Z", OPUS, 1, 100, 1505, 7),
        {"type": "assistant", "timestamp": "2026-10-03T08:00:06.000Z",
         "message": {"id": "syn", "model": "<synthetic>", "role": "assistant",
                     "content": [{"type": "text"}],
                     "usage": {"input_tokens": 0, "cache_creation_input_tokens": 0,
                               "cache_read_input_tokens": 0, "output_tokens": 0}}},
        # A real request carrying a pasted image: the image is the owner's, not a tool's.
        prompt("2026-10-03T09:00:00.000Z", "Second request, look at crop 3",
               extra=[{"type": "image", "source": {"type": "base64", "data": "AAAA"}}]),
        # The day's last millisecond: a date-only --until covers it.
        assistant("m4", "r4", "2026-10-04T23:59:59.999Z", OPUS, 1, 0, 1606, 1),
        # After the window.
        assistant("m6", "r6", "2026-10-05T00:00:00.000Z", OPUS, 9, 9, 9, 2),
        prompt("2026-10-05T00:00:01.000Z", "After the window"),
    ]
    write_jsonl(fx.root / f"{MAIN1}.jsonl", main1,
                extra_raw=['{"type":"assistant","usage" this line is corrupt'])

    # main2 began before the window: its first call is 5001 tokens, then one
    # call inside, so it adds to the totals but not to starting sizes.
    write_jsonl(fx.root / f"{MAIN2}.jsonl", [
        assistant("s1", "t1", "2026-10-01T12:00:00.000Z", OPUS, 1, 5000, 0, 9),
        prompt("2026-10-01T12:00:01.000Z", "Resumed " + "w" * 150),
        assistant("s2", "t2", "2026-10-03T12:00:00.000Z", OPUS, 1, 10, 5001, 4),
        prompt("2026-10-03T12:00:05.000Z", "Continue with the second sample"),
    ])

    # The delegate: its own two calls, a copy of main1's m2 (a duplicate across
    # files, collapsed), one tool image, and one call after the window.
    write_jsonl(fx.root / MAIN1 / "subagents" / f"{AGENT}.jsonl", [
        prompt("2026-10-03T10:00:00.000Z", "Delegate brief: do the thing"),
        assistant("d1", "q1", "2026-10-03T10:00:01.000Z", SONNET, 4, 2000, 0, 11),
        tool_result("2026-10-03T10:00:03.000Z", images=1),
        assistant("d2", "q2", "2026-10-03T10:00:04.000Z", SONNET, 1, 300, 2004, 9),
        assistant("m2", "r2", "2026-10-02T09:00:10.000Z", OPUS, 2, 500, 1003, 20),
        assistant("d3", "q3", "2026-10-05T10:00:00.000Z", SONNET, 1, 1, 1, 1),
    ])
    (fx.root / MAIN1 / "subagents" / f"{AGENT}.meta.json").write_text(json.dumps(
        {"agentType": "delegate", "description": "the thing", "spawnDepth": 1}))
    (fx.root / "sidecar.meta.json").write_text("{}")  # not a transcript

    # An in-window record in a file last written before the window: never opened.
    stale = fx.root / f"{STALE}.jsonl"
    write_jsonl(stale, [assistant("z1", "zz1", "2026-10-03T00:00:00.000Z",
                                  OPUS, 7777, 7777, 7777, 7777)])
    old = time.mktime((2026, 9, 1, 0, 0, 0, 0, 0, -1))
    os.utime(stale, (old, old))


# Worked by hand (window 2026-10-02..2026-10-04):
#   main1   m5 802, m1 1003, m2 1505, m3 1606, m4 1607  -> 5 calls, re-read 6523
#           input 9, written 2400, read 4114, output 3+40+20+7+1 = 71
#           start 802, peak 1607, images 2
#   main2   s2 5012 (in 1, write 10, read 5001, out 4) -> 1 call; began 10-01
#   agent   d1 2004, d2 2305 -> 2 calls, re-read 4309; written 2300; output 20
#           start 2004, peak 2305, images 1
#   total   8 calls, re-read 6523 + 5012 + 4309 = 15844, input 15, written 4710,
#           read 11119, output 95, images 3, owner messages 3 (one a bare "go")
class WindowedTotals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = Fixture()
        build_standard(cls.fx)
        cls.res = cls.fx.result("--since", "2026-10-02", "--until", "2026-10-04")
        cls.w = cls.res["windows"][0]

    @classmethod
    def tearDownClass(cls):
        cls.fx.close()

    def test_totals_are_exact(self):
        t = self.w["totals"]
        self.assertEqual(t["calls"], 8)
        self.assertEqual(t["reread"], 15844)
        self.assertEqual((t["input"], t["cache_write"], t["cache_read"]), (15, 4710, 11119))
        self.assertEqual(t["input"] + t["cache_write"] + t["cache_read"], t["reread"])
        self.assertEqual(t["output"], 95)
        self.assertEqual(t["transcripts"], 3)

    def test_streamed_chunks_collapse_and_output_keeps_the_final_count(self):
        main1 = next(r for r in self.w["transcripts"] if r["transcript"] == MAIN1)
        self.assertEqual(main1["calls"], 5)
        self.assertEqual(main1["output"], 71)
        self.assertEqual(self.res["meta"]["duplicate_records"], 2)

    def test_duplicate_across_files_is_counted_once(self):
        agent = next(r for r in self.w["transcripts"] if r["transcript"] == AGENT)
        self.assertEqual((agent["calls"], agent["reread"]), (2, 4309))

    def test_synthetic_zero_usage_record_is_not_a_call(self):
        self.assertEqual(self.w["totals"]["calls"], 8)

    def test_window_edges(self):
        # m5 sits on the opening millisecond, m4 on the closing one; m6 and d3
        # are after, and the pre-window prompt and first call are before.
        calls = {r["transcript"]: r["calls"] for r in self.w["transcripts"]}
        self.assertEqual(calls[MAIN1], 5)
        self.assertEqual(calls[MAIN2], 1)

    def test_images_count_tool_results_once_and_not_pasted_images(self):
        t = {r["transcript"]: r["images"] for r in self.w["transcripts"]}
        self.assertEqual(t[MAIN1], 2)
        self.assertEqual(t[AGENT], 1)
        self.assertEqual(self.w["totals"]["images"], 3)
        self.assertEqual(self.w["by_kind"]["main"]["images"], 2)
        self.assertEqual(self.w["by_kind"]["delegate"]["images"], 1)

    def test_owner_messages_exclude_scaffolding_meta_and_delegates(self):
        t = self.w["totals"]
        self.assertEqual(t["owner_messages"], 3)
        self.assertEqual(t["owner_confirmations"], 1)
        self.assertAlmostEqual(t["reread_per_owner_message"], 15844 / 3)

    def test_commits_in_window(self):
        t = self.w["totals"]
        self.assertEqual(t["commits"], 2)
        self.assertAlmostEqual(t["reread_per_commit"], 15844 / 2)

    def test_by_kind_and_share(self):
        k = self.w["by_kind"]
        self.assertEqual((k["main"]["reread"], k["delegate"]["reread"]), (11535, 4309))
        self.assertEqual((k["main"]["transcripts"], k["delegate"]["transcripts"]), (2, 1))
        self.assertAlmostEqual(k["delegate"]["share"], 4309 / 15844)

    def test_by_model(self):
        m = self.w["by_model"]
        self.assertEqual(list(m), [OPUS, SONNET])
        self.assertEqual((m[OPUS]["calls"], m[OPUS]["reread"]), (6, 11535))
        self.assertEqual((m[SONNET]["calls"], m[SONNET]["reread"]), (2, 4309))

    def test_by_day(self):
        d = self.w["by_day"]
        self.assertEqual(list(d), ["2026-10-02", "2026-10-03", "2026-10-04"])
        self.assertEqual(d["2026-10-02"]["main"]["reread"], 802 + 1003 + 1505)
        self.assertEqual(d["2026-10-03"]["main"]["reread"], 1606 + 5012)
        self.assertEqual(d["2026-10-03"]["delegate"]["reread"], 4309)
        self.assertEqual(d["2026-10-04"]["main"]["calls"], 1)

    def test_starting_size_counts_only_transcripts_that_began_in_the_window(self):
        main = self.w["distribution"]["main"]
        self.assertEqual((main["transcripts"], main["started_in_window"]), (2, 1))
        self.assertEqual(main["start"], {"p10": 802, "p50": 802, "p90": 802, "max": 802})
        self.assertAlmostEqual(main["start_share"], 802 * 5 / 6523)
        row = next(r for r in self.w["transcripts"] if r["transcript"] == MAIN2)
        self.assertIsNone(row["start"])
        self.assertFalse(row["began_in_window"])

    def test_peak_and_calls_distribution(self):
        main = self.w["distribution"]["main"]
        self.assertEqual(main["peak"], {"p10": 1607, "p50": 5012, "p90": 5012, "max": 5012})
        self.assertEqual(main["calls"]["p50"], 5)
        delegate = self.w["distribution"]["delegate"]
        self.assertEqual(delegate["start"]["p50"], 2004)
        self.assertEqual(delegate["peak"]["max"], 2305)

    def test_transcripts_ranked_with_first_prompt(self):
        order = [r["transcript"] for r in self.w["transcripts"]]
        self.assertEqual(order, [MAIN1, MAIN2, AGENT])
        by = {r["transcript"]: r for r in self.w["transcripts"]}
        self.assertEqual(by[MAIN1]["first_prompt"], "First owner prompt about the sharpener")
        self.assertEqual(by[MAIN2]["first_prompt"], ("Resumed " + "w" * 150)[:100])
        self.assertEqual(len(by[MAIN2]["first_prompt"]), 100)
        self.assertEqual(by[AGENT]["first_prompt"], "Delegate brief: do the thing")
        self.assertEqual(by[AGENT]["session"], MAIN1)

    def test_a_delegate_row_carries_its_agent_type_and_a_main_row_none(self):
        by = {r["transcript"]: r for r in self.w["transcripts"]}
        self.assertEqual(by[AGENT]["agent_type"], "delegate")
        self.assertIsNone(by[MAIN1]["agent_type"])
        self.assertEqual(list(self.w["by_type"]), ["delegate"])

    def test_meta_accounts_for_what_was_skipped(self):
        m = self.res["meta"]
        self.assertEqual(m["files_skipped_by_mtime"], 1)
        self.assertEqual(m["transcript_files"], 3)
        self.assertEqual(m["bad_lines"], 1)
        self.assertEqual(m["unreadable_files"], 0)
        self.assertEqual(m["project"], "my.proj")
        self.assertIn("duration_s", m)

    def test_text_report_carries_every_section_and_its_own_duration(self):
        rc, text = self.fx.run("--since", "2026-10-02", "--until", "2026-10-04")
        self.assertEqual(rc, 0)
        for needle in ("== window 2026-10-02..2026-10-04 (UTC)", "== totals", "== by model",
                       "== main sessions vs delegates", "== by day (UTC)",
                       "== context size distribution", "== 3 costliest transcripts",
                       "3 owner messages (1 bare confirmations)", "2 commits",
                       "re-read per owner message", "First owner prompt about the sharpener",
                       "1 skipped as older than the window", "2 duplicate stream records",
                       "1 malformed line(s) skipped", "took "):
            self.assertIn(needle, text)
        self.assertNotIn(str(self.fx.home), text)

    def test_top_limits_the_transcript_list(self):
        _, text = self.fx.run("--since", "2026-10-02", "--until", "2026-10-04", "--top", "1")
        self.assertIn("== 1 costliest transcripts", text)
        self.assertNotIn("Delegate brief", text)


class WindowsAndCompare(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = Fixture()
        build_standard(cls.fx)

    @classmethod
    def tearDownClass(cls):
        cls.fx.close()

    def test_open_ended_since(self):
        w = self.fx.result("--since", "2026-10-05")["windows"][0]
        # m6 (27) and d3 (3) are the only calls on or after 10-05.
        self.assertEqual((w["totals"]["calls"], w["totals"]["reread"]), (2, 30))
        self.assertEqual(w["totals"]["owner_messages"], 1)
        self.assertEqual(w["totals"]["commits"], 1)

    def test_open_ended_until(self):
        w = self.fx.result("--until", "2026-10-01")["windows"][0]
        # Only main2's s1 (5001) falls on or before 10-01 (the stale file is
        # older than any bound, but with no --since it is opened: z1 is 10-03).
        self.assertEqual(w["totals"]["calls"], 1)
        self.assertEqual(w["totals"]["reread"], 5001)

    def test_no_window_counts_everything_opened(self):
        w = self.fx.result()["windows"][0]
        self.assertEqual(w["totals"]["calls"], 8 + 1 + 2 + 1)  # + s1, m6/d3, z1
        self.assertEqual(self.fx.result()["meta"]["files_skipped_by_mtime"], 0)

    def test_date_time_bounds(self):
        w = self.fx.result("--since", "2026-10-03T08:00:05", "--until",
                           "2026-10-03T10:00:01")["windows"][0]
        # m3 is on the opening second; s2 is 12:00; d1 is on the closing second.
        self.assertEqual([r["transcript"] for r in w["transcripts"]], [AGENT, MAIN1])
        self.assertEqual(w["totals"]["calls"], 2)

    def test_compare_side_by_side_with_change(self):
        res = self.fx.result("--compare", "2026-10-02..2026-10-02", "2026-10-03..2026-10-04")
        a, b = res["windows"]
        self.assertEqual((a["totals"]["calls"], a["totals"]["reread"]), (3, 3310))
        self.assertEqual((b["totals"]["calls"], b["totals"]["reread"]), (5, 12534))
        self.assertEqual((a["totals"]["commits"], b["totals"]["commits"]), (1, 1))
        rc, text = self.fx.run("--compare", "2026-10-02..2026-10-02", "2026-10-03..2026-10-04")
        self.assertEqual(rc, 0)
        self.assertIn("== compare", text)
        # (12534 - 3310) / 3310 = +278.7%
        line = next(l for l in text.splitlines() if l.startswith("calls"))
        self.assertEqual(line.split(), ["calls", "3", "5", "+67%"])
        line = next(l for l in text.splitlines() if l.startswith("tokens re-read"))
        self.assertIn("+279%", line)
        self.assertIn("== re-read by model", text)


class Robustness(unittest.TestCase):
    def test_unreadable_transcript_is_reported_and_skipped(self):
        fx = Fixture(with_git=False)
        try:
            build_standard(fx)
            (fx.root / "zzz.jsonl").mkdir()  # opening a directory fails for any user
            seen = []
            with contextlib.redirect_stderr(io.StringIO()):
                rc, text = fx.run("--json", "--since", "2026-10-02", "--until", "2026-10-04",
                                  progress=seen.append)
            res = json.loads(text)
            self.assertEqual(res["meta"]["unreadable_files"], 1)
            self.assertEqual(res["windows"][0]["totals"]["reread"], 15844)
            self.assertTrue(any("cannot read zzz.jsonl" in m for m in seen), seen)
            self.assertIsNone(res["windows"][0]["totals"]["commits"])
        finally:
            fx.close()

    def test_progress_is_reported_at_start_and_on_cadence(self):
        fx = Fixture(with_git=False)
        try:
            build_standard(fx)
            seen = []
            with mock.patch.object(tu, "PROGRESS_EVERY_S", 0.0):
                fx.run("--json", "--since", "2026-10-02", "--until", "2026-10-04",
                       progress=seen.append)
            self.assertTrue(seen[0].startswith("reading 3 transcript files"), seen)
            self.assertEqual(sum(1 for m in seen if " files, " in m), 3, seen)
        finally:
            fx.close()

    def test_spaced_json_is_read_like_compact_json(self):
        results = []
        for compact in (True, False):
            fx = Fixture(with_git=False)
            try:
                write_jsonl(fx.root / f"{MAIN1}.jsonl", [
                    prompt("2026-10-02T00:00:00.000Z", "hello"),
                    assistant("a", "ra", "2026-10-02T00:00:01.000Z", OPUS, 1, 10, 0, 2),
                    tool_result("2026-10-02T00:00:02.000Z", images=2),
                    assistant("b", "rb", "2026-10-02T00:00:03.000Z", OPUS, 1, 5, 11, 3),
                ], compact=compact)
                t = fx.result("--since", "2026-10-02")["windows"][0]["totals"]
                results.append((t["calls"], t["reread"], t["images"], t["owner_messages"]))
            finally:
                fx.close()
        self.assertEqual(results[0], (2, 11 + 17, 2, 1))
        self.assertEqual(results[0], results[1])

    def test_non_git_project_reports_no_commit_count(self):
        fx = Fixture(with_git=False)
        try:
            build_standard(fx)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                t = fx.result("--since", "2026-10-02", "--until", "2026-10-04")["windows"][0]["totals"]
            self.assertIsNone(t["commits"])
            self.assertIsNone(t["reread_per_commit"])
            self.assertIn("commit count unavailable for window 2026-10-02..2026-10-04", err.getvalue())
        finally:
            fx.close()

    def test_default_project_is_the_working_directory(self):
        fx = Fixture(with_git=False)
        try:
            build_standard(fx)
            r = fx.cli("--json", "--since", "2026-10-02", "--until", "2026-10-04")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["windows"][0]["totals"]["reread"], 15844)
        finally:
            fx.close()


class Units(unittest.TestCase):
    def test_transcript_directory_names(self):
        names = tu.candidate_names("/home/someone/dev/my.proj")
        self.assertEqual(names[0], "-home-someone-dev-my-proj")
        names = tu.candidate_names("/a/b_c d")
        self.assertEqual(names, ["-a-b-c-d", "-a-b_c d"])

    def test_missing_transcript_directory_names_the_project(self):
        fx = Fixture(with_git=False)
        try:
            other = fx.home / "work" / "elsewhere"
            other.mkdir()
            with mock.patch.dict(os.environ, fx.env):
                with self.assertRaises(FileNotFoundError) as cm:
                    tu.transcript_root(other)
            self.assertIn("elsewhere", str(cm.exception))
        finally:
            fx.close()

    def test_percentiles_use_the_nearest_rank_from_below(self):
        self.assertEqual(tu.percentiles(list(range(1, 11))),
                         {"p10": 2, "p50": 6, "p90": 10, "max": 10})
        self.assertEqual(tu.percentiles([7]), {"p10": 7, "p50": 7, "p90": 7, "max": 7})
        self.assertEqual(tu.percentiles([]), {"p10": None, "p50": None, "p90": None, "max": None})

    def test_token_formatting(self):
        self.assertEqual([tu.tok(x) for x in (None, 999, 79295, 1_234_567, 17_150_000_000)],
                         ["-", "999", "79k", "1.2M", "17.15B"])

    def test_change_percentage(self):
        self.assertEqual([tu.change(3, 5), tu.change(100, 100), tu.change(1000, 1001),
                          tu.change(200, 100), tu.change(0, 5), tu.change(None, 5)],
                         ["+67%", "0%", "0%", "-50%", "-", "-"])

    def test_window_bounds(self):
        w = tu.Window("2026-10-01", "2026-10-12")
        self.assertTrue(w.contains("2026-10-01T00:00:00.000Z"))
        self.assertTrue(w.contains("2026-10-12T23:59:59.999Z"))
        self.assertFalse(w.contains("2026-09-30T23:59:59.999Z"))
        self.assertFalse(w.contains("2026-10-13T00:00:00.000Z"))
        self.assertFalse(w.contains(""))
        self.assertEqual(tu.parse_bound("2026-10-01T12:00:00+02:00", False),
                         "2026-10-01T10:00:00.000Z")
        with self.assertRaises(ValueError):
            tu.Window("2026-10-05", "2026-10-01")
        with self.assertRaises(ValueError) as cm:
            tu.Window("yesterday", None)
        self.assertIn("yesterday", str(cm.exception))


class Refusals(unittest.TestCase):
    """P14: an argument the tool does not understand runs nothing."""

    @classmethod
    def setUpClass(cls):
        cls.fx = Fixture(with_git=False)
        build_standard(cls.fx)

    @classmethod
    def tearDownClass(cls):
        cls.fx.close()

    def refused(self, *argv, naming):
        r = self.fx.cli(*argv)
        self.assertNotEqual(r.returncode, 0, argv)
        self.assertEqual(r.stdout, "", argv)
        self.assertIn(naming, r.stderr, argv)
        for option in ("--project", "--since", "--until", "--compare", "--top", "--json"):
            self.assertIn(option, r.stderr, (argv, option))
        return r

    def test_unknown_option(self):
        self.refused("--bogus", naming="--bogus")

    def test_abbreviation_is_not_guessed(self):
        self.refused("--sin", "2026-10-01", naming="--sin")

    def test_positional_argument(self):
        self.refused("2026-10-01", naming="2026-10-01")

    def test_missing_value(self):
        self.refused("--since", naming="--since")
        self.refused("--top", naming="--top")

    def test_bad_date_names_the_text(self):
        self.refused("--since", "yesterday", naming="yesterday")

    def test_inverted_window(self):
        self.refused("--since", "2026-10-05", "--until", "2026-10-01", naming="ends before it begins")

    def test_compare_needs_two_ranges(self):
        self.refused("--compare", "2026-10-01..2026-10-02", naming="--compare")
        self.refused("--compare", "2026-10-01", "2026-10-02", naming="SINCE..UNTIL")
        self.refused("--compare", "2026-10-01..", "2026-10-02..2026-10-03", naming="both a SINCE and an UNTIL")

    def test_compare_excludes_since_until(self):
        self.refused("--compare", "2026-10-01..2026-10-02", "2026-10-03..2026-10-04",
                     "--since", "2026-10-01", naming="cannot be combined")

    def test_top_must_be_positive(self):
        self.refused("--top", "0", naming="--top must be at least 1")

    def test_project_must_exist(self):
        self.refused("--project", str(self.fx.home / "nowhere"), naming="not a directory")

    def test_help_exits_zero_and_lists_the_set(self):
        for flag in ("--help", "-h"):
            r = self.fx.cli(flag)
            self.assertEqual(r.returncode, 0)
            for option in ("--project", "--since", "--until", "--compare", "--top", "--json"):
                self.assertIn(option, r.stdout)


# The split fixture, worked by hand (window 2026-10-02..2026-10-04). A call is
# named by its context, in thousands where the number is large.
#
#   MAINA  began in the window. Calls 1000, 200000, 280000; an auto compaction
#          (harness count 283000, 15000 after); calls 16000, 100000, 250000; a
#          manual compaction (255000, 17000 after); call 20000. Peak 280000.
#          Two compact summaries, one real prompt.
#   MAINB  calls 5000, 340000, no compaction: the one transcript over the ceiling.
#          One prompt, a bare "go".
#   MAINC  begins with a compact summary, then a real prompt. Calls 1000, 330000:
#          a peak exactly at the ceiling is not above it.
#   MAIND  began before the window. Call 255000 and a boundary the day before
#          (not counted); call 261000 at 23:59 the day before, then a boundary in
#          the window (auto, harness 264000) whose last call lies before the
#          window: pre-compaction context 261000; call 100000 in the window; a
#          call and a boundary after the window (not counted).
#   Delegates, by type (calls by context):
#     delegate     D1 3000, 8000 (began in window); D7 began the day before
#                      (500) then 9000 in the window.
#     general-purpose  D2 20000, 50000; D3 40000, 60000.
#     Explore          D8 10000, 300000, an auto compaction (305000), 12000.
#     unknown          D4 no metadata, 7000; D5 metadata that is not JSON, 6000;
#                      D6 metadata with no agentType, 5000.
#   Re-read: MAINA 867000, MAINB 345000, MAINC 331000, MAIND 100000 = 1643000
#            delegate 11000 + 9000 = 20000; general-purpose 70000 + 100000 =
#            170000; Explore 322000; unknown 18000 = 530000. Total 2173000,
#            25 calls, 12 transcripts. Owner messages: MAINA's prompt, MAINB's
#            "go", MAINC's prompt = 3 (compact summaries are not requests).
CEILING = 330_000


def build_split(fx):
    write_jsonl(fx.root / f"{MAIN1}.jsonl", [
        prompt("2026-10-02T00:30:00.000Z", "Do the thing"),
        call("a1", "2026-10-02T01:00:00.000Z", 1000),
        call("a2", "2026-10-02T02:00:00.000Z", 200000),
        call("a3", "2026-10-02T03:00:00.000Z", 280000),
        boundary("2026-10-02T03:00:30.000Z", "auto", 283000, 15000),
        compact_summary("2026-10-02T03:00:29.000Z"),
        call("a4", "2026-10-02T04:00:00.000Z", 16000),
        call("a5", "2026-10-02T05:00:00.000Z", 100000),
        call("a6", "2026-10-02T06:00:00.000Z", 250000),
        boundary("2026-10-02T06:00:30.000Z", "manual", 255000, 17000),
        compact_summary("2026-10-02T06:00:29.000Z"),
        call("a7", "2026-10-02T07:00:00.000Z", 20000),
        # A tool result that merely quotes the marker is not a compaction.
        {"type": "user", "timestamp": "2026-10-02T08:00:00.000Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t9", "content": [
                 {"type": "text", "text": '{"type":"system","subtype":"compact_boundary"}'}]}]}},
    ])
    write_jsonl(fx.root / f"{MAIN2}.jsonl", [
        prompt("2026-10-03T08:00:00.000Z", "go"),
        call("b1", "2026-10-03T09:00:00.000Z", 5000),
        call("b2", "2026-10-03T10:00:00.000Z", 340000),
    ])
    write_jsonl(fx.root / "44444444-aaaa-bbbb-cccc-000000000004.jsonl", [
        compact_summary("2026-10-02T09:00:00.000Z"),
        prompt("2026-10-02T09:00:01.000Z", "C real prompt"),
        call("c1", "2026-10-02T09:01:00.000Z", 1000),
        call("c2", "2026-10-02T09:02:00.000Z", CEILING),
    ])
    write_jsonl(fx.root / "55555555-aaaa-bbbb-cccc-000000000005.jsonl", [
        call("e0", "2026-10-01T20:00:00.000Z", 255000),
        boundary("2026-10-01T20:01:00.000Z", "auto", 258000, 9000),
        call("e1", "2026-10-01T23:59:00.000Z", 261000),
        boundary("2026-10-02T00:00:30.000Z", "auto", 264000, 12000),
        call("e2", "2026-10-02T00:10:00.000Z", 100000),
        call("e3", "2026-10-05T01:00:00.000Z", 270000),
        boundary("2026-10-05T02:00:00.000Z", "auto", 273000, 11000),
    ])
    g = lambda mid, ts, ctx: call(mid, ts, ctx, SONNET)
    write_agent(fx, MAIN1, "d1", [g("d1a", "2026-10-02T10:00:00.000Z", 3000),
                                  g("d1b", "2026-10-02T10:01:00.000Z", 8000)],
                meta={"agentType": "delegate", "description": "x"})
    write_agent(fx, MAIN1, "d7", [g("d7a", "2026-10-01T10:00:00.000Z", 500),
                                  g("d7b", "2026-10-02T11:00:00.000Z", 9000)],
                meta={"agentType": "delegate"})
    write_agent(fx, MAIN1, "d2", [g("d2a", "2026-10-03T10:00:00.000Z", 20000),
                                  g("d2b", "2026-10-03T10:01:00.000Z", 50000)],
                meta={"agentType": "general-purpose"})
    write_agent(fx, MAIN1, "d3", [g("d3a", "2026-10-03T11:00:00.000Z", 40000),
                                  g("d3b", "2026-10-03T11:01:00.000Z", 60000)],
                meta={"agentType": "general-purpose"})
    write_agent(fx, MAIN1, "d8", [g("d8a", "2026-10-03T12:00:00.000Z", 10000),
                                  g("d8b", "2026-10-03T12:01:00.000Z", 300000),
                                  boundary("2026-10-03T12:02:00.000Z", "auto", 305000, 8000),
                                  g("d8c", "2026-10-03T12:03:00.000Z", 12000)],
                meta={"agentType": "Explore"})
    write_agent(fx, MAIN1, "d4", [g("d4a", "2026-10-03T13:00:00.000Z", 7000)])
    write_agent(fx, MAIN1, "d5", [g("d5a", "2026-10-03T13:01:00.000Z", 6000)],
                raw_meta="{not json")
    write_agent(fx, MAIN1, "d6", [g("d6a", "2026-10-03T13:02:00.000Z", 5000)],
                meta={"description": "no type"})


def row_of(w, name):
    return next(r for r in w["transcripts"] if r["transcript"].endswith(name))


class AgentTypes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = Fixture()
        build_split(cls.fx)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cls.res = cls.fx.result("--since", "2026-10-02", "--until", "2026-10-04")
        cls.w = cls.res["windows"][0]

    @classmethod
    def tearDownClass(cls):
        cls.fx.close()

    @property
    def bt(self):
        return self.w["by_type"]

    def test_the_totals_the_fixture_was_worked_out_to(self):
        t = self.w["totals"]
        self.assertEqual((t["calls"], t["reread"], t["transcripts"]), (25, 2173000, 12))

    def test_types_are_listed_by_reread_and_unknown_is_a_type(self):
        self.assertEqual(list(self.bt),
                         ["Explore", "general-purpose", "delegate", "unknown"])
        self.assertEqual([self.bt[k]["reread"] for k in self.bt],
                         [322000, 170000, 20000, 18000])
        self.assertEqual([self.bt[k]["calls"] for k in self.bt], [3, 4, 3, 3])
        self.assertEqual([self.bt[k]["transcripts"] for k in self.bt], [1, 2, 2, 3])
        self.assertAlmostEqual(self.bt["Explore"]["share"], 322000 / 2173000)

    def test_nothing_is_dropped_a_type_split_adds_up_to_the_delegates(self):
        k = self.w["by_kind"]["delegate"]
        self.assertEqual(sum(v["transcripts"] for v in self.bt.values()), k["transcripts"])
        self.assertEqual(sum(v["calls"] for v in self.bt.values()), k["calls"])
        self.assertEqual(sum(v["reread"] for v in self.bt.values()), k["reread"])

    def test_starting_size_per_type_counts_only_transcripts_that_began_in_the_window(self):
        slim = self.bt["delegate"]
        self.assertEqual(slim["started_in_window"], 1)      # D7 began the day before
        self.assertEqual(slim["start"], {"p10": 3000, "p50": 3000, "p90": 3000, "max": 3000})
        gp = self.bt["general-purpose"]
        self.assertEqual((gp["started_in_window"], gp["start"]["p50"], gp["start"]["max"]),
                         (2, 40000, 40000))
        unk = self.bt["unknown"]
        self.assertEqual((unk["started_in_window"], unk["start"]["p50"], unk["start"]["max"]),
                         (3, 6000, 7000))
        ex = self.bt["Explore"]
        self.assertEqual((ex["started_in_window"], ex["start"]["p50"]), (1, 10000))

    def test_peak_per_type(self):
        self.assertEqual((self.bt["delegate"]["peak"]["p50"],
                          self.bt["delegate"]["peak"]["max"]), (9000, 9000))
        self.assertEqual((self.bt["general-purpose"]["peak"]["p50"],
                          self.bt["general-purpose"]["peak"]["max"]), (60000, 60000))
        self.assertEqual(self.bt["Explore"]["peak"]["max"], 300000)
        self.assertEqual((self.bt["unknown"]["peak"]["p50"],
                          self.bt["unknown"]["peak"]["max"]), (6000, 7000))

    def test_rows_carry_their_type(self):
        self.assertEqual(row_of(self.w, "agent-d1")["agent_type"], "delegate")
        self.assertEqual(row_of(self.w, "agent-d8")["agent_type"], "Explore")
        for name in ("agent-d4", "agent-d5", "agent-d6"):
            self.assertEqual(row_of(self.w, name)["agent_type"], "unknown", name)
        self.assertIsNone(row_of(self.w, MAIN1)["agent_type"])

    def test_missing_and_unusable_metadata_is_counted_and_the_unusable_one_logged(self):
        m = self.res["meta"]
        self.assertEqual(m["delegate_metadata_missing"], 1)   # D4
        self.assertEqual(m["delegate_metadata_unusable"], 2)  # D5 not JSON, D6 no agentType
        seen = []
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.fx.run("--json", "--since", "2026-10-02", "--until", "2026-10-04",
                        progress=seen.append)
        self.assertTrue(any("agent-d5.meta.json" in x and "unknown" in x for x in seen), seen)
        self.assertTrue(any("agent-d6.meta.json" in x and "agentType" in x for x in seen), seen)

    def test_text_report_has_the_by_type_section(self):
        rc, text = self.fx.run("--since", "2026-10-02", "--until", "2026-10-04")
        self.assertEqual(rc, 0)
        self.assertIn("== delegates by agent type", text)
        section = text.split("== delegates by agent type")[1].split("\n==")[0]
        for needle in ("Explore", "general-purpose", "delegate", "unknown"):
            self.assertIn(needle, section)
        self.assertIn("3 delegate(s) without a usable agent type, counted as unknown", text)


class Compactions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = Fixture()
        build_split(cls.fx)
        with contextlib.redirect_stderr(io.StringIO()):
            cls.res = cls.fx.result("--since", "2026-10-02", "--until", "2026-10-04")
        cls.w = cls.res["windows"][0]

    @classmethod
    def tearDownClass(cls):
        cls.fx.close()

    @property
    def c(self):
        return self.w["compactions"]

    def test_summary_counts_the_window_and_names_the_largest(self):
        self.assertEqual(self.c["count"], 4)
        self.assertEqual(self.c["largest_pre_context"], 300000)
        self.assertEqual(self.c["ceiling"], CEILING)

    def test_a_transcript_lists_each_compaction_with_the_context_before_it(self):
        ev = row_of(self.w, MAIN1)["compactions"]
        self.assertEqual([(e["trigger"], e["pre_context"], e["harness_pre_tokens"],
                           e["post_tokens"]) for e in ev],
                         [("auto", 280000, 283000, 15000), ("manual", 250000, 255000, 17000)])
        self.assertEqual([e["ts"] for e in ev],
                         ["2026-10-02T03:00:30.000Z", "2026-10-02T06:00:30.000Z"])
        self.assertEqual(row_of(self.w, "agent-d8")["compactions"][0]["pre_context"], 300000)
        self.assertEqual(row_of(self.w, MAIN2)["compactions"], [])

    def test_the_context_before_may_come_from_before_the_window(self):
        ev = row_of(self.w, "55555555-aaaa-bbbb-cccc-000000000005")["compactions"]
        self.assertEqual(len(ev), 1)           # the day-before and day-after ones are outside
        self.assertEqual(ev[0]["pre_context"], 261000)
        self.assertEqual(ev[0]["harness_pre_tokens"], 264000)

    def test_a_quoted_marker_in_a_tool_result_is_not_a_compaction(self):
        self.assertEqual(len(row_of(self.w, MAIN1)["compactions"]), 2)
        self.assertEqual(self.c["count"], 4)

    def test_counts_per_kind_and_per_type(self):
        self.assertEqual(self.w["by_kind"]["main"]["compactions"], 3)
        self.assertEqual(self.w["by_kind"]["delegate"]["compactions"], 1)
        self.assertEqual(self.w["by_type"]["Explore"]["compactions"], 1)
        self.assertEqual(self.w["by_type"]["general-purpose"]["compactions"], 0)

    def test_a_peak_above_the_ceiling_without_a_compaction_is_flagged_by_name(self):
        flagged = self.c["peak_over_ceiling_without_compaction"]
        self.assertEqual([f["transcript"] for f in flagged], [MAIN2])
        self.assertEqual(flagged[0]["peak"], 340000)
        self.assertEqual(flagged[0]["kind"], "main")
        # MAINC peaks exactly at the ceiling, MAINA compacted, the rest are small.

    def test_compact_summaries_are_not_owner_messages_or_first_prompts(self):
        t = self.w["totals"]
        self.assertEqual((t["owner_messages"], t["owner_confirmations"]), (3, 1))
        self.assertEqual(row_of(self.w, "44444444-aaaa-bbbb-cccc-000000000004")["first_prompt"],
                         "C real prompt")

    def test_text_report_states_the_summary_and_names_the_flagged_transcript(self):
        rc, text = self.fx.run("--since", "2026-10-02", "--until", "2026-10-04")
        self.assertEqual(rc, 0)
        self.assertIn("== compactions", text)
        section = text.split("== compactions")[1].split("\n==")[0]
        self.assertIn("4 compactions in the window; largest pre-compaction context 300k", section)
        self.assertIn("peak above 330k with no compaction: 1 transcript", section)
        self.assertIn(MAIN2[:14], section)
        self.assertIn("340k", section)

    def test_no_compaction_and_nothing_flagged_says_so(self):
        fx = Fixture(with_git=False)
        try:
            write_jsonl(fx.root / f"{MAIN1}.jsonl", [
                prompt("2026-10-02T00:00:00.000Z", "hello"),
                call("a", "2026-10-02T00:00:01.000Z", 1000)])
            rc, text = fx.run("--since", "2026-10-02")
            self.assertEqual(rc, 0)
            section = text.split("== compactions")[1].split("\n==")[0]
            self.assertIn("0 compactions in the window; largest pre-compaction context -", section)
            self.assertIn("peak above 330k with no compaction: none", section)
            c = fx.result("--since", "2026-10-02")["windows"][0]["compactions"]
            self.assertEqual((c["count"], c["largest_pre_context"]), (0, None))
        finally:
            fx.close()

    def test_compare_carries_the_compaction_rows(self):
        rc, text = self.fx.run("--compare", "2026-10-02..2026-10-04", "2026-10-05..2026-10-05")
        self.assertEqual(rc, 0)
        # Window B holds only MAIND's compaction after window A, 270000 before it.
        self.assertRegex(text, r"(?m)^compactions\s+4\s+1\s")
        self.assertRegex(text, r"(?m)^largest pre-compaction context\s+300k\s+270k\s")
        self.assertRegex(text, r"(?m)^peak above 330k, no compaction\s+1\s+0\s")


class Timed(unittest.TextTestResult):
    """Records each test's duration so a slow gate is visible (AUR P7)."""

    def startTest(self, test):
        self._t0 = time.monotonic()
        super().startTest(test)

    def stopTest(self, test):
        self.timings = getattr(self, "timings", [])
        self.timings.append((time.monotonic() - self._t0, test.id().split(".", 1)[-1]))
        super().stopTest(test)


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0], allow_abbrev=False)
    ap.add_argument("--verbose", action="store_true", help="name each test as it runs")
    args = ap.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1,
                                     resultclass=Timed)
    started = time.monotonic()
    result = runner.run(suite)
    slow = sorted(getattr(result, "timings", []), reverse=True)[:3]
    print("slowest: " + "; ".join(f"{name} {d:.2f}s" for d, name in slow))
    print(f"token-usage gate: {'PASSED' if result.wasSuccessful() else 'FAILED'} "
          f"({result.testsRun} tests, {time.monotonic() - started:.1f}s)")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
