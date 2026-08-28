#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0 OR LGPL-2.1-or-later
#
# The alias guard's whole value is that it speaks UNPROMPTED and never blocks.
# So the cases are: a dead id in a command produces additionalContext naming
# the live id; a live id produces nothing; a command with no hex produces
# nothing and does not even start python; and every case exits 0.
#
# Self-contained: builds a throwaway repository with one commit and a
# hand-written map that retires a fictitious old id onto it.
set -u
GUARD="${GUARD:-$HOME/.local/aur/hooks/commit-alias-guard.sh}"
FAILURES=0
TMP=$(mktemp -d) || { echo "cannot create a temp dir" >&2; exit 2; }
trap 'rm -rf "$TMP"' EXIT INT TERM

REPO="$TMP/repo"
git init -q "$REPO"
# --no-verify: the machine's git template installs the universal pre-commit
# into every new repo, and its content guard has nothing to say about an
# empty commit except that it is empty.
git -C "$REPO" -c user.name=t -c user.email=t@t commit -q --no-verify --allow-empty -m "the live commit"
LIVE=$(git -C "$REPO" rev-parse HEAD)
OLD="1111111111111111111111111111111111111111"
mkdir -p "$REPO/tools/history"
printf 'old new\n%s %s\n' "$OLD" "$LIVE" > "$REPO/tools/history/test.commit-map"

run() {  # run <label> <command> <expect: context|silent>
    local label=$1 cmd=$2 expect=$3 out rc
    out=$(jq -cn --arg c "$cmd" --arg d "$REPO" '{tool_input:{command:$c},cwd:$d}' | bash "$GUARD")
    rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "FAIL  $label: exit $rc (must never block)"; FAILURES=$((FAILURES+1)); return
    fi
    case "$expect" in
      context)
        if printf '%s' "$out" | grep -q "additionalContext" && printf '%s' "$out" | grep -q "${LIVE:0:7}"; then
            echo "ok    $label"
        else
            echo "FAIL  $label: expected the live id ${LIVE:0:7} in context, got: $out"; FAILURES=$((FAILURES+1))
        fi;;
      silent)
        if [ -z "$out" ]; then echo "ok    $label"
        else echo "FAIL  $label: expected silence, got: $out"; FAILURES=$((FAILURES+1)); fi;;
    esac
}

run "dead id, short form"   "git show ${OLD:0:7} -- README"      context
run "dead id, full form"    "git log $OLD"                       context
run "live id"               "git show ${LIVE:0:7}"               silent
run "no hex at all"         "ls -la"                             silent
run "md5-looking token"     "grep 9e107d9d372bb6826bd81d3542a419d6 x"  silent

[ "$FAILURES" -eq 0 ] && echo "all cases passed" || echo "$FAILURES failure(s)"
exit $(( FAILURES > 0 ))
