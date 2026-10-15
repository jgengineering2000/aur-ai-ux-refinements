#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
rustfmt-on-edit -- PostToolUse hook (Edit|Write|MultiEdit): format a `.rs` file the
moment an agent writes it.

Why: on 2026-10-14 a worker edited rust/src/proc.rs, ran a 13-minute test build, then
rustfmt changed the file and the 18-minute rebuild of all 162 units was thrown away.
Formatting at edit time means unformatted code never reaches a build or the commit
gate's `cargo fmt --check`.

Reads the hook JSON on stdin (`tool_input.file_path`). Only existing `*.rs` files are
touched; an already-formatted file is never rewritten (output compared with the original bytes). Always exits 0 -- it never blocks an edit; a reformat or a parse failure is
reported to the agent as one `additionalContext` line.

Knobs: none. The edition is inferred from the nearest Cargo.toml (following
`edition.workspace = true` to `[workspace.package]`), and rustfmt finds rustfmt.toml
itself, so nothing is left for a setting to say (AUR P16). Unknown CLI arguments are
refused (exit 64).
"""

import json
import os
import tomllib
import subprocess
import sys
import time
from pathlib import Path

USAGE = ("usage: rustfmt-on-edit.py   (hook JSON on stdin; takes no arguments)\n")
TIMEOUT_S = 20


def log(msg):
    sys.stderr.write(f"rustfmt-on-edit: {msg}\n")


def emit(text):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                             "additionalContext": f"rustfmt-on-edit: {text}"}}))


def load_manifest(path):
    """Parsed Cargo.toml, or None when unparsable (logged)."""
    try:
        return tomllib.loads(path.read_text(errors="replace"))
    except (tomllib.TOMLDecodeError, OSError) as e:
        log(f"{path}: unparsable manifest ({e}); route: skip")
        return None


class BadManifest(Exception):
    pass


def find_edition(rs):
    """Edition for rs: nearest [package] edition; {workspace=true} resolves at the first
    ancestor manifest with workspace.package.edition; [package] with none is 2015."""
    inherit = False
    for d in rs.parents:
        m = d / "Cargo.toml"
        if not m.is_file():
            continue
        doc = load_manifest(m)
        if doc is None:
            raise BadManifest(str(m))
        if inherit:
            ed = doc.get("workspace", {}).get("package", {}).get("edition")
            if isinstance(ed, str):
                return ed
            continue
        pkg = doc.get("package")
        if pkg is None:
            continue
        ed = pkg.get("edition")
        if isinstance(ed, str):
            return ed
        if isinstance(ed, dict) and ed.get("workspace") is True:
            inherit = True
            continue
        return "2015"
    return None


def first_error(text):
    for line in text.splitlines():
        if line.startswith("error"):
            return line.strip()
    return (text.strip().splitlines() or ["unknown rustfmt failure"])[0]


def main():
    if len(sys.argv) > 1:
        sys.stderr.write(f"unknown argument `{sys.argv[1]}`\n{USAGE}")
        return 64
    try:
        fp = json.loads(sys.stdin.read() or "{}").get("tool_input", {}).get("file_path")
    except (ValueError, AttributeError) as e:
        log(f"unreadable hook input ({e}); route: ignore")
        return 0
    if not fp or not str(fp).endswith(".rs") or not Path(fp).is_file():
        return 0
    rs = Path(fp).resolve()
    try:
        try:
            ed = find_edition(rs)
        except BadManifest as e:
            emit(f"skipped {fp} (unparsable manifest {e})")
            return 0
        if ed is None:
            emit(f"skipped {fp} (no Cargo.toml with an edition above it; not a cargo file)")
            return 0
        t0 = time.perf_counter()
        st = rs.stat()
        orig = rs.read_bytes()
        # stdin input never follows `mod x;`, so only this file can change; cwd = its
        # directory keeps rustfmt.toml discovery working.
        p = subprocess.run(["rustfmt", "--edition", ed, "--emit", "stdout"], input=orig,
                           capture_output=True, cwd=rs.parent, timeout=TIMEOUT_S)
        if p.returncode != 0:
            emit(f"did not parse: {first_error(p.stderr.decode(errors='replace') or p.stdout.decode(errors='replace'))}")
            return 0
        if p.stdout == orig:
            return 0
        now = rs.stat()
        if (now.st_mtime_ns, now.st_size) != (st.st_mtime_ns, st.st_size):
            emit(f"{fp} changed underneath, skipped")
            return 0
        with open(rs, "r+b") as fh:  # in place: keeps mode, owner and inode
            fh.write(p.stdout)
            fh.truncate()
        ms = round((time.perf_counter() - t0) * 1000)
        emit(f"reformatted {fp} (edition {ed}, {ms} ms) \u2014 re-read before the next edit")
    except (OSError, subprocess.SubprocessError) as e:
        log(f"{fp}: {type(e).__name__}: {e}; phase: format; route: file left as written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
