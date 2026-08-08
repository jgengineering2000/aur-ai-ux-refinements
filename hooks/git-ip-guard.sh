#!/bin/bash
# PreToolUse(Bash) hook: deterministic patent-IP guard.
# Default-deny: network-touching git/gh operations are blocked unless the
# repo is listed in ~/.claude/git-public-projects.txt (exact path or listed
# path + "/"). Enforces in the harness what ai_common.md states as policy,
# so it holds even if the model forgets or a permission rule allows the call.

WHITELIST="$HOME/.claude/git-public-projects.txt"

input=$(cat)
cmd=$(jq -r '.tool_input.command // empty' <<<"$input")
cwd=$(jq -r '.cwd // empty' <<<"$input")
[ -z "$cmd" ] && exit 0

deny() {
  jq -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}

# Fast path: nothing git/gh-shaped in the command.
grep -qE '(^|[;&|[:space:]])(git|gh)([[:space:]]|$)' <<<"$cmd" || exit 0

# Network-touching operations (per ai_common.md Patent-IP section).
# URL = ://-style or scp-style git@host:, excluding file:// and local paths.
url_re='((ssh|https?|git)://|git@[^ ]+:)'
net=0
grep -qE '(^|[;&|[:space:]])gh[[:space:]]+(repo|pr|release|issue|gist|api|workflow|run|secret|codespace|search|browse)' <<<"$cmd" && net=1
grep -qE 'git([[:space:]]+-C[[:space:]]+[^ ]+)?[[:space:]]+push([[:space:]]|$)' <<<"$cmd" && net=1
grep -qE 'git[^;&|]*[[:space:]](send-email|request-pull)([[:space:]]|$)' <<<"$cmd" && net=1
grep -qE 'git[^;&|]*[[:space:]]ls-remote[[:space:]]' <<<"$cmd" && grep -qE "$url_re" <<<"$cmd" && net=1
grep -qE 'git[^;&|]*[[:space:]](fetch|pull|clone)[[:space:]]' <<<"$cmd" && grep -qE "$url_re" <<<"$cmd" && net=1
grep -qE 'git[^;&|]*[[:space:]]remote[[:space:]]+(add|set-url)[[:space:]]' <<<"$cmd" && grep -qE "$url_re" <<<"$cmd" && net=1
[ "$net" -eq 0 ] && exit 0

# Hook circumvention on a network op is a flag regardless of whitelist.
grep -qE '(--no-verify|hooks/pre-push)' <<<"$cmd" && \
  deny "PATENT-IP GUARD: --no-verify / pre-push-hook circumvention on a network git operation requires explicit in-conversation owner acknowledgement of public disclosure."

# Resolve the repo the operation targets: git -C <path> wins, else hook cwd.
dir=$(grep -oE '(git|gh)[[:space:]]+-C[[:space:]]+[^ ]+' <<<"$cmd" | head -1 | awk '{print $3}')
[ -z "$dir" ] && dir="$cwd"
[ -z "$dir" ] && dir="$PWD"
repo=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null || realpath "$dir" 2>/dev/null)

if [ -n "$repo" ] && [ -f "$WHITELIST" ]; then
  while IFS= read -r line; do
    line="${line%%#*}"; line="$(tr -d '[:space:]' <<<"$line")"
    [ -z "$line" ] && continue
    if [ "$repo" = "$line" ] || [[ "$repo" == "$line"/* ]]; then
      exit 0   # whitelisted: allowed
    fi
  done < "$WHITELIST"
fi

deny "PATENT-IP GUARD: '$repo' is not in ~/.claude/git-public-projects.txt (default-deny). Network git/gh operations are blocked for patent-IP-protected projects. Command: $cmd"
