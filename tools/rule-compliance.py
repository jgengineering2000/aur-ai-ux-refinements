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
import json
import os
import re
import subprocess
import sys
from pathlib import Path

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

EFFORT_RE = re.compile(r"Effort\s*=\s*(\d)")
VERDICT_RE = re.compile(r"Model settings (are appropriate|change to)")


def session_rows(path):
    """(started, sid, texts) for one transcript, assistant text only."""
    started, texts = None, []
    try:
        fh = open(path, errors="replace")
    except OSError:
        return None
    with fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if started is None and rec.get("timestamp"):
                started = rec["timestamp"][:10]
            if rec.get("type") != "assistant":
                continue
            content = (rec.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                    texts.append(b["text"])
    return started or "?", Path(path).stem[:8], texts


def analyse(files, since):
    out = []
    for f in files:
        row = session_rows(f)
        if not row:
            continue
        started, sid, texts = row
        if since and started < since:
            continue
        big = [t for t in texts if len(t) >= SUBSTANTIVE_CHARS]
        if not big:
            continue
        rec = {"started": started, "sid": sid, "turns": len(big)}
        for key, _label, test in MARKERS:
            rec[key] = sum(1 for t in big if test(t))
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
