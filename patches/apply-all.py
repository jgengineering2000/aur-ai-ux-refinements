#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Apply all Claude Code UX patches.

Usage:
    python3 apply-all.py              # all three patches (sudo prompted for A3 if needed)
    python3 apply-all.py --no-browser # skip A3 (Simple Browser title)
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent

# Target list and askpass ladder live in ONE place — docinfra — shared with
# simplebrowser-title.py and both installer front ends.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from docinfra import SIMPLEBROWSER_CANDIDATES as BROWSER_CANDIDATES, find_askpass


def run(script, *args, sudo=False, env=None):
    cmd = [sys.executable, str(HERE / script)] + list(args)
    if sudo:
        cmd = ["sudo", "-A", "--"] + cmd
    print(f"\n{'='*50}")
    print(f"Running: {'sudo -A ' if sudo else ''}{script}")
    print('='*50)
    subprocess.run(cmd, env=env)


def browser_needs_sudo():
    """True iff any existing A3 target is not writable by the current user."""
    targets = [p for p in BROWSER_CANDIDATES if p.exists()]
    if not targets:
        return False  # nothing to patch; let the script report cleanly
    return any(not os.access(p, os.W_OK) for p in targets)


def main():
    skip_browser = "--no-browser" in sys.argv

    run("claude-ui-layout.py")
    run("claude-ui-vocabulary.py")

    if not skip_browser:
        if browser_needs_sudo() and shutil.which("sudo"):
            askpass = find_askpass()
            if not askpass:
                print("\n[A3] Simple Browser target is root-owned and no askpass "
                      "helper found. Install one (e.g. seahorse) or run:")
                print(f"  sudo python3 {HERE/'simplebrowser-title.py'}")
                sys.exit(1)
            print(f"\n[A3] Simple Browser target is root-owned — invoking via "
                  f"sudo -A (askpass: {askpass}).")
            env = dict(os.environ)
            env["SUDO_ASKPASS"] = askpass
            run("simplebrowser-title.py", sudo=True, env=env)
        else:
            run("simplebrowser-title.py")

    print("\n" + "="*50)
    print("Done. Restart the extension host:")
    print("  Ctrl+Shift+P → Developer: Restart Extension Host")


if __name__ == "__main__":
    main()
