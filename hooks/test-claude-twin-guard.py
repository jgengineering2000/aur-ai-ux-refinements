#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
test-claude-twin-guard -- executable table for hooks/claude-twin-guard.py.

GROUND TRUTH, verified 2026-10-14 against the live registry entries for pids
1120753, 1132621 and 1144455 (all `claude-vscode`, parent `code`):
  * ~/.claude/sessions/<pid>.json holds pid, sessionId, procStart, startedAt (ms),
    entrypoint (`claude-vscode` under VS Code), status, name.
  * procStart is field 22 of /proc/<pid>/stat (process start time in clock ticks
    since boot), as a decimal string: 55332904 == awk '{print $22}' /proc/1120753/stat
    for all three. A recycled pid gets a different start time, so a registry entry
    whose procStart differs from the live process is stale, not a twin.
  * Each of those claude processes' parent (stat field 4) is a `code` process (the
    VS Code extension host). Orphaned, the parent becomes `systemd` or pid 1.
  * The hook's own claude = nearest ancestor of the hook process with a registry
    entry whose procStart matches. Subagents are in-process, so they share it.

Real short-lived child processes (sleep) stand in wherever a live pid is needed;
a fixture proc root fakes a parent. Fixtures live in a tempdir removed at exit.
Nothing signals or touches a real claude process; ~/.claude/sessions is not read.

    python3 hooks/test-claude-twin-guard.py [-v|--verbose]
Exit 0 all pass, 1 any failure, 64 bad argument.
"""
import json, os, shutil, statistics, subprocess, sys, tempfile, time
from pathlib import Path

GUARD = Path(__file__).resolve().parent / "claude-twin-guard.py"
BUDGET_MS = 50.0
USAGE = "usage: test-claude-twin-guard.py [-v|--verbose]\n"


def starttime(pid, root="/proc"):
    s = Path(root, str(pid), "stat").read_text()
    return s[s.rindex(")") + 2:].split()[19]


def entry(pid, sid, proc_start, entrypoint="claude-vscode", status="idle", name="n"):
    return {"pid": pid, "sessionId": sid, "procStart": proc_start, "startedAt": 1792024414718,
            "entrypoint": entrypoint, "status": status, "name": name}


class Env:
    def __init__(self):
        self.d = Path(tempfile.mkdtemp(prefix="twin-guard-test-"))
        self.sessions = self.d / "sessions"
        self.sessions.mkdir()
        self.children = []

    def spawn(self, *argv):
        p = subprocess.Popen(argv or ["sleep", "30"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.children.append(p)
        for _ in range(200):  # wait until /proc shows it
            if Path("/proc", str(p.pid), "stat").exists():
                break
            time.sleep(0.005)
        return p

    def register(self, pid, sid, proc_start, **kw):
        (self.sessions / f"{pid}.json").write_text(json.dumps(entry(pid, sid, proc_start, **kw)))

    def fake_proc(self, root, pid, ppid, comm, start, cmdline=""):
        p = Path(root, str(pid))
        p.mkdir(parents=True, exist_ok=True)
        fields = ["S", str(ppid)] + ["0"] * 17 + [str(start)] + ["0"] * 10
        (p / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields) + "\n")
        (p / "comm").write_text(comm + "\n")
        (p / "cmdline").write_bytes(cmdline.replace(" ", "\0").encode())

    def run(self, mode, start_pid, proc_root="/proc", stdin="{}", extra=()):
        t = time.perf_counter()
        r = subprocess.run([sys.executable, str(GUARD), mode, "--sessions-dir", str(self.sessions),
                            "--proc-root", str(proc_root), "--start-pid", str(start_pid), *extra],
                           input=stdin, capture_output=True, text=True)
        return r, (time.perf_counter() - t) * 1000

    def close(self):
        for p in self.children:
            p.kill(); p.wait()
        shutil.rmtree(self.d)


FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(name)


def main():
    args = sys.argv[1:]
    if args not in ([], ["-v"], ["--verbose"]):
        sys.stderr.write(USAGE); return 64
    e = Env()
    try:
        sid = "11111111-aaaa-bbbb-cccc-222222222222"
        me = e.spawn(); twin = e.spawn(); child = e.spawn("sleep", "31")
        # no twin: only our own entry
        e.register(me.pid, sid, starttime(me.pid))
        r, _ = e.run("session-start", me.pid, stdin=json.dumps({"session_id": sid}))
        check("no twin -> silent exit 0", r.returncode == 0 and r.stdout == "", repr((r.returncode, r.stdout, r.stderr)))

        # live twin with matching procStart; give it a running child via a fixture-free real tree
        holder = e.spawn("sh", "-c", "sleep 32 & wait")
        e.register(holder.pid, sid, starttime(holder.pid), status="busy", name="twin")
        time.sleep(0.2)
        r, _ = e.run("session-start", me.pid, stdin=json.dumps({"session_id": sid}))
        out = r.stdout
        try:
            ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
            ev = json.loads(out)["hookSpecificOutput"]["hookEventName"]
        except Exception as ex:  # noqa
            ctx, ev = "", ""
        check("twin -> SessionStart additionalContext JSON", ev == "SessionStart" and ctx, repr(out))
        check("warning names twin pid", f"pid {holder.pid}" in ctx, ctx)
        check("warning names parent comm and pid", f"parent {Path('/proc', str(os.getpid()), 'comm').read_text().strip()} pid {os.getpid()}" in ctx, ctx)
        check("warning names status and children", "status busy" in ctx and "sleep 32" in ctx, ctx)
        check("warning carries instruction", "Tell the owner first." in ctx and "until that process exits" in ctx, ctx)
        check("warning starts TWIN: conversation <id>", ctx.startswith(f"TWIN: conversation {sid} is also live"), ctx)

        # stale: dead pid, and pid reuse (procStart mismatch)
        e2 = Env()
        try:
            me2 = e2.spawn(); other = e2.spawn(); dead = e2.spawn()
            e2.register(me2.pid, sid, starttime(me2.pid))
            e2.register(other.pid, sid, "1")  # mismatch = reused pid
            dstart = starttime(dead.pid); dpid = dead.pid
            dead.kill(); dead.wait()
            e2.register(dpid, sid, dstart)
            r, _ = e2.run("session-start", me2.pid, stdin=json.dumps({"session_id": sid}))
            check("stale entries (dead pid, procStart mismatch) ignored", r.returncode == 0 and r.stdout == "", repr((r.stdout, r.stderr)))
        finally:
            e2.close()

        # pre-tool with fixture proc root
        root = e.d / "proc"
        e.fake_proc(root, 1, 0, "systemd", 1)
        e.fake_proc(root, 50, 1, "code", 10, "code --type=utility")
        e.fake_proc(root, 60, 1, "systemd", 11)
        e.fake_proc(root, 200, 60, "claude", 500)   # orphaned, parent systemd (not pid 1)
        e.fake_proc(root, 201, 1, "claude", 501)    # orphaned, parent pid 1
        e.fake_proc(root, 202, 50, "claude", 502)   # healthy
        e.fake_proc(root, 203, 60, "claude", 503)   # orphaned terminal
        e.register(200, "s200", "500"); e.register(201, "s201", "501")
        e.register(202, "s202", "502"); e.register(203, "s203", "503", entrypoint="cli")
        r, _ = e.run("pre-tool", 200, root)
        check("orphaned VS Code (parent systemd) -> exit 2", r.returncode == 2, repr((r.returncode, r.stderr)))
        want = "orphan-guard: this session's VS Code window is gone (claude pid 200 now parented to systemd pid 60); no new actions"
        check("orphan stderr text", want in r.stderr and "the owner resumes this conversation in a new window." in r.stderr, r.stderr)
        r, _ = e.run("pre-tool", 201, root)
        check("orphaned VS Code (parent pid 1) -> exit 2", r.returncode == 2 and "systemd pid 1" in r.stderr, repr((r.returncode, r.stderr)))
        r, _ = e.run("pre-tool", 202, root)
        check("healthy VS Code parent -> allowed silently", r.returncode == 0 and r.stderr == "" and r.stdout == "", repr((r.returncode, r.stderr)))
        r, _ = e.run("pre-tool", 203, root)
        check("terminal session orphaned -> allowed", r.returncode == 0, repr((r.returncode, r.stderr)))

        # hook sits below claude: start at a descendant of 202/200 finds the claude ancestor
        e.fake_proc(root, 300, 200, "sh", 600)
        e.fake_proc(root, 301, 300, "python3", 601)
        r, _ = e.run("pre-tool", 301, root)
        check("nearest claude ancestor found through intermediates", r.returncode == 2, repr((r.returncode, r.stderr)))

        # unreadable registry: exit 0 + one stderr line
        r = subprocess.run([sys.executable, str(GUARD), "pre-tool", "--sessions-dir", str(e.d / "nope"),
                            "--proc-root", str(root), "--start-pid", "200"], input="{}", capture_output=True, text=True)
        lines = [l for l in r.stderr.splitlines() if l]
        check("unreadable registry -> exit 0 + one stderr line naming it",
              r.returncode == 0 and len(lines) == 1 and str(e.d / "nope") in lines[0], repr((r.returncode, r.stderr)))
        r = subprocess.run([sys.executable, str(GUARD), "session-start", "--sessions-dir", str(e.d / "nope"),
                            "--proc-root", str(root), "--start-pid", "200"], input='{"session_id":"x"}', capture_output=True, text=True)
        check("unreadable registry (session-start) -> exit 0, silent stdout",
              r.returncode == 0 and r.stdout == "" and r.stderr.strip() != "", repr((r.returncode, r.stdout, r.stderr)))

        # argument policy
        G = [sys.executable, str(GUARD)]
        for argv, frag in ((["bogus"], "unknown mode `bogus`"), ([], "missing mode"),
                           (["pre-tool", "--nonsense", "x"], "unknown option `--nonsense`"),
                           (["pre-tool", "--proc-root"], "missing value for `--proc-root`")):
            r = subprocess.run(G + argv, capture_output=True, text=True)
            check(f"bad argument {argv} names the token, usage, exit 2",
                  r.returncode == 2 and frag in r.stderr and "usage" in r.stderr.lower(), repr((r.returncode, r.stderr)))
        for h in ("-h", "--help"):
            r = subprocess.run(G + [h], capture_output=True, text=True)
            check(f"{h} -> usage on stdout, exit 0", r.returncode == 0 and "usage" in r.stdout.lower(), repr((r.returncode, r.stdout)))

        # latency: healthy path, process start included (budget 50 ms median)
        times = [e.run("pre-tool", 202, root)[1] for _ in range(30)]
        med = statistics.median(times)
        print(f"INFO pre-tool latency over 30 runs: median {med:.1f} ms, max {max(times):.1f} ms (budget {BUDGET_MS} ms)")
        check("pre-tool median latency under budget", med < BUDGET_MS, f"{med:.1f} ms")
    finally:
        e.close()
    print("ALL PASSED" if not FAILS else f"FAILED: {len(FAILS)}: {FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
