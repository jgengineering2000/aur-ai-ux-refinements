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
    # invoking shell/hook exiting before it finishes (plain `&` does not —
    # a killed process group takes the backgrounded push with it).
    # Success stays silent; failure (e.g. the router branch diverged from a
    # local rebase/amend) is both logged AND surfaced with a desktop
    # notification — a "backup" that fails invisibly isn't a backup. This is
    # what caught the 2026-07-18 divergence: silent failure let two repos drift
    # unbacked-up for a week before anyone noticed.
    setsid bash -c '
        LOG="$HOME/.claude/git-templates/router-push.log"
        REPO=$(basename "$(git rev-parse --show-toplevel 2>/dev/null)")
        if ! OUT=$(git push router HEAD --no-verify 2>&1); then
            printf "%s [%s] push FAILED: %s\n" "$(date -Iseconds)" "$REPO" "$(tr "\n" " " <<<"$OUT")" >> "$LOG"
            command -v notify-send >/dev/null 2>&1 && notify-send -u critical \
                "Router backup push FAILED — $REPO" \
                "$(head -c 200 <<<"$OUT")  See $LOG"
        fi
    ' < /dev/null > /dev/null 2>&1 &
fi
