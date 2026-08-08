---
name: git-discipline
description: Read BEFORE any git operation that writes — staging, committing, untracking, renaming, stashing, or anything touching .gitignore. Covers the shared-index hazard, the commit idioms that silently drop work, untracking that does not take, and the tooling. Also read before a first push or remote change on any project.
---

# Git discipline

Git's write operations fail quietly. `git commit` reports a hash whether or not
it committed what you meant; `git rm --cached` reports nothing when its effect is
undone a moment later. Everything below is a behaviour that produced a wrong
result while reporting success.

## Tooling

| Tool | Purpose |
|---|---|
| `tools/git-scope.py` | Commit exactly this unit of work; refuse if the index holds anything else |
| `tools/commit-guard.py <repo>` | Scan **added lines** for PII, personal-tree paths, cross-project leakage |
| `tools/scan-hazards.py <roots>` | Tracked absolute symlinks, NUL bytes, CRLF, invalid UTF-8 |

```
python3 tools/git-scope.py begin  <repo>          # before touching anything
python3 tools/git-scope.py status <repo>          # mine / theirs / ambiguous
python3 tools/commit-guard.py     <repo>          # before every commit
printf '%s\n' "$MSG" | python3 tools/git-scope.py commit <repo> -F -
```

---

## 1 · The index is shared between sessions

Two agent sessions in one repository share one index. A bare `git commit` takes
whatever the other session happened to have staged, and the result looks
deliberate to everyone who reads it later.

- **Record what was already dirty before you start.** "Mine" is then a computed
  set, not a remembered one — `git-scope.py begin`.
- **Never commit without asserting the index contains only your paths.**
  `git-scope.py commit` refuses otherwise, naming the intruding paths.
- **Leave pre-existing changes strictly alone**, and confirm your edits do not
  overlap them before you commit around them.

## 2 · Commit idioms that silently drop work

- **`git commit -- <paths>` commits WORKTREE state for those paths and ignores
  the index.** A staged `git rm --cached` deletion is dropped without a word.
  Correct for edits; wrong for staged removals. If you have staged a removal,
  commit through the index instead.
- **`git add <paths>` does not record deletions.** Use `git add -A -- <paths>`.
- **A heredoc needs `-F -`.** `git commit <<EOF` reads nothing from stdin and
  aborts with "empty commit message" — after the rest of the compound command has
  already run.

## 3 · Untracking is a state, not an operation

- **gitignore does not apply to a path already in the index.** Adding a rule for
  a tracked file does nothing.
- **`git check-ignore` reports a tracked file as *not ignored*** — so correct
  rules look broken when they are merely moot. It is answering a different
  question than the one you asked.
- **`git rm --cached` can be undone by a later `git add -A`** in the same
  sequence, before you commit.
- **Therefore: verify with `git ls-files <path>`.** Empty output is the only
  evidence that untracking took.

## 4 · Renames

- **`git mv`, so history follows.** Verify with `git status --short | grep '^R'`;
  a rename recorded as delete-plus-add has lost exactly what you were preserving.
- **`git mv` fails on an untracked path** — fall back to a plain move. Check
  `git ls-files --error-unmatch` first rather than letting it error.
- **Renaming a directory does not update `.gitignore`.** Its rules — especially
  negations that whitelist specific tracked files — must move with it, or those
  files silently leave the index.

## 5 · What must never be committed

- **A symlink's target is its tracked content.** Committing one publishes its
  target string, so an absolute link publishes a home path. Links a tool
  regenerates are output: the generator is tracked, the output is gitignored.
- **External data and its derivatives** — personal files, `~/` fixtures, other
  repos, local-system tool output, and any manifest, index, path or basename
  derived from them. Basenames are the ones that get through, because they look
  like ordinary strings.
- **Run `commit-guard.py` before every commit.** It scans added lines only;
  whole-file scanning reports the same legacy findings every run, and a guard
  that cries wolf is one you learn to skip.

## 6 · Stash

- **Stash/restore invalidates build caches.** Content goes to HEAD and back, so
  the build system correctly rebuilds. A gate went from 46s to 517s this way —
  an 11× regression that looks like a performance defect and is a cold cache.
- **Stashing another session's work parks it where they cannot see it.** If it
  must be done, restore it the moment the operation completes, and verify every
  file came back.

## 7 · Hooks, gates and authority

- **A red gate may not be yours.** Check whether the failure originates in work
  that was already in the tree before assuming your change caused it.
- **`--no-verify` requires explicit owner authorization, every time.** Bypassing
  a gate to land unrelated work is the same defect as loosening a test to make it
  pass. When authorized, say so in the commit message and state what was checked
  instead.
- **A gate that neither ran nor was skipped is not a pass.** Report it.

## 8 · Network operations

- **Default-deny.** Push, fetch, clone-by-URL, `ls-remote` and `gh` are refused
  for any project not on the public whitelist; the guard hook enforces it.
- **Commit and push are separate decisions.** "Commit" is never authority to
  push. Pushing publishes.
- **A repository's name is a published identifier**, not a path. Renaming it in
  documentation while the remote keeps its name produces instructions that 404.

---

## The shape of the failure

None of these announce themselves. The commit succeeds. The removal reports
nothing. The rename looks like a rename. **On a git write, absence of an error is
not evidence that the intended state was reached** — check the state itself:
`git ls-files`, `git status --short`, `git diff --cached --name-only`.
