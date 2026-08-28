#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Score delegate gradings against the answer key in the case file.

The delegate never sees the key — it is handed grader-prompt.md and the case
text only. This scores what came back.

The key is **adjudicated, never invented**. Synthetic cases were deleted in
5babb52 because they are written by the same mind being tested, so a case earns
`must_flag` / `must_not_flag` only once the owner has ruled on a real sampled
response. Until then it is UNKEYED: reported so the verdict can be read and
argued with, but counted as neither a pass nor a failure. A harness that scored
an un-adjudicated case as passing would report a green suite that tested
nothing — which is the shape of the defect this file was found in, pointing at
a cases.json deleted underneath it.

Two failure modes are reported separately because they mean opposite things.
A MISS (an expected violation not flagged) says the rule is not operable as
written: a competent reader with the rule in front of them could not apply it.
A FALSE POSITIVE (a must-not-flag that was flagged) says the rule is
over-broad and will fire on correct work, which is how a rule earns a
reputation for being noise and stops being applied at all.

Usage:
    score.py <delegate-output-file> [--cases FILE]   one JSON object per line
    score.py - [--cases FILE]                        read the same from stdin

    --cases FILE   case file carrying the adjudicated key
                   (default: real-cases.json beside this script)
    -h, --help     this text
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(argv):
    ap = argparse.ArgumentParser(
        add_help=True, description="Score delegate gradings against the key.")
    ap.add_argument("gradings", help="delegate output file, or - for stdin")
    ap.add_argument("--cases", default=str(HERE / "real-cases.json"))
    # argparse already refuses an unknown option, names it, and exits non-zero.
    args = ap.parse_args(argv)

    case_file = Path(args.cases)
    if not case_file.exists():
        ap.error(f"no case file at {case_file}")

    raw = (sys.stdin.read() if args.gradings == "-"
           else Path(args.gradings).read_text())
    got = {}
    for line in raw.splitlines():
        line = line.strip().strip("`")
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if "id" in rec:
            got[rec["id"]] = set(rec.get("violates") or [])

    key = json.loads(case_file.read_text())["cases"]
    width = max(len(c["id"]) for c in key)
    passed = failed = unkeyed = 0
    for case in key:
        cid = case["id"]
        adjudicated = "must_flag" in case or "must_not_flag" in case
        if cid not in got:
            print(f"  {cid:<{width}}  NOT GRADED")
            failed += 1
            continue
        flagged = got[cid]
        if not adjudicated:
            print(f"  {cid:<{width}}  UNKEYED  flagged {sorted(flagged) or '[]'}")
            unkeyed += 1
            continue
        misses = sorted(set(case.get("must_flag", [])) - flagged)
        falsepos = sorted(set(case.get("must_not_flag", [])) & flagged)
        if not misses and not falsepos:
            print(f"  {cid:<{width}}  PASS     flagged {sorted(flagged) or '[]'}")
            passed += 1
        else:
            bits = []
            if misses:
                bits.append(f"MISS {misses}")
            if falsepos:
                bits.append(f"FALSE POSITIVE {falsepos}")
            print(f"  {cid:<{width}}  FAIL     " + "  ".join(bits))
            failed += 1

    tail = f", {unkeyed} awaiting adjudication" if unkeyed else ""
    print(f"\n  {passed} passed, {failed} failed{tail}, {len(key)} cases")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
