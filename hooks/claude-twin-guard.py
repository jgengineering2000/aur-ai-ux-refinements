#!/usr/bin/env -S python3 -S
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
claude-twin-guard -- detect one conversation live in two claude processes.

WHY. When VS Code dies its `claude` children are re-parented to systemd and keep
working with no window. Resuming the conversation then runs it in TWO processes
writing one transcript, and the copy's task notifications call the original's
running workers "stopped", inviting a duplicate relaunch.

MODES (argv[1]):
  session-start  SessionStart hook (stdin JSON has session_id). For every OTHER live
                 ~/.claude/sessions/<pid>.json with the same sessionId whose procStart
                 equals field 22 of /proc/<pid>/stat, print SessionStart
                 additionalContext naming the twin. No twin -> print nothing.
  pre-tool       PreToolUse hook. If our session's entrypoint is `claude-vscode` and our
                 claude process is now parented to systemd or pid 1, exit 2 with the
                 reason on stderr. Otherwise exit 0 silently.

Our own claude = the nearest ancestor of this process with a registry entry whose
procStart matches (subagents are in-process: same pid). procStart defeats pid reuse.
Any fault reading the registry or /proc: exit 0 and one stderr line naming what
could not be read (AUR P10) -- the guard never blocks on its own fault.
Read-only; signals nothing. Wiring is the supervisor's, not this file's.
"""
import json
import os
import sys
import time

USAGE = ("usage: claude-twin-guard.py {session-start|pre-tool|--help}\n"
         "  session-start  warn if this conversation is also live in another claude process\n"
         "  pre-tool       refuse new actions when this VS Code session's window is gone\n")


def log(msg):
    sys.stderr.write("claude-twin-guard: " + msg + "\n")


def stat_fields(root, pid):
    """(ppid, starttime) from <root>/<pid>/stat; comm may contain spaces/parens."""
    with open(f"{root}/{pid}/stat") as f:
        s = f.read()
    rest = s[s.rindex(")") + 2:].split()
    return int(rest[1]), rest[19]


def comm(root, pid):
    try:
        with open(f"{root}/{pid}/comm") as f:
            return f.read().strip()
    except OSError:
        return "?"


def load_entry(sdir, pid):
    try:
        with open(f"{sdir}/{pid}.json") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def live(root, e):
    try:
        return stat_fields(root, e["pid"])[1] == str(e["procStart"])
    except (OSError, ValueError, KeyError, IndexError):
        return False


def find_self(root, sdir, start):
    pid = start
    for _ in range(64):
        if pid <= 1:
            return None
        e = load_entry(sdir, pid)
        if e is not None and live(root, e):
            return e
        try:
            pid = stat_fields(root, pid)[0]
        except (OSError, ValueError, IndexError):
            return None
    return None


def descendants(root, pid):
    kids = {}
    for name in os.listdir(root):
        if not name.isdigit():
            continue
        try:
            kids.setdefault(stat_fields(root, name)[0], []).append(int(name))
        except (OSError, ValueError, IndexError):
            continue
    out, todo = [], list(kids.get(pid, []))
    while todo:
        c = todo.pop()
        out.append(c)
        todo.extend(kids.get(c, []))
    return sorted(out)


def cmdline(root, pid):
    try:
        with open(f"{root}/{pid}/cmdline", "rb") as f:
            t = f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except OSError:
        t = ""
    return (t or comm(root, pid))[:120]


def session_start(root, sdir, start):
    try:
        sid = json.loads(sys.stdin.read() or "{}").get("session_id")
    except ValueError as ex:
        log(f"session-start: stdin is not JSON ({ex}); no twin check")
        return 0
    try:
        names = os.listdir(sdir)
    except OSError as ex:
        log(f"cannot read sessions registry {sdir}: {ex}; no twin check")
        return 0
    me = find_self(root, sdir, start)
    if me is None:
        log(f"no registry entry found for ancestors of pid {start} in {sdir}; no twin check")
        return 0
    sid = sid or me.get("sessionId")
    notes = []
    for n in sorted(names):
        if not n.endswith(".json") or not n[:-5].isdigit():
            continue
        e = load_entry(sdir, n[:-5])
        if e is None or e.get("pid") == me.get("pid") or e.get("sessionId") != sid or not live(root, e):
            continue
        try:
            ppid = stat_fields(root, e["pid"])[0]
        except (OSError, ValueError, IndexError):
            continue
        kids = "; ".join(cmdline(root, c) for c in descendants(root, e["pid"])) or "none"
        started = time.strftime("%H:%M:%S", time.localtime(e.get("startedAt", 0) / 1000))
        notes.append(f"TWIN: conversation {sid} is also live in pid {e['pid']} (started {started}, "
                     f"parent {comm(root, ppid)} pid {ppid}, status {e.get('status', '?')}, children: {kids}). "
                     "Tell the owner first. Do not relaunch work your task notifications call stopped, "
                     "build, or commit until that process exits.")
    if notes:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": "\n".join(notes)}}))
    return 0


def pre_tool(root, sdir, start):
    if not os.path.isdir(sdir):
        log(f"cannot read sessions registry {sdir}; allowing")
        return 0
    me = find_self(root, sdir, start)
    if me is None:
        log(f"no registry entry found for ancestors of pid {start} in {sdir}; allowing")
        return 0
    if me.get("entrypoint") != "claude-vscode":
        return 0
    try:
        ppid = stat_fields(root, me["pid"])[0]
    except (OSError, ValueError, IndexError) as ex:
        log(f"cannot read {root}/{me['pid']}/stat: {ex}; allowing")
        return 0
    pc = comm(root, ppid)
    if ppid == 1 or pc == "systemd":
        sys.stderr.write(f"orphan-guard: this session's VS Code window is gone (claude pid {me['pid']} now "
                         f"parented to {pc} pid {ppid}); no new actions — running background jobs continue. "
                         "Stop and wait; the owner resumes this conversation in a new window.\n")
        return 2
    return 0


def main(argv):
    mode, rest = (argv[0], argv[1:]) if argv else (None, [])
    if mode in ("-h", "--help"):
        sys.stdout.write(USAGE)
        return 0
    if mode not in ("session-start", "pre-tool"):
        sys.stderr.write(f"claude-twin-guard: unknown mode `{mode}`\n" if mode else
                         "claude-twin-guard: missing mode\n")
        sys.stderr.write(USAGE)
        return 2
    # --sessions-dir/--proc-root/--start-pid exist ONLY so tests can point the guard at fixtures
    # and a fake process tree; production infers all three (~/.claude/sessions, /proc, our own
    # pid), so no flag is ever needed to run it (AUR P16).
    opts = {"--sessions-dir": os.path.expanduser("~/.claude/sessions"), "--proc-root": "/proc",
            "--start-pid": str(os.getpid())}
    i = 0
    while i < len(rest):
        if rest[i] not in opts or i + 1 >= len(rest):
            why = "unknown option" if rest[i] not in opts else "missing value for"
            sys.stderr.write(f"claude-twin-guard: {why} `{rest[i]}`\n{USAGE}")
            return 2
        opts[rest[i]] = rest[i + 1]
        i += 2
    try:
        start = int(opts["--start-pid"])
    except ValueError:
        sys.stderr.write(USAGE)
        return 2
    fn = session_start if mode == "session-start" else pre_tool
    try:
        return fn(opts["--proc-root"], opts["--sessions-dir"], start)
    except Exception as ex:  # never block on the guard's own fault
        log(f"{mode}: unexpected {type(ex).__name__}: {ex}; allowing")
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
