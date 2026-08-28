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
# --template= : NO hooks. The machine's init template installs the universal
# post-commit into every new repository, and that hook pushes to a `router`
# remote a throwaway repo does not have — logged as "[repo] push FAILED" and
# raised as a critical desktop notification, five times on 2026-08-28, each
# one a run of this test. A test must not page the owner.
git init -q --template= "$REPO"
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
# The shell's cwd is not the command's: the session that first exercised the
# guard ran `cd <repo> && git show <dead>` from ANOTHER repository and got
# silence, which read as the hook not firing.
OTHER="$TMP/other"; git init -q --template= "$OTHER"
run_from() {  # run_from <label> <cwd> <command> <expect>
    local label=$1 cwd=$2 cmd=$3 expect=$4 out
    out=$(jq -cn --arg c "$cmd" --arg d "$cwd" '{tool_input:{command:$c},cwd:$d}' | bash "$GUARD")
    if [ "$expect" = context ] && printf '%s' "$out" | grep -q "${LIVE:0:7}"; then echo "ok    $label"
    elif [ "$expect" = silent ] && [ -z "$out" ]; then echo "ok    $label"
    else echo "FAIL  $label: got: $out"; FAILURES=$((FAILURES+1)); fi
}
run_from "cd into the repo, from elsewhere" "$OTHER" "cd $REPO && git show ${OLD:0:7}" context
run_from "git -C the repo, from elsewhere"  "$OTHER" "git -C $REPO log ${OLD:0:7}"  context
run_from "unrelated repo, no cd"            "$OTHER" "git show ${OLD:0:7}"          silent
run "dead id, full form"    "git log $OLD"                       context
run "live id"               "git show ${LIVE:0:7}"               silent
run "no hex at all"         "ls -la"                             silent
run "md5-looking token"     "grep 9e107d9d372bb6826bd81d3542a419d6 x"  silent

[ "$FAILURES" -eq 0 ] && echo "all cases passed" || echo "$FAILURES failure(s)"
exit $(( FAILURES > 0 ))
