#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Claude Code UI Layout Patch
============================
Reduces the excessive left/right margins in the Claude Code chat panel,
moves the timeline dot closer to the left edge, aligns the vertical line
with the dot, and uses an overlay scrollbar so it doesn't steal content width.

Targets: ~/.vscode/extensions/anthropic.claude-code-*/webview/index.css
         ~/.antigravity/extensions/anthropic.claude-code-*/webview/index.css
         (whichever exist; both are patched when both are present)

No sudo required — the extension lives in the user's home directory.
Safe to re-run: detects already-patched files.

Exit status: 0 when every target is patched or already patched; 1 when no
target exists or any rule was NOT FOUND (the extension changed shape) — so a
caller, human or systemd, can tell "done" from "silently did nothing".
"""

import argparse
import glob
import shutil
import sys
from pathlib import Path

# Search paths for Claude Code extension (add others as needed)
SEARCH_PATTERNS = [
    "~/.antigravity/extensions/anthropic.claude-code-*/webview/index.css",
    "~/.vscode/extensions/anthropic.claude-code-*/webview/index.css",
]

PATCHES = [
    # Container: reduce left/right padding from 20px to 8px
    ("padding:20px 20px 40px}",
     "padding:20px 8px 40px}",
     "container left/right padding 20→8px"),

    ("padding:0 20px 40px}",
     "padding:0 8px 40px}",
     "sticky container right padding 20→8px"),

    # Container: overlay scrollbar so it doesn't reserve horizontal space
    ("overflow-y:auto;overflow-x:hidden;display:flex;background-color:var(--app-primary-background)",
     "overflow-y:overlay;overflow-x:hidden;display:flex;background-color:var(--app-primary-background)",
     "scrollbar overlay mode"),

    # Assistant message: reduce left indent from 30px to 14px
    ("padding-left:30px}",
     "padding-left:14px}",
     "assistant message indent 30→14px"),

    # Dot (::before): move from left:9px to left:1px
    ("top:15px;left:9px}",
     "top:15px;left:1px}",
     "dot position left 9→1px"),

    # Vertical line (::after): move from left:12px to left:4px (centred on dot)
    ("top:0;bottom:0;left:12px}",
     "top:0;bottom:0;left:4px}",
     "vertical line left 12→4px"),

    # Input box: match reduced container padding
    ("bottom:16px;left:16px;right:16px}",
     "bottom:16px;left:8px;right:8px}",
     "input box inset 16→8px"),
]


def find_css_files():
    files = []
    for pattern in SEARCH_PATTERNS:
        files.extend(glob.glob(str(Path(pattern).expanduser())))
    return files


def patch_file(path):
    """Apply every rule; return (changed, already, missing) message lists.

    `missing` is the failure signal: a rule whose before AND after values are
    both absent means the extension changed shape and this patch no longer
    knows where to land.
    """
    src = Path(path).read_text()
    changed, already, missing = [], [], []

    for old, new, label in PATCHES:
        if new in src:
            already.append(f"  already applied: {label}")
        elif old in src:
            src = src.replace(old, new, 1)
            changed.append(f"  patched: {label}")
        else:
            missing.append(f"  NOT FOUND (extension updated?): {label}")

    if changed:
        backup = path + ".bak"
        shutil.copy2(path, backup)
        print(f"Backup: {backup}")
        Path(path).write_text(src)

    return changed, already, missing


def main():
    # The accepted argument set, in one place: nothing but --help. An
    # unrecognised token prints usage and exits 2 before any file is touched.
    argparse.ArgumentParser(description=__doc__.strip().splitlines()[0]).parse_args()

    files = find_css_files()
    if not files:
        print("ERROR: Claude Code extension CSS not found.")
        print("Searched:", SEARCH_PATTERNS)
        sys.exit(1)

    failed = 0
    for f in files:
        print(f"\nPatching: {f}")
        changed, already, missing = patch_file(f)
        for msg in changed + already + missing:
            print(msg)
        if missing:
            failed += 1

    print("\nDone — restart the extension host:")
    print("  Ctrl+Shift+P → Developer: Restart Extension Host")
    if failed:
        print(f"\nERROR: {failed} of {len(files)} file(s) had rules that were NOT FOUND — "
              "update PATCHES for the new extension layout.")
        sys.exit(1)


if __name__ == "__main__":
    main()
