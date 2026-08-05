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

Each excerpt carries the owner message it answered, in `asked`, or null where it
followed a tool call rather than a request. Without that field rule 1 could not
be graded at all: whether a response acknowledged the request is not a property
of the response, and a grader shown only the reply can never flag a missing
acknowledgement — which is how the rule scored 10/10 while sitting at zero.

Usage:
    sample.py [--projects GLOB] [--since YYYY-MM-DD] [--count N] [--seed N]
              [--min-chars N] [--answering] [--brief]

    --since DATE     only responses on or after DATE
    --count N        how many excerpts (default 9)
    --seed N         sampling seed (default 1)
    --min-chars N    ignore responses shorter than this (default 400)
    --answering      only responses that answer an owner message, in sessions
                     that are dialogues — the draw to use when grading rule 1
    --brief          emit the grader brief followed by the rendered cases,
                     ready to hand a delegate, instead of JSON
    -h, --help       this text
"""

import argparse
import glob
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from transcript import turns  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# Excerpts are taken WHOLE or not at all. Truncating mid-response manufactures
# violations that are artifacts of the cut: a proposed solution stated in the
# paragraph after the knife looks exactly like a missing one, and rule 6 then
# fires on a response that satisfied it. Verified on a real draw — one of three
# rule-6 verdicts was a truncation artifact.
MAX_CHARS = 4000

# The request is shown to the grader so rule 1 can be graded at all, but it is
# context rather than the thing under test, so a long one is cut where a long
# response is not.
ASK_CHARS = 700


def responses(paths, since, min_chars):
    out = []
    for f in paths:
        session = list(turns(f))
        # A session carrying one owner message and no follow-up is a delegate
        # brief or a one-shot task, not a dialogue — and the contract governs
        # dialogue. Rule 1 exists so a misunderstanding can be corrected before
        # the work is built on it, which presumes the owner is still there to
        # correct it. Drawing these in scores a delegate obeying a strict output
        # contract as having failed to acknowledge, which is the false positive
        # the first grading run produced. 114 of 200 sessions here are one-shots.
        dialogue = sum(1 for t in session if t["kind"] == "ask") > 1
        for turn in session:
            if turn["kind"] != "reply":
                continue
            if since and turn["at"][:10] < since:
                continue
            t = turn["text"]
            if not (min_chars <= len(t) <= MAX_CHARS):
                continue
            ask = turn["answers"]
            out.append({"session": os.path.basename(f)[:8],
                        "at": turn["at"],
                        "dialogue": dialogue,
                        "asked": (ask[:ASK_CHARS] + " …"
                                  if ask and len(ask) > ASK_CHARS else ask),
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
    ap.add_argument("--answering", action="store_true")
    ap.add_argument("--brief", action="store_true")
    args = ap.parse_args(argv)

    if args.since and not re.match(r"^\d{4}-\d{2}-\d{2}$", args.since):
        ap.error(f"--since expects YYYY-MM-DD, got {args.since!r}")

    paths = sorted(glob.glob(os.path.join(args.projects, "*.jsonl")))
    pool = responses(paths, args.since, args.min_chars)
    if args.answering:
        pool = [c for c in pool if c["asked"] and c["dialogue"]]
    for c in pool:
        del c["dialogue"]
    if not pool:
        print("no responses matched", file=sys.stderr)
        return 1

    random.Random(args.seed).shuffle(pool)
    picked = pool[:args.count]
    for i, c in enumerate(picked, 1):
        c["id"] = f"R{i}-{c['session']}"

    if args.brief:
        # The delegate starts cold and must not read the repo, so the brief and
        # the cases are emitted as one self-contained document. Assembling this
        # by hand once per grading run is the repetition this flag removes.
        print(open(os.path.join(HERE, "grader-prompt.md")).read().rstrip())
        print("\n## Cases\n")
        for c in picked:
            print(f"### {c['id']}\n")
            print("**asked** (the owner message this answered):\n")
            print("```\n" + (c["asked"] or "null") + "\n```\n")
            print("**response under test:**\n")
            print("```\n" + c["text"] + "\n```\n")
        return 0

    json.dump({"drawn_from": len(pool), "seed": args.seed, "cases": picked},
              sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
