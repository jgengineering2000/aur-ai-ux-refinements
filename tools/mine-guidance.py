#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Mine the transcript corpus for the owner's guidance about INTERACTION.

The owner has restated how responses, questions and documents should be shaped
many times over months, in different words each time, scattered across sessions
that no longer exist in anyone's memory. Any single restatement reads as a
one-off preference; the whole set reads as a specification. This recovers the
set so it can be consolidated rather than re-derived a twenty-first time.

Only the OWNER's words count. Tool results arrive inside `user` records — that
is the API's turn structure, not something the owner said — so they are excluded
explicitly. Without that filter the output is mostly command output that happens
to contain the word "table".

Categories exist because the restatements are not interchangeable: "give me more
context" and "stop writing walls of text" pull in opposite directions, and a
consolidation that flattens them loses the tension that makes the guidance
useful.

Usage:
    mine-guidance.py [--projects GLOB] [--min-score N] [--category NAME] [--full]

    --projects GLOB   transcript roots (default: ~/.claude/projects/*)
    --min-score N     require N category hits in one message (default: 2)
    --category NAME   restrict to one category
    --full            print whole messages instead of excerpts
    -h, --help        this text
"""

import argparse
import glob
import json
import os
import re
import sys

CATEGORIES = {
    "context": r"(more context|context for|explain why|background|rationale|"
               r"the why\b|reasoning|justif|so i (can|know) )",
    "structure": r"(table|bullet|list|whitespace|white space|section|header|"
                 r"heading|break (it|them|things) up|organiz|structur|format|"
                 r"columns?|align)",
    "brevity": r"(too long|too much|verbose|wall of text|jammed|huge mess|"
               r"concise|terse|shorter|trim|brief|tighten|bloat)",
    "one_idea": r"(one at a time|too many|muddy|muddying|one idea|per step|"
                r"separate(ly)?|piecemeal|split (it|them)|overwhelm|digest)",
    "asking": r"(clarify|ask me|question|options|recommend|before you|"
              r"check with me|approval|confirm)",
    "artifacts": r"(audit|plan|document|report|spec|summary|manifest)",
    "attention": r"(my time|attention|precious|scarce|efficient|readable|"
                 r"comprehen|scan|at a glance|follow along)",
}
COMPILED = {k: re.compile(v, re.I) for k, v in CATEGORIES.items()}

# Boilerplate that is not the owner talking.
NOISE = re.compile(r"^\s*(<|\{|\[|command-name|local-command|caveat:)", re.I)


def owner_messages(paths):
    """Every message the OWNER actually typed, oldest first."""
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
                if rec.get("type") != "user":
                    continue
                content = (rec.get("message") or {}).get("content")
                if isinstance(content, str):
                    blocks = [{"type": "text", "text": content}]
                elif isinstance(content, list):
                    blocks = content
                else:
                    continue
                # A record carrying any tool_result is the TOOL answering, not
                # the owner — the same attribution rule the transcript view uses.
                if any(isinstance(b, dict) and b.get("type") == "tool_result"
                       for b in blocks):
                    continue
                for b in blocks:
                    if not (isinstance(b, dict) and b.get("type") == "text"):
                        continue
                    t = (b.get("text") or "").strip()
                    if len(t) < 40 or NOISE.match(t):
                        continue
                    out.append((rec.get("timestamp", "")[:10],
                                os.path.basename(f)[:8], t))
    out.sort()
    return out


def main(argv):
    ap = argparse.ArgumentParser(description="Mine owner interaction guidance.")
    ap.add_argument("--projects",
                    default=os.path.expanduser("~/.claude/projects/*"))
    ap.add_argument("--min-score", type=int, default=2)
    ap.add_argument("--category", choices=sorted(CATEGORIES))
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args(argv)

    paths = sorted(glob.glob(os.path.join(args.projects, "*.jsonl")))
    if not paths:
        print(f"no transcripts under {args.projects}", file=sys.stderr)
        return 1

    msgs = owner_messages(paths)
    seen, rows = set(), []
    for date, sid, text in msgs:
        hits = {k for k, rx in COMPILED.items() if rx.search(text)}
        if args.category and args.category not in hits:
            continue
        if len(hits) < args.min_score:
            continue
        key = " ".join(text.split())[:70]
        if key in seen:
            continue
        seen.add(key)
        rows.append((date, sid, sorted(hits), text))

    print(f"{len(msgs)} owner messages scanned, {len(rows)} carry "
          f"{args.min_score}+ interaction categories\n")
    tally = {}
    for _d, _s, hits, _t in rows:
        for h in hits:
            tally[h] = tally.get(h, 0) + 1
    print("category frequency: "
          + ", ".join(f"{k} {v}" for k, v in sorted(tally.items(),
                                                    key=lambda kv: -kv[1])))
    print()
    for date, sid, hits, text in rows:
        body = " ".join(text.split())
        print(f"[{date} {sid}] {','.join(hits)}")
        print("   " + (body if args.full else body[:400]))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
