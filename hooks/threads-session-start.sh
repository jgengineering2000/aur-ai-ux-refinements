#!/bin/sh
# SessionStart hook: print the thread ledger's one status line (the fast sweep: no per-lab tree scan).
# Output on stdout becomes session context. Never fails the session: a missing tool prints one line.

USAGE="usage: threads-session-start.sh [-h|--help]"
if [ "$#" -eq 1 ] && { [ "$1" = "-h" ] || [ "$1" = "--help" ]; }; then
  printf '%s\n' "$USAGE"
  exit 0
elif [ "$#" -ne 0 ]; then
  printf 'threads-session-start.sh: unknown or unexpected argument `%s`\n%s\n' "$1" "$USAGE" >&2
  exit 2
fi

H="$HOME"
TOOL="$H/.local/aur/threads.py"
if [ -f "$TOOL" ]; then
  python3 -I "$TOOL" --root "${CLAUDE_PROJECT_DIR:-.}" status --fast 2>&1 || echo "threads: status failed (exit $?); run threads.py status"
else
  echo "threads: threads.py is absent from the AUR shared root; the thread ledger is not being checked"
fi
