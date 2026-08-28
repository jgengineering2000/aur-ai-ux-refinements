#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Simple Browser / Jetski Tab Title Patch
=========================================
VS Code's Simple Browser and Antigravity's "Jetski" both set a static
panel title when first opened and never update it as the URL changes.
Every document you open shows the same generic tab label.

This patch makes the tab title reflect the filename of the loaded page,
so multiple documents are distinguishable at a glance.

Targets (auto-detected):
  Antigravity:   /usr/share/antigravity/resources/app/extensions/simple-browser/dist/extension.js
  VS Code:       /usr/share/code/resources/app/extensions/simple-browser/dist/extension.js
  VS Code (snap):/snap/code/current/usr/share/code/resources/app/extensions/simple-browser/dist/extension.js

Requires write access to the target file. System-installed (apt/dpkg) builds
own those files as root, so sudo is needed there. User-local installs
(AppImage, ~/.local/) are writable without elevation.

Usage:
    python3 simplebrowser-title.py          # user-local install
    sudo python3 simplebrowser-title.py     # system (apt) install

Exit status: 0 when every target is patched or already patched; 1 when no
target exists, one is not writable, or the show() shape was not found.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

# Target list lives in ONE place — docinfra — shared with both installer front
# ends and apply-all.py. This script must stay runnable standalone (including
# under `sudo python3` from any cwd), hence the path insert.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from docinfra import SIMPLEBROWSER_CANDIDATES as CANDIDATES

# The show() method, matched by its STRUCTURE rather than by a literal.
#
# The two parameter names are assigned by the minifier and are not stable: the
# Antigravity build emits `show(t,e)` and the VS Code build `show(e,t)` — the
# same source, renamed. A literal encoding one spelling reports "pattern not
# found" on the other, which reads as "the extension changed its behaviour"
# when nothing about the behaviour changed at all.
#
# So capture the names and reuse them in the replacement. Backreferences make
# the match self-consistent: \1 must be the argument passed to getHtml() and
# \2 the object whose viewColumn is read, so this cannot match a same-shaped
# method that happens to use its parameters differently.
SHOW_RE = re.compile(
    r"show\((?P<url>[A-Za-z_$][\w$]*),(?P<opts>[A-Za-z_$][\w$]*)\)\{"
    r"this\._webviewPanel\.webview\.html=this\.getHtml\((?P=url)\),"
    r"this\._webviewPanel\.reveal\((?P=opts)\?\.viewColumn,(?P=opts)\?\.preserveFocus\)\}"
)

# Marker identifying an already-patched file. Independent of the minifier's
# naming, because the local it introduces is ours.
APPLIED_MARKER = "_webviewPanel.title=_p"


def _replacement(m):
    """Rebuild show() with a title derived from the URL, in the file's own names."""
    url, opts = m.group("url"), m.group("opts")
    return (
        f"show({url},{opts}){{"
        f'const _p={url}.split("/").pop().replace(/\\.[^.]+$/,"")||"Preview";'
        f"this._webviewPanel.title=_p;"
        f"this._webviewPanel.webview.html=this.getHtml({url}),"
        f"this._webviewPanel.reveal({opts}?.viewColumn,{opts}?.preserveFocus)}}"
    )


def patch(path):
    """Return 'patched', 'already', or 'error' — three outcomes, never two."""
    src = path.read_text()

    if APPLIED_MARKER in src:
        print(f"  already patched")
        return "already"

    m = SHOW_RE.search(src)
    if not m:
        print(f"  ERROR: show() pattern not found — extension may have updated")
        print(f"         Check extension.js manually")
        return "error"

    backup = str(path) + ".bak"
    shutil.copy2(path, backup)
    print(f"  backup: {backup}")
    path.write_text(src[:m.start()] + _replacement(m) + src[m.end():])
    print(f"  patched OK (params {m.group('url')},{m.group('opts')})")
    return "patched"


def main():
    # The accepted argument set, in one place: nothing but --help.
    argparse.ArgumentParser(description=__doc__.strip().splitlines()[0]).parse_args()

    targets = [p for p in CANDIDATES if p.exists()]

    if not targets:
        print("No Simple Browser extension found at known paths.")
        print("Searched:")
        for p in CANDIDATES:
            print(f"  {p}")
        sys.exit(1)

    # Warn early if any target needs elevated access we don't have
    import os
    unwritable = [p for p in targets if not os.access(p, os.W_OK)]
    if unwritable:
        print("The following file(s) require elevated access (re-run with sudo):")
        for p in unwritable:
            print(f"  {p}")
        sys.exit(1)

    outcomes = []
    for target in targets:
        # IDE name for display: '/usr/share/<ide>/…' → parts[3]; '/snap/<ide>/…' → parts[2].
        label = target.parts[2] if target.parts[1] == "snap" else target.parts[3]
        print(f"\n[{label}] {target}")
        outcomes.append(patch(target))

    if "patched" in outcomes:
        print("\nRestart the extension host (no full IDE restart needed):")
        print("  Ctrl+Shift+P → Developer: Restart Extension Host")
    failed = outcomes.count("error")
    if failed:
        print(f"\nERROR: {failed} of {len(targets)} target(s) could not be patched.")
        sys.exit(1)


if __name__ == "__main__":
    main()
