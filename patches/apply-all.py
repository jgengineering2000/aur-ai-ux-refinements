#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Apply all Claude Code UX patches.

Usage:
    python3 apply-all.py              # all three patches (sudo prompted for A3 if needed)
    python3 apply-all.py --no-browser # skip A3 (Simple Browser title)

Exit status: 0 only when every patch script exited 0. The systemd oneshot
runs this after an extension update, so its recorded result must mean what
it says — a patch that reported NOT FOUND is a failed run, not a finished one.
"""

import argparse
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
    """Run one patch script; return its exit status (0 = success)."""
    cmd = [sys.executable, str(HERE / script)] + list(args)
    if sudo:
        cmd = ["sudo", "-A", "--"] + cmd
    print(f"\n{'='*50}")
    print(f"Running: {'sudo -A ' if sudo else ''}{script}")
    print('='*50)
    rc = subprocess.run(cmd, env=env).returncode
    if rc:
        print(f"FAILED: {script} exited {rc}")
    return rc


def browser_needs_sudo():
    """True iff any existing A3 target is not writable by the current user."""
    targets = [p for p in BROWSER_CANDIDATES if p.exists()]
    if not targets:
        return False  # nothing to patch; let the script report cleanly
    return any(not os.access(p, os.W_OK) for p in targets)


def main():
    # The accepted argument set, in one place. An unrecognised token — a typo
    # of --no-browser included — prints usage and exits 2 before any patch runs,
    # rather than silently running the sudo-gated A3 it was meant to skip.
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--no-browser", action="store_true",
                    help="skip A3 (Simple Browser tab title), the only patch that may need sudo")
    skip_browser = ap.parse_args().no_browser

    failures = []
    if run("claude-ui-layout.py"):
        failures.append("A1 claude-ui-layout.py")
    if run("claude-ui-vocabulary.py"):
        failures.append("A2 claude-ui-vocabulary.py")

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
            rc = run("simplebrowser-title.py", sudo=True, env=env)
        else:
            rc = run("simplebrowser-title.py")
        if rc:
            failures.append("A3 simplebrowser-title.py")

    print("\n" + "="*50)
    if failures:
        print(f"FAILED: {', '.join(failures)} — see output above.")
        sys.exit(1)
    print("Done. Restart the extension host:")
    print("  Ctrl+Shift+P → Developer: Restart Extension Host")


if __name__ == "__main__":
    main()
