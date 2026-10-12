#!/usr/bin/env -S python3 -S
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
supervisor-bash-guard — PreToolUse(Bash) hook: the MAIN conversation runs only
cheap, bounded, read-only commands (plus git status/diff/log/add/commit);
anything else is refused with an instruction to delegate it.

WHY. Bash output is about half of what a long session re-reads on every turn
(token-usage measurement, 2026-10-12). Execution belongs to a delegate whose
context is thrown away; the supervisor keeps a small context and judges
results. A rule the model must remember mid-task lapses; a refusal it cannot
talk past does not — so the harness enforces it.

WHAT IT ALLOWS BEYOND THE READ-ONLY SET. AUR's own tools through the shared root
(`python3 [-I] <shared root>/{git-scope,commit-guard,token-usage}.py`, with the
root spelled as tilde, $HOME, ${HOME} or the expanded home) and shell structure: `for`/`if`/`while`/
`until` and `{ }` are syntax, so only the commands inside them are judged and a
`for` in-list is data. A loop whose condition is literally endless
(`while true`, `until false`) is refused as unbounded.

WHO IT BINDS. The hook input carries `agent_id` only inside a subagent, so a
subagent is never restricted (exit 0 silently). `agent_type` alone (set by
`--agent`) is still the main thread.

WIRING. settings.json hooks.PreToolUse, matcher "Bash", command = the path of
this executable file (<aur>/hooks/supervisor-bash-guard.py). Run directly, the
shebang's `python3 -S` skips the site import: ~36 ms per call instead of ~45 ms
under `python3 file`, against a 50 ms budget. This file does not wire itself.

THE OVERRIDE. Environment `AUR_SUPERVISOR_BASH=off` allows everything, for a
hands-on session the owner opts into. The knob is the environment because the
hook runs in the environment that launched Claude Code; each overridden call
is logged so an override left on is visible, never silent.

EVIDENCE (P10/P17). Every refusal, override and internal error appends one line
to ${XDG_STATE_HOME:-~/.local/state}/aur/supervisor-bash-guard.log:
timestamp, session, verdict, rule, offending word, first 120 chars of the
command. The count of refusals is part of the proof the guard is working.

FAILURE MODE. Malformed input or an internal error exits 0 (a guard that wedges
the session is worse than one that lapses) but says so on stderr and in the log.

Exit codes: 0 allow, 2 refuse (blocks the tool call; stderr goes to the model),
64 bad invocation (P14: an unrecognised argument runs nothing).
"""

import json
import os
import sys
import time

ENV_OVERRIDE = "AUR_SUPERVISOR_BASH"
LOG_CAP_BYTES = 8 * 1024 * 1024          # one rotated generation is kept

# The cheap read-only set. `cd` is shell state, not work; a refused `cd` would
# turn every `cd dir && git status` into a delegation for no saving.
SIMPLE_ALLOWED = (
    "ls cat head tail wc grep rg egrep fgrep sort uniq cut tr jq stat file du df "
    "ps pgrep pidof date echo printf pwd which type readlink realpath basename "
    "dirname test [ true false id uname diff cmp md5sum sha256sum column nl cd"
).split()
CONDITIONAL_ALLOWED = ("sed (-n, no -i)", "find (no -exec/-execdir/-delete/-ok/-fprint*)",
                       "awk (no system()/print >)")
GIT_READ = frozenset("status diff log show rev-parse merge-base ls-files blame "
                     "grep describe".split())
GIT_ALLOWED = sorted(GIT_READ | {"branch (listing only)", "add", "commit"})
STD_BIN_DIRS = ("/bin", "/usr/bin", "/usr/local/bin", "/sbin", "/usr/sbin")

# AUR's own tools, run through the shared root (under the user's home): committing, the
# commit scan and the token measurement are the supervisor's work, and they are
# reachable only as `python3 [-I] <shared root>/<tool>` - an exact path, so a
# neighbouring script or `python3 -c` is not laundered by living near them. When
# AUR_SHARE_ROOT relocates the root (aurshare.py honours it), that absolute path is accepted too.
AUR_TOOLS = ("git-scope.py", "commit-guard.py", "token-usage.py")
AUR_SHARED_ROOT = ".local/aur"                   # relative to a home; aurshare.SHARE
AUR_ROOT_HEADS = ("~", "$HOME", "${HOME}")       # the spellings of "home" a shell accepts

# Shell structure. These words are syntax, not commands: only the commands
# inside a loop, conditional or group are judged. `for` is dropped whole (its
# in-list is data); the others are peeled and what follows them is judged.
STRUCT_FOR = "for"
STRUCT_PEEL = frozenset("do done if then elif else fi while until { }".split())

# Variables that change what an allowed command DOES (code loading, lookup path,
# external programs git will spawn). Assigning them makes `ls` not `ls`.
ASSIGN_DENY_EXACT = frozenset(
    "PATH BASH_ENV ENV IFS PAGER GIT_PAGER GIT_EXTERNAL_DIFF GIT_SSH_COMMAND "
    "GIT_ASKPASS GIT_EDITOR EDITOR VISUAL".split())
ASSIGN_DENY_PREFIX = ("LD_",)

RULES = {
    "first-word-not-allowed": "the command is not in the cheap read-only set",
    "redirect-to-file": "output redirection to a file writes; only >/dev/null, "
                        "2>&1 and 2>/dev/null are allowed",
    "command-substitution": "$( ) and backticks run commands the guard cannot see",
    "process-substitution": "<( ) and >( ) run commands the guard cannot see",
    "subshell-grouping": "( ) subshells and groups cannot be judged command by command",
    "heredoc-expansion": "an unquoted heredoc delimiter lets the body run $( ) or backticks; "
                         "quote the delimiter",
    "unparseable": "the command line could not be parsed safely",
    "assignment-denied": "this variable changes what an allowed command executes",
    "wrapper-unparseable": "a nice/timeout/env wrapper's options were not understood",
    "option-writes": "this option makes an otherwise read-only command write or execute",
    "option-unbounded": "this option or loop never returns on its own",
    "option-uninspectable": "this option reads a program from a file the guard cannot inspect",
    "git-subcommand-not-allowed": "this git subcommand is outside the read/add/commit set",
    "git-option": "this git option can run a program or is not understood",
    "git-interactive": "this git invocation waits for an editor or terminal that is not there",
    "git-branch-mutates": "git branch is allowed for listing only",
}

# Writers whose refusal deserves a precise reason rather than the generic one.
KNOWN_WRITERS = {
    "tee": "tee writes files",
    "make": "make builds and writes",
    "cargo": "cargo builds and writes",
    "rm": "rm deletes", "mv": "mv moves", "cp": "cp writes",
    "mkdir": "mkdir writes", "touch": "touch writes", "chmod": "chmod writes",
    "python": "interpreters run arbitrary code", "python3": "interpreters run arbitrary code",
    "bash": "interpreters run arbitrary code", "sh": "interpreters run arbitrary code",
    "xargs": "xargs runs arbitrary commands", "sleep": "waiting belongs in a delegate",
}


# ---------------------------------------------------------------------------
# Scanner: command line -> simple commands + structural violations
# ---------------------------------------------------------------------------

class Simple:
    __slots__ = ("words",)

    def __init__(self, words):
        self.words = words


def is_assign(w):
    eq = w.find("=")
    if eq <= 0:
        return False
    name = w[:eq].rstrip("+")
    if not name or name[0].isdigit():
        return False
    return all(c.isalnum() or c == "_" for c in name)


def heredoc_subst_end(s, i):
    """If s[i:] starts `$(cat <<'TAG' ... TAG )` with a QUOTED tag, return the
    index just past the closing paren, else None. The body of a quoted heredoc
    is data (no expansion), and this is the standard idiom for a multi-line
    commit message, so it must not read as command substitution."""
    if not s.startswith("$(", i):
        return None
    j = i + 2
    while j < len(s) and s[j] in " \t":
        j += 1
    if not s.startswith("cat", j):
        return None
    j += 3
    while j < len(s) and s[j] in " \t":
        j += 1
    if not s.startswith("<<", j):
        return None
    j += 2
    if j < len(s) and s[j] == "-":
        j += 1
    while j < len(s) and s[j] in " \t":
        j += 1
    if j >= len(s) or s[j] not in "'\"\\":
        return None                         # unquoted tag: the body may expand
    if s[j] == "\\":
        j += 1
        k = j
        while k < len(s) and (s[k].isalnum() or s[k] == "_"):
            k += 1
        tag = s[j:k]
        j = k
    else:
        q = s[j]
        k = s.find(q, j + 1)
        if k < 0:
            return None
        tag = s[j + 1:k]
        j = k + 1
    if not tag:
        return None
    nl = s.find("\n", j)
    if nl < 0 or s[j:nl].strip():
        return None                         # something else follows the delimiter
    pos = nl + 1
    while pos <= len(s):
        end = s.find("\n", pos)
        line = s[pos:] if end < 0 else s[pos:end]
        if line.strip("\t") == tag:
            k = (len(s) if end < 0 else end + 1)
            while k < len(s) and s[k] in " \t\n":
                k += 1
            if k < len(s) and s[k] == ")":
                return k + 1
            return None
        if end < 0:
            return None
        pos = end + 1
    return None


def scan(cmd):
    """Split `cmd` on | || && ; & and newlines, honouring quotes. Returns
    (list of Simple, list of (word, rule)). Redirections are consumed here:
    safe ones vanish, file-writing ones become violations."""
    cmds, viol = [], []
    words = []
    cur = []
    has_word = False        # a word is in progress (an empty '' still counts)
    quoted = False          # the word in progress used a quote or backslash
    redir = None            # (kind, op, amp) awaiting its target word
    heredocs = []           # (tag, delimiter_was_quoted, strip_tabs) awaiting body
    n = len(cmd)
    i = 0

    def end_word():
        nonlocal cur, has_word, quoted, redir
        if not has_word:
            return
        text = "".join(cur)
        was_quoted = quoted
        cur, has_word, quoted = [], False, False
        if redir is not None:
            kind, op, amp = redir
            redir = None
            if kind == "out":
                if text == "/dev/null":
                    return
                if op == ">&" and not amp and (text == "-" or text.rstrip("-").isdigit()):
                    return                  # fd duplication: 2>&1, >&2, >&-
                viol.append((f"{op} {text}", "redirect-to-file"))
            elif kind == "heredoc":
                heredocs.append((text, was_quoted, op == "<<-"))
            return                          # 'in' / dup-in: nothing to judge
        words.append(text)

    def end_cmd():
        nonlocal words
        end_word()
        if words:
            cmds.append(Simple(words))
        words = []

    while i < n:
        c = cmd[i]
        if c == "\\":
            if i + 1 < n and cmd[i + 1] == "\n":
                i += 2
                continue
            cur.append(cmd[i + 1] if i + 1 < n else "\\")
            has_word = quoted = True
            i += 2
        elif c == "'":
            k = cmd.find("'", i + 1)
            if k < 0:
                viol.append(("'", "unparseable"))
                return cmds, viol
            cur.append(cmd[i + 1:k])
            has_word = quoted = True
            i = k + 1
        elif c == '"':
            i += 1
            has_word = quoted = True
            while True:
                if i >= n:
                    viol.append(('"', "unparseable"))
                    return cmds, viol
                d = cmd[i]
                if d == '"':
                    i += 1
                    break
                if d == "\\" and i + 1 < n:
                    if cmd[i + 1] in '$`"\\\n':
                        if cmd[i + 1] != "\n":
                            cur.append(cmd[i + 1])
                        i += 2
                    else:
                        cur.append("\\")
                        i += 1
                elif d == "`":
                    viol.append(("`", "command-substitution"))
                    i += 1
                elif d == "$" and cmd.startswith("$(", i):
                    e = heredoc_subst_end(cmd, i)
                    if e is None:
                        viol.append(("$(", "command-substitution"))
                        i += 2
                    else:
                        cur.append("HEREDOC")
                        i = e
                else:
                    cur.append(d)
                    i += 1
        elif c == "`":
            viol.append(("`", "command-substitution"))
            i += 1
        elif c == "$":
            if cmd.startswith("$(", i):
                e = heredoc_subst_end(cmd, i)
                if e is None:
                    viol.append(("$(", "command-substitution"))
                    i += 2
                else:
                    cur.append("HEREDOC")
                    has_word = quoted = True
                    i = e
            elif i + 1 < n and cmd[i + 1] == "'":     # $'...' ANSI-C quoting
                k = i + 2
                while k < n and cmd[k] != "'":
                    k += 2 if cmd[k] == "\\" else 1
                if k >= n:
                    viol.append(("$'", "unparseable"))
                    return cmds, viol
                cur.append(cmd[i + 2:k])
                has_word = quoted = True
                i = k + 1
            else:
                cur.append("$")
                has_word = True
                i += 1
        elif c in " \t":
            end_word()
            i += 1
        elif c == "\n":
            end_cmd()
            i += 1
            for tag, tag_quoted, strip in heredocs:
                body, i = read_heredoc(cmd, i, tag, strip)
                if not tag_quoted and ("$(" in body or "`" in body):
                    viol.append(("<<" + tag, "heredoc-expansion"))
            heredocs = []
        elif c == "#" and not has_word:
            k = cmd.find("\n", i)
            i = n if k < 0 else k
        elif c == ";":
            end_cmd()
            i += 2 if cmd.startswith(";;", i) else 1
        elif c == "|":
            end_cmd()
            i += 2 if cmd.startswith(("||", "|&"), i) else 1
        elif c == "&" and cmd.startswith("&&", i):
            end_cmd()
            i += 2
        elif c == "&" and cmd.startswith("&>", i):
            end_word()
            op = "&>>" if cmd.startswith("&>>", i) else "&>"
            redir = ("out", op, True)
            i += len(op)
        elif c == "&":
            end_cmd()
            i += 1
        elif c == ">":
            if cmd.startswith(">(", i):
                viol.append((">(", "process-substitution"))
                i += 2
                continue
            if has_word and not quoted and "".join(cur).isdigit():
                cur, has_word = [], False          # the fd prefix in 2>file
            else:
                end_word()
            if cmd.startswith(">>", i):
                op = ">>"
            elif cmd.startswith(">&", i):
                op = ">&"
            elif cmd.startswith(">|", i):
                op = ">|"
            else:
                op = ">"
            redir = ("out", op, False)
            i += len(op)
        elif c == "<":
            if cmd.startswith("<(", i):
                viol.append(("<(", "process-substitution"))
                i += 2
                continue
            if has_word and not quoted and "".join(cur).isdigit():
                cur, has_word = [], False
            else:
                end_word()
            if cmd.startswith("<<<", i):
                redir = ("in", "<<<", False)
                i += 3
            elif cmd.startswith("<<", i):
                op = "<<-" if cmd.startswith("<<-", i) else "<<"
                redir = ("heredoc", op, False)
                i += len(op)
            elif cmd.startswith("<>", i):
                viol.append(("<>", "redirect-to-file"))
                i += 2
            elif cmd.startswith("<&", i):
                redir = ("in", "<&", False)
                i += 2
            else:
                redir = ("in", "<", False)
                i += 1
        elif c in "()":
            viol.append((c, "subshell-grouping"))
            end_word()
            i += 1
        else:
            cur.append(c)
            has_word = True
            i += 1
    end_cmd()
    if redir is not None:
        viol.append((redir[1], "unparseable"))
    return cmds, viol


def read_heredoc(cmd, i, tag, strip):
    """Consume lines from i up to and including the terminator line."""
    n = len(cmd)
    start = i
    while i < n:
        end = cmd.find("\n", i)
        line = cmd[i:] if end < 0 else cmd[i:end]
        nxt = n if end < 0 else end + 1
        if (line.lstrip("\t") if strip else line) == tag:
            return cmd[start:i], nxt
        i = nxt
    return cmd[start:], n


# ---------------------------------------------------------------------------
# Judge: one simple command -> violations
# ---------------------------------------------------------------------------

def command_name(word):
    if "/" in word:
        d, _, b = word.rpartition("/")
        if d in STD_BIN_DIRS:
            return b
    return word


def check_assign(word, viol):
    name = word[:word.find("=")].rstrip("+")
    if name in ASSIGN_DENY_EXACT or name.startswith(ASSIGN_DENY_PREFIX):
        viol.append((word, "assignment-denied"))


def is_number(s):
    return s.lstrip("-").isdigit()


def is_duration(s):
    body = s[:-1] if s[-1:] in ("s", "m", "h", "d") else s
    return bool(body) and body.replace(".", "", 1).isdigit()


def strip_wrappers(words, viol):
    """Peel leading VAR=val, nice, timeout and env; return the effective words."""
    for _ in range(8):
        while words and is_assign(words[0]):
            check_assign(words[0], viol)
            words = words[1:]
        if not words:
            return words
        name = command_name(words[0])
        if name == "nice":
            j = 1
            while j < len(words):
                a = words[j]
                if a == "-n" and j + 1 < len(words) and is_number(words[j + 1]):
                    j += 2
                elif (a.startswith("-n") and is_number(a[2:])) or \
                        (a.startswith("-") and a[1:].isdigit()) or \
                        (a.startswith("--adjustment=") and is_number(a[13:])):
                    j += 1
                else:
                    break
            words = words[j:]
        elif name == "timeout":
            j = 1
            while j < len(words):
                a = words[j]
                if a in ("-s", "-k", "--signal", "--kill-after"):
                    j += 2
                elif a.startswith(("--signal=", "--kill-after=")) or \
                        a in ("--preserve-status", "--foreground", "-v", "--verbose"):
                    j += 1
                else:
                    break
            if j >= len(words) or not is_duration(words[j]):
                viol.append((words[0], "wrapper-unparseable"))
                return []
            words = words[j + 1:]
        elif name == "env":
            j = 1
            while j < len(words):
                a = words[j]
                if is_assign(a):
                    check_assign(a, viol)
                    j += 1
                elif a in ("-i", "-", "--ignore-environment"):
                    j += 1
                elif a in ("-u", "--unset"):
                    j += 2
                elif a.startswith("--unset="):
                    j += 1
                elif a.startswith("-"):
                    viol.append((words[0] + " " + a, "wrapper-unparseable"))
                    return []
                else:
                    break
            words = words[j:]
        else:
            return words
    viol.append((words[0] if words else "", "wrapper-unparseable"))
    return []


def short_cluster(a):
    """Letters of a single-dash option cluster ('-nr' -> 'nr'), else ''."""
    if a.startswith("-") and not a.startswith("--") and len(a) > 1 \
            and not a[1:].replace(".", "").isdigit():
        return a[1:]
    return ""


def cluster_hits(a, want, values=""):
    """Letters of `want` present in the short-option cluster `a`, scanning left to
    right and stopping at the first letter that takes a value (the remainder of
    the cluster is that value, not further flags: `-Iseconds`, `-mpatch`). A
    value-taking letter that is itself in `want` still counts as a hit."""
    hits = set()
    for ch in short_cluster(a):
        if ch in want:
            hits.add(ch)
        if ch in values:
            break
    return hits


def check_options(name, args):
    """Option-level rules for the conditionally-allowed commands. Returns a
    rule name (for the offending word see the caller) or None."""
    if name == "tail":
        for a in args:
            if a in ("--follow", "--retry") or a.startswith("--follow=") or \
                    cluster_hits(a, "fF", "ncs"):
                return a, "option-unbounded"
    elif name == "sort":
        for a in args:
            if a == "--output" or a.startswith("--output=") or \
                    a.startswith("--compress-program") or cluster_hits(a, "o", "kStT"):
                return a, "option-writes"
    elif name == "uniq":
        pos, skip = 0, False
        for a in args:
            if skip:
                skip = False
            elif a in ("-f", "-s", "-w"):
                skip = True
            elif not a.startswith("-") or a == "-":
                pos += 1
        if pos > 1:
            return "uniq INPUT OUTPUT", "option-writes"      # second operand is a file written
    elif name == "date":
        for a in args:
            if a == "--set" or a.startswith("--set=") or cluster_hits(a, "s", "Idr"):
                return a, "option-writes"
    elif name == "file":
        for a in args:
            if a == "--compile" or cluster_hits(a, "C", "mfeFP"):
                return a, "option-writes"
    elif name == "rg":
        for a in args:
            if a == "--pre" or a.startswith("--pre="):
                return a, "option-writes"                   # runs a program per file
    elif name == "find":
        bad = {"-exec", "-execdir", "-delete", "-ok", "-okdir",
               "-fprint", "-fprint0", "-fprintf", "-fls"}
        for a in args:
            if a in bad:
                return a, "option-writes"
    elif name == "awk":
        import re
        for a in args:
            if a in ("-f", "-i", "-l", "-E") or a.startswith(("--file", "--include",
                                                              "--load", "--exec")):
                return a, "option-uninspectable"
        text = "\n".join(args)
        for pat, label in (
                (r"\bsystem\s*\(", "system("),
                (r"\bprintf?\b[^;}\n]*>", "print >"),
                (r"\bprintf?\b[^;}\n]*\|", "print |"),
                (r"\|\s*&?\s*getline\b", "| getline")):
            if re.search(pat, text):
                return f"awk {label}", "option-writes"
    elif name == "sed":
        return check_sed(args)
    return None


def check_sed(args):
    import re
    n_flag = False
    scripts = []
    pos = []
    j = 0
    while j < len(args):
        a = args[j]
        if a == "--":
            pos.extend(args[j + 1:])
            break
        if a in ("--quiet", "--silent"):
            n_flag = True
        elif a.startswith("--in-place"):
            return a, "option-writes"
        elif a == "--file" or a.startswith("--file="):
            return a, "option-uninspectable"
        elif a == "--expression":
            if j + 1 < len(args):
                scripts.append(args[j + 1])
            j += 1
        elif a.startswith("--expression="):
            scripts.append(a.split("=", 1)[1])
        elif a.startswith("--"):
            pass                                    # --regexp-extended, --null-data, ...
        elif a.startswith("-") and len(a) > 1:
            cluster = a[1:]
            k = 0
            while k < len(cluster):
                ch = cluster[k]
                if ch == "n":
                    n_flag = True
                elif ch == "i":
                    return a, "option-writes"
                elif ch == "f":
                    return a, "option-uninspectable"
                elif ch in "el":
                    rest = cluster[k + 1:]
                    if rest:
                        val = rest
                    elif j + 1 < len(args):
                        val = args[j + 1]
                        j += 1
                    else:
                        val = ""
                    if ch == "e":
                        scripts.append(val)
                    break
                k += 1
        else:
            pos.append(a)
        j += 1
    if not scripts and pos:
        scripts.append(pos[0])
    if not n_flag:
        return "sed (no -n)", "option-unbounded"
    for s in scripts:
        # Heuristic: blank out /.../ bodies (addresses, s and y arguments), then
        # a stand-alone w, W or e is the write-file / execute command or the
        # s///w and s///e flags. Over-refuses rather than under-refuses.
        flat = re.sub(r"([/|#,:@%])(?:\\.|(?!\1).)*\1", "", s)
        if re.search(r"(?:^|[;{}!\s,/\d$])[wWe]\b", flat):
            return "sed script w/W/e", "option-writes"
    return None


GIT_BRANCH_OK_SHORT = frozenset("arvli")
GIT_BRANCH_OK_LONG = frozenset(
    "--all --remotes --verbose --list --merged --no-merged --contains --no-contains "
    "--show-current --sort --format --ignore-case --points-at --column --no-column "
    "--abbrev --no-abbrev --color --no-color".split())
GIT_BRANCH_FILTERS = frozenset("--list -l --contains --no-contains --merged --no-merged "
                               "--points-at".split())


def judge_git(words):
    """words[0] == 'git'. Returns (offending word, rule) or None."""
    j = 1
    while j < len(words):
        a = words[j]
        if a == "-C" or a == "--git-dir" or a == "--work-tree":
            j += 2
        elif a.startswith(("--git-dir=", "--work-tree=")) or \
                a in ("--no-pager", "-P", "--no-optional-locks"):
            j += 1
        elif a.startswith("-") and a != "--version":
            return "git " + a, "git-option"
        else:
            break
    if j >= len(words) or words[j] == "--version":
        return None
    sub, args = words[j], words[j + 1:]
    base = args[:args.index("--")] if "--" in args else args
    if sub in GIT_READ:
        for a in base:
            if a == "--output" or a.startswith("--output=") or a == "--ext-diff":
                return f"git {sub} {a}", "option-writes"
            if sub == "grep" and (a == "--open-files-in-pager" or a.startswith("-O")
                                  or a.startswith("--open-files-in-pager=")):
                return f"git grep {a}", "option-writes"
        return None
    if sub == "add":
        for a in base:
            if a in ("--interactive", "--patch", "--edit") or cluster_hits(a, "ipe"):
                return f"git add {a}", "git-interactive"
        return None
    if sub == "commit":
        has_msg = False
        for a in base:
            if a in ("--message", "--file", "--reuse-message", "--no-edit") or \
                    a.startswith(("--message=", "--file=", "--reuse-message=")):
                has_msg = True
            elif a in ("--edit", "--patch", "--interactive") or \
                    cluster_hits(a, "ep", "mFCctSu") & {"e", "p"}:
                return f"git commit {a}", "git-interactive"
            elif cluster_hits(a, "mFC", "mFCctSu"):
                has_msg = True
        if not has_msg:
            return "git commit (no -m/-F/-C/--no-edit)", "git-interactive"
        return None
    if sub == "branch":
        listing = False
        positional = False
        for a in base:
            if a.startswith("--"):
                opt = a.split("=", 1)[0]
                if opt not in GIT_BRANCH_OK_LONG:
                    return f"git branch {a}", "git-branch-mutates"
                listing = listing or opt in GIT_BRANCH_FILTERS
            elif a.startswith("-") and len(a) > 1:
                if not set(a[1:]) <= GIT_BRANCH_OK_SHORT:
                    return f"git branch {a}", "git-branch-mutates"
                listing = listing or "l" in a[1:]
            else:
                positional = True
        if positional and not listing:
            return "git branch <name>", "git-branch-mutates"     # would create a branch
        return None
    return "git " + sub, "git-subcommand-not-allowed"


def endless_condition(kw, rest):
    """`while true` / `until false` (and their negations): a loop condition fixed
    by its own text, so the loop never returns on its own."""
    neg = bool(rest) and rest[0] == "!"
    core = rest[1:] if neg else rest
    if len(core) != 1:
        return False
    name = command_name(core[0])
    if kw == "while":
        return (name == "true") != neg and name in ("true", "false")
    if kw == "until":
        return (name == "false") != neg and name in ("true", "false")
    return False


def strip_structure(words, viol):
    """Peel leading shell structure words (do, done, if, then, elif, else, fi,
    while, until, { and }); a `for` clause is data and vanishes whole. Returns
    the words of the command that remains, [] when the whole simple command was
    syntax. A loop whose condition can never end is a violation."""
    for _ in range(16):
        if not words:
            return words
        w = words[0]
        if w == STRUCT_FOR:
            return []
        if w not in STRUCT_PEEL:
            return words
        if w in ("while", "until") and endless_condition(w, words[1:]):
            viol.append((" ".join(words[:3 if words[1:2] == ["!"] else 2]), "option-unbounded"))
            return []
        words = words[1:]
    viol.append((words[0], "unparseable"))
    return []


def is_aur_tool(words):
    """`python3 [-I] <shared root>/<tool>` for an AUR tool, any spelling of the
    shared root that names this user's home."""
    if command_name(words[0]) != "python3":
        return False
    j = 2 if words[1:2] == ["-I"] else 1
    if len(words) <= j:
        return False
    heads = AUR_ROOT_HEADS + (os.path.expanduser("~").rstrip("/"),)
    if any(words[j] == f"{head}/{AUR_SHARED_ROOT}/{tool}" for head in heads for tool in AUR_TOOLS):
        return True
    reloc = relocated_root()
    return bool(reloc) and any(words[j] == f"{reloc}/{tool}" for tool in AUR_TOOLS)


def relocated_root():
    """The shared root when AUR_SHARE_ROOT relocates it (aurshare.py reads the same variable;
    this hook runs standalone, so it does not import it): the expanded value, no trailing
    slash. Empty, or not absolute once expanded, is no root: '.' or a cwd-relative path would
    accept whatever script happens to sit under the working directory."""
    value = os.path.expanduser(os.environ.get("AUR_SHARE_ROOT", ""))
    return value.rstrip("/") if value.startswith("/") else ""


def judge(words, allow):
    """One simple command -> list of (offending word, rule)."""
    viol = []
    words = strip_structure(words, viol)
    if words and words[0] == "!":           # pipeline negation does not change what runs
        words = words[1:]
    words = strip_wrappers(words, viol)
    if not words:
        return viol
    if is_aur_tool(words):
        return viol
    for pref in allow:                      # the project's own cheap status reads
        if words[:len(pref)] == pref:
            return viol
    name = command_name(words[0])
    if name == "git":
        v = judge_git(words)
        if v:
            viol.append(v)
    elif name in SIMPLE_ALLOWED or name in ("sed", "find", "awk"):
        v = check_options(name, words[1:])
        if v:
            viol.append(v)
    else:
        viol.append((name, "first-word-not-allowed"))
    return viol


# ---------------------------------------------------------------------------
# Project allowlist, logging, messages, main
# ---------------------------------------------------------------------------

def load_allow(cwd):
    """<project>/.claude/supervisor-allow.txt: one command prefix per line,
    '#' comments. Project root = cwd walked up to the directory holding .claude/.

    This is a knob because inference cannot serve it (AUR P16): which of a
    project's own tools are cheap status reads rather than work is a fact about
    that project's tools, not discoverable from the command line. Scope: the
    main conversation of that one project; subagents are never restricted."""
    d = cwd or ""
    seen = 0
    while d and seen < 64:
        if os.path.isdir(os.path.join(d, ".claude")):
            path = os.path.join(d, ".claude", "supervisor-allow.txt")
            try:
                with open(path, encoding="utf-8") as f:
                    lines = f.read().splitlines()
            except OSError:
                return [], path
            out = []
            for ln in lines:
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    out.append(ln.split())
            return out, path
        parent = os.path.dirname(d)
        if parent == d:
            break
        d, seen = parent, seen + 1
    return [], ""


def log_path():
    base = os.environ.get("XDG_STATE_HOME") or \
        os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "aur", "supervisor-bash-guard.log")


def log_line(session, verdict, rule, word, cmd, note=""):
    path = log_path()
    line = "ts={} session={} verdict={} rule={} word={} cmd={}{}\n".format(
        time.strftime("%Y-%m-%dT%H:%M:%S%z"), session or "-", verdict, rule or "-",
        json.dumps(word or "-"), json.dumps((cmd or "")[:120]),
        (" note=" + json.dumps(note)) if note else "")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            if os.path.getsize(path) > LOG_CAP_BYTES:
                os.replace(path, path + ".1")
        except OSError:
            pass
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode("utf-8", "replace"))
        finally:
            os.close(fd)
    except OSError as e:
        sys.stderr.write(f"supervisor-bash-guard: WARNING could not write {path}: {e}; "
                         f"verdict={verdict} rule={rule} word={word!r}\n")


def refusal_message(viol, allow_path):
    shown = viol[:5]
    lines = ["supervisor-bash-guard: REFUSED - the main conversation runs only cheap, "
             "bounded, read-only commands."]
    for word, rule in shown:
        why = KNOWN_WRITERS.get(word) if rule == "first-word-not-allowed" else None
        lines.append(f"  offending `{word}` - rule {rule}: {why or RULES.get(rule, rule)}")
    if len(viol) > len(shown):
        lines.append(f"  ... and {len(viol) - len(shown)} more")
    lines.append("Action: delegate it: spawn a delegate with a brief that carries this "
                 "command and the question it answers; keep this conversation for judgement.")
    lines.append("Allowed here: " + " ".join(SIMPLE_ALLOWED) + "; "
                 + "; ".join(CONDITIONAL_ALLOWED) + "; git " + ", ".join(GIT_ALLOWED)
                 + "; python3 [-I] <shared root>/{" + ",".join(t[:-3] for t in AUR_TOOLS) + "}.py"
                 + (f"; plus prefixes in {allow_path}" if allow_path else
                    "; plus prefixes in <project>/.claude/supervisor-allow.txt")
                 + ". Pipes (|, &&, ||, ;) are fine if every part is allowed; for/if/while "
                 "and { } are judged by the commands inside them.")
    lines.append(f"Override: set {ENV_OVERRIDE}=off in the environment that launches Claude "
                 "Code (a hands-on session the owner opts into). Subagents are never restricted.")
    return "\n".join(lines) + "\n"


USAGE = ("usage: supervisor-bash-guard.py   (reads one PreToolUse hook JSON object on stdin)\n"
         "  -h, --help   this text\n"
         f"  env {ENV_OVERRIDE}=off   allow everything (logged)\n"
         "  exit 0 allow, 2 refuse, 64 bad invocation\n")


def main():
    if len(sys.argv) > 1:
        if sys.argv[1] in ("-h", "--help"):
            sys.stdout.write(USAGE)
            return 0
        sys.stderr.write(f"supervisor-bash-guard: unknown option `{sys.argv[1]}`\n{USAGE}")
        return 64
    if sys.stdin.isatty():
        sys.stderr.write("supervisor-bash-guard: stdin is a terminal; expected hook JSON\n" + USAGE)
        return 64

    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("hook input is not a JSON object")
    except ValueError as e:
        sys.stderr.write(f"supervisor-bash-guard: WARNING malformed hook input ({e}); "
                         "allowing the call\n")
        log_line("", "error", "malformed-input", "", raw[:120], note=str(e))
        return 0

    if data.get("agent_id") is not None:
        return 0                            # a subagent: never restricted
    if data.get("tool_name") not in (None, "Bash"):
        return 0
    tool_input = data.get("tool_input")
    cmd = tool_input.get("command") if isinstance(tool_input, dict) else None
    if cmd is None or cmd == "":
        return 0
    session = data.get("session_id") or ""

    if not isinstance(cmd, str):
        sys.stderr.write("supervisor-bash-guard: WARNING tool_input.command is not a "
                         "string; allowing the call\n")
        log_line(session, "error", "malformed-input", "", repr(cmd), note="command not a string")
        return 0

    if os.environ.get(ENV_OVERRIDE, "").lower() == "off":
        log_line(session, "override", "env-" + ENV_OVERRIDE, "", cmd)
        return 0

    try:
        cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else os.getcwd()
        allow, allow_path = load_allow(cwd)
        commands, viol = scan(cmd)
        for sc in commands:
            viol.extend(judge(sc.words, allow))
    except Exception as e:                  # a guard must not wedge the session
        sys.stderr.write(f"supervisor-bash-guard: WARNING internal error "
                         f"({type(e).__name__}: {e}); allowing the call\n")
        log_line(session, "error", "internal-error", "", cmd, note=f"{type(e).__name__}: {e}")
        return 0

    if not viol:
        return 0
    log_line(session, "refuse", viol[0][1], viol[0][0], cmd)
    sys.stderr.write(refusal_message(viol, allow_path))
    return 2


if __name__ == "__main__":
    sys.exit(main())
