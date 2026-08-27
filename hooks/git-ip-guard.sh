#!/bin/bash
# PreToolUse(Bash) hook: deterministic patent-IP guard.
# Default-deny: network-touching git/gh operations are blocked unless the
# repo is listed in ~/.claude/git-public-projects.txt (exact path or listed
# path + "/"). Enforces in the harness what ai_common.md states as policy,
# so it holds even if the model forgets or a permission rule allows the call.
#
# The question is "does this send data off this machine", and that is a
# property of the RESOLVED TARGET, not of the command's spelling. A named
# remote is therefore resolved to its configured URL before being judged:
# `git push router main` reaches a bare repo on a mounted USB drive and is a
# backup, while `git fetch origin` may reach GitHub and is not. Matching
# command text alone got both of those wrong, in opposite directions — it
# blocked the sanctioned router backup for every protected repo, and let a
# fetch against a configured public remote straight through.

WHITELIST="$HOME/.claude/git-public-projects.txt"

input=$(cat)
cmd=$(jq -r '.tool_input.command // empty' <<<"$input")
cwd=$(jq -r '.cwd // empty' <<<"$input")
[ -z "$cmd" ] && exit 0

# A heredoc body is DATA, not command. Without this, writing a commit message
# that mentions `git push --no-verify` is refused as though it performed one —
# which it did, on the commit explaining this very rule. Same root cause as the
# defect below: judging text instead of what the text does.
#
# The exception is the body that IS executed: `bash <<EOF` and friends feed
# their heredoc to an interpreter, so those bodies stay in scope. Stripping
# them would be a bypass with an obvious recipe.
strip_heredocs() {
  # Built as variables: `\<` inside an ERE is a word BOUNDARY, not a literal
  # '<', so the escaped-looking form silently matched nothing and stripped
  # nothing. The test caught it; the eye did not.
  local heredoc_re="<<-?[[:space:]]*['\"]?([A-Za-z_][A-Za-z0-9_]*)"
  local interp_re='(^|[[:space:]/])(bash|sh|zsh|ksh|dash|python3?|perl|ruby|env)([[:space:]]|$)'
  local line tag="" out="" trimmed
  while IFS= read -r line; do
    if [ -n "$tag" ]; then
      trimmed="${line#"${line%%[![:space:]]*}"}"
      trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"
      [ "$trimmed" = "$tag" ] && tag=""
      continue
    fi
    # Capture the tag IMMEDIATELY. Every `=~` rewrites BASH_REMATCH, including
    # one that fails, so reading it after the interpreter test below always
    # yielded the empty string and nothing was ever stripped.
    if [[ "$line" =~ $heredoc_re ]]; then
      local candidate="${BASH_REMATCH[1]}"
      [[ "$line" =~ $interp_re ]] || tag="$candidate"
    fi
    out+="$line"$'\n'
  done <<<"$1"
  printf '%s' "$out"
}
cmd=$(strip_heredocs "$cmd")

deny() {
  jq -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}

# Fast path: nothing git/gh-shaped in the command.
grep -qE '(^|[;&|[:space:]])(git|gh)([[:space:]]|$)' <<<"$cmd" || exit 0

# Resolve the repo the operation targets: git -C <path> wins, else hook cwd.
dir=$(grep -oE '(git|gh)[[:space:]]+-C[[:space:]]+[^ ]+' <<<"$cmd" | head -1 | awk '{print $3}')
[ -z "$dir" ] && dir="$cwd"
[ -z "$dir" ] && dir="$PWD"
repo=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null || realpath "$dir" 2>/dev/null)

# A target leaves this machine only if it is a real network URL. Local paths
# and file:// reach a mounted filesystem; the router's USB drive is the
# sanctioned backup for exactly the repos this guard protects, so treating
# that push as disclosure blocked the backup it exists to permit.
is_offbox() {
  case "$1" in
    ""|/*|./*|../*|~/*|file://*)    return 1 ;;
    ssh://*|https://*|http://*|git://*) return 0 ;;
    *:*)  [ -e "${1%%:*}" ] && return 1 || return 0 ;;   # scp-style host:path
    *)    return 1 ;;
  esac
}

# A token that names a place on this filesystem, as opposed to one that might
# be a remote's NAME. The distinction matters: `origin` is a bare word and could
# resolve to anything, so it must be looked up; `/mnt/router-usb/...` is a path
# and needs no lookup to be judged.
is_local_path() {
  case "$1" in
    /*|./*|../*|~/*|file://*) return 0 ;;
    *) [ -e "$1" ] && return 0 || return 1 ;;
  esac
}

# Named remote -> configured URL. Failure to resolve is NOT permission: an
# unknown name is judged offbox, because unknown provenance is not a reason
# to allow.
#
# Tried in the -C directory first, then the hook's cwd. `dir` is taken from the
# FIRST `git -C` anywhere in the command line, so in a compound command it can
# belong to a different invocation entirely — a read against the backup followed
# by a push to it resolved `router` inside the bare backup repo, found no such
# remote, and failed closed. Falling back to cwd fixes that without loosening
# anything: a name that resolves to a network URL still condemns.
resolve_remote() {
  git -C "$dir" remote get-url "$1" 2>/dev/null \
    || { [ -n "$cwd" ] && git -C "$cwd" remote get-url "$1" 2>/dev/null; }
}

# Judge a subcommand by its target. Any candidate resolving offbox condemns
# the command; a named remote resolving on-box clears it.
targets_offbox() {
  local sub="$1" tok u tail saw_target=0
  tail=$(sed -E "s/.*[[:space:]]${sub}([[:space:]]|$)//" <<<"$cmd")
  # EVERY candidate is examined before clearing, never just the first. A
  # `clone . <somewhere>` names a local source and a destination that may not
  # be local, so returning on the first on-box token would clear a command by
  # its own working directory.
  for tok in $tail; do
    case "$tok" in -*|'|'*|';'*|'&'*) continue ;; esac
    is_offbox "$tok" && return 0
    if is_local_path "$tok"; then
      saw_target=1
      continue
    fi
    if u=$(resolve_remote "$tok") && [ -n "$u" ]; then
      is_offbox "$u" && return 0
      saw_target=1
    fi
  done
  # A concrete target was named and nothing about it leaves this machine. This
  # arm was missing, so a plainly local path fell past the loop into the
  # fail-closed fallback below — which exists for "no target named at all" and
  # was condemning "target named, and it was local". That blocked the sanctioned
  # backup this guard's own comments say it exists to permit.
  [ "$saw_target" -eq 1 ] && return 1
  # No target named (`git push`, `git fetch`): fall back to the branch's
  # configured upstream remote, else origin.
  local br def
  br=$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null)
  def=$(git -C "$dir" config --get "branch.${br}.remote" 2>/dev/null)
  [ -z "$def" ] && def="origin"
  if u=$(resolve_remote "$def") && [ -n "$u" ]; then
    is_offbox "$u" && return 0
    return 1
  fi
  return 0    # nothing resolvable: fail closed
}

net=0
grep -qE '(^|[;&|[:space:]])gh[[:space:]]+(repo|pr|release|issue|gist|api|workflow|run|secret|codespace|search|browse)' <<<"$cmd" && net=1
grep -qE 'git[^;&|]*[[:space:]](send-email|request-pull)([[:space:]]|$)' <<<"$cmd" && net=1
for sub in push fetch pull clone ls-remote; do
  grep -qE "git([[:space:]]+-C[[:space:]]+[^ ]+)?[^;&|]*[[:space:]]${sub}([[:space:]]|$)" <<<"$cmd" || continue
  targets_offbox "$sub" && net=1
done
if grep -qE 'git[^;&|]*[[:space:]]remote[[:space:]]+(add|set-url)[[:space:]]' <<<"$cmd"; then
  for tok in $cmd; do is_offbox "$tok" && net=1; done
fi
[ "$net" -eq 0 ] && exit 0

# Bypassing the pre-push hook on a network op is a flag regardless of the
# whitelist.
#
# This tests --no-verify ONLY. It also matched the literal string
# "hooks/pre-push", which cannot distinguish INSTALLING that hook from
# bypassing it, and so refused every command that wrote, read or listed the
# file — including deploying the guard itself. A path appearing in a command
# is not evidence of intent, and a real bypass has routes no string match
# could enumerate (core.hooksPath, git config, chmod), so it bought nothing
# and cost the ability to administer the thing it protected.
grep -qE '(^|[[:space:]])--no-verify([[:space:]]|$)' <<<"$cmd" && \
  deny "PATENT-IP GUARD: --no-verify on a network git operation requires explicit in-conversation owner acknowledgement of public disclosure."

if [ -n "$repo" ] && [ -f "$WHITELIST" ]; then
  while IFS= read -r line; do
    line="${line%%#*}"; line="$(tr -d '[:space:]' <<<"$line")"
    [ -z "$line" ] && continue
    if [ "$repo" = "$line" ] || [[ "$repo" == "$line"/* ]]; then
      exit 0   # whitelisted: allowed
    fi
  done < "$WHITELIST"
fi

deny "PATENT-IP GUARD: '$repo' is not in ~/.claude/git-public-projects.txt (default-deny), and this target resolves OFF this machine, so it would publish. Command: $cmd"
