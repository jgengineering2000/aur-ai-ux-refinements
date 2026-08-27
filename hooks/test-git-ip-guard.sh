#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0 OR LGPL-2.1-or-later
#
# The patent-IP guard decides whether a command would publish a protected
# repository. It is a security control, so the interesting half of this test is
# not that it permits a local backup — it is that widening it to permit one did
# not widen it to permit anything else.
#
# Self-contained: it builds its own throwaway repository and its own local bare
# backup in a temp directory, so it names no real project, depends on no
# particular machine, and runs anywhere.
#
# The cases live in this FILE rather than in the invoking command line, because
# the guard judges the command it is handed: a probe spelling them out inline is
# judged on its own contents and refuses to run. Worth knowing before writing
# the next one.
#
# Exit 0 = every case matched its expectation, 1 = at least one did not.
set -u
GUARD="${GUARD:-$HOME/.claude/git-ip-guard.sh}"
FAILURES=0

TMP=$(mktemp -d) || { echo "cannot create a temp dir" >&2; exit 2; }
trap 'rm -rf "$TMP"' EXIT INT TERM

# A protected repo: any path not on the whitelist is protected by default, and a
# freshly created temp repo never is.
REPO="$TMP/protected"
BACKUP="$TMP/backup.git"
git init -q "$REPO"
git init -q --bare "$BACKUP"
git -C "$REPO" remote add localbak "$BACKUP"

verdict() {
  jq -n --arg c "$1" --arg d "$REPO" '{tool_input:{command:$c},cwd:$d}' \
  | bash "$GUARD" 2>/dev/null | jq -r '.hookSpecificOutput.permissionDecision // "allow"'
}

expect() {   # expect <allow|deny> <label> <command>
  local want="$1" label="$2" cmd="$3" got
  got=$(verdict "$cmd")
  [ -z "$got" ] && got="allow"
  if [ "$got" = "$want" ]; then
    printf '  PASS  %-8s %s\n' "$want" "$label"
  else
    printf '  FAIL  want=%-6s got=%-6s %s\n' "$want" "$got" "$label"
    FAILURES=$((FAILURES + 1))
  fi
}

echo "── must ALLOW: everything that stays on this machine ──"
# A local backup is the case the guard's own comments say it exists to permit,
# and four of these were DENIED until 2026-08-26.
expect allow "push to a local-backup remote"      "git push --mirror localbak"
expect allow "push to a literal local path"       "git push --mirror $BACKUP"
expect allow "clone into a local directory"       "git clone --mirror . $TMP/copy.git"
expect allow "push after a read on the backup"    "git -C $BACKUP rev-list --all --count; git push --mirror localbak"
expect allow "file:// destination"                "git push file://$BACKUP"
expect allow "no git verb at all"                 'ls -la'
expect allow "local-only git verbs"               'git status --short; git commit -m x'

echo "── must DENY: anything that leaves it ──"
expect deny  "https push"                         'git push https://example.com/x/y.git'
expect deny  "ssh push"                           'git push ssh://example.com/x/y.git'
expect deny  "scp-style push to a host"           'git push example.com:x/y.git HEAD'
expect deny  "clone FROM the network"             'git clone https://example.com/x/y.git'
expect deny  "ls-remote against the network"      'git ls-remote https://example.com/x/y.git'
expect deny  "remote add with a network URL"      'git remote add pub https://example.com/x/y.git'
expect deny  "gh subcommand"                      'gh pr create --title x'
expect deny  "send-email"                         'git send-email HEAD'
# Fail-closed: no target named and nothing resolvable. Permission is never the
# default for an unknown destination.
expect deny  "bare push, no resolvable upstream"  'git push'
# The local-path arm must not be reachable by naming a local source alongside a
# network destination — the reason every candidate is examined before clearing,
# rather than the first one winning.
expect deny  "local source, network destination"  'git clone . https://example.com/x/y.git'

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "git-ip-guard: all cases matched."
else
  echo "git-ip-guard: $FAILURES case(s) did not match." >&2
fi
exit $([ "$FAILURES" -eq 0 ] && echo 0 || echo 1)
