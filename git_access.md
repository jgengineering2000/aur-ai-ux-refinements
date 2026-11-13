# Router Backup & Hook Template Setup

Local repositories mirror commits to a bare `router` remote (for example, on
LAN-attached router USB storage) automatically on commit via the shared AUR
hooks in `~/.local/aur/hooks/`.

## 1. Gateway Verification (`git_access.local`)

`hooks/router-backup-push.sh` checks that the current machine's default-route
gateway resolves to a trusted MAC address before touching the `router` remote,
so backup pushes never run on untrusted networks.

It looks for `git_access.local` in order:

1. `${XDG_CONFIG_HOME:-$HOME/.config}/aur/git_access.local`
2. `~/.local/aur/git_access.local`
3. `<repo-parent>/git_access.local`

`git_access.local` contains the trusted gateway MAC address (mode `0600`) and
must stay untracked (never committed).

## 2. Shared Hook Templates

Repositories sharing AUR's hook template (`~/.claude/git-templates/hooks/`)
inherit:

- `post-commit`: invokes `~/.local/aur/hooks/router-backup-push.sh`, which spawns
  a per-repo locked background `--worker` and appends one-line status records to
  `~/.claude/git-templates/router-push.log` (sending a desktop notification on
  failure).
- `pre-commit` / `pre-push`: run AUR's commit/content and alias guards (deployed
  into `~/.local/aur/`) across repositories.

Detached-HEAD commits and repositories without a `router` remote are skipped and
recorded as a single log line.

## 3. Diverged Branch Recovery (`router-safe-resync.sh`)

`router-backup-push.sh` never silently overwrites a foreign branch tip on the
router. If the local branch was rewritten (`commit --amend`, `rebase`, etc.)
and the router's old tip is present in the branch reflog, the push script saves
that old tip under `refs/backup/` before fast-forwarding/updating. Otherwise it
logs an error pointing to `router-safe-resync.sh`.

To recover after inspecting the divergence:

```bash
~/.local/aur/hooks/router-safe-resync.sh /path/to/repo
```

`router-safe-resync.sh` fetches `router/<branch>`, saves any diverged router
tip under `refs/backup/<repo>-<branch>-<timestamp>` on `router` so no commit
is ever lost, and force-pushes `HEAD:<branch>` (`--no-verify`).

## 4. History Rewrites (`git filter-repo`)

When rewriting repository history with `git filter-repo`:

1. Record `.git/filter-repo/commit-map` under `tools/history/<YYYY-MM-DD>-<slug>.commit-map`.
2. Preserve the router's pre-rewrite tip under `refs/backup/` (or run
   `router-safe-resync.sh`) before pushing rewritten branches to `router` and `origin`.
3. Update `refs/notes/commits` via `python3 tools/commit-alias.py notes <repo>`
   and verify references with `commit-alias.py stale` / `commit-alias.py translate`.
4. Remove obsolete pre-rewrite `refs/replace/*` and stale branches once every
   commit has been verified against `tools/history/*.commit-map`.
