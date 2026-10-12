#!/usr/bin/env bash
# reply-card.sh — puts the owner's presentation rules (reply-card.md) at the END of the context,
# where they are read.
#   (no argument)  UserPromptSubmit hook: on every genuine owner message. Skips harness-generated
#                  prompts (task notifications, agent hand-backs): the reply after one is usually
#                  an intermediate step, and the card from the owner's last message is still recent.
#   compact        SessionStart(compact) hook: the card plus the instruction to re-read task state.
# The card's authority is AUR's ai_common.md, "The Interaction Contract"; reply-card.md is its
# condensation. Installed as symlinks into ~/.claude/ beside reply-card.md (the card is found
# next to this script), and wired by AUR's installer: UserPromptSubmit -> reply-card.sh,
# SessionStart matcher "compact" -> reply-card.sh compact.
card="$(dirname "$0")/reply-card.md"
if [ "$#" -gt 1 ]; then
  echo "reply-card.sh: unexpected extra argument '$2' - usage: reply-card.sh [compact]" >&2; exit 64
fi
case "${1-}" in
  "")
    prompt=$(python3 -I -c 'import json,sys
try: print(json.load(sys.stdin).get("prompt","")[:200])
except Exception as e: print("reply-card: unreadable hook input: %s" % e, file=sys.stderr)')
    case "$prompt" in
      *"<task-notification>"*|*"<agent-message"*|*"[SYSTEM NOTIFICATION"*) exit 0 ;;
    esac
    cat "$card" ;;
  compact)
    cat "$card"
    echo "CONTEXT WAS JUST COMPACTED: before acting, re-read the active brief / plan / resume file named in the summary - task state lives there, not in the summary." ;;
  -h|--help)
    echo "usage: reply-card.sh [compact]"; exit 0 ;;
  *)
    echo "reply-card.sh: unknown argument '$1' - usage: reply-card.sh [compact]" >&2; exit 64 ;;
esac
