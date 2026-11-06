#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
test-tokeneconomy — executable decision table for tokeneconomy.py, the AUR
component that wires the token strategy (supervisor Bash guard, reply card,
delegate agent, auto-compact window) into a user's ~/.claude.

The component edits the one file that governs every hook a user runs, so its
failure modes are the quiet kind: a clobbered settings.json, a second run that
doubles an entry, a hook that fires twice, a link that dangles. The table
therefore proves idempotence, that foreign settings survive, that nothing is
overwritten without a trace, that the wired commands WORK when run the way
Claude Code runs them, and the argument policy (P14).

HERMETIC. Every case runs with HOME and the shared root inside a temp
directory; the machine's real ~/.claude is only stat()ed, before and after, to
prove it was not touched.

    python3 tools/test-tokeneconomy.py [-v|--verbose]

Exit 0 all pass, 1 any failure, 64 bad argument.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

AUR = Path(__file__).resolve().parent.parent
COMPONENT = AUR / "tokeneconomy.py"
sys.path.insert(0, str(AUR))
import aurshare  # noqa: E402  (after sys.path; reads SHARED_ASSETS only)

USAGE = ("usage: test-tokeneconomy.py [-v|--verbose]\n"
         "  -v, --verbose   list every case, not only failures\n"
         "  -h, --help      this text\n")

ENV_KEY = "CLAUDE_CODE_AUTO_COMPACT_WINDOW"
REPLY = '"$HOME/.claude/reply-card.sh"'
GUARD = '"$HOME/.claude/supervisor-bash-guard.py"'
EXPECTED_SETTINGS = {
    "env": {ENV_KEY: "300000"},
    "hooks": {
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": REPLY, "timeout": 5}]}],
        "SessionStart": [{"matcher": "compact",
                          "hooks": [{"type": "command", "command": REPLY + " compact", "timeout": 5}]}],
        "PreToolUse": [{"matcher": "Bash",
                        "hooks": [{"type": "command", "command": GUARD, "timeout": 5}]}],
    },
}


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


class Fixture:
    """A temp account: HOME, the shared root under it, and room for projects."""

    def __init__(self, root):
        self.root = Path(root)
        self.home = self.root / "home"
        self.share = self.home / ".local" / "aur"
        self.claude = self.home / ".claude"
        self.home.mkdir(parents=True)

    def env(self):
        e = {k: v for k, v in os.environ.items() if k != "AUR_SHARE_ROOT"}
        e["HOME"] = str(self.home)
        return e

    def run(self, action, *extra):
        r = subprocess.run([sys.executable, str(COMPONENT), action, *extra],
                           env=self.env(), capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout, r.stderr

    def kinds(self, out):
        """{kind: [message, ...]} from the [kind] lines of a run's output."""
        found = {}
        for line in out.splitlines():
            if line.startswith("[") and "] " in line:
                kind, _, msg = line[1:].partition("] ")
                found.setdefault(kind, []).append(msg)
        return found

    def settings(self):
        return json.loads((self.claude / "settings.json").read_text())

    def tree(self):
        """Every path under home with its kind and link target: a comparable snapshot."""
        out = {}
        for p in sorted(self.home.rglob("*")):
            rel = str(p.relative_to(self.home))
            if p.is_symlink():
                out[rel] = ("link", os.readlink(p))
            elif p.is_dir():
                out[rel] = ("dir", "")
            else:
                out[rel] = ("file", p.read_bytes())
        return out

    def seed_project(self, name, **files):
        proj = self.root / name
        (proj / ".claude").mkdir(parents=True)
        for fname, text in files.items():
            (proj / ".claude" / fname).write_text(text)
        return proj

    def register(self, *paths):
        self.share.mkdir(parents=True, exist_ok=True)
        (self.share / "projects.tsv").write_text(
            "#path\tlayout\tversion\n" + "".join(f"{p}\taur\t1.0\n" for p in paths))


def stat_real_claude():
    d = Path.home() / ".claude"
    names = sorted(p.name for p in d.iterdir()) if d.is_dir() else []
    s = d / "settings.json"
    return names, (s.stat().st_mtime_ns, s.stat().st_size) if s.exists() else None


def shell_hook(fx, command, stdin=""):
    """Run a settings.json hook command the way Claude Code does: through a shell."""
    r = subprocess.run(["sh", "-c", command], input=stdin, env=fx.env(),
                       capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout, r.stderr


def check_check_mode(report, tmp):
    fx = Fixture(tmp / "check")
    code, out, err = fx.run("check")
    k = fx.kinds(out)
    report(code == 1 and len(k.get("would", [])) == 11 and "changed" not in k,
           "check on a bare account -> exit 1, 11 'would' lines (3 shared, 4 links, 4 settings)",
           f"code={code} would={len(k.get('would', []))} err={err[:200]!r}")
    report(fx.tree() == {} , "check writes nothing at all (home still empty)", f"{list(fx.tree())[:6]}")


def check_install(report, tmp):
    fx = Fixture(tmp / "install")
    code, out, err = fx.run("install")
    k = fx.kinds(out)
    report(code == 0 and len(k.get("changed", [])) == 11 and not k.get("problem") and not err,
           "install on a bare account -> exit 0, 11 changes, no problems",
           f"code={code} changed={len(k.get('changed', []))} problems={k.get('problem')} err={err[:200]!r}")

    for name, key in (("token-usage.py", "token-usage.py"), ("hooks", "hooks/"), ("agents", "agents/")):
        link = fx.share / name
        want = AUR / aurshare.SHARED_ASSETS[key].rstrip("/")
        report(link.is_symlink() and Path(os.readlink(link)) == want and link.exists(),
               f"shared root {name} -> this checkout's {aurshare.SHARED_ASSETS[key].rstrip('/')}",
               f"{os.readlink(link) if link.is_symlink() else 'not a link'}")
    for rel_link, rel_target in (("supervisor-bash-guard.py", "hooks/supervisor-bash-guard.py"),
                                 ("reply-card.sh", "hooks/reply-card.sh"),
                                 ("reply-card.md", "hooks/reply-card.md"),
                                 ("agents/delegate.md", "agents/delegate.md")):
        link = fx.claude / rel_link
        report(link.is_symlink() and Path(os.readlink(link)) == fx.share / rel_target and link.is_file(),
               f"~/.claude/{rel_link} -> shared root {rel_target}, resolves to a file",
               f"{os.readlink(link) if link.is_symlink() else 'not a link'}")

    text = (fx.claude / "settings.json").read_text()
    report(json.loads(text) == EXPECTED_SETTINGS, "settings.json holds exactly the env key and three hook groups",
           text[:600])
    report(text == json.dumps(EXPECTED_SETTINGS, indent=2, ensure_ascii=False) + "\n",
           "settings.json is written in Claude Code's own format (2-space indent, trailing newline)")
    report(not list(fx.claude.glob("settings.json.bak-*")), "no backup when there was no prior settings.json")

    # The wired commands work when run the way Claude Code runs them.
    before = fx.tree()
    cmds = {ev: grp["hooks"][0]["command"] for ev, groups in fx.settings()["hooks"].items() for grp in groups}
    code, out, err = shell_hook(fx, cmds["UserPromptSubmit"], json.dumps({"prompt": "hello"}))
    report(code == 0 and out.startswith("REPLY CARD") and not err,
           "UserPromptSubmit command -> prints the reply card (card found through the symlink)",
           f"code={code} out={out[:60]!r} err={err[:160]!r}")
    code, out, err = shell_hook(fx, cmds["SessionStart"])
    report(code == 0 and out.startswith("REPLY CARD") and "CONTEXT WAS JUST COMPACTED" in out and not err,
           "SessionStart[compact] command -> card + re-read line", f"code={code} err={err[:160]!r}")
    hook_in = lambda c: json.dumps({"tool_name": "Bash", "tool_input": {"command": c}, "cwd": str(fx.home),
                                    "session_id": "t"})
    code, out, err = shell_hook(fx, cmds["PreToolUse"], hook_in("git status"))
    report(code == 0, "PreToolUse command lets 'git status' pass (exit 0)", f"code={code} err={err[:160]!r}")
    code, out, err = shell_hook(fx, cmds["PreToolUse"], hook_in("make build"))
    report(code == 2 and "delegate it" in err, "PreToolUse command refuses 'make build' (exit 2, says delegate)",
           f"code={code} err={err[:160]!r}")
    report(fx.tree().keys() - before.keys() <= {".local/state", ".local/state/aur",
                                                 ".local/state/aur/supervisor-bash-guard.log"},
           "running the hooks adds nothing to the account except the guard's own log")

    # Second run: nothing changes, and it says so.
    snap = fx.tree()
    st = (fx.claude / "settings.json").stat()
    code, out, err = fx.run("install")
    report(code == 0 and "token-economy: no change" in out and not fx.kinds(out).get("changed"),
           "second install -> exit 0, 'no change', zero changed lines", f"code={code} tail={out[-200:]!r}")
    st2 = (fx.claude / "settings.json").stat()
    report(fx.tree() == snap and (st.st_mtime_ns, st.st_size) == (st2.st_mtime_ns, st2.st_size),
           "second install leaves every path and settings.json (bytes + mtime) as it was")
    code, out, err = fx.run("check")
    report(code == 0 and "token-economy: no change" in out,
           "check after install -> exit 0, 'no change'", f"code={code} tail={out[-200:]!r}")


def check_settings_merge(report, tmp):
    # Foreign settings survive; a backup of the original bytes is kept.
    fx = Fixture(tmp / "merge")
    fx.claude.mkdir()
    original = json.dumps({
        "permissions": {"allow": ["Grep", "Glob"]},
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "/x/.claude/git-ip-guard.sh", "timeout": 10}]}]},
        "model": "opus",
    }, indent=4)
    (fx.claude / "settings.json").write_text(original)
    code, out, err = fx.run("install")
    s = fx.settings()
    report(code == 0 and s["permissions"] == {"allow": ["Grep", "Glob"]} and s["model"] == "opus",
           "foreign keys (permissions, model) survive the merge", f"code={code} keys={list(s)}")
    pre = s["hooks"]["PreToolUse"]
    report(len(pre) == 2 and pre[0]["hooks"][0]["command"] == "/x/.claude/git-ip-guard.sh"
           and pre[1] == EXPECTED_SETTINGS["hooks"]["PreToolUse"][0],
           "an existing PreToolUse Bash group is kept first; ours is appended as its own group",
           json.dumps(pre)[:300])
    report(list(s)[:3] == ["permissions", "hooks", "model"], "existing key order is preserved", f"{list(s)}")
    baks = list(fx.claude.glob("settings.json.bak-*"))
    report(len(baks) == 1 and baks[0].read_text() == original,
           "a backup with the original bytes was kept before the change", f"{[b.name for b in baks]}")

    # An env value the user set is theirs.
    fx = Fixture(tmp / "env-own")
    fx.claude.mkdir()
    (fx.claude / "settings.json").write_text(json.dumps({"env": {ENV_KEY: "150000", "OTHER": "1"}}))
    code, out, err = fx.run("install")
    s = fx.settings()
    report(code == 0 and s["env"] == {ENV_KEY: "150000", "OTHER": "1"} and "user's own value; kept" in out,
           "an existing env.CLAUDE_CODE_AUTO_COMPACT_WINDOW is kept, reported as the user's own",
           f"code={code} env={s.get('env')}")

    # An equivalent hook written another way is recognised, not doubled.
    fx = Fixture(tmp / "equiv")
    fx.claude.mkdir()
    (fx.claude / "settings.json").write_text(json.dumps({"hooks": {"UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": "/somewhere/else/.claude/reply-card.sh"}]}]}}))
    code, out, err = fx.run("install")
    s = fx.settings()
    report(code == 0 and len(s["hooks"]["UserPromptSubmit"]) == 1
           and "SessionStart" in s["hooks"] and "PreToolUse" in s["hooks"],
           "reply-card.sh under another path counts as present (not added twice); the others are added",
           json.dumps(s["hooks"])[:300])
    report("hooks.UserPromptSubmit already runs reply-card.sh" in out,
           "…and the output says which hook it recognised")

    # Unusable settings are never rewritten.
    for label, body in (("not valid JSON", "{ this is not json"), ("a JSON array", "[1, 2]")):
        fx = Fixture(tmp / ("bad-" + label.split()[0]))
        fx.claude.mkdir()
        (fx.claude / "settings.json").write_text(body)
        code, out, err = fx.run("install")
        report(code == 1 and (fx.claude / "settings.json").read_text() == body
               and str(fx.claude / "settings.json") in out and not list(fx.claude.glob("settings.json.bak-*")),
               f"settings.json that is {label} -> exit 1, bytes untouched, no backup, path named",
               f"code={code} tail={out[-240:]!r}")
        report((fx.claude / "reply-card.sh").is_symlink(),
               f"…the links are still made when settings.json is {label}")


def check_settings_symlink(report, tmp):
    """A settings.json that is a symlink (dotfiles managers do this) is written THROUGH: the
    link stays exactly as it was and its target receives the merge, the backup and the temp
    file. Replacing the link would leave the target silently stale."""
    original = json.dumps({"permissions": {"allow": ["Grep"]}, "model": "opus"}, indent=2) + "\n"
    for label, how in (("absolute", "abs"), ("relative", "rel")):
        fx = Fixture(tmp / f"symlink-{label}")
        fx.claude.mkdir()
        store = fx.root / "dotfiles"
        store.mkdir()
        target = store / "claude-settings.json"
        target.write_text(original)
        link = fx.claude / "settings.json"
        link_text = str(target) if how == "abs" else os.path.relpath(target, fx.claude)
        link.symlink_to(link_text)

        code, out, err = fx.run("install")
        merged = json.loads(target.read_text()) if target.is_file() else {}
        report(link.is_symlink() and os.readlink(link) == link_text and link.resolve() == target.resolve(),
               f"settings.json symlink ({label}) -> still that very symlink after install",
               f"code={code} is_symlink={link.is_symlink()} "
               f"link={os.readlink(link) if link.is_symlink() else 'a plain file'!r} want={link_text!r}")
        report(code == 0 and merged.get("permissions") == {"allow": ["Grep"]} and merged.get("model") == "opus"
               and merged.get("env") == EXPECTED_SETTINGS["env"]
               and merged.get("hooks") == EXPECTED_SETTINGS["hooks"],
               f"settings.json symlink ({label}) -> the TARGET holds foreign keys and the merged entries",
               f"code={code} keys={list(merged)} err={err[:160]!r}")
        baks = sorted(store.glob("claude-settings.json.bak-*"))
        report(len(baks) == 1 and baks[0].read_text() == original
               and not list(fx.claude.glob("settings.json.bak-*")),
               f"settings.json symlink ({label}) -> one backup of the original bytes beside the target, none beside the link",
               f"beside target={[b.name for b in baks]} beside link={[b.name for b in fx.claude.glob('settings.json.bak-*')]}")
        report(not list(store.glob("*.tmp-tokeneconomy")) and not list(fx.claude.glob("*.tmp-tokeneconomy")),
               f"settings.json symlink ({label}) -> no temp file left behind")

        def state():
            return (sorted(p.name for p in store.iterdir()), target.read_bytes(),
                    target.stat().st_mtime_ns, os.readlink(link) if link.is_symlink() else None,
                    fx.tree())

        snap = state()
        code, out, err = fx.run("install")
        again = state()
        report(code == 0 and "token-economy: no change" in out and again == snap,
               f"settings.json symlink ({label}) -> second install: no change, target, link and backups as they were",
               f"code={code} tail={out[-160:]!r}")

    # A dangling link (dotfiles not cloned yet, drive unmounted): a [problem], nothing written, no
    # directory created, the link left as it was - install and check alike, exit 1.
    for action in ("install", "check"):
        fx = Fixture(tmp / f"symlink-dangling-{action}")
        fx.claude.mkdir()
        gone_dir = fx.root / "not-cloned" / "dotfiles"
        target = gone_dir / "claude-settings.json"
        link = fx.claude / "settings.json"
        link.symlink_to(target)
        code, out, err = fx.run(action)
        problems = fx.kinds(out).get("problem", [])
        report(link.is_symlink() and os.readlink(link) == str(target)
               and not target.exists() and not gone_dir.exists() and not gone_dir.parent.exists(),
               f"settings.json dangling symlink, {action} -> link unchanged, target and its directories not created",
               f"is_symlink={link.is_symlink()} target={target.exists()} dir={gone_dir.exists()}")
        report(code == 1 and any(str(link) in m and str(target) in m and "dangling" in m for m in problems),
               f"settings.json dangling symlink, {action} -> a [problem] naming link and target, exit 1",
               f"code={code} problems={problems} err={err[:160]!r}")
        report(not list(fx.claude.glob("settings.json.*")) and not list(fx.claude.glob("*.tmp-tokeneconomy")),
               f"settings.json dangling symlink, {action} -> no backup or temp file beside the link")


def check_links(report, tmp):
    fx = Fixture(tmp / "links")
    fx.claude.mkdir()
    (fx.claude / "reply-card.sh").write_text("# the user's own script\n")
    other = fx.root / "elsewhere.py"
    other.write_text("x\n")
    (fx.claude / "supervisor-bash-guard.py").symlink_to(other)
    (fx.claude / "agents").mkdir()
    (fx.claude / "agents" / "delegate.md").symlink_to(fx.root / "gone.md")           # dangling
    (fx.claude / "reply-card.md").symlink_to(AUR / "hooks" / "reply-card.md")         # same file, other route
    code, out, err = fx.run("install")
    k = fx.kinds(out)
    report(code == 1 and (fx.claude / "reply-card.sh").read_text() == "# the user's own script\n"
           and not (fx.claude / "reply-card.sh").is_symlink(),
           "a real file where a link belongs -> problem, exit 1, file kept", f"code={code}")
    report(os.readlink(fx.claude / "supervisor-bash-guard.py") == str(other),
           "a link pointing somewhere else -> problem, left as it was")
    report(os.readlink(fx.claude / "agents" / "delegate.md") == str(fx.share / "agents" / "delegate.md"),
           "a dangling link is repointed at the shared root")
    report(os.readlink(fx.claude / "reply-card.md") == str(AUR / "hooks" / "reply-card.md")
           and any("same file as" in m for m in k.get("ok", [])),
           "a link to the same file by another route (a checkout path) is accepted and left as it is",
           f"{os.readlink(fx.claude / 'reply-card.md')}")
    report(sum(1 for m in k.get("problem", [])) == 2, "exactly the two real conflicts are problems",
           f"{k.get('problem')}")

    # A shared root that was deployed before these files existed is reported, not patched.
    fx = Fixture(tmp / "stale")
    (fx.share / "hooks").mkdir(parents=True)
    (fx.share / "hooks" / "pre-commit").write_text("#!/bin/sh\n")
    before = sorted(p.name for p in (fx.share / "hooks").iterdir())
    code, out, err = fx.run("install")
    k = fx.kinds(out)
    report(code == 1 and sorted(p.name for p in (fx.share / "hooks").iterdir()) == before
           and not (fx.claude / "reply-card.sh").exists() and (fx.claude / "agents" / "delegate.md").is_symlink()
           and any("predates the token-economy files" in m for m in k.get("problem", [])),
           "a shared-root hooks/ lacking the new files -> problems naming the fix; hooks/ untouched; "
           "the links that can be made are made",
           f"code={code} problems={k.get('problem')}")

    # A shared root already populated (deployed or devlinked) is left alone.
    fx = Fixture(tmp / "populated")
    fx.share.mkdir(parents=True)
    for name, key in (("token-usage.py", "token-usage.py"), ("hooks", "hooks/"), ("agents", "agents/")):
        (fx.share / name).symlink_to(AUR / aurshare.SHARED_ASSETS[key].rstrip("/"))
    code, out, err = fx.run("install")
    report(code == 0 and len(fx.kinds(out).get("changed", [])) == 8
           and len([m for m in fx.kinds(out).get("ok", []) if m.startswith("shared root")]) == 3,
           "a populated shared root is reported present and not touched: 8 changes (4 links + 4 settings)",
           f"code={code} changed={len(fx.kinds(out).get('changed', []))}")


def check_duplicates(report, tmp):
    fx = Fixture(tmp / "dups")
    p1 = fx.seed_project("proj1", **{"settings.json": json.dumps({
        "env": {ENV_KEY: "300000"},
        "hooks": {
            "UserPromptSubmit": [{"hooks": [{"type": "command",
                                             "command": '"$CLAUDE_PROJECT_DIR/.claude/reply-card.sh"'}]}],
            "SessionStart": [{"matcher": "compact", "hooks": [
                {"type": "command", "command": '"$CLAUDE_PROJECT_DIR/.claude/reply-card.sh" compact'}]}],
            "PreToolUse": [{"matcher": "Bash", "hooks": [
                {"type": "command", "command": "$HOME/.claude/supervisor-bash-guard.py"}]}],
        }})})
    p2 = fx.seed_project("proj2", **{"settings.local.json": json.dumps({"env": {ENV_KEY: "150000"}})})
    p3 = fx.root / "gone-project"
    p4 = fx.seed_project("proj4", **{"settings.json": "{ broken"})
    p5 = fx.seed_project("proj5", **{"settings.json": json.dumps({"hooks": {"UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": "/opt/hooks/other-hook.sh"}]}]}})})
    fx.register(p1, p2, p3, p4, p5, p1)
    files = {p: (p / ".claude" / n).read_bytes() for p, n in
             ((p1, "settings.json"), (p2, "settings.local.json"), (p4, "settings.json"), (p5, "settings.json"))}
    code, out, err = fx.run("install")
    k = fx.kinds(out)
    dups = k.get("duplicate", [])
    report(code == 0 and len(dups) == 5, "5 duplicates reported (3 hooks + env in proj1, env in proj2); exit stays 0",
           f"code={code} n={len(dups)}: {dups}")
    report(sum(str(p1) in d for d in dups) == 4 and sum(str(p2) in d for d in dups) == 1,
           "…attributed to the right project files", f"{dups}")
    report(any("same value" in d for d in dups) and any("overriding" in d for d in dups),
           "…the env duplicate says whether the value matches or overrides")
    report(all("fires twice" in d or "project's wins" in d for d in dups) and all("untouched" in d for d in dups),
           "…each says what happens and that the project file is left alone")
    report(not any(str(p5) in d for d in dups) and not any("gone-project" in d for d in dups),
           "an unrelated hook and a vanished project produce no duplicate")
    report(any(str(p4) in m and "not checked" in m for m in k.get("ok", [])),
           "an unreadable project settings file is noted, not guessed at")
    report(all((p / ".claude" / n).read_bytes() == files[p] for p, n in
               ((p1, "settings.json"), (p2, "settings.local.json"), (p4, "settings.json"), (p5, "settings.json"))),
           "no project file was modified")
    code, out, err = fx.run("check")
    report(len(fx.kinds(out).get("duplicate", [])) == 5, "check reports the same duplicates",
           f"{len(fx.kinds(out).get('duplicate', []))}")


def check_arguments(report, tmp):
    fx = Fixture(tmp / "args")
    for label, argv in (("no action", []), ("unknown action", ["bogus"]), ("unknown option", ["install", "--bogus"]),
                        ("option missing its value", ["install", "--home"]), ("extra positional", ["install", "x"]),
                        ("two actions", ["install", "check"])):
        r = subprocess.run([sys.executable, str(COMPONENT), *argv], env=fx.env(), capture_output=True,
                           text=True, timeout=30)
        report(r.returncode == 2 and r.stdout == "" and "usage:" in r.stderr and not fx.tree(),
               f"{label} -> exit 2, nothing on stdout, usage on stderr, nothing written",
               f"code={r.returncode} out={r.stdout[:80]!r} err={r.stderr[:160]!r}")
    for flag in ("--help", "-h"):
        r = subprocess.run([sys.executable, str(COMPONENT), flag], capture_output=True, text=True, timeout=30)
        report(r.returncode == 0 and "{install,check}" in r.stdout and "--home" in r.stdout,
               f"{flag} -> exit 0 and the accepted set", f"code={r.returncode}")
    code, out, err = fx.run("install", "--aur", str(fx.root / "no-such-checkout"))
    report(code == 1 and "is not an AUR checkout" in out and not fx.tree(),
           "--aur that is not a checkout -> exit 1, says so, nothing written", f"code={code} out={out[-200:]!r}")


def check_installer_menu(report, tmp):
    """install.py offers the component as an opt-in Part C and runs it on 'y'."""
    # Answers: features=a; A1..A5 all n; Part C; Proceed.
    for part_c, label in (("y", "yes"), ("n", "no")):
        fx = Fixture(tmp / ("menu-" + label))
        answers = "a\n" + "n\n" * 5 + part_c + "\ny\n"
        r = subprocess.run([sys.executable, str(AUR / "install.py")], input=answers, env=fx.env(),
                           capture_output=True, text=True, timeout=120)
        if part_c == "y":
            report(r.returncode == 0 and "Installing Token Economy" in r.stdout
                   and "token-economy: 11 changed" in r.stdout and fx.settings() == EXPECTED_SETTINGS
                   and (fx.claude / "reply-card.sh").is_symlink(),
                   "install.py Part C answered yes -> component runs against the account, exit 0",
                   f"code={r.returncode} tail={r.stdout[-300:]!r} err={r.stderr[-200:]!r}")
        else:
            report(r.returncode == 0 and "Part C" in r.stdout and not (fx.claude / "settings.json").exists()
                   and not (fx.claude / "reply-card.sh").exists(),
                   "install.py Part C answered no (the default) -> nothing is written to ~/.claude",
                   f"code={r.returncode} tail={r.stdout[-300:]!r} err={r.stderr[-200:]!r}")


def check_authority(report):
    for key in ("token-usage.py", "hooks/", "agents/"):
        src = AUR / aurshare.SHARED_ASSETS.get(key, "-").rstrip("/")
        report(key in aurshare.SHARED_ASSETS and src.exists(),
               f"aurshare.SHARED_ASSETS carries {key} and its source exists", str(src))
    ag = (AUR / "agents" / "delegate.md").read_text()
    head, _, body = ag.partition("\n---\n")
    fm = dict(line.split(": ", 1) for line in head.splitlines()[1:] if ": " in line)
    report(ag.startswith("---\n") and fm.get("name") == "delegate" and fm.get("model") == "sonnet"
           and fm.get("omitClaudeMd") == "true" and fm.get("tools") == "Bash, Read, Write, Edit, Skill",
           "agents/delegate.md frontmatter: name delegate, model sonnet, omitClaudeMd true, the five tools", str(fm))
    # Generic means it names no project this machine knows (the registry is the
    # list of them; none is spelled out here) and none of their hazards.
    # AUR itself is recognised by its repository, not its path: a lab is a linked worktree of it.
    common = subprocess.run(["git", "-C", str(AUR), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True).stdout.strip()
    this_repo = {AUR, Path(common).parent} if common else {AUR}
    projects = [Path(r["path"]).name.lower() for r in aurshare.registry_read()
                if Path(r["path"]).resolve() not in this_repo]
    named = [w for w in projects + ["sidecar", "at-spi", "wayland"] if w and w in ag.lower()]
    report(".claude/DELEGATE_BRIEF.md" in body and "if the project has one" in body and not named,
           "…the body reads the project's delegate card if present and names nothing project-specific",
           f"names: {named}")
    report(all(s in body for s in ("## Start", "## Keep your context small", "## Your final message")),
           "…and keeps the context-economy and report sections")


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
    t0 = time.perf_counter()
    report = Tally(verbose)
    real_before = stat_real_claude()
    with tempfile.TemporaryDirectory(prefix="tokeneconomy-") as t:
        tmp = Path(t)
        check_authority(report)
        check_check_mode(report, tmp)
        check_install(report, tmp)
        check_settings_merge(report, tmp)
        check_settings_symlink(report, tmp)
        check_links(report, tmp)
        check_duplicates(report, tmp)
        check_arguments(report, tmp)
        check_installer_menu(report, tmp)
    report(stat_real_claude() == real_before,
           "the machine's real ~/.claude (names, settings.json mtime and size) is exactly as it was")
    fails, total = report.fails, report.total
    print(f"\ntest-tokeneconomy: {total - fails}/{total} passed in {time.perf_counter() - t0:.1f}s")
    if fails:
        print("  A failing case is not a test bug until proven otherwise - the component is "
              "the thing under test.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
