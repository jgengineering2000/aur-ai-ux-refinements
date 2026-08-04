#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Score delegate gradings against the answer key in cases.json.

The delegate never sees the key — it is handed grader-prompt.md and the case
text only. This scores what came back.

Two failure modes are reported separately because they mean opposite things.
A MISS (an expected violation not flagged) says the rule is not operable as
written: a competent reader with the rule in front of them could not apply it.
A FALSE POSITIVE (a must-not-flag that was flagged) says the rule is
over-broad and will fire on correct work, which is how a rule earns a
reputation for being noise and stops being applied at all.

Usage:
    score.py <delegate-output-file>          one JSON object per line
    score.py -                               read the same from stdin
"""

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(argv):
    if len(argv) != 1 or argv[0] in ("-h", "--help"):
        print(__doc__.strip(), file=sys.stderr)
        return 0 if argv and argv[0] in ("-h", "--help") else 2

    raw = sys.stdin.read() if argv[0] == "-" else Path(argv[0]).read_text()
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

    key = json.loads((HERE / "cases.json").read_text())["cases"]
    width = max(len(c["id"]) for c in key)
    passed = failed = 0
    for case in key:
        cid = case["id"]
        if cid not in got:
            print(f"  {cid:<{width}}  NOT GRADED")
            failed += 1
            continue
        flagged = got[cid]
        misses = sorted(set(case["must_flag"]) - flagged)
        falsepos = sorted(set(case["must_not_flag"]) & flagged)
        if not misses and not falsepos:
            print(f"  {cid:<{width}}  PASS   flagged {sorted(flagged) or '[]'}")
            passed += 1
        else:
            bits = []
            if misses:
                bits.append(f"MISS {misses}")
            if falsepos:
                bits.append(f"FALSE POSITIVE {falsepos}")
            print(f"  {cid:<{width}}  FAIL   " + "  ".join(bits))
            failed += 1

    print(f"\n  {passed} passed, {failed} failed, {len(key)} cases")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
