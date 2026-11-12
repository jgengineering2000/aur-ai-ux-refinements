#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
De-brand AUR: remove one vendor's name from artifacts that are AUR's own.

AUR enables capabilities for Claude, Gemini, and anything else that can use them.
Nothing about its implementation, layout, format, links or appearance should carry
a particular vendor's name. What a vendor imposes — `~/.claude/`, `CLAUDE.md`,
`.claude/skills/` — is left alone, because those paths are read by the vendor and
are not ours to name.

Dry run by default. `--apply` performs it.

    python3 tools/debrand.py            # show every change, touch nothing
    python3 tools/debrand.py --apply

Renames of tracked files use `git mv` so history follows. The one coupling that
matters: `open-manifest.sh` builds PROJECT_ID as "<dir>/claude-docs" and compares
it against the subtitle `server.py` reports from `/meta`. Both are rewritten in the
same pass — drift there makes every project fail its own identity check and start a
duplicate server.
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Literal text replacements. Order matters: longest first, so a shorter pattern
# cannot eat a prefix of a longer one.
TEXT = [
    # NOT `claude-ux-refined`. That is the published GitHub repository name, and
    # rewriting it in docs produced a README instructing people to clone a URL
    # that 404s. A repository name changes when the repository is renamed — an
    # outward-facing act — and the docs follow that, not the other way round.
    ("claude-ux-installer", "aur-installer"),
    ("claude-ux-reapply", "aur-reapply"),
    ("claude-docs", "aur-docs"),
    ("claude_master.md", "ai_common.md"),
]

# Directory / file renames, relative to each target root.
RENAMES = [("claude-docs", "aur-docs")]
AUR_ONLY_RENAMES = [
    ("claude_master.md", "ai_common.md"),
    ("systemd/claude-ux-reapply.service", "systemd/aur-reapply.service"),
    ("systemd/claude-ux-reapply.path", "systemd/aur-reapply.path"),
]

EDIT_SUFFIXES = {".py", ".sh", ".md", ".json", ".template", ".service", ".path",
                 ".txt", ".html", ".js", ".css"}

# Extensionless files that still carry paths. `.gitignore` is the one that bites:
# one project had eight `claude-docs/` rules including negations whitelisting specific
# tracked documents, so renaming the directory without rewriting them drops those
# files out of the index — a suffix-only filter never sees it.
EDIT_NAMES = {".gitignore", ".gitattributes", "Makefile", "CLAUDE.md",
              "AGENTS.md", "GEMINI.md"}
SKIP_DIRS = {".git", "build", "dist", "__pycache__", "node_modules", ".gradle"}

# HISTORICAL RECORD — never rewritten. These files say "claude-docs" because that
# is what it was called on the day they were written. Editing them would make a
# May checkpoint reference a directory that did not exist until August, which is
# not a migration, it is falsifying the record.
# NAMES ONLY: this is never a PII exemption — a home path or a project's
# absolute path in a dated record is anonymized like anything else (ai_common.md,
# the ceiling), and the content guard scans these files like every other. Docs state current truth; a dated
# record states what was true then.
SKIP_HISTORICAL = ("checkpoints/", "checkpoints-archive/", "/rule-tests/",
                   "pending-insights.md", "/transcripts/",
                   # Session transcript persistence — the crash-survival record
                   # of past conversations. Rewriting it edits what was said.
                   "/.claude/projects/")

# Generated output already written: the artifact is a dated snapshot, and its
# links are served by path at read time regardless of the directory's name.
SKIP_GENERATED = ("/claude-docs/", "/aur-docs/")

# This script rewrites text. Left in scope, it rewrites its OWN replacement table
# mid-run — ("claude-docs","aur-docs") becomes ("aur-docs","aur-docs") — and every
# later substitution silently becomes a no-op.
SKIP_SELF = ("tools/debrand.py",)

# Paths the VENDOR owns: never RENAMED, because the vendor reads them by name.
# Their CONTENTS are edited like anything else — a shim is mostly references to
# our paths, so leaving it untouched is how `CLAUDE.md` ends up still telling the
# agent to write into a directory that no longer exists.
VENDOR_OWNED_NAMES = ("CLAUDE.md", "GEMINI.md", "AGENTS.md",
                      ".claude", ".gemini", "settings.json", "keybindings.json")

# Vendor shims and user-level config live outside every project root, so they are
# edited explicitly rather than discovered by walking a project.
EXTRA_EDIT_ROOTS = [Path.home() / ".claude", Path.home() / ".local" / "aur"]

# Symlinks whose TARGET moves during this migration.
SYMLINK_REPOINTS = [
    (Path.home() / ".claude" / "CLAUDE.md", "claude_master.md", "ai_common.md"),
]


def repoint_symlinks(apply, log):
    """A renamed target leaves its symlinks dangling — follow them across."""
    n = 0
    for link, old, new in SYMLINK_REPOINTS:
        if not link.is_symlink():
            continue
        target = str(Path(link).readlink())
        if old not in target:
            continue
        newt = target.replace(old, new)
        log(f"    relink {link} -> {newt}")
        if apply:
            link.unlink()
            link.symlink_to(newt)
        n += 1
    return n


def is_tracked(repo, rel):
    try:
        return subprocess.run(["git", "-C", str(repo), "ls-files", "--error-unmatch", str(rel)],
                              capture_output=True).returncode == 0
    except OSError:
        return False


def in_git(path):
    return (Path(path) / ".git").is_dir()


def iter_files(root):
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix not in EDIT_SUFFIXES and p.name not in EDIT_NAMES:
            continue
        s = str(p)
        if any(k in s for k in SKIP_HISTORICAL + SKIP_GENERATED + SKIP_SELF):
            continue
        yield p


def plan_edits(root):
    """Files whose text contains a brand string, with a per-file hit count."""
    out = []
    for p in iter_files(root):
        try:
            s = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits = sum(s.count(a) for a, _ in TEXT)
        if not hits:
            continue
        # Vendor-owned files ARE edited — only their names are protected.
        out.append((p, hits))
    return out


def apply_edits(files, apply, log, show_text=True):
    """Apply, or in dry run SHOW THE ACTUAL SUBSTITUTIONS.

    A dry run that prints "edit README.md (10 occurrences)" reports the blast
    radius and nothing else. It is structurally blind to the failure that
    matters here: a replacement that is textually correct but semantically
    wrong. `claude-ux-refined` -> `aur-refined` looks identical whether it sits
    in a filesystem path (right) or in the GitHub URL the README tells people to
    clone (wrong — that repository does not exist under the new name). Counts
    cannot distinguish those; the surrounding line can. Show the line.
    """
    for p, hits in files:
        log(f"    edit  {p}  ({hits} occurrence{'s' if hits != 1 else ''})")
        s = p.read_text(encoding="utf-8")
        if not apply and show_text:
            for i, line in enumerate(s.splitlines(), 1):
                if not any(a in line for a, _ in TEXT):
                    continue
                after = line
                for a, b in TEXT:
                    after = after.replace(a, b)
                log(f"        {i}: - {line.strip()[:110]}")
                log(f"        {i}: + {after.strip()[:110]}")
        if not apply:
            continue
        for a, b in TEXT:
            s = s.replace(a, b)
        p.write_text(s, encoding="utf-8")


def do_rename(root, src_rel, dst_rel, apply, log):
    src, dst = root / src_rel, root / dst_rel
    if not src.exists():
        return False
    if dst.exists():
        log(f"    SKIP  {src_rel} -> {dst_rel} (destination exists)")
        return False
    tracked = in_git(root) and is_tracked(root, src_rel)
    how = "git mv" if tracked else "mv"
    log(f"    {how:<6} {src_rel} -> {dst_rel}")
    if not apply:
        return True
    if tracked:
        subprocess.run(["git", "-C", str(root), "mv", str(src_rel), str(dst_rel)],
                       check=True)
    else:
        src.rename(dst)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="perform it (default: dry run)")
    ap.add_argument("--aur", default=str(Path(__file__).resolve().parent.parent))
    a = ap.parse_args()

    aur = Path(a.aur).resolve()
    sys.path.insert(0, str(aur))
    import aurshare

    roots = [aur] + [Path(r["path"]) for r in aurshare.registry_read()
                     if Path(r["path"]).resolve() != aur]

    mode = "APPLYING" if a.apply else "DRY RUN — nothing will be modified"
    print(f"=== de-brand: {mode} ===\n")

    renamed = edited = 0
    for root in roots:
        if not root.is_dir():
            continue
        print(f"  {root}{'  (git)' if in_git(root) else ''}")
        for src, dst in RENAMES + (AUR_ONLY_RENAMES if root == aur else []):
            if do_rename(root, src, dst, a.apply, print):
                renamed += 1
        files = plan_edits(root)
        apply_edits(files, a.apply, print)
        edited += len(files)
        if not files:
            print("    (no text changes)")
        print()

    # Vendor shims and user config: names untouched, contents updated.
    for root in EXTRA_EDIT_ROOTS:
        if not root.is_dir():
            continue
        print(f"  {root}  (vendor shim / user config — contents only)")
        files = plan_edits(root)
        apply_edits(files, a.apply, print)
        edited += len(files)
        if not files:
            print("    (no text changes)")
        print()

    print("  symlinks whose targets moved")
    repointed = repoint_symlinks(a.apply, print)
    if not repointed:
        print("    (none)")
    print()

    print(f"=== {renamed} rename(s), {edited} file(s) edited, "
          f"{repointed} symlink(s) repointed ===")
    if not a.apply:
        print("Re-run with --apply to perform it.")
    else:
        print("  re-devlinking the shared root (renamed sources)")
        aurshare.devlink(aur, log=print)
        print()
        print("  REMAINING, by hand:")
        print("    - restart every doc server (served ROOT path changed)")
        print("    - systemd units are installed as claude-ux-reapply.*;")
        print("      re-run systemd/install-autoreapply.py to move to aur-reapply.*")
    return 0


if __name__ == "__main__":
    sys.exit(main())
