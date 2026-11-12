#!/usr/bin/env -S python3 -S
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
goal-gate — PreToolUse(Agent) hook: a worker is not spawned on a brief that has
drifted from its project's goal file.

WHY. A supervisor that has the goal in its head drifts off it under load; a check
it must remember lapses. The goal is written once, in the owner's words, as a
GOAL.md; the checks run by themselves; a fresh auditor with no stake in the work
judges the work against that file alone; and an off-task verdict blocks new
workers until the reason and a re-plan are written. This hook is the block. It is
generic: it knows only the shapes below, nothing of any project.

WHAT IT READS. From the spawn's prompt, every token that names an existing .md
file (absolute, or relative to the working directory or its git toplevel) is a
brief. For each, the nearest GOAL.md in its folder or above it (up to the git
toplevel) is its goal. State sits in a GOAL-audit/ directory beside the GOAL.md:
  verdict-<YYYYMMDDTHHMMSSZ>.md   the auditor's file; first line `verdict: on task`
                                  or `verdict: off task`
  replan-<YYYYMMDDTHHMMSSZ>.md    the reason, then the re-plan, written by the supervisor
The newest of each is chosen by the stamp in its name.

WHAT IT DECIDES (per goal brief, in this order; the first to apply decides).
  no-brief / no-goal   the prompt names no brief, or the brief has no GOAL.md: allow
  check-card           the brief is one a heading containing "checks" in GOAL.md names
                       (the auditor's own card and the tracker's run card): allow
  brief-check          <toplevel>/tools/brief_check.py refuses the brief: deny
  no-verdict           no verdict file: deny
  off-task-no-replan   newest verdict says off task and no re-plan is newer: deny
  stale-vs-commit      newest verdict older than the newest commit on the brief's
                       branch (the `branch X` of its Lab: line, else the checked-out
                       branch): deny
  stale-vs-compaction  newest verdict older than the last compact_boundary in the
                       session transcript (skipped, and logged `compaction=none`,
                       when the transcript has no marker): deny
  checker-fault        the checker is absent, times out (8 s, under the hook's 30 s
                       timeout: a hook killed by its own timeout lets the spawn
                       through) or crashes: deny a goal brief; any other is allowed
  ok                   allow
Every decision is one line in ${XDG_STATE_HOME:-~/.local/state}/aur/goal-gate.log
(8 MiB cap, one rotated generation): ts, session, decision, rule, brief, goal,
verdict (the file), commit time compared, compaction time compared. A deny is exit 2
with the rule and its evidence on stderr. There is no off switch: a gate the model
can lift is no gate; the owner edits the settings.

WIRING. settings.json hooks.PreToolUse, matcher "Agent", timeout 30, command = this
executable file (linked as ~/.claude/goal-gate.py by tokeneconomy.py).
Exit codes: 0 allow, 2 deny, 64 bad invocation (an unrecognised argument runs nothing).
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

LOG_CAP_BYTES = 8 * 1024 * 1024
CHECK_TIMEOUT = 8                      # seconds; the hook's own timeout is 30
GIT_TIMEOUT = 5
STAMP_RE = re.compile(r"^(verdict|replan)-(\d{8}T\d{6}Z)\.md$")
MD_RE = re.compile(r"[~\w./@+-]+\.md\b")
HELP = ("usage: goal-gate.py   (reads one PreToolUse JSON object on stdin)\n"
        "  exit 0 allow, 2 deny, 64 bad invocation\n")


def log_path():
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "aur", "goal-gate.log")


def log_line(session, decision, rule, **kv):
    path = log_path()
    line = "ts={} session={} decision={} rule={}".format(
        time.strftime("%Y-%m-%dT%H:%M:%S%z"), session or "-", decision, rule)
    for k, v in kv.items():
        line += " {}={}".format(k, json.dumps(str(v)) if v is not None else "-")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            if os.path.getsize(path) > LOG_CAP_BYTES:
                os.replace(path, path + ".1")
        except OSError:
            pass
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8", "replace"))
        finally:
            os.close(fd)
    except OSError as e:
        sys.stderr.write("goal-gate: WARNING could not write {}: {}; {}\n".format(path, e, line))


def git(cwd, *args):
    r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=GIT_TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError("git {} failed: {}".format(" ".join(args), r.stderr.strip()[:200]))
    return r.stdout.strip()


def toplevel(path):
    try:
        return git(path, "rev-parse", "--show-toplevel")
    except Exception:
        return None


def named_briefs(prompt, cwd):
    seen, out = set(), []
    top = toplevel(cwd) if cwd and os.path.isdir(cwd) else None
    for tok in MD_RE.findall(prompt or ""):
        cands = [os.path.expanduser(tok)] if tok.startswith(("/", "~")) else [p for p in (os.path.join(cwd or "", tok), os.path.join(top or "", tok)) if cwd or top]
        for c in cands:
            if os.path.isfile(c) and os.path.basename(c) != "GOAL.md":
                c = os.path.realpath(c)
                if c not in seen:
                    seen.add(c)
                    out.append(c)
                break
    return out


def find_goal(brief):
    d = os.path.dirname(brief)
    top = toplevel(d)
    while True:
        g = os.path.join(d, "GOAL.md")
        if os.path.isfile(g):
            return g
        if d == top or os.path.dirname(d) == d:
            return None
        d = os.path.dirname(d)


def card_names(goal):
    """basenames of every .md file named in a section of GOAL.md whose heading contains "checks" (except GOAL.md itself)"""
    names, inside = set(), False
    for line in open(goal, encoding="utf-8"):
        if line.startswith("#"):
            inside = "checks" in line.lower()
        elif inside:
            names.update(os.path.basename(m) for m in MD_RE.findall(line))
    names.discard("GOAL.md")
    return names


def newest(audit_dir, kind):
    best = None
    try:
        for n in os.listdir(audit_dir):
            m = STAMP_RE.match(n)
            if m and m.group(1) == kind and (best is None or m.group(2) > best[0]):
                best = (m.group(2), os.path.join(audit_dir, n))
    except OSError:
        pass
    return best


def stamp_epoch(stamp):
    return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()


def last_compaction(transcript):
    """epoch of the last system/compact_boundary line of the session transcript, or None"""
    if not transcript or not os.path.isfile(transcript):
        return None
    last = None
    with open(transcript, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "compact_boundary" not in line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("type") == "system" and o.get("subtype") == "compact_boundary" and o.get("timestamp"):
                last = datetime.fromisoformat(o["timestamp"].replace("Z", "+00:00")).timestamp()
    return last


def branch_of(brief_text, top):
    m = re.search(r"^.*\bLab:[^\n]*?\bbranch\s+([\w./-]+)", brief_text, re.M)
    return m.group(1).rstrip(".,;") if m else git(top, "rev-parse", "--abbrev-ref", "HEAD")


def judge(brief, cwd, transcript):
    """(allow, rule, evidence, fields) for one brief"""
    goal = find_goal(brief)
    if goal is None:
        return True, "no-goal", "", {"brief": brief}
    f = {"brief": brief, "goal": goal}
    if os.path.basename(brief) in card_names(goal):
        return True, "check-card", "named in the checks section of the goal file", f
    top = toplevel(os.path.dirname(brief))
    try:
        timeout = float(os.environ.get("AUR_GOAL_GATE_CHECK_TIMEOUT") or CHECK_TIMEOUT)
        checker = os.path.join(top or "", "tools", "brief_check.py")
        if not os.path.isfile(checker):
            raise RuntimeError("no brief checker at " + checker)
        r = subprocess.run([sys.executable, "-I", checker, brief], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "checker-fault", "the brief checker did not finish in time", f
    except Exception as e:
        return False, "checker-fault", "{}: {}".format(type(e).__name__, e), f
    if r.returncode == 2:
        return False, "brief-check", (r.stderr or r.stdout).strip()[:1500], f
    if r.returncode != 0:
        return False, "checker-fault", "the brief checker exited {}: {}".format(r.returncode, r.stderr.strip()[:300]), f
    audit = os.path.join(os.path.dirname(goal), "GOAL-audit")
    verdict = newest(audit, "verdict")
    if verdict is None:
        return False, "no-verdict", "no verdict file in " + audit, f
    f["verdict"] = verdict[1]
    first = open(verdict[1], encoding="utf-8").readline().strip()
    replan = newest(audit, "replan")
    if first == "verdict: off task":
        if replan is None or replan[0] <= verdict[0]:
            return False, "off-task-no-replan", "{}: {}; write the reason and the re-plan as a replan file newer than it".format(verdict[1], first), f
    elif first != "verdict: on task":
        return False, "checker-fault", "{} has no `verdict: on task|off task` first line".format(verdict[1]), f
    try:
        commit = float(git(top, "log", "-1", "--format=%ct", branch_of(open(brief, encoding="utf-8").read(), top)))
    except Exception as e:
        return False, "checker-fault", "newest commit unknown: {}".format(e), f
    f["commit"] = int(commit)
    if stamp_epoch(verdict[0]) < commit:
        return False, "stale-vs-commit", "verdict {} is older than the newest commit on the branch (epoch {}); run the auditor".format(verdict[0], int(commit)), f
    comp = last_compaction(transcript)
    f["compaction"] = int(comp) if comp is not None else "none"
    if comp is not None and stamp_epoch(verdict[0]) < comp:
        return False, "stale-vs-compaction", "verdict {} is older than the last compaction (epoch {}); run the auditor".format(verdict[0], int(comp)), f
    return True, "ok", "", f


def main():
    args = sys.argv[1:]
    if args:
        if args in (["-h"], ["--help"]):
            sys.stdout.write(HELP)
            return 0
        sys.stderr.write("goal-gate: unrecognised argument {!r}\n{}".format(args[0], HELP))
        return 64
    session = None
    try:
        data = json.loads(sys.stdin.read())
        session = data.get("session_id")
        prompt = (data.get("tool_input") or {}).get("prompt") or ""
        cwd, transcript = data.get("cwd") or "", data.get("transcript_path")
    except Exception as e:
        log_line(session, "allow", "fault", note="unreadable input: {}".format(e))
        sys.stderr.write("goal-gate: WARNING unreadable input, allowed: {}\n".format(e))
        return 0
    try:
        briefs = named_briefs(prompt, cwd)
    except Exception as e:
        log_line(session, "allow", "fault", note="reading the prompt: {}".format(e))
        return 0
    if not briefs:
        log_line(session, "allow", "no-brief")
        return 0
    decided = None
    for b in briefs:
        try:
            allow, rule, ev, f = judge(b, cwd, transcript)
        except Exception as e:   # a fault before the goal is known: not a goal spawn
            log_line(session, "allow", "fault", brief=b, note="{}: {}".format(type(e).__name__, e))
            continue
        log_line(session, "allow" if allow else "deny", rule, **f)
        if not allow:
            decided = (rule, ev, f)
            break
    if decided:
        rule, ev, f = decided
        sys.stderr.write("goal-gate: spawn refused, rule {}: {}\n  brief {}\n  goal {}\n"
                         "  (the goal file is the owner's words; the auditor's card and the re-plan are in its GOAL-audit directory)\n"
                         .format(rule, ev, f.get("brief"), f.get("goal")))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
