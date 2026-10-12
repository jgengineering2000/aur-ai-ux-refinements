#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
test-supervisor-bash-guard — executable decision table for the PreToolUse(Bash)
hook that keeps the main conversation to cheap, bounded, read-only commands.

The guard's two failure modes are both silent: refuse what should pass and the
session stalls for reasons nobody attributes to a hook; allow what should be
delegated and the supervisor's context fills with build output again. The
decision table is therefore executable. It also proves the evidence trail
(every refusal/override/error is logged), the failure mode (malformed input
never wedges the session), the argument policy (P14) and the latency budget
(median under 50 ms per call, process start included).

HERMETIC. Fixture HOME, XDG_STATE_HOME and project live under a temp dir; no
real project, path or log is read or written.

    python3 hooks/test-supervisor-bash-guard.py [-v|--verbose]

Exit 0 all pass, 1 any failure, 64 bad argument.
"""

import importlib.util
import io
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

GUARD = Path(__file__).resolve().parent / "supervisor-bash-guard.py"
LATENCY_BUDGET_MS = 50.0
REFUSE_FRAGMENTS = ["delegate it: spawn a delegate with a brief", "AUR_SUPERVISOR_BASH=off"]

USAGE = ("usage: test-supervisor-bash-guard.py [-v|--verbose]\n"
         "  -v, --verbose   list every case, not only failures\n"
         "  -h, --help      this text\n")


class Fixture:
    def __init__(self, root):
        self.root = root
        self.home = root / "home"
        self.state = root / "state"
        self.proj = root / "proj"
        self.sub = self.proj / "rust" / "x"
        self.bare = root / "bare"
        for d in (self.home, self.sub, self.proj / ".claude", self.bare):
            d.mkdir(parents=True)
        (self.proj / ".claude" / "supervisor-allow.txt").write_text(
            "# cheap status reads of this project\n"
            "make status\n"
            "python3 tools/procstats_watch.py status\n"
            "\n")

    def env(self, **extra):
        e = {k: v for k, v in os.environ.items() if k != "AUR_SUPERVISOR_BASH"}
        e.update(HOME=str(self.home), XDG_STATE_HOME=str(self.state))
        e.update(extra)
        return e

    def log_lines(self):
        p = self.state / "aur" / "supervisor-bash-guard.log"
        return p.read_text().splitlines() if p.exists() else []


class Tally:
    """Counts cases; prints failures always, passes only when verbose."""

    def __init__(self, verbose):
        self.verbose, self.fails, self.total = verbose, 0, 0

    def __call__(self, ok, label, detail=""):
        self.total += 1
        self.fails += 0 if ok else 1
        if self.verbose or not ok:
            print(f"  [{'ok  ' if ok else 'FAIL'}] {label}")
            if not ok and detail:
                print(f"         {detail}")


def payload(cmd, cwd, **extra):
    d = {"session_id": "s-main", "hook_event_name": "PreToolUse", "tool_name": "Bash",
         "tool_input": {"command": cmd}, "cwd": str(cwd)}
    d.update(extra)
    return d


def invoke(fx, stdin_obj, env, args=()):
    # Executed directly, as a wired hook is, so the shebang (python3 -S: no site
    # import, ~10 ms) is part of what the latency budget measures.
    raw = stdin_obj if isinstance(stdin_obj, str) else json.dumps(stdin_obj)
    t0 = time.perf_counter()
    r = subprocess.run([str(GUARD), *args], input=raw, env=env,
                       capture_output=True, text=True, timeout=30)
    return r.returncode, r.stderr, (time.perf_counter() - t0) * 1000.0


# ── allowed: (command, why) ──────────────────────────────────────────────────
ALLOW = [
    ("git status", "plain read"),
    ("git status --short", "plain read with flag"),
    ("ls | grep x", "pipeline of two allowed commands"),
    ("ls -la 2>&1 | head -5", "2>&1 is fd duplication, not a file"),
    ("ls >/dev/null 2>&1", ">/dev/null and 2>&1"),
    ("cat f 2>/dev/null", "2>/dev/null"),
    ("ls &>/dev/null", "&>/dev/null"),
    ("echo hi >&2", ">&2 is fd duplication"),
    ("git commit -m m -- a b", "path-limited commit (the brief's case)"),
    ("git commit -F - <<'EOF'\nbody mentions $(rm -rf x) and git push --force\nEOF",
     "quoted heredoc body is data, not command"),
    ("git commit -m \"$(cat <<'EOF'\nsubject\n\nbody with $(id) and `ls`\nEOF\n)\"",
     "the standard quoted-heredoc commit-message idiom is data"),
    ("git commit --amend --no-edit", "no-edit amend has a message source"),
    ("git commit -am msg", "-am cluster carries -m"),
    ("git commit --message=msg -- a", "--message= form"),
    ("git add a b", "add by name"),
    ("git add -u", "add -u is not interactive"),
    ("git log --oneline -5", "read"),
    ("git --no-pager log -3", "global --no-pager"),
    ("git -C /somewhere status", "global -C"),
    ("git diff --stat HEAD -- a b", "read"),
    ("git show HEAD:file", "read"),
    ("git rev-parse HEAD", "read"),
    ("git merge-base --is-ancestor a b", "read"),
    ("git ls-files -m", "read"),
    ("git blame -L1,5 f", "read"),
    ("git grep -n foo", "read"),
    ("git describe --tags", "read"),
    ("git branch", "listing"),
    ("git branch -a", "listing"),
    ("git branch -vv", "listing"),
    ("git branch --list 'x*'", "listing with a pattern"),
    ("git branch --contains HEAD", "listing filter with a value"),
    ("git branch --show-current", "listing"),
    ("git --version", "no subcommand"),
    ("nice -n 10 ls", "nice wrapper"),
    ("nice -n 19 timeout 30 git status", "nested wrappers"),
    ("timeout 20 grep x f", "timeout wrapper"),
    ("timeout -s KILL 5s ls", "timeout with a signal option"),
    ("FOO=1 ls", "leading assignment"),
    ("env FOO=1 ls", "env wrapper"),
    ("a=1", "assignment only"),
    ("sed -n '1,5p' f", "sed -n"),
    ("sed -n -e 's/a/b/p' f", "sed -n -e"),
    ("sed -nE '/a|b/p' f", "sed -nE"),
    ("sed -n '/w/,/e/p' f", "w and e inside addresses are not commands"),
    ("find . -name x -type f", "find without exec"),
    ("find . -newer a -print", "find -print"),
    ("awk '{print $1}' f", "awk print"),
    ("awk -F: '$1>5 {print $2}' f", "awk comparison before print"),
    ("echo \"a;b && c > d | e\"", "operators inside double quotes are text"),
    ("echo 'x $(id) `id` > f'", "single quotes are literal"),
    ("grep -E '(a|b)' f", "parens inside quotes"),
    ("find . \\( -name a -o -name b \\)", "escaped parens"),
    ("cd /x && git status", "cd is shell state"),
    ("/usr/bin/ls", "absolute path in a standard bin dir"),
    ("tail -n 20 f", "tail without follow"),
    ("sort f | uniq -c | sort -rn | head", "sort/uniq pipeline"),
    ("uniq -f 1 f", "uniq flag value is not an output operand"),
    ("date +%s", "date format"),
    ("date -Iseconds", "-I takes an attached value; its 's' is not -s"),
    ("date -u -d tomorrow +%F", "date -d"),
    ("sort -t, -k2,2 -nr f", "sort separator and key values are not flags"),
    ("tail -n5 f", "tail -n with an attached value"),
    ("git commit -mpatch -- a", "-m's attached value 'patch' is a message, not -p"),
    ("! grep -q x f", "pipeline negation"),
    ("ls # comment > x", "comment is not a redirect"),
    ("printf '%s\\n' a", "printf"),
    ("test -f x && echo y || echo n", "test with && and ||"),
    ("[ -d x ]", "bracket test"),
    ("diff -u a b", "diff"),
    ("jq '.a' f", "jq"),
    ("cat < f", "input redirection reads"),
    ("git status; git diff", "semicolon"),
    ("ls \\\n -la", "backslash-newline continuation"),
    ("ls\ngit status", "newline separates commands"),
    ("ls &", "backgrounding an allowed command"),
    ("make status", "project allow-file prefix"),
    ("make status --foo", "allow-file prefix extends"),
    ("timeout 5 python3 tools/procstats_watch.py status", "allow-file prefix under a wrapper"),
    ("nice -n 10 make status | head", "allow-file prefix in a pipeline"),
    # AUR's own tools, invoked through the shared root. The root is spelled with
    # placeholders (see expand): the home-relative spellings are what the content
    # guard flags in tracked text, and the table is about those very spellings.
    ("python3 @TILDE@/@ROOT@/git-scope.py status /r", "AUR tool, ~ form"),
    ("python3 -I @TILDE@/@ROOT@/commit-guard.py /r", "AUR tool, ~ form, isolated interpreter"),
    ("python3 @TILDE@/@ROOT@/token-usage.py --help", "AUR tool, ~ form"),
    ("python3 @HOME@/@ROOT@/git-scope.py begin /r", "AUR tool, expanded HOME"),
    ("python3 -I @HOME@/@ROOT@/token-usage.py", "AUR tool, expanded HOME, -I"),
    ("python3 @VAR@/@ROOT@/commit-guard.py /r", "AUR tool, $HOME form"),
    ("python3 \"@BRACED@/@ROOT@/commit-guard.py\" /r", "AUR tool, quoted ${HOME} form"),
    ("nice -n 10 python3 @TILDE@/@ROOT@/token-usage.py | head", "AUR tool under a wrapper, in a pipeline"),
    ("printf '%s\\n' msg | python3 @TILDE@/@ROOT@/git-scope.py commit /r -F -", "AUR tool reading stdin"),
    # Shell structure is not a command: only the commands inside it are judged.
    ("for b in A B; do ls -la \"$b\"; done", "all-allowed for loop"),
    ("for b in A B; do make status; done", "for loop whose body is a project allow-file prefix"),
    ("for f in *.txt\ndo\n  grep x \"$f\"\ndone", "multi-line for loop"),
    ("for x in a b; do for y in c d; do echo $x$y; done; done", "nested for loops"),
    ("for x in a; do ls; done | head -3", "pipeline after done"),
    ("for x in a; do ls; done 2>&1", "fd duplication after done"),
    ("for x in make rm sleep; do echo $x; done", "the in-list is data, not commands"),
    ("if [ -f x ]; then echo y; else echo n; fi", "if/then/else/fi"),
    ("if ! grep -q x f; then echo y; elif test -d d; then echo z; fi", "if ! / elif"),
    ("if ls; then\n  git status\nfi", "multi-line if"),
    ("if make status; then ls; fi", "if condition that is a project allow-file prefix"),
    ("while test -f x; do ls; done", "while with an allowed condition"),
    ("until test -f x; do ls; done", "until with an allowed condition"),
    ("{ ls; git status; }", "brace group"),
    ("ls do done then fi", "keywords as arguments are arguments"),
    ("echo for", "keyword as an argument"),
]

# ── refused: (command, offending word fragment, rule, why) ───────────────────
REFUSE = [
    ("make build-dev", "make", "first-word-not-allowed", "builds belong to a delegate"),
    ("cargo test -p x", "cargo", "first-word-not-allowed", "builds belong to a delegate"),
    ("python3 x.py", "python3", "first-word-not-allowed", "interpreter"),
    ("bash -c 'ls'", "bash", "first-word-not-allowed", "interpreter"),
    ("echo hi | sh", "sh", "first-word-not-allowed", "interpreter at pipe end"),
    ("xargs ls", "xargs", "first-word-not-allowed", "runs arbitrary commands"),
    ("sleep 5", "sleep", "first-word-not-allowed", "waiting belongs in a delegate"),
    ("$CMD x", "$CMD", "first-word-not-allowed", "first word is an expansion"),
    ("rm -rf x", "rm", "first-word-not-allowed", "writes"),
    ("ls; make build-dev", "make", "first-word-not-allowed", "second command judged too"),
    ("ls && rm -rf x", "rm", "first-word-not-allowed", "&& does not launder"),
    ("ls || make", "make", "first-word-not-allowed", "|| does not launder"),
    ("git status\nmake", "make", "first-word-not-allowed", "newline does not launder"),
    ("nice -n 10 make test", "make", "first-word-not-allowed", "wrapper does not launder"),
    ("timeout 30 cargo build", "cargo", "first-word-not-allowed", "wrapper does not launder"),
    ("env X=1 make", "make", "first-word-not-allowed", "env does not launder"),
    ("make statusx", "make", "first-word-not-allowed", "allow prefix is word-wise"),
    ("make status && make build", "make", "first-word-not-allowed", "allow prefix covers one command"),
    ("cat a > b", "> b", "redirect-to-file", "the brief's case"),
    ("cat a >> b", ">> b", "redirect-to-file", "append"),
    ("ls > /tmp/x", "> /tmp/x", "redirect-to-file", "absolute path"),
    ("ls 2>err.txt", "> err.txt", "redirect-to-file", "stderr to a file"),
    ("ls >&err", ">& err", "redirect-to-file", ">&file is a file"),
    ("ls &>out", "&> out", "redirect-to-file", "&> file"),
    ("ls >| out", ">| out", "redirect-to-file", "clobber"),
    ("ls <> f", "<>", "redirect-to-file", "read-write open"),
    ("make status > out", "> out", "redirect-to-file", "structural rules apply to allow-listed too"),
    ("cat > f <<EOF\nx\nEOF", "> f", "redirect-to-file", "heredoc write"),
    ("echo $(id)", "$(", "command-substitution", "the brief's case"),
    ("echo `id`", "`", "command-substitution", "backticks"),
    ("echo \"$(id)\"", "$(", "command-substitution", "inside double quotes"),
    ("echo \"`id`\"", "`", "command-substitution", "backticks inside double quotes"),
    ("echo $((1+2))", "$(", "command-substitution", "arithmetic form"),
    ("cat <(ls)", "<(", "process-substitution", "process substitution"),
    ("ls | tee out", "tee", "first-word-not-allowed", "tee writes files"),
    ("(ls)", "(", "subshell-grouping", "subshell"),
    ("cat <<EOF\n$(id)\nEOF", "<<EOF", "heredoc-expansion", "unquoted heredoc body expands"),
    ("echo 'abc", "'", "unparseable", "unterminated quote"),
    ("git reset --hard", "git reset", "git-subcommand-not-allowed", "destructive"),
    ("git restore x", "git restore", "git-subcommand-not-allowed", "destructive"),
    ("git checkout .", "git checkout", "git-subcommand-not-allowed", "destructive"),
    ("git clean -fd", "git clean", "git-subcommand-not-allowed", "destructive"),
    ("git stash", "git stash", "git-subcommand-not-allowed", "moves work"),
    ("git rebase main", "git rebase", "git-subcommand-not-allowed", "rewrites"),
    ("git push origin x", "git push", "git-subcommand-not-allowed", "outward"),
    ("git status && git reset --soft HEAD~1", "git reset", "git-subcommand-not-allowed",
     "second git command judged too"),
    ("git commit", "git commit (no -m/-F/-C/--no-edit)", "git-interactive", "no message source opens an editor"),
    ("git commit -e -m x", "git commit -e", "git-interactive", "explicit editor"),
    ("git add -p", "git add -p", "git-interactive", "interactive"),
    ("git add -i", "git add -i", "git-interactive", "interactive"),
    ("git branch -D x", "git branch -D", "git-branch-mutates", "delete"),
    ("git branch -d x", "git branch -d", "git-branch-mutates", "delete"),
    ("git branch newbr", "git branch <name>", "git-branch-mutates", "create"),
    ("git branch -m a b", "git branch -m", "git-branch-mutates", "rename"),
    ("git branch --set-upstream-to=o/x", "git branch --set-upstream-to=o/x",
     "git-branch-mutates", "mutates config"),
    ("git diff --output=x", "git diff --output=x", "option-writes", "writes a file"),
    ("git log --ext-diff", "git log --ext-diff", "option-writes", "runs a program"),
    ("git grep -O less foo", "git grep -O", "option-writes", "runs a pager program"),
    ("git -c core.pager=x log", "git -c", "git-option", "config can run programs"),
    ("sed s/a/b/ f", "sed (no -n)", "option-unbounded", "sed without -n"),
    ("sed -n -i p f", "-i", "option-writes", "in place"),
    ("sed -ni p f", "-ni", "option-writes", "in place in a cluster"),
    ("sed --in-place=.b -n p f", "--in-place=.b", "option-writes", "in place, long"),
    ("sed -n -f s.sed f", "-f", "option-uninspectable", "script file"),
    ("sed -n 'w out' f", "sed script w/W/e", "option-writes", "w command writes"),
    ("sed -n 's/a/b/w out' f", "sed script w/W/e", "option-writes", "s///w writes"),
    ("sed -n 'e' f", "sed script w/W/e", "option-writes", "e command executes"),
    ("find . -exec rm {} \\;", "-exec", "option-writes", "executes"),
    ("find . -execdir ls {} +", "-execdir", "option-writes", "executes"),
    ("find . -delete", "-delete", "option-writes", "deletes"),
    ("find . -ok rm {} \\;", "-ok", "option-writes", "executes"),
    ("find . -fprint x", "-fprint", "option-writes", "writes"),
    ("find . -fprintf x %p", "-fprintf", "option-writes", "writes"),
    ("awk 'BEGIN{system(\"x\")}'", "awk system(", "option-writes", "executes"),
    ("awk '{print > \"f\"}' g", "awk print >", "option-writes", "writes"),
    ("awk '{print >> \"f\"}' g", "awk print >", "option-writes", "appends"),
    ("awk '{print | \"sh\"}' g", "awk print |", "option-writes", "pipes to a command"),
    ("awk '{\"date\" | getline x}'", "awk | getline", "option-writes", "runs a command"),
    ("awk -f prog.awk f", "-f", "option-uninspectable", "program file"),
    ("sort -o out f", "-o", "option-writes", "writes"),
    ("sort --output=out f", "--output=out", "option-writes", "writes"),
    ("tail -f log", "-f", "option-unbounded", "follows forever"),
    ("tail -F log", "-F", "option-unbounded", "follows forever"),
    ("tail -5f log", "-5f", "option-unbounded", "follow inside a numeric cluster"),
    ("date -us x", "-us", "option-writes", "-s inside a cluster"),
    ("git commit -pm x", "git commit -pm", "git-interactive", "-p inside a cluster before -m"),
    ("tail --follow=name log", "--follow=name", "option-unbounded", "follows forever"),
    ("date -s '2020-01-01'", "-s", "option-writes", "sets the clock"),
    ("uniq in out", "uniq INPUT OUTPUT", "option-writes", "second operand is written"),
    ("rg --pre x pat", "--pre", "option-writes", "runs a program per file"),
    ("file -C", "-C", "option-writes", "writes magic.mgc"),
    ("PATH=/x ls", "PATH=/x", "assignment-denied", "changes what ls is"),
    ("LD_PRELOAD=x ls", "LD_PRELOAD=x", "assignment-denied", "loads code"),
    ("env -S 'x' ls", "env -S", "wrapper-unparseable", "env -S runs a string"),
    ("timeout foo ls", "timeout", "wrapper-unparseable", "bad duration"),
    # AUR-tool near-misses: only the three named scripts, exactly, through the shared root.
    ("python3 @TILDE@/@ROOT@/other.py", "python3", "first-word-not-allowed", "not one of AUR's allowed tools"),
    ("python3 -I @TILDE@/@ROOT@/other.py", "python3", "first-word-not-allowed", "not one of AUR's allowed tools"),
    ("python3 @TILDE@/@ROOT@/git-scope.py.evil", "python3", "first-word-not-allowed", "exact script name"),
    ("python3 @TILDE@/@ROOT@/../aur/git-scope.py", "python3", "first-word-not-allowed", "exact path, not normalised"),
    ("python3 @HOME@/.local/other/git-scope.py", "python3", "first-word-not-allowed", "not the shared root"),
    ("python3 /elsewhere/.local/aur/git-scope.py", "python3", "first-word-not-allowed", "a different HOME"),
    ("python3 ~bob/.local/aur/git-scope.py", "python3", "first-word-not-allowed", "another user's root"),
    ("python3 -c x @TILDE@/@ROOT@/git-scope.py", "python3", "first-word-not-allowed", "-c runs code, script is data"),
    ("python @TILDE@/@ROOT@/git-scope.py status", "python", "first-word-not-allowed", "only python3"),
    ("bash @TILDE@/@ROOT@/git-scope.py status", "bash", "first-word-not-allowed", "only python3"),
    ("PATH=/x python3 @TILDE@/@ROOT@/git-scope.py", "PATH=/x", "assignment-denied", "assignment still judged"),
    ("python3 @TILDE@/@ROOT@/git-scope.py x > out", "> out", "redirect-to-file", "structural rules still apply"),
    ("python3 @TILDE@/@ROOT@/git-scope.py $(id)", "$(", "command-substitution", "structural rules still apply"),
    # Shell structure does not launder: the commands inside are judged.
    ("for b in A B; do make build; done", "make", "first-word-not-allowed", "loop body judged"),
    ("for b in A; do rm x; done", "rm", "first-word-not-allowed", "loop body judged"),
    ("for b in A; do ls > out; done", "> out", "redirect-to-file", "loop body redirect judged"),
    ("for b in A; do ls; done > out", "> out", "redirect-to-file", "redirect on done judged"),
    ("for b in $(ls); do ls; done", "$(", "command-substitution", "substitution in the in-list"),
    ("for x in a; do nice -n 5 make; done", "make", "first-word-not-allowed", "wrapper in a loop body"),
    ("if ls; then make; fi", "make", "first-word-not-allowed", "then-branch judged"),
    ("if ls; then ls; else rm x; fi", "rm", "first-word-not-allowed", "else-branch judged"),
    ("if cargo build; then ls; fi", "cargo", "first-word-not-allowed", "if condition judged"),
    ("while make; do ls; done", "make", "first-word-not-allowed", "while condition judged"),
    ("while true; do ls; done", "while true", "option-unbounded", "a literally endless loop never returns"),
    ("until false; do ls; done", "until false", "option-unbounded", "a literally endless loop never returns"),
    ("while ! false; do ls; done", "while ! false", "option-unbounded", "negated endless condition"),
    ("until ! true; do ls; done", "until ! true", "option-unbounded", "negated endless condition"),
    ("{ make; }", "make", "first-word-not-allowed", "brace body judged"),
    ("do rm x", "rm", "first-word-not-allowed", "a stray keyword does not launder"),
    ("fortune", "fortune", "first-word-not-allowed", "a word merely starting like a keyword"),
    ("forx b in A", "forx", "first-word-not-allowed", "a word merely starting like a keyword"),
    ("case x in x) ls;; esac", "case", "first-word-not-allowed", "case is not structure the guard reads"),
]


def expand(cmd, fx):
    """Fill a table row's placeholders: the fixture's home, and the shell's
    spellings of home (tilde, $HOME, ${HOME}) plus the shared root under it."""
    for token, text in (("@HOME@", str(fx.home)), ("@TILDE@", "~"), ("@VAR@", "$HOME"),
                        ("@BRACED@", "${HOME}"), ("@ROOT@", ".local/aur")):
        cmd = cmd.replace(token, text)
    return cmd


def check_allow_refuse(fx, report):
    env = fx.env()
    for cmd, why in ALLOW:
        cmd = expand(cmd, fx)
        code, err, _ = invoke(fx, payload(cmd, fx.sub), env)
        report(code == 0 and not err, f"want=ALLOW got={code}  main: {cmd!r}",
               f"{why}; stderr: {err[:300]!r}")
    for cmd, word, rule, why in REFUSE:
        cmd = expand(cmd, fx)
        code, err, _ = invoke(fx, payload(cmd, fx.sub), env)
        missing = [f for f in REFUSE_FRAGMENTS + [f"`{word}`", f"rule {rule}"] if f not in err]
        report(code == 2 and not missing, f"want=REFUSE got={code}  main: {cmd!r}",
               f"{why}; missing in stderr: {missing}; stderr: {err[:300]!r}")


def check_relocated_root(fx, report):
    """AUR_SHARE_ROOT (read by aurshare.py) relocates the shared root; the guard reads the
    same variable, so a relocated install keeps its three tools reachable - and nothing else."""
    reloc = fx.root / "relocated" / "aur"
    reloc.mkdir(parents=True)
    r = str(reloc)
    for tool in ("token-usage.py", "git-scope.py", "commit-guard.py"):
        code, err, _ = invoke(fx, payload(f"python3 {r}/{tool} --help", fx.sub),
                              fx.env(AUR_SHARE_ROOT=r))
        report(code == 0 and not err, f"AUR_SHARE_ROOT set: python3 <root>/{tool} -> 0, silent",
               err[:200])
    code, err, _ = invoke(fx, payload(f"python3 -I {r}/token-usage.py", fx.sub),
                          fx.env(AUR_SHARE_ROOT=r))
    report(code == 0 and not err, "AUR_SHARE_ROOT set: python3 -I <root>/token-usage.py -> 0", err[:200])
    code, err, _ = invoke(fx, payload(f"python3 {r}/token-usage.py", fx.sub),
                          fx.env(AUR_SHARE_ROOT=r + "/"))
    report(code == 0 and not err, "AUR_SHARE_ROOT with a trailing slash -> <root>/token-usage.py -> 0",
           err[:200])
    code, err, _ = invoke(fx, payload(f"python3 {fx.home}/relocated/aur/token-usage.py", fx.sub),
                          fx.env(AUR_SHARE_ROOT=os.path.join("~", "relocated", "aur")))
    report(code == 0 and not err, "AUR_SHARE_ROOT written with ~ is expanded against HOME -> 0",
           err[:200])
    code, err, _ = invoke(fx, payload(f"python3 {fx.home}/.local/aur/token-usage.py", fx.sub),
                          fx.env(AUR_SHARE_ROOT=r))
    report(code == 0 and not err, "AUR_SHARE_ROOT set: the default home root is still accepted -> 0",
           err[:200])
    for cmd, why in ((f"python3 {r}/other.py", "an unlisted script in the relocated root"),
                     (f"python3 {r}/sub/token-usage.py", "a tool one directory deeper"),
                     (f"python3 {r}/../token-usage.py", "a tool reached by .. out of the root"),
                     (f"python3 {r}x/token-usage.py", "a sibling that merely shares the prefix"),
                     (f"python3 -c 'print(1)' {r}/token-usage.py", "python3 -c before the tool")):
        code, err, _ = invoke(fx, payload(cmd, fx.sub), fx.env(AUR_SHARE_ROOT=r))
        report(code == 2 and "rule first-word-not-allowed" in err,
               f"AUR_SHARE_ROOT set: {why} -> 2", err[:200])
    # Unset, empty or relative: today's behaviour - a root that depends on cwd is no root.
    for label, extra in (("unset", {}), ("empty", {"AUR_SHARE_ROOT": ""}),
                         ("relative", {"AUR_SHARE_ROOT": "relocated/aur"})):
        env = fx.env(**extra)
        if label == "unset":
            env.pop("AUR_SHARE_ROOT", None)
        cmd = f"python3 {r}/token-usage.py" if label != "relative" else \
            "python3 relocated/aur/token-usage.py"
        code, err, _ = invoke(fx, payload(cmd, fx.sub), env)
        report(code == 2 and "rule first-word-not-allowed" in err,
               f"AUR_SHARE_ROOT {label}: the relocated tool is not accepted -> 2", err[:200])
    code, err, _ = invoke(fx, payload("python3 ./token-usage.py", fx.sub),
                          fx.env(AUR_SHARE_ROOT=""))
    report(code == 2, "AUR_SHARE_ROOT empty is not '.': python3 ./token-usage.py -> 2", err[:200])


def check_context_cases(fx, report):
    """Who is asking, where from, and what the environment says."""
    env = fx.env()
    sub_payload = payload("make build-dev", fx.sub, agent_id="a-123", agent_type="delegate")
    code, err, _ = invoke(fx, sub_payload, env)
    report(code == 0 and not err, "subagent make build-dev -> 0, silent", err[:200])

    code, err, _ = invoke(fx, payload("cargo test -p x", fx.sub, agent_id="a-1"), env)
    report(code == 0 and not err, "subagent cargo test -p x -> 0, silent", err[:200])

    code, err, _ = invoke(fx, payload("make build-dev", fx.sub, agent_type="delegate"), env)
    report(code == 2, "agent_type alone (--agent) is still the main thread -> 2", err[:200])

    code, err, _ = invoke(fx, payload("make build-dev", fx.sub), fx.env(AUR_SUPERVISOR_BASH="off"))
    report(code == 0 and not err, "override AUR_SUPERVISOR_BASH=off -> 0", err[:200])

    code, err, _ = invoke(fx, payload("make build-dev", fx.sub), fx.env(AUR_SUPERVISOR_BASH="on"))
    report(code == 2, "AUR_SUPERVISOR_BASH=on does not override -> 2", err[:200])

    outside = fx.root / "noproject"
    outside.mkdir()
    code, err, _ = invoke(fx, payload("python3 tools/procstats_watch.py status", outside), env)
    report(code == 2, "allow-file prefix does not apply outside its project -> 2", err[:200])

    code, err, _ = invoke(fx, payload("python3 tools/procstats_watch.py status", fx.sub), env)
    report(code == 0 and not err, "allow-file prefix found by walking up from a subdirectory -> 0",
           err[:200])

    code, err, _ = invoke(fx, payload("python3 tools/procstats_watch.py status", fx.proj), env)
    report(code == 0, "allow-file prefix at the project root -> 0", err[:200])

    # Allow-file with CRLF-free comment/blank lines parsed: a '#' line is not a prefix.
    code, err, _ = invoke(fx, payload("# cheap status reads of this project", fx.sub), env)
    report(code == 0, "a command line that is only a comment -> 0", err[:200])

    # Inputs the guard did not expect: never wedge the session.
    for label, raw in (("malformed JSON", "not json {"), ("empty stdin", ""),
                       ("JSON that is not an object", "[1,2]")):
        code, err, _ = invoke(fx, raw, env)
        report(code == 0 and "WARNING malformed" in err, f"{label} -> 0 + warning", err[:200])
    code, err, _ = invoke(fx, {"tool_name": "Bash", "tool_input": {"command": 123}}, env)
    report(code == 0 and "WARNING" in err, "non-string command -> 0 + warning", err[:200])
    code, err, _ = invoke(fx, {"tool_name": "Bash", "tool_input": {}}, env)
    report(code == 0 and not err, "no command -> 0 silent", err[:200])
    code, err, _ = invoke(fx, {"tool_name": "Write", "tool_input": {"command": "make"}}, env)
    report(code == 0 and not err, "another tool is not this guard's business -> 0", err[:200])


def check_arguments(fx, report):
    """P14: an unrecognised argument runs nothing, names the token, exits non-zero."""
    env = fx.env()
    ok_payload = payload("git status", fx.sub)
    code, err, _ = invoke(fx, ok_payload, env, args=("--bogus",))
    report(code == 64 and "unknown option `--bogus`" in err and "usage:" in err,
           "unknown option -> 64, names the token, prints usage", f"{code} {err[:200]!r}")
    code, err, _ = invoke(fx, payload("make", fx.sub), env, args=("--bogus",))
    report(code == 64, "unknown option refuses before judging anything", f"{code}")
    for flag in ("--help", "-h"):
        r = subprocess.run([str(GUARD), flag], capture_output=True,
                           text=True, env=env, timeout=30)
        report(r.returncode == 0 and "usage:" in r.stdout, f"{flag} -> 0 + usage", f"{r.returncode}")


def parse_log(line):
    m = re.match(r'ts=(\S+) session=(\S+) verdict=(\S+) rule=(\S+) word=("(?:[^"\\]|\\.)*") '
                 r'cmd=("(?:[^"\\]|\\.)*")(?: note=("(?:[^"\\]|\\.)*"))?$', line)
    if not m:
        return None
    ts, session, verdict, rule, word, cmd, note = m.groups()
    return dict(ts=ts, session=session, verdict=verdict, rule=rule, word=json.loads(word),
                cmd=json.loads(cmd), note=json.loads(note) if note else "")


def check_log(root, report):
    """Every refusal, override and error leaves exactly one line; allowed calls and
    subagents leave none."""
    fx = Fixture(root / "logfx")
    env = fx.env()
    long_cmd = "make " + "x" * 300
    invoke(fx, payload("git status", fx.sub), env)                              # allowed
    invoke(fx, payload("make build-dev", fx.sub, agent_id="a-1"), env)          # subagent
    invoke(fx, payload("make build-dev", fx.sub), env)                          # refuse 1
    invoke(fx, payload(long_cmd, fx.sub), env)                                  # refuse 2 (long)
    invoke(fx, payload("make build-dev", fx.sub), fx.env(AUR_SUPERVISOR_BASH="off"))  # override
    invoke(fx, "not json {", env)                                               # error
    lines = fx.log_lines()
    report(len(lines) == 4, "log: 4 lines (allowed + subagent leave none)", f"{len(lines)}: {lines}")
    parsed = [parse_log(ln) for ln in lines]
    report(all(parsed), "log: every line parses as ts/session/verdict/rule/word/cmd", f"{lines}")
    if len(parsed) == 4 and all(parsed):
        a, b, c, d = parsed
        report(a["verdict"] == "refuse" and a["rule"] == "first-word-not-allowed"
               and a["word"] == "make" and a["session"] == "s-main"
               and a["cmd"] == "make build-dev",
               "log: refusal names verdict, rule, offending word, session, command", f"{a}")
        report(len(b["cmd"]) == 120 and b["cmd"].startswith("make xxx"),
               "log: command truncated to 120 chars", f"{len(b['cmd'])}")
        report(c["verdict"] == "override" and c["session"] == "s-main",
               "log: override is logged", f"{c}")
        report(d["verdict"] == "error" and d["rule"] == "malformed-input" and d["note"],
               "log: malformed input is logged with the cause", f"{d}")

    # Internal error path (a guard bug) allows, warns, and logs: patch scan() to raise.
    fx2 = Fixture(root / "logfx2")
    sys.dont_write_bytecode = True      # keep the hooks directory free of a cache of the module under test
    spec = importlib.util.spec_from_file_location("guard_under_test", GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def boom(_cmd):
        raise RuntimeError("injected")
    mod.scan = boom
    saved = (os.environ.get("XDG_STATE_HOME"), sys.stdin, sys.stderr, sys.argv)
    os.environ["XDG_STATE_HOME"] = str(fx2.state)
    os.environ.pop("AUR_SUPERVISOR_BASH", None)
    sys.stdin, sys.stderr, sys.argv = io.StringIO(json.dumps(payload("make", fx2.sub))), \
        io.StringIO(), [str(GUARD)]
    try:
        code = mod.main()
        err = sys.stderr.getvalue()
    finally:
        if saved[0] is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = saved[0]
        sys.stdin, sys.stderr, sys.argv = saved[1], saved[2], saved[3]
    lines = fx2.log_lines()
    p = parse_log(lines[0]) if lines else None
    report(code == 0 and "WARNING internal error" in err and p is not None
           and p["verdict"] == "error" and p["rule"] == "internal-error" and "injected" in p["note"],
           "internal error -> 0 + warning + log line", f"{code} {err!r} {lines}")

    # The log is capped: past 8 MiB the previous generation moves aside, nothing is lost silently.
    fx4 = Fixture(root / "logfx4")
    (fx4.state / "aur").mkdir(parents=True)
    big = fx4.state / "aur" / "supervisor-bash-guard.log"
    big.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    invoke(fx4, payload("make", fx4.sub), fx4.env())
    rotated = fx4.state / "aur" / "supervisor-bash-guard.log.1"
    report(rotated.exists() and rotated.stat().st_size == 8 * 1024 * 1024 + 1
           and len(fx4.log_lines()) == 1,
           "log rotates past the cap: previous generation kept, new log starts at one line",
           f"{rotated.exists()} {len(fx4.log_lines())}")

    # A log sink that cannot be written must not change the verdict, and must say so.
    fx3 = Fixture(root / "logfx3")
    (root / "logfx3" / "blocker").write_text("a file where a directory is needed\n")
    code, err, _ = invoke(fx3, payload("make", fx3.sub),
                          fx3.env(XDG_STATE_HOME=str(root / "logfx3" / "blocker")))
    report(code == 2 and "could not write" in err and "rule=first-word-not-allowed" in err,
           "unwritable log -> verdict unchanged (2) + stderr says the line was lost", f"{code} {err[:300]!r}")


def check_latency(fx, report):
    env = fx.env()
    for cmd in ("git status", "ls | grep x"):
        for _ in range(3):
            invoke(fx, payload(cmd, fx.sub), env)                  # warm the page cache
        times = sorted(invoke(fx, payload(cmd, fx.sub), env)[2] for _ in range(21))
        med = statistics.median(times)
        print(f"  latency {cmd!r}: median {med:.1f} ms  p90 {times[int(len(times) * 0.9)]:.1f} ms"
              f"  max {times[-1]:.1f} ms  (budget median < {LATENCY_BUDGET_MS:.0f} ms)")
        report(med < LATENCY_BUDGET_MS, f"latency median under {LATENCY_BUDGET_MS:.0f} ms: {cmd!r}",
               f"median {med:.1f} ms")


def main():
    verbose = False
    for a in sys.argv[1:]:
        if a in ("-h", "--help"):
            print(USAGE, end="")
            return 0
        if a in ("-v", "--verbose"):
            verbose = True
        else:
            sys.stderr.write(f"unknown option `{a}`\n{USAGE}")
            return 64

    report = Tally(verbose)
    with tempfile.TemporaryDirectory(prefix="svguard-") as tmp:
        root = Path(tmp)
        fx = Fixture(root / "main")
        check_allow_refuse(fx, report)
        check_context_cases(fx, report)
        check_relocated_root(fx, report)
        check_arguments(fx, report)
        check_log(root, report)
        check_latency(fx, report)
    fails, total = report.fails, report.total

    print(f"\ntest-supervisor-bash-guard: {total - fails}/{total} passed")
    if fails:
        print("  A failing case is not a test bug until proven otherwise - the guard is "
              "the thing under test.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
