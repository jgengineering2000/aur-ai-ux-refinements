#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
commit-guard — check staged content for PII and cross-project leakage.

Written because the alternative is a manual audit before every commit, and a
check performed by remembering to perform it is one that eventually is not.

    python3 tools/commit-guard.py <repo>            # staged content (default)
    python3 tools/commit-guard.py <repo> --all      # whole worktree
    python3 tools/commit-guard.py <repo> --quiet    # exit code only

Exit 0 clean, 1 findings, 2 usage. Two classes of finding:

  PII / external data — absolute home paths, addresses, and references into the
  user's personal tree. The policy is not "no personal information in obvious
  places" but "data sourced from outside the project tree, AND ITS DERIVATIVES —
  manifests, indexes, paths, basenames — never lands in a commit." Basenames are
  the ones that get missed, because they look like ordinary strings.

  Cross-project leakage — a file owned by shared infrastructure that names one
  consuming project. Shared code that knows a project's name has already forked;
  it just has not been noticed yet.

Both are reported with file:line so the finding is actionable rather than a
verdict. Historical records are exempt: a dated checkpoint states what was true
then, and rewriting it to satisfy a scanner is falsification.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

HOME = str(Path.home())
USER = Path.home().name

# Personal tree: directories whose contents are the user's, never the project's.
PERSONAL_DIRS = ("Pictures", "Documents", "Downloads", "Desktop", "Videos",
                 "Music", "git-backups", ".ssh", ".gnupg")

PII = [
    ("absolute home path", re.compile(rf"{re.escape(HOME)}/")),
    ("home path (~ form)", re.compile(rf"/home/{re.escape(USER)}\b")),
    # Extension ids (foo@ubuntu.com, bar@gnome.org) share the email shape and
    # are not addresses; excluding the known suffixes beats a cleverer regex.
    # An scp-style git remote (git@host:org/repo.git) has the same shape too,
    # and appears legitimately in any code that talks about remotes — flagging
    # it is the cry-wolf failure that teaches people to skip the guard. The
    # trailing ':' is what distinguishes a host from a mailbox.
    ("email address",
     re.compile(r"(?![\w.+-]+@(?:ubuntu\.com|gnome\.org|localhost)\b)"
                r"(?<![\w.+-])(?!git@[\w.-]+:)"
                r"[\w.+-]+@[\w-]+\.[\w.]{2,}")),
    ("personal tree reference",
     re.compile(rf"[~/]\s*(?:{'|'.join(PERSONAL_DIRS)})/")),
    ("private IPv4", re.compile(r"\b(?:192\.168|10\.\d+|172\.(?:1[6-9]|2\d|3[01]))\.\d+\.\d+\b")),
]

# Paths whose CONTENT is a dated record. A scanner that forces edits here is
# asking for the record to be falsified.
EXEMPT = ("checkpoints/", "checkpoints-archive/", "/rule-tests/",
          "pending-insights.md", "/.claude/projects/", "aur-docs/",
          "claude-docs/", "/memory/", "transcript")

# Files that legitimately carry a home path because they ARE user-level config
# or documentation of it.
EXEMPT_PII_FILES = ("commit-guard.py", "aurshare.py", "debrand.py",
                    "shared-root-design.md", "git-backup", "install")


def tracked_projects():
    """Consuming project names, from the shared-root registry."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import aurshare
        return [Path(r["path"]).name for r in aurshare.registry_read()]
    except Exception:
        return []


def added_lines(repo, mode):
    """Lines this change ADDS, as {path: [(lineno, text), ...]}.

    Scanning whole files was the first version and it was wrong: every commit
    reported the same pre-existing findings, and a guard that cries wolf on
    legacy content is one you learn to skip. Only what the change introduces is
    actionable; what was already there is a separate, deliberate cleanup.
    """
    args = ["git", "-C", repo, "diff", "--unified=0"]
    if mode == "staged":
        args += ["--cached"]
    elif mode.startswith("range:"):
        # Everything a push would introduce. The pre-push hook uses this: the
        # commit-time scan is bypassable with --no-verify, and this stage is
        # where the content stops being recoverable by a cheap history rewrite.
        # Split, because a new branch's range is multi-token
        # ("<oid> --not --remotes=router"), not a single A..B revision.
        args += mode[len("range:"):].split()
    else:
        args += ["HEAD"]
    out = subprocess.run(args, capture_output=True, text=True).stdout
    files, cur, lineno = {}, None, 0
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:]
            files.setdefault(cur, [])
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            lineno = int(m.group(1)) if m else 0
        elif line.startswith("+") and not line.startswith("+++") and cur:
            files[cur].append((lineno, line[1:]))
            lineno += 1
    return {k: v for k, v in files.items() if v}


def scan(repo, added, projects, log):
    findings = []
    repo_name = Path(repo).name
    for rel, lines in added.items():
        if any(k in f"/{rel}" for k in EXEMPT):
            continue
        pii_exempt = any(k in rel for k in EXEMPT_PII_FILES)
        # Shared infrastructure: anything AUR owns and every project inherits.
        shared = repo_name == "aur" and not rel.startswith((".claude/", "aur-docs/"))

        for n, line in lines:
            if not pii_exempt:
                for label, pat in PII:
                    if pat.search(line):
                        findings.append(("PII", rel, n, label, line.strip()[:100]))
                        break
            if shared:
                for proj in projects:
                    if proj in ("aur",) or len(proj) < 3:
                        continue
                    if re.search(rf"\b{re.escape(proj)}\b", line, re.I):
                        findings.append(("LEAK", rel, n,
                                         f"names project '{proj}'",
                                         line.strip()[:100]))
                        break
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--all", action="store_true", help="whole worktree, not just staged")
    ap.add_argument("--range", metavar="A..B",
                    help="everything a push would introduce (pre-push hook)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    if a.all and a.range:
        print("commit-guard: --all and --range are mutually exclusive",
              file=sys.stderr)
        return 2

    repo = str(Path(a.repo).expanduser().resolve())
    if not (Path(repo) / ".git").is_dir():
        print(f"commit-guard: not a git repo: {repo}", file=sys.stderr)
        return 2

    mode = f"range:{a.range}" if a.range else ("all" if a.all else "staged")
    added = added_lines(repo, mode)

    # An empty staged set is not a pass. Reporting "clean" here is the exact
    # failure this tool exists to prevent — a guard that examined nothing and
    # said so in the vocabulary of approval. It happens routinely, because
    # git-scope.py does not stage until commit time, so the documented
    # "guard, then commit" order runs the guard against an empty index.
    if not added and mode == "staged":
        print("commit-guard: NOTHING STAGED — this is not a clean result, it is "
              "no result.\n  Stage the work first (`git add -A -- <paths>`), or "
              "pass --all to scan the\n  worktree. git-scope.py stages at commit "
              "time, so run the guard after\n  staging, not before.",
              file=sys.stderr)
        return 2

    projects = tracked_projects()
    findings = scan(repo, added, projects, print)

    if not a.quiet:
        n_lines = sum(len(v) for v in added.values())
        print(f"commit-guard: {Path(repo).name} — {len(added)} file(s), "
              f"{n_lines} added line(s), {mode}")
        if not findings:
            print("  clean — no PII, no external-data references, no cross-project leakage")
        else:
            for kind, rel, n, label, text in findings:
                print(f"  [{kind}] {rel}:{n}  {label}")
                print(f"         {text}")
            print(f"\n  {len(findings)} finding(s). Each is a location, not a verdict — "
                  f"read it before acting.")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
