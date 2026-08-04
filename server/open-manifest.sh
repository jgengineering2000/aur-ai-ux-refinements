#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
#
# Ensure the AUR doc server is running, then keep the Alt+D keybinding pointed at
# it. Installed into a project's aur/ by install.py; invoked by the
# "Ensure doc server" VSCode task, which Alt+D runs BEFORE opening the browser.
#
# Why the two-phase design: a keybinding that calls `simpleBrowser.show` directly
# races the folderOpen auto-start task — press Alt+D before the server has bound
# (or after it died) and Simple Browser shows connection-refused. Running this
# health-check first guarantees something is listening before the browser
# connects.
#
#   1. Resolve the port (aur/server.port, else $CLAUDE_DOC_PORT, else 7432).
#   2. If nothing answers on that port, start server.py detached and wait for it.
#   3. Sync the Alt+D keybinding's URL to the resolved port.
#
# Step 3 exists because server.py scans upward when its preferred port is busy,
# so a port baked into keybindings.json at install time can go stale and never
# self-correct. Because VSCode binds a keybinding's args at key-press time, a
# port CHANGE takes effect one Alt+D later (this run rewrites the file; the next
# press opens the new port) — in steady state, where the port is stable, the URL
# is always correct.
#
# Idempotent: server.py refuses to double-bind, and step 3 is a no-op when the
# URL already matches. Safe to run on every Alt+D.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT_FILE="$HERE/server.port"
SERVER="$HERE/server.py"
PORT="$(cat "$PORT_FILE" 2>/dev/null || echo "${CLAUDE_DOC_PORT:-7432}")"

is_up() {
  python3 - "$1" <<'PY'
import socket, sys
try:
    with socket.create_connection(("localhost", int(sys.argv[1])), timeout=0.5):
        sys.exit(0)
except OSError:
    sys.exit(1)
PY
}

if ! is_up "$PORT"; then
  # Start detached so it survives this task's shell exiting.
  setsid nohup python3 "$SERVER" >/dev/null 2>&1 &
  # Wait up to ~5s for it to bind. server.py writes the chosen port to
  # server.port, so re-read it in case it scanned past a busy preferred port.
  for _ in $(seq 1 50); do
    sleep 0.1
    PORT="$(cat "$PORT_FILE" 2>/dev/null || echo "$PORT")"
    if is_up "$PORT"; then break; fi
  done
fi

# Step 3 — sync the Alt+D keybinding URL to the resolved port. Surgical: only the
# alt+d entry's Simple Browser URL is rewritten (both the current runCommands
# form and the legacy direct form, so pre-existing installs also stop drifting);
# every other keybinding is preserved, and a file that cannot be parsed as plain
# JSON is left untouched rather than risk corrupting it.
python3 - "$PORT" <<'PY' || true
import json, os, sys

port = sys.argv[1]
url = f"http://localhost:{port}/manifest.html"

# Same locations install.py writes to; update whichever IDE(s) are present.
paths = [
    os.path.expanduser("~/.config/Antigravity/User/keybindings.json"),
    os.path.expanduser("~/.config/Code/User/keybindings.json"),
]

def retarget(entry):
    """Point an alt+d entry at `url`. Returns True if it changed anything."""
    if entry.get("key") != "alt+d":
        return False
    cmd = entry.get("command")
    # Current form: runCommands chaining the ensure-task then simpleBrowser.show.
    if cmd == "runCommands":
        changed = False
        for sub in entry.get("args", {}).get("commands", []):
            if isinstance(sub, dict) and sub.get("command") == "simpleBrowser.show":
                if sub.get("args") != url:
                    sub["args"] = url
                    changed = True
        return changed
    # Legacy form: a bare simpleBrowser.show with the URL as args.
    if cmd == "simpleBrowser.show" and entry.get("args") != url:
        entry["args"] = url
        return True
    return False

for path in paths:
    if not os.path.exists(path):
        continue
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        continue  # unparseable (comments / hand-edited) — never clobber it
    if not isinstance(data, list):
        continue
    # List, not a generator: any() short-circuits, and a file can carry more
    # than one alt+d entry (e.g. a half-migrated install) — retarget them all.
    results = [retarget(e) for e in data if isinstance(e, dict)]
    if not any(results):
        continue
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
PY
