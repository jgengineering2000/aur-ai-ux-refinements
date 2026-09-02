#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Session dialog for the A4 auto-repair.
======================================
The root half (repatch-and-report.sh) publishes an event file when it
re-applied — or failed to re-apply — the VS Code spellcheck patch after an
app update. This user-session script explains that in a dialog and offers the
standing opt-out the repair contract promises: "Disable auto-repair" writes
the flag file the root service's Condition checks, so no further repairs run
until the flag is deleted.

Runs from aur-repatch-notify.{path,service}: on every event edge, plus once
per login as a backstop (an event published while logged out has no edge to
see at login). A seen-stamp keeps the backstop from re-announcing old news.

Fallback ladder (never ends in "give up"): zenity dialog → notify-send
notification (no opt-out button; the message names the flag path) → plain
stdout into the journal.

Usage:
    python3 repatch-notify.py            # announce an unseen event, if any
    python3 repatch-notify.py --test     # show the dialog with sample text
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

EVENT = Path("/var/lib/aur/vscode-repatch-event")
SEEN = Path.home() / ".local" / "state" / "aur" / "vscode-repatch-seen"
OPTOUT = Path.home() / ".config" / "aur" / "vscode-repatch.optout"

OK_TEXT = (
    "VS Code was updated, which reverted the AUR spellcheck-suggestions "
    "patch (A4) — the right-click corrections in the integrated browser.\n\n"
    "It has been re-applied automatically. Restart VS Code to activate it.\n\n"
    "Repaired at: {when}\n\n"
    "To stop these automatic repairs permanently, choose “Disable "
    "auto-repair”. (Re-enable later by deleting\n{optout})"
)
FAIL_TEXT = (
    "VS Code was updated, and the AUR spellcheck-suggestions patch (A4) "
    "could NOT be re-applied — the code it anchors on has changed.\n\n"
    "Right-click corrections in the integrated browser are gone until the "
    "patch is updated: see PATCHES.md §A4 in the AUR checkout, and "
    "`journalctl -u aur-vscode-repatch.service` for the exact miss.\n\n"
    "Attempted at: {when}\n\n"
    "“Disable auto-repair” stops further attempts. (Re-enable by "
    "deleting\n{optout})"
)


def read_event():
    """(when, ok) from the event file — first line is the timestamp, second rc."""
    lines = EVENT.read_text().splitlines()
    when = lines[0] if lines else "unknown"
    ok = len(lines) > 1 and lines[1] == "rc=0"
    return when, ok


def show(text, title):
    """The ladder: dialog with opt-out → notification → journal line."""
    if shutil.which("zenity"):
        # --extra-button, NOT --cancel-label: a --question's cancel exit code
        # is shared by Esc and the window-close button, so "make this dialog
        # go away" would silently become a standing opt-out. The extra button
        # emits its label on stdout, and only that explicit press opts out.
        proc = subprocess.run(
            ["zenity", "--info", "--title", title, "--icon=dialog-information",
             "--width", "420", "--text", text,
             "--ok-label", "OK", "--extra-button", "Disable auto-repair"],
            capture_output=True, text=True)
        if proc.stdout.strip() == "Disable auto-repair":
            OPTOUT.parent.mkdir(parents=True, exist_ok=True)
            OPTOUT.write_text("created by repatch-notify.py 'Disable auto-repair'\n")
            subprocess.run(
                ["zenity", "--info", "--title", title, "--width", "360",
                 "--text", f"Auto-repair disabled. Re-enable by deleting:\n{OPTOUT}"],
                check=False)
        return
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", title,
                        text + f"\n\n(Opt out: create {OPTOUT})"], check=False)
        return
    print(f"{title}: {text}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--test", action="store_true",
                    help="show the success dialog with sample text; touch nothing")
    args = ap.parse_args()

    title = "AUR — VS Code auto-repair"
    if args.test:
        show(OK_TEXT.format(when="(test run)", optout=OPTOUT), title)
        return 0

    if not EVENT.exists():
        return 0
    if SEEN.exists() and SEEN.stat().st_mtime >= EVENT.stat().st_mtime:
        return 0

    when, ok = read_event()
    show((OK_TEXT if ok else FAIL_TEXT).format(when=when, optout=OPTOUT), title)

    # Stamp AFTER showing, mirroring the event's own mtime, so a crash before
    # this line re-announces rather than silently swallowing the news.
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.touch()
    os.utime(SEEN, (EVENT.stat().st_mtime, EVENT.stat().st_mtime))
    return 0


if __name__ == "__main__":
    sys.exit(main())
