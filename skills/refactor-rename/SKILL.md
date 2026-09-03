---
name: refactor-rename
description: Read BEFORE any cross-cutting rename or refactor — renaming a directory, symbol, brand or path across projects, moving assets between locations, or any mechanical edit spanning git repos, configuration, symlinks and generated output. Carries the pre-flight, the dry-run contract, the git behaviours that silently drop work, and the tooling.
---

# Cross-cutting rename and refactor

A mechanical edit across many files looks like the safest kind of change and is
the most dangerous, because **every failure mode is silent**. The edit reports
success, the tests pass, and the damage surfaces hours later somewhere unrelated.

Every item below was paid for once. None is hypothetical.

## Tooling — use it, do not re-derive it

Paths are the shared root, `~/.local/aur/`, never `tools/` — you are reading
this from inside some project, where `tools/` is THAT project's directory and an
AUR checkout may not exist. The tools were addressed as `tools/…` here until
2026-08-14, which made every command below fail wherever it was actually run.

| Tool | Purpose |
|---|---|
| `~/.local/aur/scan-hazards.py <roots>` | Pre-flight. NUL bytes, invalid UTF-8, CRLF in scripts, tracked absolute symlinks |
| `~/.local/aur/commit-guard.py <repo>` | Scans **added lines** for PII, personal-tree references, cross-project leakage |
| `tools/debrand.py` *(AUR checkout only)* | Worked example of the full pattern: dry-run-by-default, exclusions, `git mv`, symlink repointing |
| `aurshare.py canonical <aur>` | Every distributable source exists and is tracked |
| `aurshare.py verify` | Shared-root integrity + drift (a copy where a link belongs) |

---

## 1 · Pre-flight, before writing any migration

- [ ] **`scan-hazards.py` over every affected root.** A file containing a NUL
      byte is classified binary by grep, which then **silently suppresses every
      match**. Searches return nothing and nothing says why; you conclude a
      string is absent when it is present. This cost four tool calls and a wrong
      diagnosis. `grep -c` vs `grep -ac` is the tell.
- [ ] **Name the coupled pairs.** Two files that compare a constructed string
      must change together. Here `open-manifest.sh` builds `<dir>/claude-docs`
      and compares it to the subtitle `server.py` reports from `/meta`; changing
      one alone makes every project fail its own health check and silently start
      a second server.
- [ ] **Separate current truth from dated record.** Checkpoints, session
      transcripts, sampled-response corpora and generated documents say the old
      name *because that is what it was called then*. Rewriting them is not
      migration, it is falsification. Exclude them explicitly. **This is about
      NAMES and nothing else** — it is never a PII exemption: a home path, a
      project's absolute path or a personal basename in a dated record is
      anonymized like anything else (ai_common.md, the ceiling), because the
      record's truth lives in the mapping outside git, not in the path.
- [ ] **Separate paths from published identifiers.** A repository name, a
      released binary, an installed systemd unit and a package are not paths.
      Renaming them in docs while the real thing keeps its name produces
      instructions that 404.

## 2 · The dry run must show the TEXT

- [ ] **Print `- old` / `+ new` per line, never a hit count.** A count reports
      the blast radius and is *structurally blind* to the failure that matters:
      a replacement that is textually correct and semantically wrong.
      `claude-ux-refined → aur-refined` reads identically in a filesystem path
      (right) and in the clone URL a README tells people to use (a 404). Only the
      surrounding line distinguishes them.
- [ ] **Exclude the migration script from its own run.** Left in scope it
      rewrites its own replacement table mid-run — `("a","b")` becomes
      `("b","b")` — and every later substitution silently becomes a no-op.
- [ ] **Include extensionless files.** `.gitignore` has no suffix, so a
      suffix-based filter never sees it — and it carried twelve rules including
      negations whitelisting specific tracked documents. Renaming the directory
      without it drops those files out of the index. Same for `Makefile`,
      `CLAUDE.md`, `AGENTS.md`.
- [ ] **Vendor-owned NAMES are protected; vendor-owned CONTENTS are not.**
      `~/.claude/`, `CLAUDE.md`, `.claude/skills/` keep their names because the
      vendor reads those paths. Their contents are mostly references to *your*
      paths and must be updated, or you ship a `CLAUDE.md` instructing an agent
      to write into a directory that no longer exists.
- [ ] **Re-run the dry run after applying.** It must report zero. If it wants to
      change something again, a replacement is not idempotent — usually a
      pattern that matches its own output.

## 3 · Mechanical editing

- [ ] **Never replace a line RANGE between two anchors.** Assuming everything
      between belongs to the thing being rewritten is how `_track_read` was
      destroyed here: every document fetch 500'd for hours, and no test caught it
      because the tests only ever requested `/manifest.html`, `/meta` and
      `/goto` — never an actual document. Match on content, or read the range
      first.
- [ ] **A symlink's target IS its tracked content.** Committing a symlink writes
      its target string into the repository — so an absolute link publishes a
      home path. Links that a tool regenerates are output: the generator is
      tracked, the output is gitignored.
- [ ] **Check prose, not just code.** Text replacement inside sentences produces
      "X is currently X" and rewrites historical statements into false ones.

## 4 · Git behaviours that silently drop work

- [ ] **`git commit -- <paths>` commits WORKTREE state and ignores the index for
      those paths.** A staged `git rm --cached` deletion is silently dropped.
      Correct for edits; wrong for staged removals.
- [ ] **gitignore does not apply to a path already in the index**, and
      `git check-ignore` reports a tracked file as *not ignored* — so correct
      rules look broken when they are merely moot. Untracking is a state to
      confirm with `git ls-files`, never an operation to assume.
- [ ] **`git mv` for renames**, so history follows. Verify with
      `git status --short | grep '^R'` — a rename recorded as delete-plus-add has
      lost the history you were trying to keep.
- [ ] **Path-limit every commit.** The index is shared between sessions; a bare
      `git commit` sweeps up another session's staged work.
- [ ] **Exclude work that was already modified when you started.** Confirm it
      does not overlap your edits, then leave it untouched.

## 5 · Build and test side effects

- [ ] **Stash/restore invalidates build caches.** Content changes to HEAD and
      back, so cargo/make correctly rebuild. A test gate went from **46s to
      517s** here — an 11× regression that looks like a performance defect and is
      just a cold cache. Expect it; do not diagnose it twice.
- [ ] **A red gate may belong to someone else.** Before assuming your change
      broke it, check whether the failure originates in work that was already in
      the tree.

## 6 · Verification — against the real artifact

- [ ] **Look at the rendered result.** `HTTP 200`, correct JSON shape and
      present markup cannot see a layout collapse. A header regression shipped
      here because every check was of the contract and none of the consumer.
- [ ] **Restart every process holding the old code.** A running server keeps
      serving what it loaded at start.
- [ ] **Exercise the path the tests do not.** The failure above survived because
      no test ever requested a document.
- [ ] **`commit-guard.py` before committing.** It scans added lines only —
      whole-file scanning reports the same pre-existing findings every run, and a
      guard that cries wolf is one you learn to skip.
- [ ] **`aurshare.py canonical` and `verify`.** Sources present and tracked; no
      project serving a copy where a link belongs.

---

## The shape of every failure here

Not one of these announced itself. grep returned nothing and exited zero. The
migration reported files edited. The commit reported success. The tests passed.
Each defect was found later, by accident, while looking at something else.

So the rule underneath the checklist is: **on a mechanical pass, absence of an
error message is not evidence of anything.** Verify the state you intended, in
the artifact that consumes it.
