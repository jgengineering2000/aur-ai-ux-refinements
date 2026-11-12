#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""Extract the OWNER's typed prompts from Claude Code session transcripts.

Reconstructing what the owner actually asked across a chain of sessions is a
recurring need -- it was done by hand once for a washout investigation and
crystallised so transcripts are not mined manually (PROCESS §1).

What counts as an owner prompt:
- A `type: "user"` record whose origin is the owner (`origin.kind == "human"`,
  or no origin field), excluding `isMeta`, `isCompactSummary`, `tool_result`
  blocks, and harness scaffolding (`SKIP_MARKERS`), OR
- Text queued while the assistant was busy: `type: "attachment"` with
  `attachment.type == "queued_command"` and `origin.kind == "human"` (or
  absent origin with `commandMode == "prompt"`), marked `queued`.

Usage:
    transcript-prompts.py <session.jsonl> [...] [--grep WORD] [--max CHARS]
"""

import argparse
import json
import sys

SKIP_MARKERS = (
    "<system-reminder>", "<command-name>", "<command-message>",
    "<local-command-stdout>", "[Request interrupted",
    "Caveat: The messages below", "<task-notification>",
    "<cross-session-message",
)


def texts(content):
    """Extract text strings from a string or block-list content field."""
    if isinstance(content, str):
        return [content]
    out = []
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(b.get("text", ""))
    return out


def _origin_is_non_human(rec):
    o = rec.get("origin")
    return isinstance(o, dict) and o.get("kind") is not None and o.get("kind") != "human"


def owner_items(rec):
    """Return `(text, timestamp, queued)` tuples for owner text in one record."""
    kind = rec.get("type")
    if kind == "attachment":
        a = rec.get("attachment")
        if not isinstance(a, dict) or a.get("type") != "queued_command":
            return []
        o = a.get("origin")
        if isinstance(o, dict) and o.get("kind") != "human":
            return []
        if o is None and a.get("commandMode") != "prompt":
            return []
        ts = a.get("timestamp") or rec.get("timestamp")
        return [(t, ts, True) for t in texts(a.get("prompt"))]
    if kind != "user":
        return []
    if rec.get("isMeta") or rec.get("isCompactSummary") or _origin_is_non_human(rec):
        return []
    msg = rec.get("message") or {}
    if any(isinstance(b, dict) and b.get("type") == "tool_result"
           for b in (msg.get("content") or []) if isinstance(b, dict)):
        return []
    return [(t, rec.get("timestamp"), False) for t in texts(msg.get("content"))]


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

    for p in args.paths:
        n = 0
        sid = p.rsplit("/", 1)[-1][:8]
        with open(p, errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                for t, ts_raw, queued in owner_items(rec):
                    t = t.strip()
                    if not t or any(m in t for m in SKIP_MARKERS):
                        continue
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
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
