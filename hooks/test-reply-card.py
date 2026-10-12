#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
test-reply-card — executable decision table for hooks/reply-card.sh, the hook
that puts the owner's presentation rules (hooks/reply-card.md) at the END of
the context on every genuine owner message.

The hook's failure modes are silent: a card that is not injected looks exactly
like a model that forgot the rules, and a card injected after every harness
notification is noise the session pays for on each turn. The table therefore
covers what fires, what is skipped on purpose, what an unreadable input does,
the argument policy (P14) and the installed layout (script and card reached
through symlinks, as AUR's installer places them).

HERMETIC. Everything runs from a temp directory; nothing outside it is read
or written.

    python3 hooks/test-reply-card.py [-v|--verbose]

Exit 0 all pass, 1 any failure, 64 bad argument.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "reply-card.sh"
CARD = HERE / "reply-card.md"
REREAD_MARK = "CONTEXT WAS JUST COMPACTED"
WARN_MARK = "reply-card: unreadable hook input"

USAGE = ("usage: test-reply-card.py [-v|--verbose]\n"
         "  -v, --verbose   list every case, not only failures\n"
         "  -h, --help      this text\n")


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


def run(script, args=(), stdin=""):
    r = subprocess.run([str(script), *args], input=stdin, capture_output=True,
                       text=True, timeout=30)
    return r.returncode, r.stdout, r.stderr


def prompt_json(text, **extra):
    d = {"session_id": "s", "hook_event_name": "UserPromptSubmit", "prompt": text}
    d.update(extra)
    return json.dumps(d)


def check_user_prompt(report, card):
    """No argument: the UserPromptSubmit hook."""
    for label, text in (
        ("a genuine owner message", "please fix the thing"),
        ("a bare go", "go"),
        ("an empty prompt", ""),
        ("a long prompt", "x" * 5000),
        ("a prompt that merely mentions a marker late (past the 200 chars read)",
         "y" * 250 + " <task-notification> quoted by the owner"),
        ("a prompt with a similar but different marker", "see the [SYSTEM] note"),
    ):
        code, out, err = run(SCRIPT, stdin=prompt_json(text))
        report(code == 0 and out == card and not err, f"{label} -> card, exit 0, silent",
               f"code={code} stdout_len={len(out)} stderr={err[:200]!r}")

    for label, text in (
        ("a task notification", "<task-notification>\n<task-id>b1</task-id>\n</task-notification>"),
        ("an agent message", '<agent-message from="worker-1">done</agent-message>'),
        ("a bare agent-message tag", "<agent-message>done"),
        ("a system notification", "[SYSTEM NOTIFICATION - background job finished]"),
        ("a notification after leading text inside the first 200 chars",
         "context: " + "<task-notification>x"),
    ):
        code, out, err = run(SCRIPT, stdin=prompt_json(text))
        report(code == 0 and out == "" and not err, f"{label} -> nothing, exit 0, silent",
               f"code={code} stdout={out[:120]!r} stderr={err[:200]!r}")


def check_unreadable_input(report, card):
    """Malformed stdin must still deliver the card, and say why on stderr."""
    for label, stdin in (
        ("not JSON", "this is not json"),
        ("empty stdin", ""),
        ("truncated JSON", '{"prompt": "abc'),
        ("a JSON array, not an object", "[1, 2]"),
        ("a prompt that is not a string", '{"prompt": 5}'),
    ):
        code, out, err = run(SCRIPT, stdin=stdin)
        report(code == 0 and out == card and WARN_MARK in err,
               f"{label} -> card + stderr warning, exit 0",
               f"code={code} card_ok={out == card} stderr={err[:200]!r}")

    code, out, err = run(SCRIPT, stdin='{"session_id": "s"}')
    report(code == 0 and out == card and not err,
           "JSON object with no prompt key -> card, silent (an empty prompt is a genuine one)",
           f"code={code} card_ok={out == card} stderr={err[:200]!r}")


def check_compact(report, card):
    """`compact`: the SessionStart(compact) hook."""
    code, out, err = run(SCRIPT, ["compact"])
    lines = out.splitlines()
    report(code == 0 and out.startswith(card) and not err,
           "compact -> card first, exit 0, silent", f"code={code} stderr={err[:200]!r}")
    report(len(lines) == len(card.splitlines()) + 1 and lines[-1].startswith(REREAD_MARK)
           and "re-read the active brief" in lines[-1],
           "compact -> exactly one extra last line telling the model to re-read task state",
           f"last line: {lines[-1][:120] if lines else ''!r}")

    code, out2, err = run(SCRIPT, ["compact"], stdin="ignored stdin that is not JSON")
    report(code == 0 and out2 == out and not err,
           "compact ignores stdin (no parse, no warning)", f"code={code} stderr={err[:200]!r}")

    for label, stdin in (("a task notification on stdin",
                          prompt_json("<task-notification>x")),):
        code, out3, err = run(SCRIPT, ["compact"], stdin=stdin)
        report(code == 0 and out3 == out, f"compact with {label} still emits (never skipped)",
               f"code={code}")


def check_arguments(report):
    """P14: an unrecognised argument runs nothing, names the token, prints the set."""
    for bad in ("--bogus", "compat", "-x", "COMPACT", "--compact"):
        code, out, err = run(SCRIPT, [bad], stdin=prompt_json("hello"))
        report(code == 64 and out == "" and f"unknown argument '{bad}'" in err
               and "usage: reply-card.sh [compact]" in err,
               f"unknown argument {bad!r} -> 64, nothing on stdout, token named, usage on stderr",
               f"code={code} stdout={out[:80]!r} stderr={err[:200]!r}")
    for flag in ("--help", "-h"):
        code, out, err = run(SCRIPT, [flag])
        report(code == 0 and out.startswith("usage: reply-card.sh") and not err,
               f"{flag} -> usage on stdout, exit 0", f"code={code} stdout={out[:80]!r}")
    # More than one argument is refused whatever the first one is: the first EXTRA token is
    # named, the usage line is printed, nothing reaches stdout, exit 64.
    for args, extra in ((["compact", "extra"], "extra"), (["", "x"], "x"),
                        (["--help", "more"], "more"), (["--bogus", "two"], "two"),
                        (["compact", "a", "b"], "a")):
        code, out, err = run(SCRIPT, args, stdin=prompt_json("hello"))
        report(code == 64 and out == "" and f"extra argument '{extra}'" in err
               and "usage: reply-card.sh [compact]" in err,
               f"arguments {args!r} -> 64, nothing on stdout, first extra token named, usage on stderr",
               f"code={code} stdout={out[:80]!r} stderr={err[:200]!r}")


def check_installed_layout(report, card, root):
    """The installer places symlinks to the script and the card side by side."""
    inst = root / "installed"
    inst.mkdir()
    (inst / "reply-card.sh").symlink_to(SCRIPT)
    (inst / "reply-card.md").symlink_to(CARD)
    code, out, err = run(inst / "reply-card.sh", stdin=prompt_json("hi"))
    report(code == 0 and out == card and not err,
           "both reached through symlinks side by side -> card", f"code={code} stderr={err[:200]!r}")
    code, out, err = run(inst / "reply-card.sh", ["compact"])
    report(code == 0 and out.startswith(card) and REREAD_MARK in out and not err,
           "compact through symlinks -> card + re-read line", f"code={code} stderr={err[:200]!r}")

    # A card that cannot be found is an error that says so, never an empty success.
    lone = root / "lone"
    lone.mkdir()
    copy = lone / "reply-card.sh"
    copy.write_text(SCRIPT.read_text())
    copy.chmod(0o755)
    code, out, err = run(copy, stdin=prompt_json("hi"))
    report(code != 0 and out == "" and "reply-card.md" in err,
           "no card beside the script -> non-zero and stderr names reply-card.md",
           f"code={code} stdout={out[:80]!r} stderr={err[:200]!r}")

    dangling = root / "dangling"
    dangling.mkdir()
    (dangling / "reply-card.sh").symlink_to(SCRIPT)
    (dangling / "reply-card.md").symlink_to(root / "gone.md")
    code, out, err = run(dangling / "reply-card.sh", stdin=prompt_json("hi"))
    report(code != 0 and out == "" and "reply-card.md" in err,
           "card symlink dangling -> non-zero and stderr names reply-card.md",
           f"code={code} stdout={out[:80]!r} stderr={err[:200]!r}")


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

    if not SCRIPT.exists() or not CARD.exists():
        sys.stderr.write(f"test-reply-card: missing {SCRIPT} or {CARD}\n")
        return 1
    card = CARD.read_text()
    t0 = time.perf_counter()
    report = Tally(verbose)
    with tempfile.TemporaryDirectory(prefix="replycard-") as tmp:
        check_user_prompt(report, card)
        check_unreadable_input(report, card)
        check_compact(report, card)
        check_arguments(report)
        check_installed_layout(report, card, Path(tmp))
    fails, total = report.fails, report.total
    print(f"\ntest-reply-card: {total - fails}/{total} passed in {time.perf_counter() - t0:.1f}s")
    if fails:
        print("  A failing case is not a test bug until proven otherwise - the hook is "
              "the thing under test.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
