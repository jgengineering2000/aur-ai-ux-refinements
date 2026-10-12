#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Token-economy component — wires AUR's token strategy into a user's ~/.claude.

The strategy: the main session supervises and keeps a small context, delegates
execute with theirs thrown away. Its parts are generic and live in AUR:

  hooks/supervisor-bash-guard.py   refuses build/test/run commands in the main
                                   session (PreToolUse, matcher Bash)
  hooks/reply-card.sh + .md        the owner's presentation rules, put at the end
                                   of the context (UserPromptSubmit; and after a
                                   compaction, SessionStart matcher "compact")
  agents/delegate.md               the generic worker agent
  tools/token-usage.py             the measurement tool

What a PROJECT keeps is its own: its delegate card (.claude/DELEGATE_BRIEF.md)
and its guard allow-list (.claude/supervisor-allow.txt).

This component does the user-level half of installing them:

  1. shared root   token-usage.py, hooks/, agents/ present under the shared root
                   (under the home's .local/aur). Absent ones are linked to this checkout, as
                   `aurshare.py devlink` does; present ones are never touched,
                   only checked for the files this component needs.
  2. links         ~/.claude/{supervisor-bash-guard.py, reply-card.sh,
                   reply-card.md, agents/delegate.md} -> the shared root.
  3. settings      ~/.claude/settings.json: env.CLAUDE_CODE_AUTO_COMPACT_WINDOW
                   and the three hook entries. Merged, never replaced; a backup
                   is kept before the first change; an existing value of the env
                   key is the user's and is kept.
  4. duplicates    A registered project whose own .claude/settings*.json carries
                   an equivalent entry would fire it twice. REPORTED, never
                   edited: a project's files are the project's.

Idempotent: a second run changes nothing and says "no change". Nothing here is
part of the shared-root heal; propagation to a machine's real home is a
deliberate act (`install`), `check` shows what it would do and writes nothing.

    python3 tokeneconomy.py install|check [--home DIR] [--share DIR] [--aur DIR]

Exit 0 in sync / done, 1 problems (or, for `check`, changes pending), 2 bad argument.
"""

import argparse
import copy
import json
import os
import shlex
import shutil
import sys
import time
from pathlib import Path

import aurshare

# (link relative to ~/.claude, target relative to the shared root). The stable
# names are the shared root's, never a checkout path (shared-root-design.md).
USER_LINKS = (
    ("supervisor-bash-guard.py", "hooks/supervisor-bash-guard.py"),
    ("reply-card.sh", "hooks/reply-card.sh"),
    ("reply-card.md", "hooks/reply-card.md"),
    ("agents/delegate.md", "agents/delegate.md"),
)

# Shared-root assets this component needs (keys of aurshare.SHARED_ASSETS, the
# one authority for where each comes from in a checkout).
SHARED_KEYS = ("token-usage.py", "hooks/", "agents/")

# Claude Code compacts when the context reaches this many tokens. A value the
# user already set is theirs and is kept.
ENV = {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "300000"}

# (event, matcher or None, command, timeout seconds). Commands name the user's
# ~/.claude through $HOME so the entry is the same on every machine and account.
HOOKS = (
    ("UserPromptSubmit", None, '"$HOME/.claude/reply-card.sh"', 5),
    ("SessionStart", "compact", '"$HOME/.claude/reply-card.sh" compact', 5),
    ("PreToolUse", "Bash", '"$HOME/.claude/supervisor-bash-guard.py"', 5),
)

SETTINGS_FILES_OF_A_PROJECT = ("settings.json", "settings.local.json")


class Report:
    """What happened (or, for check, would), one line each, plus the tallies."""

    def __init__(self, apply_changes, log):
        self.apply_changes = apply_changes
        self.log = log
        self.changed = self.ok = self.duplicates = self.problems = 0

    def change(self, msg):
        self.changed += 1
        self.log(f"[{'changed' if self.apply_changes else 'would'}] {msg}")

    def fine(self, msg):
        self.ok += 1
        self.log(f"[ok] {msg}")

    def duplicate(self, msg):
        self.duplicates += 1
        self.log(f"[duplicate] {msg}")

    def problem(self, msg):
        self.problems += 1
        self.log(f"[problem] {msg}")


def hook_key(command):
    """(script basename, arguments) of a hook command, so `"$HOME/.claude/x.sh" a`,
    `/abs/path/x.sh a` and `"$CLAUDE_PROJECT_DIR/.claude/x.sh" a` are one hook."""
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    if not words:
        return ("", ())
    return (os.path.basename(words[0]), tuple(words[1:]))


WANTED_KEYS = {(ev, hook_key(cmd)) for ev, _m, cmd, _t in HOOKS}


def hooks_of(settings, event):
    """Every hook key under settings["hooks"][event], across all its groups."""
    out = set()
    groups = settings.get("hooks", {}).get(event, []) if isinstance(settings.get("hooks"), dict) else []
    for group in groups if isinstance(groups, list) else []:
        for h in group.get("hooks", []) if isinstance(group, dict) else []:
            if isinstance(h, dict) and isinstance(h.get("command"), str):
                out.add(hook_key(h["command"]))
    return out


# ── 1. shared root ───────────────────────────────────────────────────────────

def source_of(aur, key):
    return aur / aurshare.SHARED_ASSETS[key].rstrip("/")


def step_shared_root(aur, share, rep):
    """Make the shared-root assets present. Returns, for `check`, the stable names
    that are absent and would be linked (so the later steps can judge their targets)."""
    pending = set()
    for key in SHARED_KEYS:
        name = key.rstrip("/")
        stable, src = share / name, source_of(aur, key)
        if stable.is_symlink() and not stable.exists():
            rep.problem(f"shared root {stable} is a dangling link to {os.readlink(stable)} - left in "
                        f"place; `python3 aurshare.py devlink|deploy` repairs it")
        elif stable.exists() or stable.is_symlink():
            rep.fine(f"shared root {stable} present (left as it is)")
        elif not src.exists():
            rep.problem(f"shared root lacks {name} and the checkout has no {src} to link it to")
        elif rep.apply_changes:
            if aurshare.devlink(aur, [key], log=lambda m: None, share=share) == 1:
                rep.change(f"shared root {stable} -> {src} (absent; linked to this checkout)")
            else:
                rep.problem(f"shared root {stable}: devlink linked nothing for {name} from {src}")
        else:
            pending.add(name)
            rep.change(f"shared root {stable} -> {src} (absent; would link to this checkout)")
    return pending


# ── 2. links in ~/.claude ────────────────────────────────────────────────────

def step_links(home, aur, share, pending, rep):
    for rel_link, rel_target in USER_LINKS:
        link, target = home / ".claude" / rel_link, share / rel_target
        head, _, tail = rel_target.partition("/")
        will_exist = target.exists() or (head in pending and (source_of(aur, head + "/") / tail).exists())
        if link.is_symlink():
            have = os.readlink(link)
            if not link.exists():
                if not will_exist:
                    rep.problem(f"{link} is a dangling link to {have} and {target} does not exist either")
                    continue
                if rep.apply_changes:
                    link.unlink()
                    link.symlink_to(target)
                rep.change(f"{link}: dangling link to {have} repointed -> {target}")
            elif os.path.realpath(link) == os.path.realpath(target):
                rep.fine(f"{link} -> {have}" + ("" if have == str(target) else
                         f" (same file as {target}; left as it is)"))
            else:
                rep.problem(f"{link} links to {have}, not to {target} - left in place; "
                            f"remove it by hand if the shared-root file is the one you want")
        elif link.exists():
            rep.problem(f"{link} is a real file, not a link to {target} - left in place; "
                        f"it will not receive updates")
        elif not will_exist:
            rep.problem(f"{link} not created: the shared root has no {target} - its {head} predates "
                        f"the token-economy files; re-run `python3 aurshare.py deploy|devlink` "
                        f"from a current checkout")
        else:
            if rep.apply_changes:
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(target)
            rep.change(f"{link} -> {target}")


# ── 3. settings.json ─────────────────────────────────────────────────────────

def load_json_object(path):
    """(dict, None) | (None, reason). A missing file is an empty object."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, None
    except OSError as e:
        return None, f"cannot read it ({e})"
    try:
        data = json.loads(text)
    except ValueError as e:
        return None, f"not valid JSON ({e})"
    if not isinstance(data, dict):
        return None, f"top level is {type(data).__name__}, not an object"
    return data, None


def merge_settings(settings, rep, where):
    """Add what is missing to `settings` in place, reporting each item."""
    env = settings.get("env")
    if env is None:
        env = {}
    if not isinstance(env, dict):
        rep.problem(f"{where}: env is {type(env).__name__}, not an object - env entries not added")
    else:
        for key, want in ENV.items():
            if key not in env:
                env[key] = want
                settings["env"] = env
                rep.change(f"{where}: env.{key} = {want!r}")
            elif env[key] == want:
                rep.fine(f"{where}: env.{key} = {want!r}")
            else:
                rep.fine(f"{where}: env.{key} = {env[key]!r} is the user's own value; kept "
                         f"(the component's is {want!r})")

    hooks = settings.get("hooks")
    if hooks is None:
        hooks = {}
    if not isinstance(hooks, dict):
        rep.problem(f"{where}: hooks is {type(hooks).__name__}, not an object - hook entries not added")
        return
    for event, matcher, command, timeout in HOOKS:
        key = hook_key(command)
        if key in hooks_of({"hooks": hooks}, event):
            rep.fine(f"{where}: hooks.{event} already runs {key[0]}" + (f" {' '.join(key[1])}" if key[1] else ""))
            continue
        groups = hooks.get(event)
        if groups is None:
            groups = hooks[event] = []
        if not isinstance(groups, list):
            rep.problem(f"{where}: hooks.{event} is {type(groups).__name__}, not a list - not added")
            continue
        group = {"hooks": [{"type": "command", "command": command, "timeout": timeout}]}
        if matcher is not None:
            group = {"matcher": matcher, **group}
        groups.append(group)
        settings["hooks"] = hooks
        rep.change(f"{where}: hooks.{event}" + (f"[{matcher}]" if matcher else "") + f" += {command}")


def write_settings(path, data):
    """Back up the old file (once per change), then replace it atomically in the
    format Claude Code itself writes: two-space indent, UTF-8, trailing newline.
    A symlinked settings.json is written THROUGH: the backup, the temp file and the
    replacement all live beside the link's target, and the link is left as it was."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + time.strftime(".bak-%Y%m%d-%H%M%S")))
    tmp = path.with_name(path.name + ".tmp-tokeneconomy")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if path.exists():
        shutil.copymode(path, tmp)
    os.replace(tmp, path)


def step_settings(home, rep):
    path = home / ".claude" / "settings.json"
    if path.is_symlink() and not path.exists():       # dotfiles not cloned yet, drive not mounted
        rep.problem(f"{path} is a dangling link to {os.readlink(path)} - left in place, nothing "
                    f"written and no directory created; restore its target, then re-run")
        return
    old, why = load_json_object(path)
    if old is None:
        rep.problem(f"{path} is {why} - left untouched; fix it, then re-run")
        return
    new = copy.deepcopy(old)
    merge_settings(new, rep, str(path))
    if new != old and rep.apply_changes:
        write_settings(path, new)


# ── 4. duplicates in registered projects ─────────────────────────────────────

def step_duplicates(share, rep):
    seen = set()
    for row in aurshare.registry_read(share):
        proj = Path(row["path"])
        if str(proj) in seen or not proj.is_dir():
            continue
        seen.add(str(proj))
        for fname in SETTINGS_FILES_OF_A_PROJECT:
            path = proj / ".claude" / fname
            if not path.exists():
                continue
            data, why = load_json_object(path)
            if data is None:
                rep.fine(f"{path} is {why}; not checked for duplicates")
                continue
            for event, _m, command, _t in HOOKS:
                if hook_key(command) in hooks_of(data, event):
                    rep.duplicate(f"{path}: hooks.{event} already runs {hook_key(command)[0]}"
                                  + (f" {' '.join(hook_key(command)[1])}" if hook_key(command)[1] else "")
                                  + " - with the user-level entry it fires twice in that project; "
                                  "remove one (project file left untouched)")
            penv = data.get("env") if isinstance(data.get("env"), dict) else {}
            for key, want in ENV.items():
                if key in penv:
                    same = "same value" if penv[key] == want else f"value {penv[key]!r}, overriding {want!r}"
                    rep.duplicate(f"{path}: env.{key} also set ({same}) - the project's wins there "
                                  "(project file left untouched)")


# ── driver ───────────────────────────────────────────────────────────────────

def run(aur, home, share, apply_changes=True, log=print):
    """Run every step; return the Report. `apply_changes` False writes nothing."""
    aur, home, share = Path(aur), Path(home), Path(share)
    rep = Report(apply_changes, log)
    if not (aur / "hooks" / "supervisor-bash-guard.py").is_file():
        rep.problem(f"{aur} is not an AUR checkout (no hooks/supervisor-bash-guard.py); nothing done")
        return rep
    pending = step_shared_root(aur, share, rep)
    step_links(home, aur, share, pending, rep)
    step_settings(home, rep)
    step_duplicates(share, rep)
    return rep


def summary(rep):
    pending = "would change" if not rep.apply_changes else "changed"
    tail = (f"{rep.ok} in place, {rep.duplicates} duplicate(s) reported, "
            f"{rep.problems} problem(s)")
    if rep.changed == 0:
        return f"token-economy: no change ({tail})"
    return f"token-economy: {rep.changed} {pending} ({tail})"


def build_parser():
    ap = argparse.ArgumentParser(
        prog="tokeneconomy.py",
        description="Wire AUR's token-economy hooks, agent and settings into a user's ~/.claude "
                    "(idempotent; see the module docstring).")
    ap.add_argument("action", choices=("install", "check"),
                    help="install: make it so; check: report what install would change, write nothing")
    # Knobs. Each names its scope and why the process cannot infer it (AUR P16).
    ap.add_argument("--home", metavar="DIR",
                    help="account to wire (default: $HOME). Scope: this run. A process cannot tell "
                         "that the account being provisioned is not its own - tests and "
                         "provisioning another user need this.")
    ap.add_argument("--share", metavar="DIR",
                    help="shared root (default: $AUR_SHARE_ROOT, else <home>/.local/aur). Scope: "
                         "this run. Only an unconventional location needs it.")
    ap.add_argument("--aur", metavar="DIR",
                    help="AUR checkout the absent shared-root assets are linked to (default: the "
                         "checkout this file is in). Scope: this run.")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    home = Path(args.home).expanduser() if args.home else Path.home()
    share = (Path(args.share).expanduser() if args.share
             else Path(os.environ["AUR_SHARE_ROOT"]).expanduser() if os.environ.get("AUR_SHARE_ROOT")
             else home / ".local" / "aur")
    aur = Path(args.aur).expanduser() if args.aur else Path(__file__).resolve().parent
    print(f"token-economy {args.action}: home={home} shared-root={share} aur={aur}")
    rep = run(aur, home, share, apply_changes=(args.action == "install"), log=print)
    print(summary(rep))
    if rep.problems:
        return 1
    if args.action == "check" and rep.changed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
