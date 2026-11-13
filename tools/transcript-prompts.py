#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""Extract the OWNER's typed prompts from Claude Code session transcripts.

Reconstructing what the owner actually asked across a chain of sessions is a
recurring need -- it was done by hand once for a washout investigation and
crystallised so transcripts are not mined manually (PROCESS §1).

Delegates owner attribution and harness-scaffolding stripping to `transcript`
(`transcript.owner_items`) so every transcript reader applies one definition.

Usage:
    transcript-prompts.py <session.jsonl> [...] [--grep WORD] [--max CHARS]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from transcript import owner_items  # noqa: E402


def positive_int(value):
    """Argparse type requiring an integer >= 1."""
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected integer, got {value!r}")
    if n < 1:
        raise argparse.ArgumentTypeError(f"expected >= 1, got {n}")
    return n


def build_parser():
    ap = argparse.ArgumentParser(
        prog="transcript-prompts.py",
        description="Extract owner prompts (typed and queued) from session transcripts.")
    ap.add_argument("paths", nargs="+", metavar="session.jsonl",
                    help="transcript JSONL file(s)")
    ap.add_argument("--grep", default=None, metavar="WORD",
                    help="case-insensitive substring filter")
    ap.add_argument("--max", dest="maxc", type=positive_int, default=600,
                    metavar="CHARS", help="truncate body after CHARS characters (default 600)")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    grep = args.grep.lower() if args.grep else None

    rc = 0
    for p in args.paths:
        n = 0
        sid = os.path.basename(p)[:8]
        try:
            fh = open(p, errors="replace")
        except OSError as e:
            print(f"transcript-prompts: cannot read {p}: {e}", file=sys.stderr)
            rc = 1
            continue
        with fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                for t, ts_raw, queued in owner_items(rec):
                    if grep and grep not in t.lower():
                        continue
                    n += 1
                    ts = (ts_raw or "")[:16].replace("T", " ")
                    body = " ".join(t.split())
                    if len(body) > args.maxc:
                        body = body[:args.maxc] + " …"
                    mark = " queued" if queued else ""
                    print(f"[{sid} #{n:03d} {ts}{mark}] {body}")
        print(f"--- {sid}: {n} owner prompt(s) ---", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
