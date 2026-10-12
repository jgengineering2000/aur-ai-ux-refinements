#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Shared root — `~/.local/aur/`.

Owns the one directory every AUR-installed project depends on, so that no project
depends on an AUR *source checkout*. Assets are stored version-suffixed with a
stable symlink beside them; a project links to the stable name and never to a
version, so an upgrade repoints one link here and every project follows without
being touched.

Design and rationale: `shared-root-design.md` in the AUR repo.

Nothing in this module scans a directory layout to find projects. Discovery is the
registry, because a layout is one person's convention and a registry is a fact.
"""

import os
import shutil
import sys
from pathlib import Path

SHARE = Path(os.environ.get("AUR_SHARE_ROOT",
                            Path.home() / ".local" / "aur")).expanduser()
REGISTRY = SHARE / "projects.tsv"
VERSION_FILE = SHARE / "VERSION"

_REGISTRY_HEADER = "#path\tlayout\tversion\n"

# Stable name -> path of the source, relative to the AUR repo root.
# A trailing slash marks a directory asset (copied as a tree).
SHARED_ASSETS = {
    "aurshare.py":      "aurshare.py",
    "server.py":        "server/server.py",
    "open-manifest.sh": "server/open-manifest.sh",
    "ann.css":          "server/ann.css",
    "ann.js":           "server/ann.js",
    "manifest-link.js": "server/manifest-link.js",
    "doc.css":          "server/doc.css",
    "manifest.html":    "templates/manifest.html",
    "ai_common.md":     "ai_common.md",
    "ai_process.md":    "PROCESS.md",
    "skills/":          "skills/",
    # Git hooks and the tool they call. These deploy to the shared root rather
    # than being read out of an AUR checkout, because a hook that depends on a
    # source tree stops working the moment that tree moves — and it fails at
    # commit time, on a machine that may not have the checkout at all.
    "commit-guard.py":  "tools/commit-guard.py",
    "hooks/":           "hooks/",
    # Tools a SKILL tells the reader to run. Same argument as the hooks above,
    # arrived at from the other side: a skill is consumed from inside whatever
    # project the session is in, where `tools/` is THAT project's directory and
    # an AUR checkout may not exist at all. commit-guard.py was registered here
    # because a hook calls it, which left the skill-invoked tools unreachable
    # from every project — scan-hazards.py, named by two skills as a pre-flight,
    # had never been runnable from any of them.
    "git-scope.py":     "tools/git-scope.py",
    "git-adopt.py":     "tools/git-adopt.py",
    "policy.json":      "policy.json",
    "libaur.so":        "server/libaur.so",
    "aur-gate":         "gate/aur-gate",
    "scan-hazards.py":  "tools/scan-hazards.py",
}

# The subset a project's aur/ directory links to. The rules files are consumed
# through the vendor's own mechanism and are not linked per project.
PROJECT_LINKS = ("aurshare.py", "server.py", "open-manifest.sh")
DOCS_LINKS = ("ann.css", "ann.js", "manifest-link.js", "manifest.html", "doc.css")


# ── versions ────────────────────────────────────────────────────────────────

def parse_version(text):
    """'2.10.1' -> (2, 10, 1). Compares as INTEGERS, never as text.

    `"2.10" < "2.9"` is true lexically, so a guard that compares filename
    suffixes as strings will install 2.9 over 2.10 and report success. The
    failure surfaces only after ten point releases, by which time nobody
    connects reverted rules to a string sort. A non-numeric component sorts as
    -1 so a malformed suffix always loses to a well-formed one instead of
    raising.
    """
    out = []
    for part in str(text).strip().lstrip("v").split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(-1)
    return tuple(out) or (-1,)


def _versioned(name):
    """Every stored version of `name`, newest first, as (version_tuple, path)."""
    stem = name.rstrip("/")
    found = []
    if not SHARE.is_dir():
        return found
    for p in SHARE.iterdir():
        if not p.name.startswith(stem + "."):
            continue
        suffix = p.name[len(stem) + 1:]
        if not suffix or suffix == "orphaned":
            continue
        found.append((parse_version(suffix), p))
    return sorted(found, reverse=True)


def installed_version(name):
    """Version the stable name currently resolves to, or None."""
    link = SHARE / name.rstrip("/")
    if not link.is_symlink():
        return None
    target = os.readlink(link)
    stem = link.name
    base = Path(target).name
    return parse_version(base[len(stem) + 1:]) if base.startswith(stem + ".") else None


# ── registry ────────────────────────────────────────────────────────────────

def ensure_root():
    """The root is a fundamental requirement — create it rather than fail."""
    SHARE.mkdir(parents=True, exist_ok=True)
    if not REGISTRY.exists():
        REGISTRY.write_text(_REGISTRY_HEADER)
    return SHARE


def _instance_of(project_dir):
    """('aur'|'server', server.py path) for a project, or (None, None)."""
    for layout in ("aur", "server"):
        cand = Path(project_dir) / layout / "server.py"
        if cand.exists():
            return layout, cand
    return None, None


def registry_read():
    """Registered projects that still exist, as dicts. Never raises."""
    out = []
    try:
        lines = REGISTRY.read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        if not cols or not cols[0]:
            continue
        out.append({"path": cols[0],
                    "layout": cols[1] if len(cols) > 1 else "aur",
                    "version": cols[2] if len(cols) > 2 else ""})
    return out


def registry_write(rows):
    tmp = REGISTRY.with_suffix(".tsv.tmp")
    body = "".join(f"{r['path']}\t{r.get('layout','aur')}\t{r.get('version','')}\n"
                   for r in rows)
    tmp.write_text(_REGISTRY_HEADER + body)
    os.replace(tmp, REGISTRY)


def register(project_dir, version="", log=print):
    """Record a project. Idempotent; refuses a path containing a tab."""
    ensure_root()
    path = str(Path(project_dir).expanduser().resolve())
    if "\t" in path:
        log(f"  refused: path contains a tab — {path!r}")
        return False
    layout, _ = _instance_of(path)
    if layout is None:
        log(f"  refused: no aur/ or server/ instance under {path}")
        return False
    rows = [r for r in registry_read() if r["path"] != path]
    rows.append({"path": path, "layout": layout, "version": str(version)})
    registry_write(sorted(rows, key=lambda r: r["path"]))
    log(f"  registered {path} ({layout})")
    return True


def discover(search_root, log=print):
    """Fallback for hand-wired or pre-registry projects. Scans ONCE, then
    registers — so the scan never becomes the discovery mechanism."""
    root = Path(search_root).expanduser().resolve()
    added = 0
    if not root.is_dir():
        log(f"  not a directory: {root}")
        return 0
    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        if _instance_of(child)[0] and register(child, log=log):
            added += 1
    return added


# ── sanity check and self-heal ──────────────────────────────────────────────

def _sync_version_file():
    """VERSION records the highest version any asset actually resolves to.

    Derived, never asserted: the filesystem is what is true, and a VERSION file
    that disagrees with the symlinks is a claim nobody checked.
    """
    best = max((v for v in (installed_version(n) for n in SHARED_ASSETS)
                if v is not None), default=None)
    if best is None:
        return None
    text = ".".join(str(x) for x in best)
    if not VERSION_FILE.exists() or VERSION_FILE.read_text().strip() != text:
        VERSION_FILE.write_text(text + "\n")
        return text
    return None


# ── the consumer's skills directory ─────────────────────────────────────────
# Skills are consumed from `~/.claude/skills/<name>` — a path the vendor reads,
# so it is literal here (tools/debrand.py leaves it alone for the same reason).
# Each entry is a symlink to the shared root's `skills/<name>`, so it resolves
# through the stable name like every other asset. Until 2026-09-11 these links
# were made by hand, one per skill, which is how a skill committed on
# 2026-08-09 was still not loadable a month later: tracked, deployed, and
# invisible to the consumer, with nothing reporting the gap. Healed here with
# the other stable names, and reported like them.
SKILLS_CONSUMER = Path.home() / ".claude" / "skills"


# ── the universal hook ───────────────────────────────────────────────────────
# `~/.claude/git-templates/hooks/pre-commit` resolves through the shared root
# to `hooks/pre-commit`, so every repository on the machine runs THIS file
# before every commit. On 2026-09-08 a project's own gate script was copied
# over it (a `cp` through the symlink), and for three days every commit on
# the machine ran that project's test gate and no content guard — invisibly,
# because the hook still exited 0. A copy in the wrong place is the drift
# this whole layout exists to prevent, so the root checks the one file whose
# job is to check everything else. Report only: the content is evidence of
# what was copied, and `git checkout -- hooks/pre-commit` is the repair.
UNIVERSAL_HOOK_MARK = "Universal pre-commit"


def hook_is_universal(path):
    """True when `path` opens with the universal hook's own header."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = "".join(fh.readline() for _ in range(3))
    except OSError:
        return False
    return UNIVERSAL_HOOK_MARK in head


def check_universal_hook(findings):
    hook = SHARE / "hooks" / "pre-commit"
    if not hook.exists():
        findings.append(f"MISSING {hook} — no repository on this machine is running a content guard")
    elif not hook_is_universal(hook):
        findings.append(
            f"DRIFT {hook} is not the universal hook (header lacks {UNIVERSAL_HOOK_MARK!r}) — "
            f"something was copied over it; every commit on this machine is running it. "
            f"Restore it from the checkout: git checkout -- hooks/pre-commit")


def link_skills(findings):
    """Every skill under the shared root has a consumer link; each repair is reported."""
    root = SHARE / "skills"
    if not root.is_dir():
        return
    SKILLS_CONSUMER.mkdir(parents=True, exist_ok=True)
    for skill in sorted(p for p in root.iterdir() if (p / "SKILL.md").is_file()):
        link = SKILLS_CONSUMER / skill.name
        target = root / skill.name  # through the stable name, never a checkout
        if link.is_symlink():
            if link.exists():
                continue  # resolves — healthy, or a deliberate alternative; left alone
            link.unlink()
            link.symlink_to(target)
            findings.append(f"repointed dangling skill link {skill.name} -> {target}")
        elif link.exists():
            findings.append(f"DRIFT {link} is a real directory, not a link into {root} "
                            f"— left in place; it will not receive updates")
        else:
            link.symlink_to(target)
            findings.append(f"linked skill {skill.name} -> {target}")


def verify_and_heal(log=print):
    """Check the shared root and repair what is repairable.

    Runs at every deployment AND at every server start. Cheap, and the failures
    it prevents are silent ones. Every repair is REPORTED: a repair that leaves
    no trace is indistinguishable from a system that was never broken, and the
    next occurrence gets diagnosed from scratch.
    """
    findings = []
    ensure_root()

    for name in SHARED_ASSETS:
        stem = name.rstrip("/")
        link = SHARE / stem
        versions = _versioned(stem)

        if link.is_symlink() and not link.exists():
            if versions:
                link.unlink()
                link.symlink_to(versions[0][1].name)
                findings.append(f"repointed dangling {stem} -> {versions[0][1].name}")
            else:
                # REPORT, do not reap. Deleting destroys the only evidence of
                # what this pointed at, and a dangling link is recoverable
                # while an absent one is a silent hole. The usual cause is a
                # devlinked source that moved — the target name is the clue,
                # and unlinking throws it away.
                findings.append(
                    f"DANGLING {stem} -> {os.readlink(link)} "
                    f"(no version present; left in place — re-run `devlink` or "
                    f"`deploy` to restore it)")
            continue

        if link.exists() and not link.is_symlink():
            # A real file where a stable name belongs: something wrote through
            # the link or a copy was dropped in. Preserve it, then restore.
            orphan = SHARE / f"{stem}.orphaned"
            shutil.move(str(link), str(orphan))
            findings.append(f"{stem} was a real file, not a link — kept as {orphan.name}")
            if versions:
                link.symlink_to(versions[0][1].name)
                findings.append(f"restored {stem} -> {versions[0][1].name}")
            continue

        if not link.exists() and versions:
            link.symlink_to(versions[0][1].name)
            findings.append(f"created missing {stem} -> {versions[0][1].name}")

    rows, pruned = [], []
    for r in registry_read():
        if not Path(r["path"]).is_dir():
            pruned.append(f"{r['path']} (gone)")
        elif _instance_of(r["path"])[0] is None:
            pruned.append(f"{r['path']} (no instance)")
        else:
            rows.append(r)
    seen, deduped = set(), []
    for r in sorted(rows, key=lambda r: (r["path"], parse_version(r["version"])),
                    reverse=True):
        if r["path"] in seen:
            pruned.append(f"{r['path']} (duplicate row)")
            continue
        seen.add(r["path"])
        deduped.append(r)
    if pruned:
        registry_write(sorted(deduped, key=lambda r: r["path"]))
        findings += [f"pruned {p}" for p in pruned]

    # DRIFT: a project serving a real file where a link belongs has forked from
    # canonical. AUR itself did exactly this — aur-docs/ held copies of ann.js,
    # ann.css and manifest-link.js that were three months stale and all three
    # diverged, in the repository that DEFINES the document system. "It is
    # symlinked so it cannot drift" is an assertion; this is the check.
    for row in registry_read():
        docs = Path(row["path"]) / "aur-docs"
        for name in DOCS_LINKS:
            f = docs / name
            if not f.exists():
                continue
            if not f.is_symlink():
                findings.append(
                    f"DRIFT {f} is a real file, not a link into {SHARE} — "
                    f"it has forked from canonical and will not receive fixes")
            elif SHARE not in Path(f).resolve().parents and \
                    not str(Path(f).readlink()).startswith(str(SHARE)):
                findings.append(f"DRIFT {f} links outside the shared root "
                                f"-> {Path(f).readlink()}")

    link_skills(findings)

    check_universal_hook(findings)

    synced = _sync_version_file()
    if synced:
        findings.append(f"VERSION reconciled to {synced} (it disagreed with the links)")

    for f in findings:
        log(f"  [heal] {f}")
    return findings


# ── deployment ──────────────────────────────────────────────────────────────

def check_canonical(aur_root, log=print):
    """Every asset the installer distributes must exist AND be tracked.

    The shared root is where assets are installed TO; this repository is where
    they are installed FROM. An untracked source is one that ships from a
    working tree and exists nowhere else — a release that cannot be rebuilt.
    """
    aur_root = Path(aur_root).expanduser().resolve()
    is_git = (aur_root / ".git").is_dir()
    missing, untracked = [], []
    for name, rel in SHARED_ASSETS.items():
        src = aur_root / rel.rstrip("/")
        if not src.exists():
            missing.append(rel)
            continue
        if is_git:
            import subprocess
            ok = subprocess.run(["git", "-C", str(aur_root), "ls-files",
                                 "--error-unmatch", rel.rstrip("/")],
                                capture_output=True).returncode == 0
            if not ok:
                untracked.append(rel)
    for m in missing:
        log(f"  [canonical] MISSING source: {m}")
    for u in untracked:
        log(f"  [canonical] UNTRACKED source: {u} — the installer would ship a "
            f"file that exists only in this working tree")
    if not missing and not untracked:
        log(f"  [canonical] all {len(SHARED_ASSETS)} sources present and tracked")
    return missing + untracked


def deploy(aur_root, version, log=print):
    """Install assets at `version`, refusing any per-asset downgrade.

    Stable names are repointed only AFTER every write succeeds, so a partial
    failure leaves the previous coherent set intact. Mixed versions across
    assets are legitimate — that is what per-file versioning buys — and this
    ordering is what separates a legitimate mix from a half-failed install.
    """
    aur_root = Path(aur_root).expanduser().resolve()
    want = parse_version(version)
    ensure_root()
    verify_and_heal(log)

    staged, skipped = [], []
    for name, rel in SHARED_ASSETS.items():
        src = aur_root / rel.rstrip("/")
        if not src.exists():
            log(f"  missing source, skipped: {rel}")
            continue
        stem = name.rstrip("/")
        have = installed_version(stem)
        if have is not None and have > want:
            skipped.append(f"{stem} ({'.'.join(map(str, have))} > {version})")
            continue
        dest = SHARE / f"{stem}.{version}"
        if src.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest, symlinks=True)
        else:
            shutil.copy2(src, dest)
        staged.append((stem, dest))

    for stem, dest in staged:
        link = SHARE / stem
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(dest.name)

    # Only on a deployment that actually placed something. Writing it
    # unconditionally records an attempt as though it were an install, so a
    # wholly-refused downgrade would leave VERSION claiming the lower number
    # while every asset still resolved to the higher one.
    if staged:
        _sync_version_file()
    for stem, dest in staged:
        log(f"  deployed {stem} -> {dest.name}")
    for s in skipped:
        log(f"  REFUSED downgrade: {s}")
    return len(staged), len(skipped)


def devlink(aur_root, names=None, log=print):
    """Point stable names at the CHECKOUT rather than at a deployed copy.

    Commit 379bfe0 made AUR self-host from canonical source so there would be
    one physical file and no copy to drift. That intent survives here: on a
    machine where AUR is developed, the shared root can front the working tree,
    so an edit to PROCESS.md or ai_common.md takes effect in the next
    session with no deploy step. Everywhere else the same stable name fronts a
    versioned copy. One mechanism, two configurations — which is what keeps AUR
    from being a special case in the consuming code.

    Reversible: `deploy` overwrites these with real versioned copies.
    """
    aur_root = Path(aur_root).expanduser().resolve()
    ensure_root()
    n = 0
    for name in (names or SHARED_ASSETS):
        rel = SHARED_ASSETS.get(name)
        if rel is None:
            continue
        src = aur_root / rel.rstrip("/")
        if not src.exists():
            continue
        link = SHARE / name.rstrip("/")
        if link.is_symlink() or link.exists():
            if link.is_dir() and not link.is_symlink():
                shutil.rmtree(link)
            else:
                link.unlink()
        link.symlink_to(src)
        log(f"  devlinked {name.rstrip('/')} -> {src}")
        n += 1
    return n


def link_project(project_dir, log=print):
    """Point a project's aur/ and aur-docs/ at the shared root.

    Absolute links: both ends used to live under develop/, so relative worked.
    A project may now live anywhere.
    """
    proj = Path(project_dir).expanduser().resolve()
    aur_dir, docs = proj / "aur", proj / "aur-docs"
    aur_dir.mkdir(parents=True, exist_ok=True)
    docs.mkdir(parents=True, exist_ok=True)
    n = 0
    for group, target_dir in ((PROJECT_LINKS, aur_dir), (DOCS_LINKS, docs)):
        for name in group:
            dst, src = target_dir / name, SHARE / name
            if not src.exists():
                log(f"  no shared asset yet: {name}")
                continue
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            dst.symlink_to(src)
            n += 1
    log(f"  linked {n} assets into {proj}")
    return n


_TASKS_JSON = """{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Start AUR doc server",
      "type": "shell",
      "command": "python3 '${workspaceFolder}/aur/server.py'",
      "isBackground": true,
      "runOptions": { "runOn": "folderOpen" },
      "presentation": { "reveal": "silent", "panel": "dedicated", "close": true },
      "problemMatcher": {
        "pattern": { "regexp": "^NEVER_MATCHES$" },
        "background": {
          "activeOnStart": true,
          "beginsPattern": "^AUR doc server",
          "endsPattern": "^AUR doc server"
        }
      }
    },
    {
      "label": "Ensure doc server",
      "type": "shell",
      "command": "bash '${workspaceFolder}/aur/open-manifest.sh'",
      "presentation": { "reveal": "never", "panel": "dedicated", "close": true },
      "problemMatcher": []
    }
  ]
}
"""


def install_project(project_dir, version="", log=print):
    """Everything a project needs to work with AUR, in one call.

    Written after doing it by hand twice: the steps are stable, the ordering
    matters (links before registration, so registration can verify an instance
    exists), and a hand sequence is where a step gets skipped silently.

    Idempotent. Never overwrites a project's own server.config.json.
    """
    proj = Path(project_dir).expanduser().resolve()
    if not proj.is_dir():
        log(f"  not a directory: {proj}")
        return False

    link_project(proj, log=log)

    cfg = proj / "aur" / "server.config.json"
    if not cfg.exists():
        cfg.write_text('{\n  "project_name": "%s"\n}\n' % proj.name.upper())
        log(f"  wrote {cfg.relative_to(proj)} (project_name only; defaults elsewhere)")

    vsc = proj / ".vscode"
    vsc.mkdir(exist_ok=True)
    tasks = vsc / "tasks.json"
    if not tasks.exists():
        tasks.write_text(_TASKS_JSON)
        log("  wrote .vscode/tasks.json (folderOpen auto-start + Alt+D ensure)")
    else:
        log("  .vscode/tasks.json exists — left alone")

    return register(proj, version or (VERSION_FILE.read_text().strip()
                                      if VERSION_FILE.exists() else ""), log=log)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "verify"
    if cmd == "canonical":
        sys.exit(1 if check_canonical(sys.argv[2] if len(sys.argv) > 2 else ".") else 0)
    elif cmd == "verify":
        found = verify_and_heal()
        print(f"shared root {SHARE}: {'healed ' + str(len(found)) if found else 'clean'}")
    elif cmd == "deploy":
        deploy(sys.argv[2], sys.argv[3])
    elif cmd == "devlink":
        devlink(sys.argv[2], sys.argv[3:] or None)
    elif cmd == "install":
        install_project(sys.argv[2])
    elif cmd == "link":
        link_project(sys.argv[2])
    elif cmd == "register":
        register(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
    elif cmd == "discover":
        print(f"registered {discover(sys.argv[2])} project(s)")
    elif cmd == "list":
        for r in registry_read():
            print(f"{r['path']}\t{r['layout']}\t{r['version']}")
    else:
        print(f"usage: {sys.argv[0]} "
              "{verify|canonical <aur_root>|deploy <aur_root> <ver>|devlink <aur_root> [names]|install <proj>|link <proj>|"
              "register <proj> [ver]|discover <root>|list}", file=sys.stderr)
        sys.exit(2)
