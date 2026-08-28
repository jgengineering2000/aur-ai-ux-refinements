#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Install the auto-reapply systemd user units.
=============================================
Wires up a path watcher so the Claude Code UX patches (chat margins + static
"Working" indicator) are re-applied automatically after a Claude Code
extension update replaces the patched webview with a fresh one.

What it does:
  1. Renders aur-reapply.service with this AUR checkout's absolute path
     substituted for __AUR_DIR__ (the checkout location is not fixed).
  2. Copies both units into ~/.config/systemd/user/.
  3. `systemctl --user daemon-reload`, then enables + starts the .path unit.

The .service is a oneshot that runs `patches/apply-all.py --no-browser`; the
.path unit triggers it on any change to ~/.antigravity/extensions/ or
~/.vscode/extensions/ (both watched unconditionally — see aur-reapply.path).

Idempotent: safe to re-run. Requires a user systemd instance (standard on a
desktop login session). No sudo — everything is in the user's home.

Usage:
    python3 install-autoreapply.py            # install + enable
    python3 install-autoreapply.py --uninstall
"""

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # <aur>/systemd
AUR_DIR = HERE.parent                            # <aur>
UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
SERVICE = "aur-reapply.service"
PATH_UNIT = "aur-reapply.path"


def systemctl(*args, check=True):
    cmd = ["systemctl", "--user", *args]
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check)


def uninstall():
    systemctl("disable", "--now", PATH_UNIT, check=False)
    systemctl("disable", "--now", SERVICE, check=False)
    for name in (PATH_UNIT, SERVICE):
        target = UNIT_DIR / name
        if target.exists():
            target.unlink()
            print(f"  removed {target}")
    systemctl("daemon-reload", check=False)
    print("Uninstalled.")


def install():
    UNIT_DIR.mkdir(parents=True, exist_ok=True)

    # Render the service with the real AUR path; copy the path unit verbatim.
    svc_src = (HERE / SERVICE).read_text().replace("__AUR_DIR__", str(AUR_DIR))
    (UNIT_DIR / SERVICE).write_text(svc_src)
    print(f"  wrote {UNIT_DIR / SERVICE} (AUR_DIR={AUR_DIR})")

    (UNIT_DIR / PATH_UNIT).write_text((HERE / PATH_UNIT).read_text())
    print(f"  wrote {UNIT_DIR / PATH_UNIT}")

    systemctl("daemon-reload")
    # Clear any prior 'start-limit-hit' wedge before (re)enabling.
    systemctl("reset-failed", SERVICE, check=False)
    systemctl("enable", "--now", PATH_UNIT)
    # Enable the service too: WantedBy=default.target makes it a login-time
    # backstop for updates the edge-triggered .path missed, and --now runs it
    # immediately so the currently-installed extension is patched on install.
    systemctl("enable", "--now", SERVICE)

    print("\nInstalled. The watcher is active and will re-apply the patches")
    print("after the next Claude Code extension update, plus once at each login")
    print("as a backstop. Status:")
    systemctl("status", PATH_UNIT, "--no-pager", check=False)


def main():
    # The accepted argument set, in one place: --uninstall and --help. A typo
    # is refused with usage, never treated as "install".
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--uninstall", action="store_true",
                    help="disable and remove the units instead of installing them")
    args = ap.parse_args()

    if not AUR_DIR.joinpath("patches", "apply-all.py").exists():
        print(f"ERROR: {AUR_DIR}/patches/apply-all.py not found — run from the "
              "AUR checkout's systemd/ dir.")
        sys.exit(1)
    if args.uninstall:
        uninstall()
    else:
        install()


if __name__ == "__main__":
    main()
