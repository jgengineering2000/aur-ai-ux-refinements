#!/usr/bin/env python3
"""
commit-alias — resolve a commit id across a history rewrite, in either direction.

    commit-alias.py resolve  [repo] <id>...       old→live or live→formerly
    commit-alias.py stale    [repo] [ref...]      dead ids inside tracked FILE content
    commit-alias.py stale    [repo] --staged      dead ids in the lines a commit ADDS (the gate)
    commit-alias.py translate [repo] [path...]    rewrite dead ids in the WORKTREE to live ones
    commit-alias.py notes    [repo]               attach "formerly <old>" notes to rewritten commits
    commit-alias.py hook                          PreToolUse(Bash) body: read JSON on stdin, warn on dead ids

WHY. `git filter-repo` rewrites the ids it finds in commit MESSAGES through its
commit-map and leaves file content, session transcripts and every other
reference alone. After a purge, an id written into a doc comment, a spec, a
memory or a transcript names an object that no longer exists — and the same
fact can be cited by two ids that look unrelated (measured 2026-08-28: a commit
body said `0db13bf`, the doc comment it wrote said `323e017`; same commit).

THE MAP IS THE AUTHORITY, AND IT IS TRACKED. filter-repo leaves its map at
`.git/filter-repo/commit-map`, untracked and lost by any fresh clone. Each
project keeps the maps it has accumulated under `tools/history/*.commit-map`
(the filter-repo format verbatim: header line, then `old new` per row) and
this tool chains them in filename order, so a second rewrite is a second file
rather than a second mechanism. The untracked filter-repo copy is read as well
and reported as such, so a project that has not yet moved its map is warned
instead of silently unresolvable.

A DEAD id is one the map knows and `git cat-file -e` rejects; a live id is
one the repository still holds. An id that is neither is not this tool's
business and is left alone, which is what keeps `translate` from touching md5
sums and other hex that merely looks like a commit.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

HEX = re.compile(r"(?<![0-9a-fA-F])[0-9a-f]{7,40}(?![0-9a-fA-F])")
NULL = "0" * 40
MAP_DIR = Path("tools/history")
MAP_GLOB = "*.commit-map"


def die(msg: str, code: int = 2) -> "NoReturn":  # noqa: F821
    print(f"commit-alias: {msg}", file=sys.stderr)
    sys.exit(code)


def git(repo: Path, *args: str, check: bool = True, binary: bool = False):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if check and r.returncode != 0:
        die(f"git {' '.join(args)} failed: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout if binary else r.stdout.decode(errors="replace")


def repo_root(start: Path) -> Path:
    r = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        die(f"{start} is not inside a git repository")
    return Path(r.stdout.strip())


# ---------------------------------------------------------------- the map ---

class AliasMap:
    """old→new over every rewrite this repository has recorded, chained.

    `sources` names where each map came from so a report can say which are
    tracked and which are the untracked filter-repo leftovers.
    """

    def __init__(self, repo: Path):
        self.repo = repo
        self.old2new: dict[str, str] = {}
        self.new2old: dict[str, list[str]] = {}
        self.sources: list[tuple[Path, bool]] = []   # (path, tracked)
        for p in sorted((repo / MAP_DIR).glob(MAP_GLOB)):
            self._load(p, tracked=True)
        # A linked worktree's .git is a FILE pointing at the shared git dir;
        # filter-repo's leftovers live in the common dir, so ask git where
        # that is rather than assuming ".git/" is a directory.
        common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
        fr = common / "filter-repo" / "commit-map"
        if fr.exists():
            tracked_copies = [p for p, _ in self.sources if p.read_bytes() == fr.read_bytes()]
            if not tracked_copies:
                self._load(fr, tracked=False)
        # chain: if a→b and b→c, a resolves to c
        for old in list(self.old2new):
            seen = {old}
            cur = self.old2new[old]
            while cur in self.old2new and cur not in seen:
                seen.add(cur)
                cur = self.old2new[cur]
            self.old2new[old] = cur
        self.new2old = {}
        for o, n in self.old2new.items():
            self.new2old.setdefault(n, []).append(o)
        # prefix index for short ids
        self.by_prefix: dict[str, set[str]] = {}
        for full in list(self.old2new) + list(self.new2old):
            self.by_prefix.setdefault(full[:7], set()).add(full)

    def _load(self, path: Path, tracked: bool):
        rows = 0
        for line in path.read_text().splitlines():
            parts = line.split()
            if len(parts) != 2 or parts[0] == "old":
                continue
            o, n = parts
            if n == NULL or o == n:
                continue           # deleted, or untouched by the rewrite
            if o in self.old2new and self.old2new[o] != n:
                die(f"{path}: {o} maps to both {self.old2new[o]} and {n}")
            self.old2new[o] = n
            rows += 1
        self.sources.append((path, tracked))

    def candidates(self, token: str) -> list[str]:
        return sorted(f for f in self.by_prefix.get(token[:7], ()) if f.startswith(token))

    def is_live(self, token: str) -> bool:
        r = subprocess.run(["git", "-C", str(self.repo), "cat-file", "-e", f"{token}^{{commit}}"],
                           capture_output=True)
        return r.returncode == 0

    def classify(self, token: str) -> dict | None:
        """None if the token is not a commit this map or repo knows about."""
        live = self.is_live(token)
        olds = [c for c in self.candidates(token) if c in self.old2new]
        if live:
            full = git(self.repo, "rev-parse", f"{token}^{{commit}}").strip()
            formerly = self.new2old.get(full, [])
            return {"id": token, "state": "live", "full": full, "formerly": formerly}
        if len(olds) == 1:
            return {"id": token, "state": "dead", "full": olds[0], "now": self.old2new[olds[0]]}
        if len(olds) > 1:
            return {"id": token, "state": "ambiguous", "full": olds}
        return None

    def subject(self, full: str) -> str:
        return git(self.repo, "log", "-1", "--format=%s", full, check=False).strip()


# --------------------------------------------------------------- commands ---

def cmd_resolve(m: AliasMap, ids: list[str], as_json: bool) -> int:
    out, rc = [], 0
    for tok in ids:
        c = m.classify(tok)
        if c is None:
            out.append({"id": tok, "state": "unknown"}); rc = 1
        else:
            out.append(c)
    if as_json:
        print(json.dumps(out, indent=1)); return rc
    for c in out:
        if c["state"] == "live":
            f = ", ".join(x[:7] for x in c["formerly"]) or "—"
            print(f"{c['id']}  live   formerly {f}   {m.subject(c['full'])}")
        elif c["state"] == "dead":
            print(f"{c['id']}  DEAD   now {c['now'][:7]}   {m.subject(c['now'])}")
        elif c["state"] == "ambiguous":
            print(f"{c['id']}  ambiguous among {', '.join(x[:12] for x in c['full'])}")
        else:
            print(f"{c['id']}  unknown — not a commit this repository or its maps know")
    return rc


def is_map_path(path: str) -> bool:
    """The maps are the one place old ids belong; every scan skips them."""
    p = Path(path)
    return p.parent == MAP_DIR and p.match(MAP_GLOB)


def scan_blob(m: AliasMap, text: str) -> dict[str, str]:
    """token → live id, for every dead id in the text."""
    hits = {}
    for tok in set(HEX.findall(text)):
        olds = [c for c in m.candidates(tok) if c in m.old2new]
        if len(olds) == 1 and not m.is_live(tok):
            hits[tok] = m.old2new[olds[0]][:len(tok)]
    return hits


def cmd_stale_staged(m: AliasMap) -> int:
    """Dead ids in the lines this commit ADDS — the pre-commit gate's form.

    Added lines only, like commit-guard: a whole-tree scan reports the same
    legacy findings every run, and a gate that cries wolf is one you skip.
    """
    diff = git(m.repo, "diff", "--cached", "--unified=0", "--no-color")
    report: dict[str, dict[str, str]] = {}
    path = None
    for line in diff.split("\n"):
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
            if path and is_map_path(path):
                path = None
        elif line.startswith("+") and path:
            hits = scan_blob(m, line[1:])
            if hits:
                report.setdefault(path, {}).update(hits)
    for f, hits in sorted(report.items()):
        for tok, new in sorted(hits.items()):
            print(f"{f}: `{tok}` is a pre-rewrite id; the live id is {new}")
    total = sum(len(v) for v in report.values())
    if total:
        print(f"\n{total} dead id(s) in added lines — translate with `commit-alias.py translate <path>`")
    return 1 if total else 0


def cmd_stale(m: AliasMap, refs: list[str], as_json: bool) -> int:
    if refs == ["--staged"]:
        return cmd_stale_staged(m)
    refs = refs or ["HEAD"]
    report = {}
    for ref in refs:
        for f in git(m.repo, "ls-tree", "-r", "--name-only", ref).split("\n"):
            if not f or is_map_path(f):
                continue
            blob = git(m.repo, "show", f"{ref}:{f}", binary=True)
            try:
                text = blob.decode()
            except UnicodeDecodeError:
                continue
            hits = scan_blob(m, text)
            if hits:
                report[f"{ref}:{f}"] = hits
    total = sum(len(v) for v in report.values())
    if as_json:
        print(json.dumps(report, indent=1))
    else:
        for k, hits in sorted(report.items()):
            print(k)
            for tok, new in sorted(hits.items()):
                print(f"    {tok} -> {new}")
        print(f"\n{len(report)} file(s), {total} dead id(s)")
        for p, tracked in m.sources:
            if not tracked:
                print(f"NOTE: {p} is untracked — copy it to {MAP_DIR}/ so a clone can resolve these")
    return 1 if total else 0


def cmd_translate(m: AliasMap, paths: list[str]) -> int:
    files = paths or git(m.repo, "ls-files").split("\n")
    changed = 0
    for f in files:
        if not f or is_map_path(f):
            continue
        p = m.repo / f
        if not p.is_file() or p.is_symlink():
            continue
        raw = p.read_bytes()
        try:
            text = raw.decode()
        except UnicodeDecodeError:
            continue
        hits = scan_blob(m, text)
        if not hits:
            continue
        new = HEX.sub(lambda mo: hits.get(mo.group(0), mo.group(0)), text)
        if new != text:
            p.write_text(new)
            changed += 1
            print(f"{f}: {len(hits)} id(s) translated")
    print(f"\n{changed} file(s) rewritten")
    return 0


def cmd_notes(m: AliasMap) -> int:
    added = kept = 0
    for new, olds in sorted(m.new2old.items()):
        if not m.is_live(new):
            continue
        existing = git(m.repo, "notes", "show", new, check=False)
        line = "formerly " + " ".join(olds) + " (history rewrite; see tools/history/)"
        if line.split(" (")[0] in existing:
            kept += 1
            continue
        git(m.repo, "notes", "append", "-m", line, new)
        added += 1
    print(f"notes: {added} added, {kept} already present")
    return 0


def cmd_hook() -> int:
    """PreToolUse(Bash): never blocks; injects the translation as context."""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    cmd = payload.get("tool_input", {}).get("command") or ""
    cwd = payload.get("cwd") or os.getcwd()
    toks = set(HEX.findall(cmd))
    if not toks:
        return 0
    try:
        m = AliasMap(repo_root(Path(cwd)))
    except SystemExit:
        return 0
    if not m.old2new:
        return 0
    lines = []
    for tok in sorted(toks):
        c = m.classify(tok)
        if c and c["state"] == "dead":
            lines.append(f"{tok} is a PRE-REWRITE id; it is now {c['now'][:7]} — {m.subject(c['now'])}")
        elif c and c["state"] == "ambiguous":
            lines.append(f"{tok} is ambiguous among pre-rewrite ids {', '.join(x[:12] for x in c['full'])}")
    if lines:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "commit-alias: " + "; ".join(lines)
            + ". Resolve with `commit-alias.py resolve <id>`; a doc or log naming the old id predates the rewrite."}}))
    return 0


USAGE = __doc__.split("WHY.")[0]


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE); return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "hook":
        return cmd_hook()
    as_json = "--json" in rest
    rest = [a for a in rest if a != "--json"]
    if cmd not in ("resolve", "stale", "translate", "notes"):
        print(f"unknown command `{cmd}`\n{USAGE}", file=sys.stderr); return 2
    repo = Path.cwd()
    if rest and Path(rest[0]).is_dir():
        repo = Path(rest.pop(0))
    m = AliasMap(repo_root(repo))
    if cmd == "resolve":
        if not rest:
            print("resolve needs at least one id\n" + USAGE, file=sys.stderr); return 2
        return cmd_resolve(m, rest, as_json)
    if cmd == "stale":
        return cmd_stale(m, rest, as_json)
    if cmd == "translate":
        return cmd_translate(m, rest)
    return cmd_notes(m)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
