#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Integrated Browser Spellcheck Suggestions Patch
===============================================
VS Code's integrated browser (the view behind `workbench.action.browser.open`,
which `simpleBrowser.show` forwards localhost URLs to since ~1.13x) runs
Chromium's spellchecker, so misspelled words in a page's text fields get red
underlines — but its right-click menu never offers the corrections. The menu
is built in `main.js`'s `showContextMenu(view, params)`, and `params` is
Electron's full ContextMenuParams: `misspelledWord` and `dictionarySuggestions`
arrive on every right-click and are dropped unread (neither token occurs
anywhere in the shipped app).

This patch appends, inside the existing isEditable branch and above
Cut/Copy/Paste: one menu item per dictionary suggestion (clicking
`webContents.replaceMisspelling()`, Chromium's own replacement primitive) and
an "Add to Dictionary" item (`session.addWordToSpellCheckerDictionary()`).
Nothing is reinvented — the dictionary, the checker and the replacement all
belong to the mechanism that draws the underline; the menu rows are the only
addition.

Targets (auto-detected):
  VS Code:        /usr/share/code/resources/app/out/main.js
  VS Code (snap): /snap/code/current/usr/share/code/resources/app/out/main.js

Antigravity is deliberately absent: its build predates the integrated browser,
so the anchor does not exist there and listing it would turn "feature absent"
into a reported failure. If a later Antigravity ships the same browser, adding
its path to VSCODE_MAINJS_CANDIDATES in docinfra is the whole change.

`out/main.js` is NOT in product.json's checksum list (verified 1.135.0), so no
"installation appears corrupt" handling is needed. System installs own the
file as root, so sudo is required there.

Usage:
    python3 vscode-spellcheck-menu.py            # user-writable install
    sudo python3 vscode-spellcheck-menu.py       # system (apt) install
    python3 vscode-spellcheck-menu.py --check    # report, write nothing
    python3 vscode-spellcheck-menu.py --selftest # gate the matcher on fixtures

Exit status: 0 when every existing target is patched or already patched (and
for --check: would-patch or already); 1 when no target exists, one is not
writable, or the showContextMenu shape was not found.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

# Target list lives in ONE place — docinfra — shared with apply-all.py and the
# installer front ends. This script must stay runnable standalone (including
# under `sudo python3` from any cwd), hence the path insert.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from docinfra import VSCODE_MAINJS_CANDIDATES as CANDIDATES

# The function head, matched by STRUCTURE rather than by literal names: the
# minifier assigns the parameter and local names per build (`e,t` today,
# anything tomorrow), so every name is captured and reused via backreference.
# The backreferences make the match self-consistent — <wc> must be the local
# assigned from <view>.webContents, so this cannot match a same-shaped method
# wiring its locals differently.
HEAD_RE = re.compile(
    r"async showContextMenu\((?P<view>[A-Za-z_$][\w$]*),(?P<params>[A-Za-z_$][\w$]*)\)\{"
    r"let (?P<win>[A-Za-z_$][\w$]*)=(?P=view)\.getElectronWindow\(\);"
    r"if\(!(?P=win)\)return;"
    r"let (?P<wc>[A-Za-z_$][\w$]*)=(?P=view)\.webContents;"
)

# The insertion anchor inside that function: the head of the isEditable
# branch, whose first item is the Cut role. <params> must be the SAME name the
# head captured, so the two stages cannot pair across different functions.
EDIT_RE_TMPL = (
    r"{params}\.isEditable\?\("
    r"(?P<menu>[A-Za-z_$][\w$]*)\.append\(new (?P<item>[A-Za-z_$][\w$]*)"
    r"\(\{{role:\"cut\",enabled:{params}\.editFlags\.canCut\}}\)\)"
)

# Marker identifying an already-patched file. Also the self-retirement test:
# if a future VS Code consumes replaceMisspelling itself, the patch stands
# down rather than doubling the menu.
APPLIED_MARKER = "replaceMisspelling"


def _suggestion_items(params, menu, item, wc):
    """The inserted menu rows, spelled in the file's own minified names."""
    return (
        f"{params}.misspelledWord&&("
        f"{params}.dictionarySuggestions.forEach(_sg=>"
        f"{menu}.append(new {item}({{label:_sg,click:()=>{wc}.replaceMisspelling(_sg)}}))),"
        f"{menu}.append(new {item}({{label:\"Add to Dictionary\","
        f"click:()=>{wc}.session.addWordToSpellCheckerDictionary({params}.misspelledWord)}})),"
        f"{menu}.append(new {item}({{type:\"separator\"}}))"
        f"),"
    )


def apply(src):
    """Return (patched_source, why) — why is None on success, else the miss."""
    head = HEAD_RE.search(src)
    if not head:
        return None, "showContextMenu head not found"
    params, wc = head.group("params"), head.group("wc")
    edit_re = re.compile(EDIT_RE_TMPL.format(params=re.escape(params)))
    edit = edit_re.search(src, head.end())
    if not edit:
        return None, "isEditable/cut anchor not found after showContextMenu"
    insert_at = src.find("?(", edit.start()) + 2
    rows = _suggestion_items(params, edit.group("menu"), edit.group("item"), wc)
    return src[:insert_at] + rows + src[insert_at:], None


def patch(path, check_only):
    """Return 'patched', 'already', or 'error' — three outcomes, never two."""
    src = path.read_text()
    if APPLIED_MARKER in src:
        print("  already patched (or upstream now handles suggestions)")
        return "already"
    out, why = apply(src)
    if out is None:
        print(f"  ERROR: {why} — VS Code may have updated; inspect main.js "
              f"and update HEAD_RE/EDIT_RE_TMPL")
        return "error"
    if check_only:
        print("  would patch (run without --check to write)")
        return "patched"
    bak = path.with_suffix(path.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(path, bak)
    path.write_text(out)
    print(f"  patched ({len(out) - len(src)} bytes added; backup: {bak.name})")
    return "patched"


# ── Selftest ─────────────────────────────────────────────────────────────────
# Synthetic fixtures of OUR construction (no shipped code is embedded),
# replicating the minified shape in two different namings — the failure the
# capture logic exists to prevent is a literal that matches one build's
# renaming and reports "not found" on the next.

_FIX_A = (
    'async showContextMenu(e,t){let r=e.getElectronWindow();if(!r)return;'
    'let o=e.webContents;if(o.isDestroyed())return;let l=new EN;'
    't.isEditable?(l.append(new tn({role:"cut",enabled:t.editFlags.canCut})),'
    'l.append(new tn({role:"copy",enabled:t.editFlags.canCopy}))):'
    't.selectionText&&l.append(new tn({role:"copy"})),l.popup({})}'
)
_FIX_B = (
    'async showContextMenu(t,i){let n=t.getElectronWindow();if(!n)return;'
    'let R=t.webContents;if(R.isDestroyed())return;let Y=new EN;'
    'i.isEditable?(Y.append(new Qz({role:"cut",enabled:i.editFlags.canCut})),'
    'Y.append(new Qz({role:"copy",enabled:i.editFlags.canCopy}))):'
    'i.selectionText&&Y.append(new Qz({role:"copy"})),Y.popup({})}'
)


def selftest():
    fails = []

    def check(cond, what):
        print(("  ok    " if cond else "  FAIL  ") + what)
        if not cond:
            fails.append(what)

    for name, fix in (("A (e,t,o,l,tn)", _FIX_A), ("B (t,i,R,Y,Qz)", _FIX_B)):
        out, why = apply(fix)
        check(out is not None, f"fixture {name}: anchor matched ({why})")
        if out is None:
            continue
        check("replaceMisspelling" in out and "Add to Dictionary" in out,
              f"fixture {name}: suggestion rows inserted")
        check(out.index("misspelledWord") < out.index('role:"cut"'),
              f"fixture {name}: rows precede Cut/Copy/Paste")
        check(APPLIED_MARKER in out,
              f"fixture {name}: applied marker present after patch")
    out, why = apply('function unrelated(){return 1}')
    check(out is None, "garbage input is refused, not half-matched")

    if fails:
        print(f"\nselftest: {len(fails)} FAILURE(S)")
        return 1
    print("\nselftest: all assertions passed")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="report what would change; write nothing")
    ap.add_argument("--selftest", action="store_true",
                    help="run the matcher against synthetic fixtures and exit")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())

    targets = [p for p in CANDIDATES if p.exists()]
    if not targets:
        print("No integrated-browser main.js found — nothing to patch.")
        print("Searched:")
        for c in CANDIDATES:
            print(f"  {c}")
        sys.exit(1)

    failures = 0
    for t in targets:
        print(f"Target: {t}")
        if not args.check and not _writable(t):
            print("  ERROR: not writable — rerun under sudo")
            failures += 1
            continue
        if patch(t, args.check) == "error":
            failures += 1
    sys.exit(1 if failures else 0)


def _writable(path):
    import os
    return os.access(path, os.W_OK)


if __name__ == "__main__":
    main()
