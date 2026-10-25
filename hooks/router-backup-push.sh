#!/usr/bin/env bash
# Shared by every project's .git/hooks/post-commit, which calls this one file
# directly, so the connectivity check lives in exactly one place.
#
# Pushes the current branch to the `router` remote, but only when this
# machine is actually on the home network right now. "Right now" is
# determined live every run: current default gateway IP (`ip route`), then
# that gateway's MAC (`arp`) — never a hardcoded IP or MAC. Both reads are
# passive (kernel route/neighbor tables); no packets are sent, so this is
# silent and harmless on a foreign network (coffee shop, etc.).
#
# The one real MAC value this compares against lives outside git entirely,
# in ~/develop/git_access.local (see aur/git_access.md for the rest of the
# router-backup setup). If that file is missing or unreadable, or nothing on
# the current network resolves, the push is skipped — fail closed.

# Internal mode: `--worker` is the detached push body, started by this script
# itself after the gateway check. Any other argument is refused before any
# side effect.
MODE=gate
case "${1:-}" in
    "") ;;
    --worker) MODE=worker ;;
    *) echo "usage: router-backup-push.sh   (no arguments; --worker is internal)" >&2; exit 2 ;;
esac

worker() {
    LOG="$HOME/.claude/git-templates/router-push.log"
    mkdir -p "$(dirname "$LOG")"
    REPO=$(basename "$(git rev-parse --show-toplevel 2>/dev/null)")
    say() { printf "%s [%s] %s\n" "$(date -Iseconds)" "$REPO" "$*" >> "$LOG"; }
    fail() {  # fail <message>: log line plus the critical popup
        say "push FAILED: $*"
        command -v notify-send >/dev/null 2>&1 && notify-send -u critical \
            "Router backup push FAILED — $REPO" "$(head -c 200 <<<"$*")  See $LOG"
    }

    # The pre-push hook is the content scan (PII, personal paths); it runs for
    # the router exactly as for a public remote, so these pushes never skip it.
    scan_refused() { grep -q "\[pre-push\]" <<<"$1"; }

    BRANCH=$(git symbolic-ref --quiet --short HEAD 2>/dev/null)
    if [ -z "$BRANCH" ]; then
        say "skipped: detached HEAD (rebase or commit mirror); the branch is pushed at its next commit on a branch"
        return 0
    fi

    # One push per repo at a time: two racing pushes of one repo produced 22
    # "cannot lock ref" failures. The second waits for the first.
    LOCKFILE="$(git rev-parse --git-common-dir)/router-push.lock"
    # MTI_ROUTER_PUSH_LOCK_WAIT: seconds to wait for the lock, default 300. Test-only override.
    LOCK_WAIT="${MTI_ROUTER_PUSH_LOCK_WAIT:-300}"
    exec 9>>"$LOCKFILE" || { fail "[$BRANCH] cannot open lock file $LOCKFILE"; return 1; }
    if ! flock -n 9; then
        say "[$BRANCH] another push of this repo is running; waiting for its lock"
        if ! flock -w "$LOCK_WAIT" 9; then
            HOLDER=$(cat "$LOCKFILE" 2>/dev/null | head -c 20)
            fail "[$BRANCH] lock wait exceeded ${LOCK_WAIT} s; a push of this repo is still holding it (holder pid ${HOLDER:-unreadable})"
            return 1
        fi
    fi
    printf "%s\n" "$$" > "$LOCKFILE"   # holder's pid, for a waiter that times out

    OLD=$(git ls-remote router "refs/heads/$BRANCH" 2>/dev/null | awk '{print $1; exit}')
    NEW=$(git rev-parse HEAD)
    if OUT=$(git push router "HEAD:refs/heads/$BRANCH" 2>&1); then
        say "[$BRANCH] pushed: route=plain push; router tip ${OLD:-none} -> $NEW"
        return 0
    fi

    if scan_refused "$OUT"; then
        fail "[$BRANCH] route=content scan refused (pre-push): $(tr "\n" " " <<<"$OUT")"
        return 1
    fi
    if ! grep -qE "rejected|non-fast-forward|fetch first" <<<"$OUT"; then
        fail "[$BRANCH] route=plain push failed, not a rejection: $(tr "\n" " " <<<"$OUT")"
        return 1
    fi
    if [ -z "$OLD" ]; then
        fail "[$BRANCH] rejected but the router tip could not be read: $(tr "\n" " " <<<"$OUT")"
        return 1
    fi

    # Prove safe or abort (AUR P8): the router tip must be in this branch's own
    # reflog, i.e. this repo once had it as the tip and the router holds this
    # repo's superseded history.
    N=$(git reflog show --format=%H "refs/heads/$BRANCH" 2>/dev/null | grep -n "^$OLD$" | head -1 | cut -d: -f1)
    if [ -z "$N" ]; then
        fail "[$BRANCH] rejected non-fast-forward; router tip $OLD is not in this repo's reflog; resolve with router-safe-resync.sh: $(tr "\n" " " <<<"$OUT")"
        return 1
    fi
    STAMP=$(date +%Y%m%d-%H%M%S)
    BACKUP_REF="refs/backup/${REPO}-${BRANCH}-${STAMP}"
    if ! OUT=$(git push router "$OLD:$BACKUP_REF" 2>&1); then
        scan_refused "$OUT" && fail "[$BRANCH] route=content scan refused (pre-push) on the backup ref: $(tr "\n" " " <<<"$OUT")" && return 1
        fail "[$BRANCH] resync aborted: cannot keep old tip $OLD as $BACKUP_REF: $(tr "\n" " " <<<"$OUT")"
        return 1
    fi
    if ! OUT=$(git push router "HEAD:refs/heads/$BRANCH" --force-with-lease="refs/heads/$BRANCH:$OLD" 2>&1); then
        scan_refused "$OUT" && fail "[$BRANCH] route=content scan refused (pre-push) on the resync push: $(tr "\n" " " <<<"$OUT")" && return 1
        fail "[$BRANCH] resync lease push failed after backing up $OLD as $BACKUP_REF: $(tr "\n" " " <<<"$OUT")"
        return 1
    fi
    say "[$BRANCH] resynced: route=reflog proof (old tip $OLD found in the local reflog at entry $((N-1))); kept as $BACKUP_REF; router tip $OLD -> $NEW"
    return 0
}

if [ "$MODE" = worker ]; then
    worker
    exit $?
fi

LOCAL_CONF="$HOME/develop/git_access.local"
[ -r "$LOCAL_CONF" ] || exit 0

# A repository with no `router` remote is not a backup target — a scratch
# clone, a test fixture's throwaway repo, a project not yet adopted — and
# pushing it is not-applicable, never a failure: on 2026-09-18/19 a test
# suite's fixture repos (seeded with this hook by the init template) raised
# 66 "push FAILED … 'router' does not appear to be a git repository" alarms
# in two evenings, and the real backup was fine the whole time.
git remote get-url router >/dev/null 2>&1 || exit 0

TARGET_MAC=$(grep -oiE '([0-9a-f]{2}:){5}[0-9a-f]{2}' "$LOCAL_CONF" | head -1)
[ -n "$TARGET_MAC" ] || exit 0

GW_IP=$(ip route show default 2>/dev/null | awk '/default/ {print $3; exit}')
[ -n "$GW_IP" ] || exit 0

GW_MAC=$(arp -n "$GW_IP" 2>/dev/null | awk -v ip="$GW_IP" '$1 == ip {print $3}')
[ -n "$GW_MAC" ] || exit 0

if [ "${GW_MAC,,}" = "${TARGET_MAC,,}" ]; then
    # setsid fully detaches into a new session so the push survives the
    # invoking shell/hook exiting before it finishes (plain `&` does not).
    # Every outcome is one log line (success included); a lasting failure is
    # also a desktop notification — a "backup" that fails invisibly isn't a
    # backup (the 2026-07-18 divergence).
    setsid "$0" --worker < /dev/null > /dev/null 2>&1 &
fi
