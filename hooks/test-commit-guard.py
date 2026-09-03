#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
test-commit-guard — executable decision table for the content guard's CEILING.

The rule (ai_common.md, PII): git's world ends at the project's own path, and
everything above it is PII by construction — the user's home, the machine's
layout, and the project's own absolute location. No directory class is exempt
and no file is allowlisted: the places a scanner is told to skip are exactly
where the path gets in, and on 2026-09-03 they were dated records, memory,
generated documents and "user-level config" files, every one a hole.

HERMETIC BY CONSTRUCTION: a fake HOME and a fixture repository under a temp
dir, never the developer's tree — and the fixture repository is placed OUTSIDE
the fake HOME on purpose, so the project-path pattern is exercised on its own.

    python3 hooks/test-commit-guard.py
Exit 0 all pass, 1 any failure.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

GUARD = Path(__file__).resolve().parent.parent / "tools" / "commit-guard.py"


def git(*args, cwd, env):
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                   capture_output=True, text=True)


def run_guard(repo, env):
    r = subprocess.run([sys.executable, str(GUARD), str(repo)], env=env,
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def main():
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        fake_home = Path(tmp) / "home" / "someone"
        fake_home.mkdir(parents=True)
        repo = Path(tmp) / "elsewhere" / "proj"     # NOT under the fake HOME
        repo.mkdir(parents=True)
        env = dict(os.environ, HOME=str(fake_home),
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@localhost",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@localhost")
        git("init", "-q", cwd=repo, env=env)
        (repo / "README").write_text("seed\n")
        git("add", "README", cwd=repo, env=env)
        git("commit", "-q", "-m", "seed", cwd=repo, env=env)

        home_path = f"{fake_home}/photos/holiday/IMG_0001.jpg"
        proj_path = f"{repo}/rust/enhance/src/pipeline.rs"

        # (relative path, content, must the guard flag it?)
        cases = [
            ("src/main.rs", "let root = \"src/lib\";\n", False),
            ("docs/notes.md", "the worktree is the ladder worktree (branch k7)\n", False),
            # Every one of these directories was on the guard's skip list.
            ("aur-docs/plan-2026-09-03.html", f"<p>see {home_path}</p>\n", True),
            (".claude/memory/project_x.md", f"corpus at {home_path}\n", True),
            (".claude/checkpoints/session_2026-09-03.md", f"ran {home_path}\n", True),
            (".claude/pending-insights.md", f"- saw {home_path}\n", True),
            ("tools/rule-tests/sample_017.txt", f"assistant: {home_path}\n", True),
            # Every one of these names was on the guard's file allowlist.
            ("install", f"cp x {home_path}\n", True),
            ("docs/shared-root-design.md", f"lives at {home_path}\n", True),
            # The project's OWN absolute path is above the ceiling too — and
            # this repo is outside the fake HOME, so only a project-path
            # pattern can catch it.
            ("research/brief.md", f"Worktree: {proj_path}\n", True),
            (".claude/briefs/phase.md", f"never touch {repo}/scratch\n", True),
        ]
        for rel, content, must_flag in cases:
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            git("add", rel, cwd=repo, env=env)
            rc, out = run_guard(repo, env)
            flagged = rc == 1 and rel in out
            ok = flagged == must_flag
            print(f"  {'PASS' if ok else 'FAIL'}  {rel}: "
                  f"{'flagged' if flagged else 'clean'} "
                  f"({'must flag' if must_flag else 'must pass'})")
            if not ok:
                failures.append(rel)
            git("reset", "-q", "HEAD", "--", rel, cwd=repo, env=env)
            p.unlink()
    print(f"\n{'FAIL' if failures else 'PASS'} — "
          f"{len(failures)} case(s) failed" if failures else
          "\nPASS — the ceiling holds in every tracked path")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
