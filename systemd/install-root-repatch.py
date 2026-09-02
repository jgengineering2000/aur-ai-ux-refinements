#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Install the A4 root auto-repair (system units + root-owned copies).
===================================================================
/usr/share/code is root-owned, so re-applying the A4 spellcheck patch after a
VS Code app update must run as root — the user-side aur-reapply watcher can
neither see the app dir usefully nor elevate unattended. This installer wires
the root half and its user-session presentation:

Root half (one askpass prompt, via self-elevation):
  1. Copies the patcher, docinfra.py and repatch-and-report.sh to
     /usr/local/lib/aur/ ROOT-OWNED — the unattended service runs only these;
     automating elevation over the user-writable checkout would hand root to
     anything that can edit it.
  2. Renders aur-vscode-repatch.service (__HOME__ → the installing user's
     home, for the opt-out flag Condition) and installs both system units to
     /etc/systemd/system/, then daemon-reload + enable --now.

User half (no elevation):
  3. Renders aur-repatch-notify.service (__AUR_DIR__ → this checkout) and
     installs both user units, daemon-reload + enable --now — the dialog that
     explains each repair and offers "Disable auto-repair".

The opt-out contract: the dialog's button creates
~/.config/aur/vscode-repatch.optout, which the root service refuses to run
past (ConditionPathExists=!). Delete the flag to re-enable. Preemptive opt-out
is the same file, created by hand at any time.

Idempotent: safe to re-run (it is also how the root copies are refreshed after
the patch script changes in the checkout).

Usage:
    python3 install-root-repatch.py              # install + enable both halves
    python3 install-root-repatch.py --uninstall  # remove both halves
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent           # <aur>/systemd
AUR_DIR = HERE.parent                            # <aur>
sys.path.insert(0, str(AUR_DIR))
from docinfra import sudo_argv                   # noqa: E402

ROOT_LIB = Path("/usr/local/lib/aur")
SYS_UNIT_DIR = Path("/etc/systemd/system")
USER_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"

SYS_PATH_UNIT = "aur-vscode-repatch.path"
SYS_SERVICE = "aur-vscode-repatch.service"
USER_PATH_UNIT = "aur-repatch-notify.path"
USER_SERVICE = "aur-repatch-notify.service"


def run(cmd, check=True, **kw):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    return subprocess.run([str(c) for c in cmd], check=check, **kw)


# ── Root half (runs re-executed under sudo) ──────────────────────────────────

def root_install(home):
    (ROOT_LIB / "patches").mkdir(parents=True, exist_ok=True)
    for src, dst in [
        (AUR_DIR / "patches" / "vscode-spellcheck-menu.py",
         ROOT_LIB / "patches" / "vscode-spellcheck-menu.py"),
        (AUR_DIR / "docinfra.py", ROOT_LIB / "docinfra.py"),
        (HERE / "repatch-and-report.sh", ROOT_LIB / "repatch-and-report.sh"),
    ]:
        shutil.copy2(src, dst)
        os.chown(dst, 0, 0)
        os.chmod(dst, 0o755 if dst.suffix == ".sh" else 0o644)
        print(f"  installed {dst} (root-owned)")
    svc = (HERE / SYS_SERVICE).read_text().replace("__HOME__", home)
    (SYS_UNIT_DIR / SYS_SERVICE).write_text(svc)
    print(f"  wrote {SYS_UNIT_DIR / SYS_SERVICE} (HOME={home})")
    shutil.copy(HERE / SYS_PATH_UNIT, SYS_UNIT_DIR / SYS_PATH_UNIT)
    print(f"  wrote {SYS_UNIT_DIR / SYS_PATH_UNIT}")
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "reset-failed", SYS_SERVICE], check=False)
    run(["systemctl", "enable", "--now", SYS_PATH_UNIT])
    run(["systemctl", "enable", "--now", SYS_SERVICE])


def root_uninstall():
    run(["systemctl", "disable", "--now", SYS_PATH_UNIT], check=False)
    run(["systemctl", "disable", "--now", SYS_SERVICE], check=False)
    for name in (SYS_PATH_UNIT, SYS_SERVICE):
        p = SYS_UNIT_DIR / name
        if p.exists():
            p.unlink()
            print(f"  removed {p}")
    if ROOT_LIB.exists():
        shutil.rmtree(ROOT_LIB)
        print(f"  removed {ROOT_LIB}")
    run(["systemctl", "daemon-reload"], check=False)


# ── User half ────────────────────────────────────────────────────────────────

def user_systemctl(*args, check=True):
    return run(["systemctl", "--user", *args], check=check)


def user_install():
    USER_UNIT_DIR.mkdir(parents=True, exist_ok=True)
    svc = (HERE / USER_SERVICE).read_text().replace("__AUR_DIR__", str(AUR_DIR))
    (USER_UNIT_DIR / USER_SERVICE).write_text(svc)
    print(f"  wrote {USER_UNIT_DIR / USER_SERVICE} (AUR_DIR={AUR_DIR})")
    shutil.copy(HERE / USER_PATH_UNIT, USER_UNIT_DIR / USER_PATH_UNIT)
    print(f"  wrote {USER_UNIT_DIR / USER_PATH_UNIT}")
    user_systemctl("daemon-reload")
    user_systemctl("reset-failed", USER_SERVICE, check=False)
    user_systemctl("enable", "--now", USER_PATH_UNIT)
    user_systemctl("enable", "--now", USER_SERVICE)


def user_uninstall():
    user_systemctl("disable", "--now", USER_PATH_UNIT, check=False)
    user_systemctl("disable", "--now", USER_SERVICE, check=False)
    for name in (USER_PATH_UNIT, USER_SERVICE):
        p = USER_UNIT_DIR / name
        if p.exists():
            p.unlink()
            print(f"  removed {p}")
    user_systemctl("daemon-reload", check=False)


# ── Entry ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--uninstall", action="store_true",
                    help="disable and remove both halves instead of installing")
    ap.add_argument("--root-half", metavar="ACTION", choices=["install", "uninstall"],
                    help="internal: the elevated re-invocation (install|uninstall); "
                         "takes --home for the opt-out flag path")
    ap.add_argument("--home", help="internal: installing user's home, for --root-half")
    args = ap.parse_args()

    if args.root_half:
        if os.geteuid() != 0:
            print("ERROR: --root-half must run as root", file=sys.stderr)
            return 1
        if args.root_half == "install":
            if not args.home:
                print("ERROR: --root-half install requires --home", file=sys.stderr)
                return 1
            root_install(args.home)
        else:
            root_uninstall()
        return 0

    for req in (AUR_DIR / "patches" / "vscode-spellcheck-menu.py",
                HERE / "repatch-and-report.sh"):
        if not req.exists():
            print(f"ERROR: {req} not found — run from the AUR checkout.")
            return 1

    prefix, env = sudo_argv()
    action = "uninstall" if args.uninstall else "install"
    print(f"Root half ({action}) — sudo will prompt once:")
    rc = run(prefix + [sys.executable, str(Path(__file__).resolve()),
                       "--root-half", action, "--home", str(Path.home())],
             check=False, env=env).returncode
    if rc:
        print(f"ERROR: root half exited {rc}; user half not touched.")
        return rc

    print(f"User half ({action}):")
    if args.uninstall:
        user_uninstall()
        print("Uninstalled.")
    else:
        user_install()
        print("\nInstalled. A VS Code app update now re-applies A4 as root and")
        print("explains itself in a session dialog offering 'Disable auto-repair'")
        print(f"(the flag it writes: ~/.config/aur/vscode-repatch.optout).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
