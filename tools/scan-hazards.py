#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
scan-hazards — pre-flight for a rename/refactor pass.

Finds the conditions that make a mechanical edit lie to you. Every check here
was paid for once by a real failure; none of them are hypothetical.

    python3 tools/scan-hazards.py <root> [<root> ...]

Exit 0 clean, 1 hazards found.

The one that costs the most time is the first: a file containing a NUL byte is
classified as binary by grep, which then SILENTLY SUPPRESSES matches. Every
search over that file returns nothing, and nothing tells you why — you conclude
the string is absent when it is present. It cost four tool calls and a wrong
diagnosis before `grep -c` vs `grep -ac` gave it away.
"""

import argparse
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {".git", "build", "dist", "__pycache__", "node_modules", ".gradle",
             ".venv", "target"}
TEXTISH = {".py", ".sh", ".md", ".json", ".js", ".css", ".html", ".txt",
           ".template", ".service", ".path", ".toml", ".yaml", ".yml", ".rs"}
NAMED = {".gitignore", ".gitattributes", "Makefile", "CLAUDE.md", "AGENTS.md",
         "GEMINI.md"}


def walk(root):
    for p in sorted(Path(root).rglob("*")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if not p.is_file() or p.is_symlink():
            continue
        if p.suffix in TEXTISH or p.name in NAMED:
            yield p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+")
    a = ap.parse_args()

    hazards = []
    for root in a.roots:
        root = Path(root).expanduser().resolve()
        if not root.is_dir():
            continue
        for p in walk(root):
            try:
                b = p.read_bytes()
            except OSError:
                continue

            # 1. NUL bytes — grep goes silent. The failure mode is a search that
            #    returns nothing and reports success.
            n = b.count(0)
            if n:
                hazards.append(("NUL", p, f"{n} NUL byte(s) — grep will treat this "
                                          f"as binary and suppress all matches"))

            # 2. Invalid UTF-8 — same class: tools that decode strictly skip the
            #    file, and a skipped file looks identical to a clean one.
            try:
                b.decode("utf-8")
            except UnicodeDecodeError as e:
                hazards.append(("UTF8", p, f"invalid UTF-8 at byte {e.start}"))

            # 3. CRLF in shell/python — a rewritten line keeps its \r and the
            #    interpreter fails with an unreadable error.
            if p.suffix in (".sh", ".py") and b"\r\n" in b:
                hazards.append(("CRLF", p, "CRLF endings in an executable script"))

        # 4. Symlinks whose target is about to move, and symlinks that are
        #    TRACKED — a symlink's target is its content, so it carries whatever
        #    absolute path it points at into the repository.
        if (root / ".git").is_dir():
            out = subprocess.run(["git", "-C", str(root), "ls-files", "-s"],
                                 capture_output=True, text=True).stdout
            for line in out.splitlines():
                if line.startswith("120000"):
                    rel = line.split("\t", 1)[-1]
                    tgt = (root / rel)
                    try:
                        t = str(tgt.readlink())
                    except OSError:
                        t = "?"
                    if t.startswith("/"):
                        hazards.append(("TRACKED-ABS-SYMLINK", root / rel,
                                        f"tracked symlink -> {t} (an absolute "
                                        f"path becomes tracked content)"))

    if not hazards:
        print("scan-hazards: clean")
        return 0
    print(f"scan-hazards: {len(hazards)} hazard(s)\n")
    for kind, p, msg in hazards:
        print(f"  [{kind}] {p}")
        print(f"          {msg}")
    print("\n  Resolve these BEFORE a mechanical pass. Each one makes an edit or a\n"
          "  search report success while doing nothing.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
