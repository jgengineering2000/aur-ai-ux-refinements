#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Claude Code UX Installer — GUI
Clickable installer for all Claude Code UX improvements.
"""

import argparse
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk
from pathlib import Path

import docinfra
from docinfra import (
    SIMPLEBROWSER_CANDIDATES,
    detect_ides,
    install_keybinding,
    install_review_infra,
    install_task,
)

# Payload root — _MEIPASS-aware so a PyInstaller build finds patches/ and
# systemd/ too, not just the doc-infra trees docinfra resolves itself.
HERE = docinfra.resource_root()

def run_patch(script, args, use_sudo, log):
    cmd = [sys.executable, str(HERE / "patches" / script)] + args
    env = None
    if use_sudo:
        # Shared elevation policy (docinfra): sudo -A with a GUI askpass. This
        # front end has NO tty, so a bare `sudo` could never prompt at all —
        # the askpass dialog is the only route to elevation here.
        prefix, env = docinfra.sudo_argv()
        cmd = prefix + cmd
    log(f"  Running {Path(script).name}...")
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    for line in result.stdout.strip().splitlines():
        log(f"    {line}")
    if result.returncode != 0:
        for line in result.stderr.strip().splitlines():
            log(f"    ERROR: {line}")
        log(f"    FAILED: {Path(script).name} exited {result.returncode}")
    return result.returncode

# ── GUI ───────────────────────────────────────────────────────────────────────

class InstallerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Claude Code UX Installer")
        self.resizable(False, False)
        self.ides = detect_ides()
        sb_targets = [p for p in SIMPLEBROWSER_CANDIDATES if p.exists()]
        self.sb_available = bool(sb_targets)
        self.sb_needs_sudo = bool([p for p in sb_targets if not os.access(p, os.W_OK)])
        self._build_ui()
        self._center()

    def _center(self):
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"+{(sw-w)//2}+{(sh-h)//2}")

    def _build_ui(self):
        # ── Header ────────────────────────────────────────────────────────
        hdr = ttk.Frame(self, padding=(14, 10, 14, 8))
        hdr.grid(row=0, column=0, sticky="ew")
        ttk.Label(hdr, text="Claude Code UX Installer", font=("", 13, "bold")).pack(anchor="w")
        ide_text = f"Detected IDE(s): {', '.join(self.ides)}" if self.ides else "No IDE detected"
        ttk.Label(hdr, text=ide_text, foreground="gray").pack(anchor="w")
        ttk.Separator(self).grid(row=1, column=0, sticky="ew")

        body = ttk.Frame(self, padding=(14, 10))
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)

        r = 0

        # ── Part A ────────────────────────────────────────────────────────
        ttk.Label(body, text="IDE Extension Patches", font=("", 10, "bold")).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(0, 6))
        r += 1

        self.do_a1 = tk.BooleanVar(value=True)
        ttk.Checkbutton(body, text="A1  Chat panel layout  (margins, overlay scrollbar)",
                        variable=self.do_a1).grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1

        self.do_a2 = tk.BooleanVar(value=True)
        ttk.Checkbutton(body, text="A2  Processing vocabulary",
                        variable=self.do_a2,
                        command=self._toggle_vocab).grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1

        vf = ttk.Frame(body)
        vf.grid(row=r, column=0, columnspan=3, sticky="w", padx=(22, 0), pady=(2, 4))
        self.vocab_choice = tk.StringVar(value="Working")
        for label, val in [("Working", "Working"), ("...", "..."), ("(hidden)", ""), ("Custom:", "__custom__")]:
            ttk.Radiobutton(vf, text=label, variable=self.vocab_choice,
                            value=val, command=self._toggle_custom).pack(side="left", padx=(0, 8))
        self.custom_entry = ttk.Entry(vf, width=14)
        self.custom_entry.pack(side="left")
        self.custom_entry.config(state="disabled")
        self.vocab_frame = vf
        r += 1

        self.do_a3 = tk.BooleanVar(value=self.sb_available)
        sudo_note = "  (requires sudo)" if self.sb_needs_sudo else ""
        not_found = "  (not found — skipped)" if not self.sb_available else ""
        ttk.Checkbutton(body,
                        text=f"A3  Simple Browser tab title fix{sudo_note}{not_found}",
                        variable=self.do_a3,
                        state="normal" if self.sb_available else "disabled").grid(
                            row=r, column=0, columnspan=3, sticky="w")
        r += 1

        ttk.Separator(body, orient="horizontal").grid(
            row=r, column=0, columnspan=3, sticky="ew", pady=10)
        r += 1

        # ── Part B ────────────────────────────────────────────────────────
        self.do_b = tk.BooleanVar(value=True)
        ttk.Label(body, text="Review Document Infrastructure", font=("", 10, "bold")).grid(
            row=r, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(body, text="Enable", variable=self.do_b,
                        command=self._toggle_b).grid(row=r, column=2, sticky="e")
        r += 1

        self.b_frame = ttk.Frame(body)
        self.b_frame.grid(row=r, column=0, columnspan=3, sticky="ew", padx=(22, 0), pady=(4, 0))
        self.b_frame.columnconfigure(1, weight=1)
        r += 1

        ttk.Label(self.b_frame, text="Project directory:").grid(
            row=0, column=0, sticky="w", pady=2)
        self.proj_var = tk.StringVar(value=str(Path.cwd()))
        ttk.Entry(self.b_frame, textvariable=self.proj_var, width=38).grid(
            row=0, column=1, sticky="ew", padx=(6, 4))
        ttk.Button(self.b_frame, text="Browse…", command=self._browse).grid(row=0, column=2)

        ttk.Label(self.b_frame, text="Port:").grid(row=1, column=0, sticky="w", pady=2)
        self.port_var = tk.StringVar(value="7432")
        ttk.Entry(self.b_frame, textvariable=self.port_var, width=8).grid(
            row=1, column=1, sticky="w", padx=(6, 0))

        self.do_task = tk.BooleanVar(value=True)
        self.do_kb = tk.BooleanVar(value=bool(self.ides))
        self.do_claude_md = tk.BooleanVar(value=True)

        ttk.Checkbutton(self.b_frame, text="Add auto-start VS Code task",
                        variable=self.do_task).grid(
                            row=2, column=0, columnspan=3, sticky="w")
        kb_label = (f"Add Alt+D keybinding  ({', '.join(self.ides)})"
                    if self.ides else "Alt+D keybinding  (no IDE detected)")
        ttk.Checkbutton(self.b_frame, text=kb_label, variable=self.do_kb,
                        state="normal" if self.ides else "disabled").grid(
                            row=3, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(self.b_frame, text="Copy CLAUDE.md template to project",
                        variable=self.do_claude_md).grid(
                            row=4, column=0, columnspan=3, sticky="w")

        ttk.Separator(body, orient="horizontal").grid(
            row=r, column=0, columnspan=3, sticky="ew", pady=10)
        r += 1

        self.install_btn = ttk.Button(body, text="Install", command=self._run, width=18)
        self.install_btn.grid(row=r, column=0, columnspan=3)
        r += 1

        self.output = scrolledtext.ScrolledText(
            body, width=66, height=14, state="disabled",
            font=("Courier", 10), background="#1e1e2e", foreground="#cdd6f4",
            insertbackground="#cdd6f4")
        self.output.grid(row=r, column=0, columnspan=3, pady=(10, 0), sticky="ew")

    # ── Toggle helpers ────────────────────────────────────────────────────────

    def _toggle_vocab(self):
        state = "normal" if self.do_a2.get() else "disabled"
        for w in self.vocab_frame.winfo_children():
            try:
                w.config(state=state)
            except tk.TclError:
                pass
        if not self.do_a2.get():
            self.custom_entry.config(state="disabled")

    def _toggle_custom(self):
        if self.vocab_choice.get() == "__custom__":
            self.custom_entry.config(state="normal")
            self.custom_entry.focus()
        else:
            self.custom_entry.config(state="disabled")

    def _toggle_b(self):
        state = "normal" if self.do_b.get() else "disabled"
        for w in self.b_frame.winfo_children():
            try:
                w.config(state=state)
            except tk.TclError:
                pass

    def _browse(self):
        path = filedialog.askdirectory(initialdir=self.proj_var.get(), title="Select project directory")
        if path:
            self.proj_var.set(path)

    # ── Output ────────────────────────────────────────────────────────────────

    def _log(self, msg):
        self.output.config(state="normal")
        self.output.insert("end", msg + "\n")
        self.output.see("end")
        self.output.config(state="disabled")
        self.update_idletasks()

    # ── Installation ──────────────────────────────────────────────────────────

    def _run(self):
        self.install_btn.config(state="disabled", text="Installing…")
        threading.Thread(target=self._do_install, daemon=True).start()

    def _do_install(self):
        log = self._log
        need_restart = False
        failures = []

        if self.do_a1.get() or self.do_a2.get() or self.do_a3.get():
            log("── IDE Patches ───────────────────────────────────────")
            if self.do_a1.get():
                if run_patch("claude-ui-layout.py", [], False, log):
                    failures.append("A1 chat panel layout")
                need_restart = True
            if self.do_a2.get():
                word = self.vocab_choice.get()
                if word == "__custom__":
                    word = self.custom_entry.get().strip() or "Working"
                if run_patch("claude-ui-vocabulary.py", [word], False, log):
                    failures.append("A2 processing vocabulary")
                need_restart = True
            if self.do_a3.get():
                if run_patch("simplebrowser-title.py", [], self.sb_needs_sudo, log):
                    failures.append("A3 Simple Browser title")
                need_restart = True

        if self.do_b.get():
            log("\n── Review Infrastructure ─────────────────────────────")
            project_dir = Path(self.proj_var.get()).expanduser().resolve()
            if not project_dir.exists():
                log(f"  ERROR: directory not found: {project_dir}")
                self.after(0, lambda: self.install_btn.config(state="normal", text="Install"))
                return
            port_str = self.port_var.get().strip()
            port = int(port_str) if port_str.isdigit() else 7432
            install_review_infra(project_dir, self.do_claude_md.get(), log)
            if self.do_task.get():
                install_task(project_dir, port, log)
            if self.do_kb.get():
                for ide in self.ides:
                    install_keybinding(ide, port, log)

        if failures:
            log("\n── FAILED ────────────────────────────────────────────")
            for f in failures:
                log(f"  ✗ {f}")
            log("See the output above for the reason. Fix, then run again —")
            log("every step is idempotent, so what already succeeded is skipped.")
            self.after(0, lambda: self.install_btn.config(text="Failed — run again", state="normal"))
            return

        log("\n── Done ──────────────────────────────────────────────")
        if need_restart:
            log("Restart the extension host to activate IDE patches:")
            log("  Ctrl+Shift+P → Developer: Restart Extension Host")
        if self.do_b.get():
            log("")
            log("Start the doc server:")
            log("  python3 <project>/aur/server.py &")
            log("Or open the project — the VS Code task starts it automatically.")
        self.after(0, lambda: self.install_btn.config(text="Done", state="disabled"))


if __name__ == "__main__":
    # The accepted argument set, in one place: nothing but --help. A GUI
    # launched with a stray token refuses before opening a window.
    argparse.ArgumentParser(description=__doc__.strip().splitlines()[0]).parse_args()
    app = InstallerApp()
    app.mainloop()
