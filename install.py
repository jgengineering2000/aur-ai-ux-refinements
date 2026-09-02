#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Claude Code UX Installer
========================
Interactive installer for all Claude Code UX improvements.

Usage:
    python3 install.py

Exit status: 0 when everything selected was applied; 1 when any patch script
failed (its output names the reason) — never a "Done" banner over a failure.
"""

import os
import subprocess
import sys
from pathlib import Path

import docinfra
from docinfra import (
    SIMPLEBROWSER_CANDIDATES,
    VSCODE_MAINJS_CANDIDATES,
    detect_ides,
    install_keybinding,
    install_review_infra,
    install_task,
)

# Payload root — _MEIPASS-aware so a PyInstaller build finds patches/ and
# systemd/ too, not just the doc-infra trees docinfra resolves itself.
HERE = docinfra.resource_root()

# ── Terminal helpers ──────────────────────────────────────────────────────────

def hr(char="─", width=58):
    print(char * width)

def section(title):
    print()
    hr()
    print(f"  {title}")
    hr()

def ask(prompt, default=None):
    hint = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"  {prompt}{hint}: ").strip()
        if raw:
            return raw
        if default is not None:
            return default

def ask_yn(prompt, default="y"):
    hint = "[Y/n]" if default == "y" else "[y/N]"
    while True:
        raw = input(f"  {prompt} {hint}: ").strip().lower()
        if not raw:
            return default == "y"
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  Please enter y or n.")

def ask_choice(prompt, choices, default=None):
    labels = "/".join(c.upper() if c == default else c for c in choices)
    while True:
        raw = input(f"  {prompt} [{labels}]: ").strip().lower()
        if not raw and default:
            return default
        if raw in choices:
            return raw
        print(f"  Enter one of: {', '.join(choices)}")

# ── Patch runner ──────────────────────────────────────────────────────────────

def run_patch(name, *args, use_sudo=False):
    """Run one patch via this installer's worker mode; return its exit status."""
    cmd = docinfra.patch_argv(name, *args)
    env = None
    if use_sudo:
        # Shared elevation policy (docinfra): sudo -A with a GUI askpass when
        # one exists, so the same call works with or without a tty.
        prefix, env = docinfra.sudo_argv()
        cmd = prefix + cmd
    print(f"\n  Running: {name} {' '.join(args)}")
    rc = subprocess.run(cmd, env=env).returncode
    if rc:
        print(f"  FAILED: {name} exited {rc}")
    return rc

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Accepted argument set (shared with the GUI front end, see docinfra):
    # --help, or the --run-patch worker mode; a stray token is refused.
    _, args = docinfra.installer_argparser(__doc__)
    if args.run_patch:
        sys.exit(docinfra.run_patch_worker(args.run_patch, args.patch_args))

    print()
    print("  Claude Code UX Installer")
    print("  " + "=" * 24)

    ides = detect_ides()
    if ides:
        print(f"\n  Detected IDE(s): {', '.join(ides)}")
    else:
        print("\n  No VS Code / Antigravity extension directory detected.")
        print("  IDE patches (A1–A3) will still be attempted if selected.")

    # ── Top-level feature selection ───────────────────────────────────────────
    section("Feature Selection")
    print()
    print("  [A]  IDE Extension Patches   — chat panel, vocabulary, tab titles")
    print("  [B]  Review Document Infra   — HTML docs, server, Alt+D keybinding")
    print("  [AB] Both")
    print()
    sel = ask_choice("Install which features?", ["a", "b", "ab"], default="ab")
    do_a = sel in ("a", "ab")
    do_b = sel in ("b", "ab")

    # ── Part A: configure ─────────────────────────────────────────────────────
    do_a1 = do_a2 = do_a3 = do_a4 = do_a5 = False
    vocab_word = "Working"
    sb_needs_sudo = sm_needs_sudo = False

    if do_a:
        section("Part A — IDE Extension Patches")
        print()

        do_a1 = ask_yn("A1  Chat panel layout  (reduced margins, overlay scrollbar)", default="y")
        do_a2 = ask_yn("A2  Processing vocabulary  (replace cycling word list)", default="y")
        do_a3 = ask_yn("A3  Simple Browser tab title fix", default="y")
        do_a4 = ask_yn("A4  Integrated browser spellcheck suggestions  (right-click corrections)", default="y")
        do_a5 = ask_yn("A5  Auto-reapply patches after extension updates  (systemd watcher)", default="y")

        if do_a2:
            print()
            print("  Replacement word options:")
            print("    1  Working   (default)")
            print("    2  ...       (ellipsis)")
            print("    3  (empty)   (hides the label entirely)")
            print("    4  Custom")
            c = ask_choice("  Choice", ["1", "2", "3", "4"], default="1")
            vocab_word = {"1": "Working", "2": "...", "3": ""}.get(c)
            if vocab_word is None:
                vocab_word = ask("    Enter word", default="Working")

        if do_a3:
            sb_targets = [p for p in SIMPLEBROWSER_CANDIDATES if p.exists()]
            if not sb_targets:
                print("\n  A3: no Simple Browser extension found at known paths — will skip.")
                do_a3 = False
            else:
                unwritable = [p for p in sb_targets if not os.access(p, os.W_OK)]
                if unwritable:
                    print("\n  A3 targets require elevated access:")
                    for p in unwritable:
                        print(f"    {p}")
                    sb_needs_sudo = ask_yn("  Apply A3 with sudo?", default="y")
                    if not sb_needs_sudo:
                        do_a3 = False

        if do_a4:
            sm_targets = [p for p in VSCODE_MAINJS_CANDIDATES if p.exists()]
            if not sm_targets:
                print("\n  A4: no integrated-browser main.js found at known paths — will skip.")
                do_a4 = False
            else:
                unwritable = [p for p in sm_targets if not os.access(p, os.W_OK)]
                if unwritable:
                    print("\n  A4 targets require elevated access:")
                    for p in unwritable:
                        print(f"    {p}")
                    sm_needs_sudo = ask_yn("  Apply A4 with sudo?", default="y")
                    if not sm_needs_sudo:
                        do_a4 = False

    # ── Part B: configure ─────────────────────────────────────────────────────
    project_dir = None
    port = 7432
    do_task = do_keybinding = do_claude_md = False

    if do_b:
        section("Part B — Review Document Infrastructure")
        print()

        proj_input = ask("Target project directory", default=str(Path.cwd()))
        project_dir = Path(proj_input).expanduser().resolve()
        if not project_dir.exists():
            print(f"\n  Directory not found: {project_dir}")
            sys.exit(1)

        port = int(ask("Doc server port", default="7432"))
        print()
        do_task      = ask_yn("Add auto-start task to .vscode/tasks.json?", default="y")
        do_keybinding = ask_yn(f"Add Alt+D keybinding to IDE keybindings.json?", default="y") if ides else False
        do_claude_md = ask_yn("Copy CLAUDE.md template into project?", default="y")

        if do_claude_md and (project_dir / "CLAUDE.md").exists():
            print("    !! CLAUDE.md already exists — will skip, not overwrite.")

    # ── Confirmation summary ──────────────────────────────────────────────────
    section("Summary")
    print()
    if do_a:
        print("  Part A — IDE patches:")
        if do_a1: print("    A1  Chat panel layout")
        if do_a2: print(f"    A2  Vocabulary → \"{vocab_word}\"")
        if do_a3: print(f"    A3  Simple Browser title {'(sudo)' if sb_needs_sudo else ''}")
        if do_a4: print(f"    A4  Browser spellcheck suggestions {'(sudo)' if sm_needs_sudo else ''}")
        if do_a5: print("    A5  Auto-reapply watcher (systemd --user)")
        if not any([do_a1, do_a2, do_a3, do_a4, do_a5]):
            print("    (nothing selected)")
    if do_b:
        print("  Part B — Review infra:")
        print(f"    Project:    {project_dir}")
        print(f"    Port:       {port}")
        print(f"    tasks.json: {'yes' if do_task else 'no'}")
        if ides:
            print(f"    Keybinding: {'yes (' + ', '.join(ides) + ')' if do_keybinding else 'no'}")
        print(f"    CLAUDE.md:  {'yes' if do_claude_md else 'no'}")
    print()
    if not ask_yn("Proceed?", default="y"):
        print("\n  Cancelled.")
        sys.exit(0)

    # ── Execute ───────────────────────────────────────────────────────────────
    failures = []
    if do_a:
        section("Applying IDE Patches")
        if do_a1 and run_patch("claude-ui-layout"):
            failures.append("A1 chat panel layout")
        if do_a2 and run_patch("claude-ui-vocabulary", vocab_word):
            failures.append("A2 processing vocabulary")
        if do_a3 and run_patch("simplebrowser-title", use_sudo=sb_needs_sudo):
            failures.append("A3 Simple Browser title")
        if do_a4 and run_patch("vscode-spellcheck-menu", use_sudo=sm_needs_sudo):
            failures.append("A4 browser spellcheck suggestions")
        if do_a5:
            print("\n  A5  Installing auto-reapply watcher...")
            rc = subprocess.run([sys.executable, str(HERE / "systemd" / "install-autoreapply.py")]).returncode
            if rc:
                print(f"  FAILED: install-autoreapply.py exited {rc}")
                failures.append("A5 auto-reapply watcher")

    if do_b:
        section("Installing Review Infrastructure")
        print()
        log = lambda msg: print(f"    {msg}")
        install_review_infra(project_dir, do_claude_md, log)
        if do_task:
            install_task(project_dir, port, log)
        if do_keybinding:
            for ide in ides:
                install_keybinding(ide, port, log)

    # ── Done ──────────────────────────────────────────────────────────────────
    if failures:
        section("FAILED")
        print()
        for f in failures:
            print(f"  ✗ {f}")
        print("\n  See the output above for the reason. Fix, then re-run — every")
        print("  step is idempotent, so what already succeeded is skipped.")
        print()
        sys.exit(1)

    section("Done")
    print()
    if do_a and any([do_a1, do_a2, do_a3]):
        print("  Restart extension host to activate IDE patches:")
        print("    Ctrl+Shift+P → Developer: Restart Extension Host")
        print()
    if do_a and do_a4:
        # main.js is the MAIN process — an extension-host or window reload
        # does not re-read it; only a full application restart does.
        print("  A4 patches the main process: quit and relaunch VS Code to activate.")
        print()
    if do_b:
        server_path = project_dir / "aur" / "server.py"
        print(f"  Start the doc server:")
        print(f"    python3 {server_path} &")
        print(f"  (or open the project in VS Code — task starts it automatically)")
        if do_claude_md and not (project_dir / "CLAUDE.md").exists():
            print(f"\n  Edit {project_dir / 'CLAUDE.md'}")
            print(f"    Add your project context below the marker line at the bottom.")
        print(f"\n  Press Alt+D to open the document manifest.")
    print()


if __name__ == "__main__":
    main()
