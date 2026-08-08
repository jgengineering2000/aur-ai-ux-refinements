#!/usr/bin/env bash
# Recovery tool for when router-push.log shows a non-fast-forward failure.
#
# Never auto-force-pushes on its own (see router-backup-push.sh) — a
# diverged router tip always needs a human to look first, because not every
# divergence is a harmless superseded amend. This script is what you run
# *after* that judgment call, and it never discards the router's old tip:
# before force-pushing, it preserves the router's current branch tip under
# refs/backup/<branch>-<timestamp> ON THE ROUTER ITSELF, so even a wrong
# call about "this is just superseded" never actually destroys a commit —
# it only becomes unreachable from the live branch, still fetchable by SHA
# or the backup ref forever (until manually pruned).
#
# Usage: router-safe-resync.sh /path/to/repo

set -euo pipefail

REPO_DIR="${1:?usage: router-safe-resync.sh /path/to/repo}"
cd "$REPO_DIR"

BRANCH=$(git symbolic-ref --short HEAD)
REPO_NAME=$(basename "$(git rev-parse --show-toplevel)")

git fetch router "$BRANCH" 2>&1 || true

if ! git rev-parse -q --verify "router/$BRANCH" >/dev/null; then
    echo "No router/$BRANCH yet — plain push, nothing to preserve."
    git push router "HEAD:$BRANCH" --no-verify
    exit 0
fi

if git merge-base --is-ancestor "router/$BRANCH" HEAD; then
    echo "router/$BRANCH is already an ancestor of HEAD — plain push suffices, no force needed."
    git push router "HEAD:$BRANCH" --no-verify
    exit 0
fi

STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_REF="refs/backup/${REPO_NAME}-${BRANCH}-${STAMP}"
OLD_TIP=$(git rev-parse "router/$BRANCH")

echo "Diverged. Preserving router's current $BRANCH tip ($OLD_TIP) as $BACKUP_REF before overwriting..."
git push router "router/${BRANCH}:${BACKUP_REF}"

echo "Force-pushing local $BRANCH ($(git rev-parse HEAD)) to router..."
git push router "HEAD:$BRANCH" --force --no-verify

echo "Done. Old tip recoverable at any time via:"
echo "  git fetch router ${BACKUP_REF} && git log FETCH_HEAD"
