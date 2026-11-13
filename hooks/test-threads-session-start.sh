#!/usr/bin/env bash
# Test hooks/threads-session-start.sh: P14 args, missing threads.py notice, live threads ledger output.
set -eu

HOOK="$(cd "$(dirname "$0")" && pwd)/threads-session-start.sh"
AUR="$(cd "$(dirname "$0")/.." && pwd)"
FAILURES=0

check() {
  if [ "$2" -eq 0 ]; then echo "ok    $1"; else echo "FAIL  $1"; FAILURES=$((FAILURES + 1)); fi
}

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

"$HOOK" --help >/dev/null; check "--help exits 0" $?
"$HOOK" -h >/dev/null; check "-h exits 0" $?
if "$HOOK" --bogus >/dev/null 2>&1; then rc=1; else rc=0; fi
check "unknown arg rejected non-zero" $rc
if "$HOOK" "" >/dev/null 2>&1; then rc=1; else rc=0; fi
check "empty string arg rejected non-zero" $rc
if "$HOOK" --help extra >/dev/null 2>&1; then rc=1; else rc=0; fi
check "extra arg after --help rejected non-zero" $rc

# Missing shared-root threads.py emits one clear notice and exits 0.
HOME="$TMP/empty_home" "$HOOK" | grep -q "threads: threads.py is absent"
check "missing threads.py prints one line" $?

# Installed shared-root threads.py prints the ledger status line.
mkdir -p "$TMP/home/.local/aur" "$TMP/proj"
git init -q "$TMP/proj"
ln -s "$AUR/tools/threads.py" "$TMP/home/.local/aur/threads.py"
out=$(HOME="$TMP/home" CLAUDE_PROJECT_DIR="$TMP/proj" "$HOOK")
echo "$out" | grep -q "^threads: 0 open"
check "status --fast prints threads line" $?

echo; [ "$FAILURES" -eq 0 ] && echo "ALL PASSED" || { echo "$FAILURES FAILED"; exit 1; }
