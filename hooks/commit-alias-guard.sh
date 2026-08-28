#!/bin/bash
# PreToolUse(Bash) hook: when a command names a commit id that a history
# rewrite retired, hand the assistant the live id BEFORE the command runs.
#
# Never blocks. A dead id in a command is not a hazard, it is a stale
# reference — and the failure it produces without this hook is a cold "not a
# valid object name", or worse, two ids for one fact that look unrelated. The
# hook exists so that translation arrives unprompted: a lookup the assistant
# has to think to run does not prevent the confusion, because the confusing
# case is precisely the one where nothing suggested looking.
#
# Fast path first: no hex token of commit length in the command means no
# python. The resolver itself lives in the shared root so every project runs
# the same one.
input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty')
[ -z "$cmd" ] && exit 0
printf '%s' "$cmd" | grep -Eq '(^|[^0-9a-fA-F])[0-9a-f]{7,40}([^0-9a-fA-F]|$)' || exit 0
printf '%s' "$input" | python3 "$HOME/.local/aur/commit-alias.py" hook
exit 0
