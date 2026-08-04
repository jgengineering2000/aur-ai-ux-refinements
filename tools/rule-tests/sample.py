#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Draw real assistant responses from the transcript corpus for rule grading.

Synthetic test cases are written by the same mind being tested, so they encode
the failures it already knows about and miss the ones it does not. Every session
is already a corpus of real cases, in unlimited supply, and a violation found in
one of them is a violation that actually happened.

Sampling is deterministic for a given seed so a grading run can be repeated
exactly, and every excerpt carries its session and timestamp so any verdict can
be traced back to the response it came from and argued with.

Usage:
    sample.py [--projects GLOB] [--since YYYY-MM-DD] [--count N] [--seed N]
              [--min-chars N]

    --since DATE     only responses on or after DATE
    --count N        how many excerpts (default 9)
    --seed N         sampling seed (default 1)
    --min-chars N    ignore responses shorter than this (default 400)
    -h, --help       this text
"""

import argparse
import glob
import json
import os
import random
import re
import sys

# Excerpts are taken WHOLE or not at all. Truncating mid-response manufactures
# violations that are artifacts of the cut: a proposed solution stated in the
# paragraph after the knife looks exactly like a missing one, and rule 6 then
# fires on a response that satisfied it. Verified on a real draw — one of three
# rule-6 verdicts was a truncation artifact.
MAX_CHARS = 4000


def responses(paths, since, min_chars):
    out = []
    for f in paths:
        try:
            fh = open(f, errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                ts = rec.get("timestamp", "")
                if since and ts[:10] < since:
                    continue
                content = (rec.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for b in content:
                    if not (isinstance(b, dict) and b.get("type") == "text"):
                        continue
                    t = (b.get("text") or "").strip()
                    if not (min_chars <= len(t) <= MAX_CHARS):
                        continue
                    out.append({"session": os.path.basename(f)[:8],
                                "at": ts[:19].replace("T", " "),
                                "text": t})
    return out


def main(argv):
    ap = argparse.ArgumentParser(description="Sample real responses for grading.")
    ap.add_argument("--projects",
                    default=os.path.expanduser("~/.claude/projects/*"))
    ap.add_argument("--since")
    ap.add_argument("--count", type=int, default=9)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--min-chars", type=int, default=400)
    args = ap.parse_args(argv)

    if args.since and not re.match(r"^\d{4}-\d{2}-\d{2}$", args.since):
        ap.error(f"--since expects YYYY-MM-DD, got {args.since!r}")

    paths = sorted(glob.glob(os.path.join(args.projects, "*.jsonl")))
    pool = responses(paths, args.since, args.min_chars)
    if not pool:
        print("no responses matched", file=sys.stderr)
        return 1

    random.Random(args.seed).shuffle(pool)
    picked = pool[:args.count]
    for i, c in enumerate(picked, 1):
        c["id"] = f"R{i}-{c['session']}"
    json.dump({"drawn_from": len(pool), "seed": args.seed, "cases": picked},
              sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
