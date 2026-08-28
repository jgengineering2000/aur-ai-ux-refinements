#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Claude Code Vocabulary Patch
==============================
Replaces the ~50-word rotating "processing" vocabulary
("Germinating", "Spelunking", "Wibbling", ...) with a single
static word. Cute the first time; annoying by the tenth session.

Targets: ~/.vscode/extensions/anthropic.claude-code-*/webview/index.js
         ~/.antigravity/extensions/anthropic.claude-code-*/webview/index.js
         (whichever exist; both are patched when both are present)

No sudo required. Safe to re-run.

Usage:
    python3 claude-ui-vocabulary.py              # defaults to "Working"
    python3 claude-ui-vocabulary.py "..."        # ellipsis
    python3 claude-ui-vocabulary.py ""           # empty string (hides label)

Exit status: 0 when every target is patched or already patched; 1 when no
target exists or the anchor could not be located in one of them.
"""

import argparse
import glob
import shutil
import sys
from pathlib import Path

SEARCH_PATTERNS = [
    "~/.antigravity/extensions/anthropic.claude-code-*/webview/index.js",
    "~/.vscode/extensions/anthropic.claude-code-*/webview/index.js",
]

# First word in the array — used as an anchor to locate it
ANCHOR = '"Germinating"'


def find_js_files():
    files = []
    for pattern in SEARCH_PATTERNS:
        files.extend(glob.glob(str(Path(pattern).expanduser())))
    return files


def patch_file(path, replacement_word):
    """Return 'patched', 'already', or 'error' — three outcomes, never two.

    Collapsing already-patched and failed into one False is how a caller ends
    up unable to tell a no-op from a defect.
    """
    src = Path(path).read_text()

    idx = src.find(ANCHOR)
    if idx < 0:
        if f'["{replacement_word}"]' in src or '["Working"]' in src:
            print(f"  already patched")
            return "already"
        print(f"  ERROR: anchor {ANCHOR} not found — extension may have updated")
        return "error"

    start = src.rfind('[', 0, idx)
    end = src.find(']', idx) + 1
    old_array = src[start:end]

    if not old_array.startswith('["'):
        print(f"  ERROR: unexpected array start: {old_array[:40]}")
        return "error"

    word_count = old_array.count('","') + 1
    new_array = f'["{replacement_word}"]'

    backup = path + ".bak"
    shutil.copy2(path, backup)
    print(f"  backup: {backup}")

    Path(path).write_text(src[:start] + new_array + src[end:])
    print(f"  replaced {word_count}-word array with {new_array}")
    return "patched"


def main():
    # The accepted argument set, in one place: an optional replacement word
    # (any string, "" included) and --help. Anything else is refused up front.
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("word", nargs="?", default="Working",
                    help='replacement label (default "Working"; "" hides it)')
    word = ap.parse_args().word

    files = find_js_files()
    if not files:
        print("ERROR: Claude Code extension JS not found.")
        print("Searched:", SEARCH_PATTERNS)
        sys.exit(1)

    failed = 0
    for f in files:
        print(f"\nPatching: {f}")
        if patch_file(f, word) == "error":
            failed += 1

    print("\nDone — restart the extension host:")
    print("  Ctrl+Shift+P → Developer: Restart Extension Host")
    if failed:
        print(f"\nERROR: {failed} of {len(files)} file(s) could not be patched.")
        sys.exit(1)


if __name__ == "__main__":
    main()
