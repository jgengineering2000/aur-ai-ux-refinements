---
name: git-discipline
description: Read BEFORE any git operation that writes — staging, committing, untracking, renaming, stashing, a first `git init` on a lived-in directory, a history rewrite, or anything touching .gitignore — and when a commit id is "not a valid object name". Covers the shared-index hazard, the commit idioms that silently drop work, untracking that does not take, and the tooling. Also read before a first push, a fetch, pull or clone, or a remote change on any project.
---

# Git discipline

Git's write operations fail quietly. `git commit` reports a hash whether or not
it committed what you meant; `git rm --cached` reports nothing when its effect is
undone a moment later. Everything below is a behaviour that produced a wrong
result while reporting success.

## Tooling

Paths are the shared root, `~/.local/aur/`, never `tools/` — you are reading
this from inside some project, where `tools/` is THAT project's directory and an
AUR checkout may not exist. The tools were addressed as `tools/…` here until
2026-08-14, which made every command below fail wherever it was actually run.

| Tool | Purpose |
|---|---|
| `~/.local/aur/git-adopt.py <dir>` | Pre-flight before a **first** `git init` on a lived-in directory |
| `~/.local/aur/git-scope.py` | Commit exactly this unit of work; refuse if the index holds anything else |
| `~/.local/aur/commit-guard.py <repo>` | Scan **added lines** for PII, personal-tree paths, cross-project leakage |
| `~/.local/aur/scan-hazards.py <roots>` | Tracked absolute symlinks, NUL bytes, CRLF, invalid UTF-8 |
| `~/.local/aur/commit-alias.py resolve <id>` | A commit id across a history rewrite, either direction |

```
python3 ~/.local/aur/git-adopt.py        <dir>    # only for a first `git init`
python3 ~/.local/aur/git-scope.py begin  <repo>   # before touching anything
python3 ~/.local/aur/git-scope.py status <repo>   # mine / theirs / ambiguous
git -C <repo> add -A -- <paths>                   # MUST precede the guard
python3 ~/.local/aur/commit-guard.py     <repo>   # scans the STAGED set
printf '%s\n' "$MSG" | python3 ~/.local/aur/git-scope.py commit <repo> -F -
```

---

## 1 · Adopting an existing directory: inventory before you init

`git init && git add -A` is the wrong move on a directory that has been lived in.
By the time anyone thinks to track it, work, output, state and links have grown
together, and **every wrong inclusion commits silently**. Run
`git-adopt.py <dir>` first; it asks each path the only question that matters —
**does this regenerate, and from what?**

- **Absolute symlinks are the sharpest edge.** A symlink's target *is* its
  tracked content, so committing one writes a home path into the repository.
  They are output: the generator is tracked, the link is not.
- **Generated output is scoped by directory AND extension**, never by directory
  alone. A bare `<dir>/*` also swallows whatever hand-written thing lands beside
  the output later, silently.
- **Something precious usually lives inside the generated tree.** Annotations,
  review comments, notes — not generated, not reconstructable, and exactly what
  a blanket ignore takes with it. Negate them back in explicitly, so a later
  broadening of the rule cannot drop them.
- **Duplicate content is a decision, not a nuisance.** The same bytes at two
  paths means one will go stale. Keep the copy the toolchain actually reads —
  check where the generator resolves its input and output, which is not always
  where the name suggests.
- **Settle the posture in the same pass.** A repo on neither the public
  whitelist nor the backup list is the worst of both: not publishable,
  and history nothing preserves. `git-adopt.py` reports both.
- **Identity is not global here.** `git init` produces a repo that cannot commit
  until `user.name`/`user.email` are set *in that repo*.

## 2 · The index is shared between sessions

Two agent sessions in one repository share one index. A bare `git commit` takes
whatever the other session happened to have staged, and the result looks
deliberate to everyone who reads it later.

- **Record what was already dirty before you start.** "Mine" is then a computed
  set, not a remembered one — `git-scope.py begin`.
- **Never commit without asserting the index contains only your paths.**
  `git-scope.py commit` refuses otherwise, naming the intruding paths.
- **Leave pre-existing changes strictly alone**, and confirm your edits do not
  overlap them before you commit around them.

## 3 · Commit idioms that silently drop work

- **`git commit -- <paths>` commits WORKTREE state for those paths and ignores
  the index.** A staged `git rm --cached` deletion is dropped without a word.
  Correct for edits; wrong for staged removals. If you have staged a removal,
  commit through the index instead.
- **`git add <paths>` does not record deletions.** Use `git add -A -- <paths>`.
- **A heredoc needs `-F -`.** `git commit <<EOF` reads nothing from stdin and
  aborts with "empty commit message" — after the rest of the compound command has
  already run.

## 4 · Untracking is a state, not an operation

- **gitignore does not apply to a path already in the index.** Adding a rule for
  a tracked file does nothing.
- **`git check-ignore` reports a tracked file as *not ignored*** — so correct
  rules look broken when they are merely moot. It is answering a different
  question than the one you asked.
- **`git rm --cached` can be undone by a later `git add -A`** in the same
  sequence, before you commit.
- **Therefore: verify with `git ls-files <path>`.** Empty output is the only
  evidence that untracking took.

## 5 · Renames

- **`git mv`, so history follows.** Verify with `git status --short | grep '^R'`;
  a rename recorded as delete-plus-add has lost exactly what you were preserving.
- **`git mv` fails on an untracked path** — fall back to a plain move. Check
  `git ls-files --error-unmatch` first rather than letting it error.
- **Renaming a directory does not update `.gitignore`.** Its rules — especially
  negations that whitelist specific tracked files — must move with it, or those
  files silently leave the index.

## 6 · What must never be committed

- **Anything above the project's own path.** Git's world ends at
  `${PROJECT_PATH}`; `$HOME`, the machine's layout and the project's own
  absolute location are all outside it (ai_common.md, the ceiling). Name
  worktrees by branch, write paths relative to the root, and expect the guard
  in EVERY tracked path — dated records, memory, generated docs and briefs
  included; there is no exempt directory and no allowlisted file.
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

## 7 · Stash

- **Stash/restore invalidates build caches.** Content goes to HEAD and back, so
  the build system correctly rebuilds. A gate went from 46s to 517s this way —
  an 11× regression that looks like a performance defect and is a cold cache.
- **Stashing another session's work parks it where they cannot see it.** If it
  must be done, restore it the moment the operation completes, and verify every
  file came back.

## 8 · Hooks, gates and authority

- **A red gate may not be yours.** Check whether the failure originates in work
  that was already in the tree before assuming your change caused it.
- **`--no-verify` requires explicit owner authorization, every time.** Bypassing
  a gate to land unrelated work is the same defect as loosening a test to make it
  pass. When authorized, say so in the commit message and state what was checked
  instead.
- **A gate that neither ran nor was skipped is not a pass.** Report it.

## 8a · A history rewrite retires every id outside the commit messages

`git filter-repo` translates the ids it finds in commit MESSAGES through its
commit-map and touches nothing else — file content, session transcripts,
worktree names, memories all keep the OLD id. After the 2026-08-26 purge one
commit was cited as `323e017` by its own doc comment and `0db13bf` by its
commit body, and they read as two commits.

- **The map is the authority and it is tracked** — `tools/history/*.commit-map`
  in each project, the filter-repo format verbatim. `.git/filter-repo/` is
  lost by the first clone. A second rewrite is a second file, chained by the
  tool, never a second mechanism.
- **Old → live:** `commit-alias.py resolve <id>`. **Live → old:** `git show`
  prints a `formerly <old>` note (`commit-alias.py notes` attaches them;
  `git push <backup-remote> refs/notes/commits` carries them to the backup — notes
  are a ref and travel only when named).
- **Unprompted:** the PreToolUse hook `commit-alias-guard.sh` injects the
  translation whenever a command names a dead id. A cold "not a valid object
  name" on a 7–40 hex token therefore means the id is from outside every map,
  not from before a rewrite.
- **Never in new content:** the universal pre-commit refuses a dead id in an
  added line (`commit-alias.py stale --staged`); `stale [ref]` audits a whole
  tree and `translate` rewrites the worktree — run once, in bulk, after a
  rewrite.
- **Do not rewrite messages to carry the old ids.** That is a third
  generation of ids the transcripts also do not know.

## 9 · Network operations

- **Default-deny.** Network git/gh operations are refused for any project not on
  the public whitelist; the `git-ip-guard.sh` PreToolUse hook enforces it.
- **The backup remote is a local path, not a network host.** Each project
  mirrors to a bare repo on a locally mounted backup drive —
  `<backup-mount>/<name>.git` — and that push is the backup. It sends
  deltas, so all four repos' full history occupies ~270 MB there, against 6.7 GB
  of accumulated whole-tree tarballs in `<tarball-dir>`. Prefer it.
- **Know what the hook actually matches, because it does not match intent.**
  `push`, `send-email`, `request-pull` and the `gh` subcommands are caught
  **unconditionally** — including `git push <backup-remote> main` to that local bare
  repo, which is a backup, not a disclosure. Meanwhile `fetch`, `pull`, `clone`,
  `ls-remote` and `remote add` are caught **only when the command text contains
  a URL**, so `git fetch origin` against a configured remote passes straight
  through. The guard matches command TEXT, never the remote's resolved target,
  which is why it is simultaneously too strict and too loose. Verify it fires;
  never treat "the hook would have stopped me" as a control.
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
