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
#   2. If THIS PROJECT's server is not answering there, start server.py detached
#      and wait for it.
#   3. Sync the Alt+D keybinding's URL to the resolved port.
#
# Step 2 asks "is my server up", not "is something listening". The distinction
# is the whole point: a project installed second has no server.port yet, so it
# falls back to the default 7432 — which the FIRST project's server is already
# holding. A liveness check answers "yes, something is there", so the new
# project starts nothing, and step 3 then points Alt+D at the other project's
# documents. The failure is silent and reads as "the doc server opened the wrong
# manifest", which is a long way from "the health check asked the wrong
# question".
#
# Identity comes from the server's existing /meta endpoint, whose `subtitle` is
# "<project-dir>/claude-docs". Reusing it rather than adding an endpoint means
# already-running servers answer correctly too, so there is no flag day. The
# residual limitation is that identity is the directory NAME: two checkouts of
# the same project under different paths would look alike. That is not the
# failure this fixes, and tightening it to an absolute path would require a
# server change and a restart of every running instance to take effect.
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

# What this project's server reports as its identity: the directory holding
# aur/, which is also ROOT.parent in server.py, so the two derive the same name
# from the same layout rather than agreeing by convention.
PROJECT_ID="$(basename "$(dirname "$HERE")")/claude-docs"

is_mine() {
  # Confirm the server answering on $1 is THIS project's instance. Connecting
  # is necessary but not sufficient; /meta says whose documents it serves.
  python3 - "$1" "$PROJECT_ID" <<'PY'
import json, sys, urllib.request
port, want = sys.argv[1], sys.argv[2]
try:
    with urllib.request.urlopen(f"http://localhost:{port}/meta", timeout=1) as r:
        sys.exit(0 if json.load(r).get("subtitle") == want else 1)
except Exception:
    # No answer, not HTTP, or no /meta — in every case we cannot establish that
    # this is ours, and starting our own is the safe move: server.py scans
    # upward from a busy port, so a needless start costs a port, not a clash.
    sys.exit(1)
PY
}

if ! is_mine "$PORT"; then
  # Start detached so it survives this task's shell exiting.
  setsid nohup python3 "$SERVER" >/dev/null 2>&1 &
  # Wait up to ~5s for it to bind. server.py writes the chosen port to
  # server.port, so re-read it in case it scanned past a busy preferred port —
  # which is exactly what happens when another project holds the default.
  for _ in $(seq 1 50); do
    sleep 0.1
    PORT="$(cat "$PORT_FILE" 2>/dev/null || echo "$PORT")"
    if is_mine "$PORT"; then break; fi
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
