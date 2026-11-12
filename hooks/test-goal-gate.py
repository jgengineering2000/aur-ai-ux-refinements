#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
test-goal-gate: executable decision table for goal-gate.py, the PreToolUse(Agent)
hook that refuses to spawn a worker on a brief that has drifted from its goal
file. Each case builds a throwaway git repo (a stub brief checker, a GOAL.md, a
verdict directory, a transcript) and runs the hook as a wired hook is run.

    test-goal-gate.py [-v|--verbose]      exit 0 iff every case holds
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

GATE = Path(__file__).resolve().parent / "goal-gate.py"
USAGE = "usage: test-goal-gate.py [-v|--verbose]\n  -v, --verbose   list every case\n  -h, --help      this text\n"
COMMIT_TIME = "2026-01-01T10:00:00+0000"
COMPACT_TIME = "2026-01-01T10:30:00.000Z"
CHECKER = "import sys\nt = open(sys.argv[1]).read()\nsys.exit(2 if 'BAD' in t else 0)\n"
GOAL = "# GOAL\n\n## 1. The goal\n\nq\n\n## 9. The checks (OURS)\n\n| a | b |\n|---|---|\n| the auditor | brief: `briefs/AUDITOR.md` |\n| run | `briefs/TRACKER.md` |\n"


def sh(d, *a, env=None):
    e = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
             GIT_AUTHOR_DATE=COMMIT_TIME, GIT_COMMITTER_DATE=COMMIT_TIME)
    e.update(env or {})
    subprocess.run(list(a), cwd=d, check=True, capture_output=True, env=e)


class Fx:
    def __init__(self, root, goal=True, checker=True):
        self.root = Path(root)
        self.repo = self.root / "repo"
        self.state = self.root / "state"
        (self.repo / "briefs").mkdir(parents=True)
        (self.repo / "tools").mkdir()
        if checker:
            (self.repo / "tools" / "brief_check.py").write_text(CHECKER)
        if goal:
            (self.repo / "briefs" / "GOAL.md").write_text(GOAL)
        for n, t in (("WORK.md", "work\n"), ("BAD.md", "BAD\n"), ("AUDITOR.md", "audit\n"), ("TRACKER.md", "run\n")):
            (self.repo / "briefs" / n).write_text(t)
        sh(self.repo, "git", "init", "-q", "-b", "main")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-q", "-m", "one")
        self.audit = self.repo / "briefs" / "GOAL-audit"
        self.audit.mkdir()
        self.transcript = self.root / "t.jsonl"
        self.transcript.write_text(json.dumps({"type": "user"}) + "\n" +
                                   json.dumps({"type": "system", "subtype": "compact_boundary", "timestamp": COMPACT_TIME}) + "\n")

    def verdict(self, stamp, first="verdict: on task"):
        (self.audit / f"verdict-{stamp}.md").write_text(first + "\nevidence\n")

    def replan(self, stamp):
        (self.audit / f"replan-{stamp}.md").write_text("reason\nre-plan\n")

    def spawn(self, brief="WORK.md", **extra):
        p = {"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(self.repo),
             "transcript_path": str(self.transcript), "tool_input": {"prompt": f"Your brief is {self.repo}/briefs/{brief}. Go."}}
        p.update(extra)
        return p

    def log(self):
        f = self.state / "aur" / "goal-gate.log"
        return f.read_text().splitlines() if f.exists() else []


def run(fx, payload, args=(), env=None):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    e = {k: v for k, v in os.environ.items()}
    e.update(XDG_STATE_HOME=str(fx.state), HOME=str(fx.root))
    e.update(env or {})
    r = subprocess.run([str(GATE), *args], input=raw, env=e, capture_output=True, text=True, timeout=60)
    return r.returncode, r.stderr


def main():
    verbose = False
    for a in sys.argv[1:]:
        if a in ("-v", "--verbose"):
            verbose = True
        elif a in ("-h", "--help"):
            sys.stdout.write(USAGE)
            return 0
        else:
            sys.stderr.write(USAGE)
            return 64
    fails = total = 0

    def check(ok, label, detail=""):
        nonlocal fails, total
        total += 1
        fails += 0 if ok else 1
        if verbose or not ok:
            print(f"  [{'ok  ' if ok else 'FAIL'}] {label}")
            if not ok and detail:
                print(f"         {detail}")

    def case(label, want_rc, want_rule, setup=None, brief="WORK.md", fixture=None, payload_extra=None, env=None, log_has=()):
        with tempfile.TemporaryDirectory() as d:
            fx = Fx(d, **(fixture or {}))
            if setup:
                setup(fx)
            rc, err = run(fx, fx.spawn(brief, **(payload_extra or {})), env=env)
            lines = fx.log()
            ok = rc == want_rc and bool(lines) and f"rule={want_rule}" in (lines[-1] + " ")[:] and (lines[-1] + " ").split(f"rule={want_rule}", 1)[1][:1] in " " and all(x in lines[-1] for x in log_has)
            if want_rc == 2:
                ok = ok and want_rule in err
            check(ok, label, f"rc={rc} want {want_rc}; log={lines[-1:]}; stderr={err[:200]!r}")

    ok_verdict = lambda fx: fx.verdict("20260101T110000Z")
    case("no brief named: allow", 0, "no-brief", payload_extra={"tool_input": {"prompt": "just do a thing"}})
    case("brief with no GOAL.md anywhere: allow", 0, "no-goal", fixture={"goal": False})
    case("the auditor's card named by GOAL.md: allow with no verdict", 0, "check-card", brief="AUDITOR.md")
    case("the tracker card named by GOAL.md: allow", 0, "check-card", brief="TRACKER.md")
    case("goal brief, checker refuses: deny", 2, "brief-check", ok_verdict, brief="BAD.md")
    case("goal brief, no verdict at all: deny", 2, "no-verdict")
    case("goal brief, off task and no re-plan: deny", 2, "off-task-no-replan", lambda fx: fx.verdict("20260101T110000Z", "verdict: off task"))

    def off_then_replan(fx):
        fx.verdict("20260101T110000Z", "verdict: off task")
        fx.replan("20260101T113000Z")
    case("off task, re-plan newer: allow", 0, "ok", off_then_replan)

    def replan_older(fx):
        fx.replan("20260101T103000Z")
        fx.verdict("20260101T110000Z", "verdict: off task")
    case("off task, re-plan older than the verdict: deny", 2, "off-task-no-replan", replan_older)
    case("verdict older than the newest commit: deny", 2, "stale-vs-commit", lambda fx: fx.verdict("20260101T090000Z"))
    case("verdict older than the last compaction: deny", 2, "stale-vs-compaction", lambda fx: fx.verdict("20260101T102000Z"))
    case("on task, fresh: allow with the route in the log", 0, "ok", ok_verdict, log_has=("brief=", "goal=", "verdict=", "commit=", "compaction="))
    case("no transcript marker: compaction comparison skipped and said so", 0, "ok", lambda fx: (ok_verdict(fx), fx.transcript.write_text("{}\n")), log_has=("compaction=\"none\"",))
    case("nested spawn (agent_id present) is judged too", 2, "no-verdict", payload_extra={"agent_id": "a1"})
    case("checker missing in a goal repo: deny checker-fault", 2, "checker-fault", ok_verdict, fixture={"checker": False})
    case("the newest verdict is chosen by stamp", 0, "ok", lambda fx: (fx.verdict("20260101T090000Z", "verdict: off task"), fx.verdict("20260101T110000Z")))

    def slow_checker(fx):
        (fx.repo / "tools" / "brief_check.py").write_text("import time\ntime.sleep(30)\n")
        ok_verdict(fx)
    case("checker timeout: deny checker-fault", 2, "checker-fault", slow_checker, env={"AUR_GOAL_GATE_CHECK_TIMEOUT": "1"})

    # a spawn that names a check card is a check spawn: allowed whole, its other files are inputs
    with tempfile.TemporaryDirectory() as d:
        fx = Fx(d)
        pl = fx.spawn("AUDITOR.md")
        pl["tool_input"]["prompt"] += f" Inputs: {fx.repo}/briefs/BAD.md and {fx.repo}/briefs/WORK.md."
        rc, err = run(fx, pl)
        lines = fx.log()
        check(rc == 0 and "rule=check-card" in lines[-1] and sum("rule=input" in l for l in lines) == 2
              and any("BAD.md" in l and "rule=input" in l for l in lines), "check spawn naming other briefs: allowed whole, inputs logged", f"rc={rc} {lines} {err[:200]}")
    # a relative name resolves against the cwd only
    with tempfile.TemporaryDirectory() as d:
        fx = Fx(d)
        pl = fx.spawn("WORK.md", cwd=str(fx.repo / "tools"))
        pl["tool_input"]["prompt"] = "Your brief is briefs/WORK.md. Go."
        rc, err = run(fx, pl)
        lines = fx.log()
        check(rc == 0 and any("rule=unresolved" in l and "briefs/WORK.md" in l for l in lines) and "rule=no-brief" in lines[-1],
              "relative name not under the cwd: logged unresolved, never matched at the toplevel", f"rc={rc} {lines}")
    with tempfile.TemporaryDirectory() as d:
        fx = Fx(d)
        pl = fx.spawn("WORK.md", cwd=str(fx.repo))
        pl["tool_input"]["prompt"] = "Your brief is briefs/BAD.md. Go."
        rc, err = run(fx, pl)
        check(rc == 2 and "rule=brief-check" in fx.log()[-1], "relative name under the cwd resolves and is judged", f"rc={rc} {fx.log()}")

    with tempfile.TemporaryDirectory() as d:
        fx = Fx(d, goal=False, checker=False)
        rc, err = run(fx, "{ not json")
        check(rc == 0 and fx.log() and "rule=fault" in fx.log()[-1], "malformed input: allow and say so", f"rc={rc} {fx.log()}")
        rc, err = run(fx, fx.spawn(), args=("--bogus",))
        check(rc == 64 and "usage" in err.lower(), "unknown argument: help and 64, nothing run", f"rc={rc}")
    print(f"{total - fails}/{total} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
