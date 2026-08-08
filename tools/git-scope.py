#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
git-scope — commit exactly the files this unit of work touched, and nothing else.

The git index is shared between concurrent sessions. A bare `git commit` sweeps
up whatever another session happened to have staged, and the mistake is invisible
until someone reads the commit. Path-limiting is the usual answer, but
`git commit -- <paths>` has its own trap: it commits WORKTREE state for those
paths and ignores the index, so a staged `git rm --cached` deletion is silently
dropped.

This records what was already dirty when the work began, so "mine" is a computed
set rather than a remembered one, then commits through the index with an
assertion that the index contains only that set.

    git-scope.py begin  [repo]          snapshot pre-existing dirt
    git-scope.py status [repo]          mine / theirs / ambiguous
    git-scope.py commit [repo] -F msg   stage mine, verify, commit
    git-scope.py end    [repo]          discard the marker

Ambiguous paths — dirty before AND touched since — are never guessed at. They
are reported, and the operator decides.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def git(repo, *args, check=False):
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(f"git {' '.join(args)} failed:\n{r.stderr}")
    return r.stdout


def dirty(repo):
    """Every path git considers changed: modified, staged, or untracked."""
    out = git(repo, "status", "--porcelain", "--untracked-files=all")
    paths = set()
    for line in out.splitlines():
        if not line.strip():
            continue
        p = line[3:]
        # Renames read "old -> new"; both ends matter.
        if " -> " in p:
            a, b = p.split(" -> ", 1)
            paths.update({a.strip('"'), b.strip('"')})
        else:
            paths.add(p.strip('"'))
    return paths


def marker(repo):
    return Path(repo) / ".git" / "aur-scope.json"


def load(repo):
    try:
        return set(json.loads(marker(repo).read_text())["baseline"])
    except (OSError, ValueError, KeyError):
        return None


def cmd_begin(repo, fresh=False):
    """Snapshot what is already dirty, so "mine" becomes computable.

    `--fresh` records an EMPTY baseline: everything currently dirty counts as
    yours. That is the escape hatch for the mistake this tool invites — running
    `begin` after the work rather than before, which silently classifies your own
    changes as somebody else's and then reports nothing to commit.
    """
    base = [] if fresh else sorted(dirty(repo))
    marker(repo).write_text(json.dumps({"baseline": base}, indent=2))
    print(f"git-scope: baseline recorded — {len(base)} path(s) already dirty")
    for p in base[:20]:
        print(f"    theirs: {p}")
    if len(base) > 20:
        print(f"    … and {len(base) - 20} more")
    return 0


def classify(repo):
    base = load(repo)
    if base is None:
        return None
    now = dirty(repo)
    return {"mine": sorted(now - base),
            "theirs": sorted(base & now),
            "gone": sorted(base - now)}


def cmd_status(repo, quiet=False):
    c = classify(repo)
    if c is None:
        print("git-scope: no baseline — run `begin` first", file=sys.stderr)
        return 2
    if not quiet:
        print(f"git-scope: {len(c['mine'])} mine, {len(c['theirs'])} theirs "
              f"(pre-existing, untouched)")
        for p in c["mine"]:
            print(f"    mine:   {p}")
        for p in c["theirs"]:
            print(f"    theirs: {p}")
        if c["gone"]:
            print(f"\n  {len(c['gone'])} path(s) that were dirty at begin are now "
                  f"clean — AMBIGUOUS: someone committed or reverted them, or you\n"
                  f"  did. Resolve before committing:")
            for p in c["gone"]:
                print(f"    ?       {p}")
    return 0


def cmd_commit(repo, msg_file, no_verify):
    c = classify(repo)
    if c is None:
        print("git-scope: no baseline — run `begin` first", file=sys.stderr)
        return 2
    if not c["mine"]:
        print("git-scope: nothing of mine to commit")
        return 0

    # Stage exactly mine. `git add -A -- <paths>` records deletions too, which a
    # bare `git add <paths>` does not.
    git(repo, "add", "-A", "--", *c["mine"], check=True)

    # The index must now contain ONLY mine. Anything else is another session's
    # staged work, and committing it is the failure this tool exists to prevent.
    staged = set()
    for line in git(repo, "diff", "--cached", "--name-only").splitlines():
        if line.strip():
            staged.add(line.strip())
    extra = staged - set(c["mine"])
    if extra:
        print("git-scope: REFUSING — the index holds paths outside this scope:",
              file=sys.stderr)
        for p in sorted(extra):
            print(f"    {p}", file=sys.stderr)
        print("\n  Another session staged these. Unstage them, or commit from "
              "that session.", file=sys.stderr)
        return 1

    args = ["commit", "-F", msg_file]
    if no_verify:
        args.append("--no-verify")
    r = subprocess.run(["git", "-C", str(repo), *args])
    if r.returncode:
        return r.returncode
    print(f"git-scope: committed {len(c['mine'])} path(s); "
          f"{len(c['theirs'])} left untouched")
    marker(repo).unlink(missing_ok=True)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["begin", "status", "commit", "end"])
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("-F", "--file", help="commit message file ('-' for stdin)")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--fresh", action="store_true",
                    help="empty baseline: treat everything dirty as mine "
                         "(use when the tree was clean before you started)")
    a = ap.parse_args()

    repo = Path(a.repo).expanduser().resolve()
    if not (repo / ".git").is_dir():
        print(f"git-scope: not a git repo: {repo}", file=sys.stderr)
        return 2

    if a.cmd == "begin":
        return cmd_begin(repo, a.fresh)
    if a.cmd == "status":
        return cmd_status(repo)
    if a.cmd == "end":
        marker(repo).unlink(missing_ok=True)
        print("git-scope: marker discarded")
        return 0
    if a.cmd == "commit":
        if not a.file:
            print("git-scope: commit needs -F <file|->", file=sys.stderr)
            return 2
        path = a.file
        if path == "-":
            tmp = repo / ".git" / "aur-scope-msg.txt"
            tmp.write_text(sys.stdin.read())
            path = str(tmp)
        return cmd_commit(repo, path, a.no_verify)


if __name__ == "__main__":
    sys.exit(main())
