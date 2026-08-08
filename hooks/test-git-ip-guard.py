#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
test-git-ip-guard — executable decision table for the patent-IP PreToolUse guard.

The guard is a security control whose two failure modes are both silent: deny
what should pass and work stops for reasons nobody attributes to the hook; allow
what should fail and a protected repository is published. Neither surfaces until
it matters, so the decision table is executable rather than written down.

HERMETIC BY CONSTRUCTION. It builds its own fixture repositories and its own
whitelist under a temporary HOME, and never reads the developer's tree. The
first version of this file pointed at real local projects, and the content guard
refused to commit it — correctly, since this repository is public and that
version named private projects and their absolute paths. A test that cannot be
published is a test that cannot ship with the thing it tests.

    python3 hooks/test-git-ip-guard.py

Exit 0 all pass, 1 any failure.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

GUARD = Path(__file__).resolve().parent / "git-ip-guard.sh"


def git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def build_fixtures(root):
    """Two repos and a whitelist, mirroring the real posture:

      protected/  — on-box 'router' remote only; must never reach the network
      public/     — whitelisted, with an off-box 'origin'; may publish
    """
    home = root / "home"
    (home / ".claude").mkdir(parents=True)
    bare = root / "backup.git"
    git("init", "--bare", "-q", str(bare))

    repos = {}
    for name, remotes in (
        ("protected", {"router": str(bare)}),
        ("public", {"router": str(bare),
                    "origin": "https://example.invalid/x/y.git"}),
    ):
        d = root / name
        d.mkdir()
        git("init", "-q", "-b", "main", str(d))
        git("config", "user.email", "t@t", cwd=d)
        git("config", "user.name", "t", cwd=d)
        (d / "f.txt").write_text("x\n")
        git("add", "f.txt", cwd=d)
        git("commit", "-q", "--no-verify", "-m", "seed", cwd=d)
        for rname, url in remotes.items():
            git("remote", "add", rname, url, cwd=d)
        repos[name] = d

    (home / ".claude" / "git-public-projects.txt").write_text(
        f"# fixture whitelist\n{repos['public']}\n")
    return home, repos


# (repo key, command template, expect_allow, why)
CASES = [
    # ── the regression the target-resolving rewrite exists for ───────────────
    ("protected", "git push -u router main", True,
     "(regression) a 'router' remote is a local path on a mounted drive — a "
     "backup, not a disclosure. Previously DENIED, which blocked the sanctioned "
     "backup for every protected repo."),
    ("protected", "git push router HEAD --no-verify", True,
     "(regression) the backup script pushes with --no-verify by design; the "
     "target is on-box so the network rule never engages"),
    ("protected", "cat .git/hooks/pre-push", True,
     "(regression) reading the hook is not circumventing it"),
    ("protected", "cp /tmp/pre-push .git/hooks/pre-push", True,
     "(regression) INSTALLING the hook must not be mistaken for bypassing it — "
     "the literal 'hooks/pre-push' match refused administering the guard"),

    # ── the hole the rewrite closes ──────────────────────────────────────────
    ("public", "git fetch origin", True,
     "a whitelisted repo may talk to its public remote"),

    # ── publication must still be refused ────────────────────────────────────
    ("protected", "git push https://example.invalid/x/y.git main", False,
     "explicit off-box URL"),
    ("protected", "git push git@example.invalid:x/y.git main", False,
     "scp-style off-box URL"),
    ("protected", "git remote add origin https://example.invalid/x/y.git", False,
     "pointing a protected repo at a public remote"),
    ("protected", "gh repo create thing --public", False, "gh publishes"),
    ("protected", "gh pr list", False, "gh subcommand"),
    ("protected", "git push origin main", False,
     "unresolvable remote name must fail CLOSED, not open"),
    ("protected", "git ls-remote https://example.invalid/x/y.git", False,
     "off-box ls-remote"),
    ("protected", "git send-email HEAD~1", False,
     "send-email is off-box by nature"),

    # ── whitelist and circumvention ──────────────────────────────────────────
    ("public", "git push --no-verify origin main", False,
     "--no-verify on an off-box push demands explicit acknowledgement even for "
     "a whitelisted repo"),
    ("public", "git push origin main", True, "a whitelisted repo may publish"),
    ("public", "git push router main", True, "whitelisted repo, on-box backup"),

    # ── heredoc bodies are data, not command ─────────────────────────────────
    ("protected",
     "git commit -F - <<'EOF'\nExplain why git push --no-verify is refused\nEOF",
     True,
     "(regression) a commit MESSAGE describing a network op does not perform "
     "one. This refused the very commit that documented the rule."),
    ("protected",
     "git commit -F - <<'EOF'\nnotes\nEOF\ngit push origin main", False,
     "a real command AFTER the heredoc terminator is still in scope"),
    ("protected",
     "bash <<'EOF'\ngit push origin main\nEOF", False,
     "an interpreter's heredoc IS executed — stripping it would be a bypass "
     "with an obvious recipe"),

    # ── nothing to do with the network ───────────────────────────────────────
    ("protected", "git status --short", True, "local read"),
    ("protected", "git commit -m x", True, "local write"),
    ("protected", "git commit --no-verify -m x", True,
     "--no-verify on a NON-network op is the sanctioned test-gate escape and "
     "must not be confused with disclosure"),
    ("protected", "git log --oneline -5", True, "local read"),
    ("protected", "ls -la", True, "not a git command at all"),
]


def run(home, cwd, cmd):
    env = dict(os.environ, HOME=str(home))
    payload = json.dumps({"tool_input": {"command": cmd}, "cwd": str(cwd)})
    r = subprocess.run(["bash", str(GUARD)], input=payload, env=env,
                       capture_output=True, text=True, timeout=30)
    if not r.stdout.strip():
        return True, ""                       # no output = allow
    try:
        d = json.loads(r.stdout)
        o = d["hookSpecificOutput"]
        return o["permissionDecision"] != "deny", o["permissionDecisionReason"]
    except (ValueError, KeyError):
        return True, f"UNPARSEABLE: {r.stdout[:120]}"


def main():
    with tempfile.TemporaryDirectory(prefix="ipguard-") as tmp:
        home, repos = build_fixtures(Path(tmp))
        fails = 0
        for key, cmd, expect_allow, why in CASES:
            got_allow, reason = run(home, repos[key], cmd)
            ok = got_allow == expect_allow
            fails += 0 if ok else 1
            print(f"  [{'ok  ' if ok else 'FAIL'}] "
                  f"want={'ALLOW' if expect_allow else 'DENY '} "
                  f"got={'ALLOW' if got_allow else 'DENY '}  {key}: {cmd}")
            if not ok:
                print(f"         {why}")
                if reason:
                    print(f"         guard said: {reason[:160]}")

    print(f"\ntest-git-ip-guard: {len(CASES) - fails}/{len(CASES)} passed")
    if fails:
        print("  A failing case is not a test bug until proven otherwise — the "
              "guard is the thing under test.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
