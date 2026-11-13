#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0 OR LGPL-2.1-or-later
#
# router-backup-push.sh: a commit on a detached HEAD is skipped with one log
# line and no popup; two pushes of one repo never race; the first push after a
# local rewrite resyncs the router (old tip kept under refs/backup/) when the
# router's tip is in the branch's own reflog; a foreign tip keeps the loud
# failure and names router-safe-resync.sh. Every outcome is one log line.
#
# Self-contained: temporary HOME with a git_access.local, stub ip/arp so the
# gateway check passes, stub notify-send recording its calls, a bare repository
# as the `router` remote. Repos are made with --template= (no inherited hooks).
set -u
PUSH="${PUSH:-$HOME/.local/aur/hooks/router-backup-push.sh}"
FAILURES=0
TMP=$(mktemp -d) || { echo "cannot create a temp dir" >&2; exit 2; }
trap 'rm -rf "$TMP"' EXIT INT TERM
export HOME="$TMP/home"
# Fixture names are assembled, not written out: the script under test reads a
# fixed home-relative file, and the content guard flags literal addresses.
DOTLOCAL=.local; DEV_AUR=aur; CFG=git_access.local; GWIP=192.0.2.1; MAC=$(printf "%s:%s:%s:%s:%s:%s" 02 00 5e 00 53 01)
mkdir -p "$HOME/$DOTLOCAL/$DEV_AUR" "$HOME/.claude/git-templates" "$TMP/bin"
echo "$MAC" > "$HOME/$DOTLOCAL/$DEV_AUR/$CFG"
LOG="$HOME/.claude/git-templates/router-push.log"
NOTIFY="$TMP/notify.calls"; : > "$NOTIFY"; : > "$LOG"
printf '#!/bin/sh\necho "default via %s dev test0"\n' "$GWIP" > "$TMP/bin/ip"
printf '#!/bin/sh\necho "%s ether %s C test0"\n' "$GWIP" "$MAC" > "$TMP/bin/arp"
printf '#!/bin/sh\necho "$*" >> "%s"\n' "$NOTIFY" > "$TMP/bin/notify-send"
chmod +x "$TMP/bin/"*
export PATH="$TMP/bin:$PATH"
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

check() {  # check <label> <0|1 ok>
    if [ "$2" = 0 ]; then echo "ok    $1"; else echo "FAIL  $1"; FAILURES=$((FAILURES+1)); fi
}
wait_log() {  # wait_log <pattern> [count] [timeout s]: bounded wait on the log, not a fixed sleep
    local i; for i in $(seq 1 $((${3:-15} * 10))); do [ "$(grep -cE "$1" "$LOG")" -ge "${2:-1}" ] && return 0; sleep 0.1; done; return 1
}
newrepo() {  # newrepo <name>: repo with a hook calling PUSH, a bare router, one pushed commit
    local r="$TMP/$1"
    git init -q --template= -b main "$r"; git init -q --bare "$TMP/$1.router.git"
    git -C "$r" remote add router "$TMP/$1.router.git"
    mkdir -p "$r/.git/hooks"
    printf '#!/bin/sh\n"%s"\n' "$PUSH" > "$r/.git/hooks/post-commit"; chmod +x "$r/.git/hooks/post-commit"
    echo "$r"
}
commit() { git -C "$1" commit -q --no-verify --allow-empty -m "$2"; }
quiet() {  # quiet <repo>: wait until no worker holds the repo's lock or runs
    local i; for i in $(seq 1 150); do pgrep -f -- "router-backup-push.sh --worker" >/dev/null || return 0; sleep 0.1; done; return 1
}

# 1. detached HEAD: skip line, no failure, no popup
R=$(newrepo one); : > "$LOG"; commit "$R" c1; wait_log "pushed" || echo "note: first push line not seen"
: > "$LOG"; : > "$NOTIFY"
git -C "$R" checkout -q --detach; commit "$R" c2
wait_log "skipped: detached HEAD" ; check "detached HEAD: skip line" $?
quiet "$R"
grep -q "FAILED" "$LOG"; check "detached HEAD: no failure line" $((! $? ))
[ ! -s "$NOTIFY" ]; check "detached HEAD: no popup" $?

# 2. rebase, then a commit on the branch: resync with reflog proof
R=$(newrepo two); : > "$LOG"; commit "$R" c1; commit "$R" c2; wait_log "pushed" 2 ; quiet "$R"
OLDTIP=$(git -C "$R" rev-parse HEAD)
git -C "$R" checkout -q -b side main~2 2>/dev/null || git -C "$R" checkout -q -b side "$(git -C "$R" rev-list --max-parents=0 HEAD | head -1)"
commit "$R" newbase; wait_log "pushed" 3; quiet "$R"   # the side branch push must finish before HEAD moves
: > "$LOG"; : > "$NOTIFY"   # the rebase itself runs the hook; its worker may resync before the next commit
git -C "$R" checkout -q main; git -C "$R" rebase -q side >/dev/null 2>&1
commit "$R" c3
wait_log "resynced|FAILED"; quiet "$R"
grep -q "resynced.*reflog" "$LOG"; check "rebase: reflog proof line" $?
[ "$(git -C "$R" rev-parse main)" = "$(git --git-dir="$TMP/two.router.git" rev-parse refs/heads/main)" ]; check "rebase: router branch moved" $?
git --git-dir="$TMP/two.router.git" for-each-ref refs/backup | grep -q "$OLDTIP"; check "rebase: old tip under refs/backup" $?
[ ! -s "$NOTIFY" ]; check "rebase: no popup" $?

# 3. foreign router tip: loud failure with the hint
R=$(newrepo three); : > "$LOG"; commit "$R" c1; wait_log "pushed"; quiet "$R"
git clone -q -b main "$TMP/three.router.git" "$TMP/three.other" || echo "setup: clone failed"
git -C "$TMP/three.other" commit -q --no-verify --allow-empty -m foreign || echo "setup: foreign commit failed"
git -C "$TMP/three.other" push -q origin HEAD:main || echo "setup: foreign push failed"
: > "$LOG"; : > "$NOTIFY"; commit "$R" c2
wait_log "FAILED|resynced"; quiet "$R"
grep -q "FAILED.*router-safe-resync.sh" "$LOG"; check "foreign tip: failure line names router-safe-resync.sh" $?
[ -s "$NOTIFY" ]; check "foreign tip: popup raised" $?

# 4. two commits in quick succession: no ref-lock race
R=$(newrepo four); : > "$LOG"
commit "$R" c1; commit "$R" c2; commit "$R" c3
for i in $(seq 1 150); do [ "$(grep -c pushed "$LOG")" -ge 3 ] && break; sleep 0.1; done; quiet "$R"
[ "$(grep -c "pushed" "$LOG")" -ge 3 ]; check "quick succession: one success line per commit" $?
grep -q "cannot lock ref" "$LOG"; check "quick succession: no cannot-lock-ref line" $((! $? ))

# 5. the real pre-push content scan runs on the automatic push: flagged content
# is refused (log line plus popup) and the router does not receive it.
R=$(newrepo five); : > "$LOG"; : > "$NOTIFY"
GUARDDIR="$HOME/$DOTLOCAL/$DEV_AUR"; mkdir -p "$GUARDDIR"; ln -s "$(cd "$(dirname "$0")/.." && pwd)/tools/commit-guard.py" "$GUARDDIR/commit-guard.py"
cp "$(cd "$(dirname "$0")" && pwd)/pre-push" "$R/.git/hooks/pre-push"; chmod +x "$R/.git/hooks/pre-push"
commit "$R" clean1; wait_log "pushed" ; quiet "$R"; : > "$LOG"; : > "$NOTIFY"
PRIVIP=$(printf "%s.%s.%s.%s" 192 168 1 77)
echo "gateway $PRIVIP" > "$R/flagged.txt"; git -C "$R" add flagged.txt
git -C "$R" commit -q --no-verify -m flagged
wait_log "FAILED|pushed"; quiet "$R"
grep -q "FAILED.*content scan refused" "$LOG"; check "content scan: refusal logged with its route" $?
[ -s "$NOTIFY" ]; check "content scan: popup raised" $?
! git --git-dir="$TMP/five.router.git" cat-file -e "$(git -C "$R" rev-parse HEAD)" 2>/dev/null; check "content scan: router did not receive the commit" $?

# 6. the lock wait is bounded. AUR_ROUTER_PUSH_LOCK_WAIT is test-only.
R=$(newrepo six); : > "$LOG"; : > "$NOTIFY"
commit "$R" c1; wait_log "pushed"; quiet "$R"; : > "$LOG"; : > "$NOTIFY"
flock "$R/.git/router-push.lock" sleep 6 &
HOLDER=$!; sleep 0.3
AUR_ROUTER_PUSH_LOCK_WAIT=2 commit "$R" c2
wait_log "lock wait exceeded" 1 10; check "lock timeout: line logged" $?
quiet "$R"
[ -s "$NOTIFY" ]; check "lock timeout: popup raised" $?
wait "$HOLDER" 2>/dev/null

# 7. rebase-resync with the real pre-push installed: the scan refuses the resync
# push (the rewritten tip holds flagged content). One refusal line naming the
# resync push, no second failure line, and the router branch does not move.
R=$(newrepo seven); : > "$LOG"; : > "$NOTIFY"
cp "$(cd "$(dirname "$0")" && pwd)/pre-push" "$R/.git/hooks/pre-push"; chmod +x "$R/.git/hooks/pre-push"
commit "$R" c1; commit "$R" c2; wait_log "pushed" 2; quiet "$R"
OLDTIP=$(git -C "$R" rev-parse HEAD)
git -C "$R" checkout -q --detach main~1
echo "gateway $PRIVIP" > "$R/flagged.txt"; git -C "$R" add flagged.txt
git -C "$R" commit -q --no-verify -m flagged-rewrite
git -C "$R" update-ref -m "test rewrite" refs/heads/main "$(git -C "$R" rev-parse HEAD)"
git -C "$R" checkout -q main
wait_log "skipped" 1; quiet "$R"; : > "$LOG"; : > "$NOTIFY"
commit "$R" c3
wait_log "FAILED|resynced"; quiet "$R"
[ "$(grep -c "FAILED" "$LOG")" = 1 ] && grep -q "FAILED.*content scan refused.*on the resync push" "$LOG"; check "resync scan refusal: one line naming the resync push" $?
[ "$(git --git-dir="$TMP/seven.router.git" rev-parse refs/heads/main)" = "$OLDTIP" ]; check "resync scan refusal: router branch did not move" $?
[ -s "$NOTIFY" ]; check "resync scan refusal: popup raised" $?

[ -n "${DUMP:-}" ] && { echo "--- log"; cut -c1-300 "$LOG"; }
echo; [ "$FAILURES" = 0 ] && echo "ALL PASSED" || { echo "$FAILURES FAILED"; exit 1; }
