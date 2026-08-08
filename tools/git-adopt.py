#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
git-adopt — pre-flight for putting an EXISTING directory of accumulated work
under git for the first time.

`git init && git add -A` is the wrong move on a directory that has been lived in.
By the time anyone thinks to track it, it holds four kinds of thing that must not
be committed, and every one of them commits silently:

    absolute symlinks   a symlink's TARGET is its tracked content, so an
                        absolute link writes a home path into the repository
    generated output    regenerable from a tracked generator; committing it
                        means every rebuild shows as a diff
    runtime state       ports, pids, sockets, caches — rewritten per run
    duplicates          the same bytes at two paths, one of which will go stale

...alongside the things that look disposable and are not. Review comments,
annotations and hand-written notes sit inside output directories and are the one
thing there that cannot be reconstructed. A blanket ignore of the directory takes
them with it.

So the question this tool asks of every path is the only one that matters —
DOES THIS REGENERATE, AND FROM WHAT? — and it reports the answer rather than
acting on it, because the residue is judgement.

    git-adopt.py <dir> [--write-gitignore] [--json]

Exit 0 clean, 1 findings to resolve, 2 usage error.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HOME = Path.home()

# Directories whose entire contents are rebuilt from tracked sources.
BUILD_DIRS = {"__pycache__", "node_modules", "target", "build", "dist",
              ".venv", ".gradle", ".pytest_cache", ".mypy_cache", ".ruff_cache",
              "venv", ".tox", ".next", ".cache"}

# Per-run state. A committed one is a diff on every invocation.
STATE_SUFFIX = {".port", ".pid", ".sock", ".lock", ".pyc", ".pyo", ".swp",
                ".log", ".tmp"}
STATE_NAMES = {".DS_Store", "Thumbs.db", "server.port"}

# The convention this doc system uses: <stem>-YYYY-MM-DD-HHMMSS.<ext>
TIMESTAMPED = re.compile(r"^(?P<stem>.+)-\d{4}-\d{2}-\d{2}-\d{6}\.[A-Za-z0-9]+$")

# Names that are review artifacts, not output, even when they sit under one of
# the generated trees. Committing the tree without these loses the only content
# in it a human wrote.
PRECIOUS_PARTS = {"annotations", "notes", "review", "comments"}


def sh(*args, cwd=None):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    return r.stdout.strip(), r.returncode


def walk(root):
    """Every path under root, excluding .git. Directories yielded too, so a
    build directory can be reported once instead of per-file."""
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        if ".git" in d.parts:
            dirnames[:] = []
            continue
        for name in sorted(dirnames):
            yield d / name
        for name in sorted(filenames):
            yield d / name


def rel(p, root):
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def classify(root):
    """Bucket every path by whether it regenerates, and from what."""
    f = {"abs_symlink": [], "rel_symlink": [], "build_dir": [], "state": [],
         "generated": [], "precious": [], "large": [], "hazard": [],
         "authored": []}
    dupes = defaultdict(list)
    covered = []          # build dirs already reported; skip their contents

    for p in walk(root):
        r = rel(p, root)

        if any(Path(r).parts[:i + 1] == Path(c).parts
               for c in covered for i in range(len(Path(r).parts))):
            continue

        if p.is_symlink():
            tgt = os.readlink(p)
            entry = (r, tgt)
            if os.path.isabs(tgt):
                f["abs_symlink"].append(entry)
            else:
                f["rel_symlink"].append(entry)
            continue

        if p.is_dir():
            if p.name in BUILD_DIRS:
                f["build_dir"].append((r, f"{p.name}/ is rebuilt from source"))
                covered.append(r)
            continue

        if p.name in STATE_NAMES or p.suffix in STATE_SUFFIX:
            f["state"].append((r, f"per-run state ({p.suffix or p.name})"))
            continue

        # Review artifacts win over the generated-tree heuristic below.
        if PRECIOUS_PARTS & set(Path(r).parts):
            f["precious"].append((r, "hand-authored inside a generated tree — "
                                     "must survive any blanket ignore"))
            continue

        m = TIMESTAMPED.match(p.name)
        if m:
            f["generated"].append((r, f"timestamped output of '{m.group('stem')}'"))
            continue

        try:
            b = p.read_bytes()
        except OSError:
            continue

        if len(b) > 5_000_000:
            f["large"].append((r, f"{len(b) // 1_000_000} MB"))

        if b.count(0):
            f["hazard"].append((r, "contains NUL bytes — grep will treat this as "
                                   "binary and SILENTLY suppress every match"))
        else:
            try:
                b.decode("utf-8")
            except UnicodeDecodeError as e:
                f["hazard"].append((r, f"invalid UTF-8 at byte {e.start}"))

        dupes[hashlib.md5(b).hexdigest()].append(r)
        f["authored"].append((r, ""))

    f["duplicate"] = [v for v in dupes.values() if len(v) > 1]
    return f


def posture(root):
    """Where this repo stands on the two policy lists. A repo on neither is
    the real hazard: not publishable, and also not backed up."""
    out = {}
    for key, path in (("public", ".claude/git-public-projects.txt"),
                      ("backup", ".claude/git-backup-projects.txt")):
        listed, lf = False, HOME / path
        if lf.is_file():
            for line in lf.read_text().splitlines():
                line = line.split("#", 1)[0].strip()
                if line and (str(root) == line or str(root).startswith(line + "/")):
                    listed = True
        out[key] = listed
    return out


def propose_gitignore(f):
    """A .gitignore carrying the REASON for each rule, because a bare pattern is
    re-litigated by the next person who wonders why their file vanished."""
    L = []
    if f["generated"]:
        # Scope by (directory, extension), never by directory alone. A bare
        # `<dir>/*` also swallows anything hand-written that later lands beside
        # the output, and it does so silently.
        pats = sorted({f"{Path(r).parent}/*{Path(r).suffix}".lstrip("./")
                       for r, _ in f["generated"]})
        L += ["# Generated output. The generator is tracked; its output is not.",
              *pats, ""]
    if f["precious"]:
        L += ["# NOT generated: review comments, and the one thing under the",
              "# output tree that cannot be reconstructed. Negated back in, so a",
              "# later broadening of the rule above cannot silently drop them.",
              *[f"!{d}/" for d in
                sorted({str(Path(r).parent) for r, _ in f["precious"]})], ""]
    if f["abs_symlink"]:
        L += ["# ABSOLUTE symlinks: a symlink's target is its tracked content, so",
              "# committing these writes a home path into the repository. They are",
              "# regenerable output — track the generator instead.",
              *[r for r, _ in f["abs_symlink"]], ""]
    if f["state"]:
        L += ["# Per-run state.", *sorted({r for r, _ in f["state"]}), ""]
    if f["build_dir"]:
        L += ["# Rebuilt from source.", *[f"{r}/" for r, _ in f["build_dir"]], ""]
    return "\n".join(L).rstrip() + "\n" if L else ""


def report(root, f, pos, already_repo):
    n = 0
    def block(title, items, why):
        nonlocal n
        if not items:
            return
        n += len(items)
        print(f"\n  {title}  ({len(items)})")
        print(f"    {why}")
        for r, note in items[:12]:
            print(f"      {r}" + (f"\n          {note}" if note else ""))
        if len(items) > 12:
            print(f"      … and {len(items) - 12} more")

    print(f"git-adopt: {root}")
    print(f"  {'EXISTING repo — reporting only' if already_repo else 'not yet a git repo'}")
    print(f"  public whitelist: {'LISTED (network ops permitted)' if pos['public'] else 'not listed (default-deny — network ops refused)'}")
    print(f"  backup list:      {'listed' if pos['backup'] else 'NOT LISTED'}")

    block("DO NOT COMMIT — absolute symlinks", f["abs_symlink"],
          "the target string becomes tracked content, publishing a home path")
    block("HAZARD — breaks search and mechanical edits", f["hazard"],
          "resolve BEFORE any grep-driven pass; these make a search lie")
    block("Generated output", f["generated"],
          "regenerable; the generator is what belongs in the repo")
    block("Per-run state", f["state"], "a diff on every invocation")
    block("Rebuilt directories", f["build_dir"], "output of a tracked source")
    block("KEEP — not generated", f["precious"],
          "hand-authored inside an output tree; a blanket ignore would drop these")
    block("Relative symlinks", f["rel_symlink"],
          "safe to track IF the layout they assume is guaranteed")
    block("Large files", f["large"], "consider whether these belong in git history")

    if f["duplicate"]:
        n += len(f["duplicate"])
        print(f"\n  DUPLICATE CONTENT  ({len(f['duplicate'])})")
        print("    identical bytes at two paths — one WILL go stale. Keep the copy")
        print("    the toolchain actually reads, and delete the other.")
        for group in f["duplicate"][:8]:
            print("      " + "\n      = ".join(group))

    print(f"\n  {len(f['authored'])} authored file(s) to track.")
    if not pos["backup"] and not pos["public"]:
        n += 1
        print("\n  NO POSTURE: on neither list. Not publishable and not backed up —")
        print("  add it to ~/.claude/git-backup-projects.txt, or it is tracked")
        print("  history that nothing preserves.")
    return n


def main():
    ap = argparse.ArgumentParser(
        prog="git-adopt.py", add_help=True,
        description="Pre-flight before putting an existing directory under git.")
    ap.add_argument("dir", help="directory to inspect")
    ap.add_argument("--write-gitignore", action="store_true",
                    help="write the proposed .gitignore (refuses to overwrite)")
    ap.add_argument("--json", action="store_true", help="machine-readable findings")
    a = ap.parse_args()

    root = Path(a.dir).expanduser().resolve()
    if not root.is_dir():
        print(f"git-adopt: not a directory: {root}", file=sys.stderr)
        return 2

    f = classify(root)
    pos = posture(root)
    already = (root / ".git").is_dir()

    if a.json:
        print(json.dumps({"root": str(root), "posture": pos,
                          "already_repo": already, "findings": f}, indent=2))
        return 0

    n = report(root, f, pos, already)
    gi = propose_gitignore(f)

    if a.write_gitignore:
        target = root / ".gitignore"
        if target.exists():
            print(f"\ngit-adopt: REFUSING — {target} exists; merge by hand",
                  file=sys.stderr)
            return 1
        target.write_text(gi)
        print(f"\ngit-adopt: wrote {target}")
    elif gi:
        print("\n  ── proposed .gitignore (--write-gitignore to apply) " + "─" * 20)
        for line in gi.splitlines():
            print("  " + line)

    print(f"\ngit-adopt: {n} item(s) needing a decision"
          if n else "\ngit-adopt: clean")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
