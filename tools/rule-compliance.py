#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Measure how consistently the assistant applied its own interaction rules.

The rules in `claude_master.md` are only worth what gets applied, and the
failure mode is not a rule being wrong — it is a rule quietly going unused
while every response still looks fine. That decay is invisible turn by turn
and obvious across a corpus, so it is measured rather than recalled.

Each detector below is a MARKER, not the rule. A marker can only see a
phrasing; it cannot see whether the reasoning behind it happened. So a marker
going to zero is strong evidence of a lapse, while a marker at 100 % is weak
evidence of compliance — read the numbers in that direction and no further.

Usage:
    rule-compliance.py [--project DIR] [--since YYYY-MM-DD] [--csv]

    --project DIR   transcript directory
                    (default: active repository under ~/.claude/projects/)
    --since DATE    ignore sessions that started before DATE
    --csv           machine-readable rows instead of the table
    -h, --help      this text
"""

import argparse
import glob
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from transcript import turns  # noqa: E402

def default_project_dir():
    """Infer the active repository's Claude transcript directory from cwd."""
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True)
    root = Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else Path.cwd()
    encoded = str(root.resolve()).replace("/", "-")
    return Path.home() / ".claude" / "projects" / encoded

# A response short enough to be an acknowledgement is not evidence of anything:
# "Done." carries no status table because it needs none, and counting it as a
# lapse would bury the real signal under noise.
SUBSTANTIVE_CHARS = 400

# marker -> (label, compiled test). Ordered for the report.
MARKERS = [
    ("table",     "status table",
     lambda t: re.search(r"^\|.+\|\s*$\n^\|[\s:|-]+\|\s*$", t, re.M) is not None),
    ("advisory",  "model-settings advisory",
     lambda t: "Model settings" in t),
    ("decisions", "decisions at the close",
     lambda t: re.search(r"(?i)decisions? (needed|for you|required)", t) is not None),
    ("beforeaft", "Before/After framing",
     lambda t: re.search(r"(?i)\bbefore\s*(:|→|->)", t) is not None),
    ("scenario",  "user-scenario framing",
     lambda t: re.search(r"(?i)^\s*\*{0,2}why\*{0,2}\s*:", t, re.M) is not None),
    ("clarify",   "ambiguity raised as a question",
     lambda t: re.search(
         r"(?i)(can you clarify|which of the|i understand that you want"
         r"|you may have missed|two readings|ambiguous)", t) is not None),
    ("structure", "structural rendering (headers/bullets)",
     lambda t: re.search(r"^\s*(#{2,}\s|\s*[-*]\s|\d+\.\s)", t, re.M) is not None),
]

# Rules 1 and 2 are owed only where the owner actually said something, so they
# get their own denominator. Scored against every assistant turn they are
# uninterpretable: most substantive turns resume after a tool call, no request
# arrived, and nothing was owed — which silently caps the achievable rate at
# whatever fraction of turns happen to follow an owner message. The markers
# above keep the all-turns denominator so their series stays comparable with
# the 84-transcript baseline.
ASK_MARKERS = [
    ("understand", "restates the ask",
     lambda t: re.search(r"(?i)\bI understand that\b", t) is not None),
    ("ground",     "agrees on the ground",
     lambda t: re.search(r"(?i)\bI (agree|disagree) that\b", t) is not None),
    ("recommend",  "recommends, naming the alternative",
     lambda t: re.search(r"(?i)\bI recommend\b", t) is not None
     and re.search(r"(?i)\balternative\b", t) is not None),
]

EFFORT_RE = re.compile(r"Effort\s*=\s*(\d)")
VERDICT_RE = re.compile(r"Model settings (are appropriate|change to)")


def session_rows(path):
    """(started, sid, texts, answers) for one transcript.

    `texts` is every assistant text block; `answers` is the subset that was the
    first reply to an owner message which was not pure assent — the only turns
    on which rules 1 and 2 are owed.
    """
    started, texts, answers = None, [], []
    confirmations = set()
    for turn in turns(path):
        if started is None and turn["at"]:
            started = turn["at"][:10]
        if turn["kind"] == "ask":
            if turn["confirmation"]:
                confirmations.add(turn["text"])
            continue
        texts.append(turn["text"])
        ask = turn["answers"]
        if ask is not None and ask not in confirmations:
            answers.append(turn["text"])
    return started or "?", Path(path).stem[:8], texts, answers


def analyse(files, since):
    out = []
    for f in files:
        row = session_rows(f)
        if not row:
            continue
        started, sid, texts, answers = row
        if since and started < since:
            continue
        big = [t for t in texts if len(t) >= SUBSTANTIVE_CHARS]
        if not big:
            continue
        replies = [t for t in answers if len(t) >= SUBSTANTIVE_CHARS]
        rec = {"started": started, "sid": sid, "turns": len(big),
               "asks": len(replies)}
        for key, _label, test in MARKERS:
            rec[key] = sum(1 for t in big if test(t))
        for key, _label, test in ASK_MARKERS:
            rec[key] = sum(1 for t in replies if test(t))
        efforts, verdicts = [], []
        for t in texts:
            efforts += EFFORT_RE.findall(t)
            verdicts += VERDICT_RE.findall(t)
        rec["efforts"] = efforts
        rec["verdicts"] = verdicts
        out.append(rec)
    out.sort(key=lambda r: (r["started"], r["sid"]))
    return out


def main(argv):
    ap = argparse.ArgumentParser(
        add_help=True, description="Measure interaction-rule compliance.")
    ap.add_argument("--project", default=str(default_project_dir()))
    ap.add_argument("--since", default=None)
    ap.add_argument("--csv", action="store_true")
    # argparse already refuses an unknown option, names it, and exits non-zero.
    args = ap.parse_args(argv)

    if args.since and not re.match(r"^\d{4}-\d{2}-\d{2}$", args.since):
        ap.error(f"--since expects YYYY-MM-DD, got {args.since!r}")

    files = sorted(glob.glob(os.path.join(args.project, "*.jsonl")))
    if not files:
        print(f"no transcripts under {args.project}", file=sys.stderr)
        return 1

    rows = analyse(files, args.since)
    if not rows:
        print("no sessions matched", file=sys.stderr)
        return 1

    keys = [k for k, _l, _t in MARKERS]
    if args.csv:
        print("started,session,turns," + ",".join(keys))
        for r in rows:
            print(f"{r['started']},{r['sid']},{r['turns']},"
                  + ",".join(str(r[k]) for k in keys))
        return 0

    hdr = f"{'started':<11}{'session':<10}{'turns':>6}  " + "".join(
        f"{k:>11}" for k in keys)
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        cells = "".join(f"{100 * r[k] / r['turns']:>10.0f}%" for k in keys)
        print(f"{r['started']:<11}{r['sid']:<10}{r['turns']:>6}  {cells}")

    print("-" * len(hdr))
    tot = sum(r["turns"] for r in rows)
    cells = "".join(f"{100 * sum(r[k] for r in rows) / tot:>10.0f}%" for k in keys)
    print(f"{'ALL':<11}{'':<10}{tot:>6}  {cells}")

    # Rules 1-2 against their own denominator. Reported apart from the table
    # above rather than as extra columns in it, because a percentage is only
    # readable next to the population it was taken over.
    akeys = [k for k, _l, _t in ASK_MARKERS]
    print("\nrules 1-2 — scored only on substantive replies to a real request "
          "(pure assent exempt)")
    ahdr = f"{'started':<11}{'session':<10}{'asks':>6}  " + "".join(
        f"{k:>12}" for k in akeys)
    print(ahdr)
    print("-" * len(ahdr))
    for r in rows:
        if not r["asks"]:
            print(f"{r['started']:<11}{r['sid']:<10}{0:>6}  "
                  + "".join(f"{'—':>12}" for _ in akeys))
            continue
        cells = "".join(f"{100 * r[k] / r['asks']:>11.0f}%" for k in akeys)
        print(f"{r['started']:<11}{r['sid']:<10}{r['asks']:>6}  {cells}")
    print("-" * len(ahdr))
    atot = sum(r["asks"] for r in rows)
    if atot:
        cells = "".join(
            f"{100 * sum(r[k] for r in rows) / atot:>11.0f}%" for k in akeys)
        print(f"{'ALL':<11}{'':<10}{atot:>6}  {cells}")

    # The advisory is the one rule that can be present and still not be
    # followed: printing it every turn satisfies the marker while saying
    # nothing. Its spread across the corpus is what distinguishes an evaluation
    # from a rubber stamp, so it is reported separately from the rate above.
    efforts = [e for r in rows for e in r["efforts"]]
    verdicts = [v for r in rows for v in r["verdicts"]]
    print()
    if efforts:
        dist = {v: efforts.count(v) for v in sorted(set(efforts))}
        print("advisory — Effort values printed: "
              + ", ".join(f"{k}×{v}" for k, v in dist.items()))
        top = max(dist.values()) / len(efforts)
        print(f"  most-repeated value is {100 * top:.0f}% of all advisories"
              + ("   <- rubber stamp" if top > 0.8 else ""))
    if verdicts:
        same = verdicts.count("are appropriate")
        print(f"advisory — 'are appropriate' {same}, "
              f"'change to' {len(verdicts) - same}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
