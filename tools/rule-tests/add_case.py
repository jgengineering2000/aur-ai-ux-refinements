#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Add one adjudicated case to the key, by session and turn index.

`sample.py` draws cases at RANDOM and caps them at MAX_CHARS, which is right for
sampling and wrong for adjudication. The cap silently excluded the four longest
responses of the 2026-08-09 session — including the two the owner ruled on most
explicitly — and length correlates with the rule-5 failure it was meant to
measure, because naming fifty things without explaining them takes room. So the
sampling cap must not gate the answer key: a case the owner has RULED on is
admissible at any length, and this is the path for it.

The text is read from the transcript, never retyped, for the reason sample.py
gives: a case written by the mind under test encodes only the failures it already
knows about. This tool selects; it does not author.

Home paths are rewritten to `~` on the way in. The case file is gitignored
precisely because it carries transcript text, and a username is PII regardless of
the destination.

Usage:
    add_case.py --session SID --turn N --id ID [--flag R...] [--not-flag R...]
                [--why TEXT] [--cases FILE] [--dry-run]

    --session SID    session id, or any unique prefix of one
    --turn N         index among that session's substantive replies, as listed
                     by --list
    --id ID          case id to record, e.g. R10-2d6e7db3
    --flag R...      rule numbers the grader MUST flag (owner-adjudicated only)
    --not-flag R...  rule numbers the grader must NOT flag
    --why TEXT       the owner's ruling, quoted — provenance for the key
    --list           list a session's substantive replies with indices, then exit
    --cases FILE     key file (default: real-cases.json beside this script)
    --dry-run        print the case that would be added, change nothing
    -h, --help       this text

Omitting both --flag and --not-flag adds the case UNKEYED, which score.py
reports without counting. That is the correct state for a response the owner has
not ruled on.
"""

import argparse
import glob
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from transcript import turns  # noqa: E402

PROJECTS = os.path.expanduser("~/.claude/projects/*")
MIN_CHARS = 400
ASK_CHARS = 700
HOME = os.path.expanduser("~")


def scrub(text):
    """Home paths carry a username; the key file is local but PII has no
    authorised destination, so it never enters the file in the first place."""
    return text.replace(HOME, "~") if text else text


def find_session(sid):
    hits = [p for p in glob.glob(os.path.join(PROJECTS, "*.jsonl"))
            if Path(p).stem.startswith(sid)]
    if not hits:
        sys.exit(f"add_case.py: no transcript whose id starts with {sid!r}")
    if len(hits) > 1:
        sys.exit(f"add_case.py: {sid!r} matches {len(hits)} sessions; "
                 "give more of the id")
    return hits[0]


def replies(path):
    """Substantive assistant replies, with the owner message each answered."""
    out, pending = [], None
    for turn in turns(path):
        if turn["kind"] == "ask":
            pending = turn["text"]
            continue
        if turn["kind"] != "reply" or len(turn["text"]) < MIN_CHARS:
            continue
        out.append({"at": turn.get("at"), "text": turn["text"],
                    "asked": pending})
        pending = None
    return out


def main(argv):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__.split("\n")[1])
    ap.add_argument("--session", required=True)
    ap.add_argument("--turn", type=int)
    ap.add_argument("--id")
    ap.add_argument("--flag", nargs="*", type=int, default=None)
    ap.add_argument("--not-flag", nargs="*", type=int, default=None,
                    dest="not_flag")
    ap.add_argument("--why")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--cases", default=str(HERE / "real-cases.json"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    path = find_session(args.session)
    items = replies(path)

    if args.list:
        for i, r in enumerate(items):
            head = r["text"].strip().splitlines()[0][:64]
            print(f"[{i:>2}] {len(r['text']):>6}ch  "
                  f"asked={'yes' if r['asked'] else 'no ':<3}  {head}")
        return 0

    if args.turn is None or args.id is None:
        ap.error("--turn and --id are required unless --list is given")
    if not 0 <= args.turn < len(items):
        ap.error(f"--turn {args.turn} out of range; this session has "
                 f"{len(items)} substantive replies (see --list)")
    if args.flag and args.not_flag:
        ap.error("a case is either a must_flag or a must_not_flag, not both")

    r = items[args.turn]
    case = {"session": Path(path).stem[:8], "at": r["at"],
            "asked": scrub(r["asked"][:ASK_CHARS]) if r["asked"] else None,
            "text": scrub(r["text"]), "id": args.id}
    if args.flag:
        case["must_flag"] = args.flag
    if args.not_flag:
        case["must_not_flag"] = args.not_flag
    if args.why:
        case["ruling"] = args.why

    keyed = "must_flag" in case or "must_not_flag" in case
    if args.dry_run:
        print(json.dumps(case, indent=2)[:1200])
        print(f"\n[dry-run] {'keyed' if keyed else 'UNKEYED'}, "
              f"{len(case['text'])} chars — nothing written")
        return 0

    cf = Path(args.cases)
    data = (json.loads(cf.read_text()) if cf.exists()
            else {"drawn_from": 0, "seed": 0, "cases": []})
    if any(c["id"] == args.id for c in data["cases"]):
        sys.exit(f"add_case.py: id {args.id!r} is already in {cf.name}")
    data["cases"].append(case)
    cf.write_text(json.dumps(data, indent=1) + "\n")
    print(f"added {args.id} ({'keyed' if keyed else 'UNKEYED'}, "
          f"{len(case['text'])} chars) — {len(data['cases'])} cases total")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
