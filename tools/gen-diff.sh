#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
#
# gen-diff.sh — render the working-tree diff to an HTML page grouped by
# BODY OF WORK, served via the doc server (Alt+D).
#
# Grouping model:
#   - Bodies of work are labeled EXPLICITLY at generation time via --group:
#       --group "Title of the body of work" file1 file2 ...  [--group "Next" ...]
#     A file may appear in MORE THAN ONE --group; it is shown WHOLE under each.
#   - Any changed file NOT named in a --group falls through to an
#     "Uncategorized (by type)" section bucketed by file type (Specs / Rust /
#     Python / C/C++ / JS/TS / Shell / Config / Memory / Docs / Other).
#
# Scope of "changed": all tracked changes (staged + unstaged combined) relative
# to --base (default HEAD).

set -euo pipefail

USAGE="usage: gen-diff.sh [--base <rev>] [--group <title> <file>...]
  --base <rev>           compare working tree against <rev> (default: HEAD)
  --group <title> ...    assign following files to a named body of work
  -h, --help             this text"

BASE="HEAD"
GROUP_TITLES=()
GROUP_FILES=()
cur_title=""
cur_files=""

flush_group() {
  if [ -n "$cur_title" ]; then
    GROUP_TITLES+=("$cur_title")
    GROUP_FILES+=("$cur_files")
  fi
  cur_title=""
  cur_files=""
}

end_of_opts=0
while [ "$#" -gt 0 ]; do
  if [ "$end_of_opts" -eq 0 ]; then
    case "$1" in
      -h|--help)
        printf '%s\n' "$USAGE"
        exit 0
        ;;
      --base)
        if [ "$#" -lt 2 ] || [ -z "${2:-}" ]; then
          printf 'gen-diff.sh: --base requires a revision argument\n%s\n' "$USAGE" >&2
          exit 2
        fi
        BASE="$2"
        shift 2
        continue
        ;;
      --group)
        if [ "$#" -lt 2 ] || [ -z "${2:-}" ]; then
          printf 'gen-diff.sh: --group requires a non-empty title argument\n%s\n' "$USAGE" >&2
          exit 2
        fi
        flush_group
        cur_title="$2"
        shift 2
        continue
        ;;
      --)
        end_of_opts=1
        shift
        continue
        ;;
      -*)
        printf 'gen-diff.sh: unknown option `%s`\n%s\n' "$1" "$USAGE" >&2
        exit 2
        ;;
    esac
  fi
  if [ -z "$cur_title" ]; then
    printf 'gen-diff.sh: unexpected argument `%s` (use --group <title> before file names)\n%s\n' "$1" "$USAGE" >&2
    exit 2
  fi
  cur_files+="${1}"$'\n'
  shift
done
flush_group

TOP="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$TOP" ]; then
  printf 'gen-diff.sh: not inside a git repository\n%s\n' "$USAGE" >&2
  exit 2
fi
cd "$TOP"

if ! git rev-parse --verify --quiet "${BASE}^{commit}" >/dev/null; then
  printf 'gen-diff.sh: unknown revision `%s`\n%s\n' "$BASE" "$USAGE" >&2
  exit 2
fi

TS="$(date +%Y-%m-%d-%H%M%S)"
OUT="aur-docs/diff-${TS}.html"
mkdir -p aur-docs

# All changed tracked files relative to BASE (covers both staged and unstaged).
mapfile -t ALL_CHANGED < <(git -c core.quotePath=false diff --name-only "$BASE" | awk 'NF' | sort -u)

# Set of files that ARE named in some group (for fallback exclusion).
labeled_set=$'\n'
if [ "${#GROUP_FILES[@]}" -gt 0 ]; then
  for gf in "${GROUP_FILES[@]}"; do labeled_set+="$gf"; done
fi

is_labeled() { printf '%s' "$labeled_set" | grep -qxF "$1"; }

categorize() {
  local f="$1" base; base="$(basename "$f")"
  case "$f" in
    .claude/memory/*) echo "Memory" ;;
    aur-docs/*|claude-docs/*) echo "Docs" ;;
    *.rs) echo "Rust" ;;
    *.py) echo "Python" ;;
    *.c|*.cc|*.cpp|*.cxx|*.h|*.hh|*.hpp) echo "C/C++" ;;
    *.js|*.jsx|*.ts|*.tsx|*.mjs|*.cjs) echo "JS/TS" ;;
    *.sh|*.bash|*.zsh) echo "Shell" ;;
    .claude/*|Makefile|*/Makefile|Cargo.toml|*/Cargo.toml|pyproject.toml|*.toml|*.yaml|*.yml|*.json|*.service|.gitignore) echo "Config" ;;
    *) if [[ "$base" =~ ^[A-Z0-9_.-]+\.md$ ]]; then echo "Specs"; else echo "Other"; fi ;;
  esac
}

esc() { sed -e 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g; s/"/\&quot;/g'; }

# Emit one <details> block with the full diff of a file against BASE.
emit_file() {
  local f="$1" stat_html
  read -r ADD DEL _ < <(git -c core.quotePath=false diff "$BASE" --numstat -- "$f" | head -1)
  ADD="${ADD:-0}"; DEL="${DEL:-0}"
  if [ "$ADD" = "-" ] && [ "$DEL" = "-" ]; then
    stat_html='<span class="mut">binary</span>'
  else
    stat_html="<span class=\"p\">+${ADD}</span> <span class=\"m\">-${DEL}</span>"
  fi
  printf '<details class="file"><summary><span class="fname">%s</span><span class="stat">%s</span></summary>' \
    "$(printf '%s' "$f" | esc)" "$stat_html"
  printf '<pre class="diff">'
  git -c core.quotePath=false diff "$BASE" -- "$f" | while IFS= read -r line || [ -n "$line" ]; do
    cls="ctx"
    case "$line" in
      '+++ '*|'--- '*) cls="meta" ;;
      '@@'*) cls="hunk" ;;
      'diff --git'*|'index '*|'new file'*|'deleted file'*|'similarity'*|'rename '*|'Binary files'*) cls="meta" ;;
      '+'*) cls="add" ;;
      '-'*) cls="del" ;;
    esac
    if [ "$cls" = "add" ] || [ "$cls" = "del" ]; then body="${line:1}"; else body="$line"; fi
    printf '<span class="ln %s">%s</span>\n' "$cls" "$(printf '%s' "$body" | esc)"
  done
  printf '</pre></details>'
}

{
cat <<'HEAD_HTML'
<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Working-tree diff (by body of work)</title>
<link rel="stylesheet" href="/ann.css">
<link rel="stylesheet" href="/doc.css">
<style>
.wrap{max-width:1200px;margin:0 auto;padding:24px 22px 120px}
h1{font-size:22px;margin:0 0 2px}
.sub{color:var(--mut);font-size:13px;margin:0 0 16px}
.toc{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin:0 0 16px;font-size:13px}
.toc a{color:var(--accent);text-decoration:none;margin-right:14px;white-space:nowrap;display:inline-block;margin-bottom:4px}
.controls{margin:0 0 14px}
.controls button{background:var(--panel2);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:5px 11px;font-size:12px;cursor:pointer;margin-right:8px}
.controls button:hover{border-color:var(--accent)}
.group{margin:24px 0 6px}
.group h2{font-size:17px;color:var(--accent);border-bottom:2px solid var(--accent);padding-bottom:6px;margin:0 0 4px}
.group.fallback h2{color:var(--mut);border-bottom-color:var(--line)}
.group .gmeta{color:var(--mut);font-size:12px;margin:2px 0 8px}
.subcat{font-size:13px;color:var(--mut);margin:12px 0 4px;font-weight:600;text-transform:uppercase;letter-spacing:.4px}
details.file{background:var(--panel);border:1px solid var(--line);border-radius:8px;margin:8px 0;overflow:hidden}
details.file>summary{cursor:pointer;padding:9px 13px;font-size:13px;line-height:1.4;
  list-style:none;display:flex;justify-content:space-between;align-items:center;gap:12px}
details.file>summary::-webkit-details-marker{display:none}
details.file>summary:hover{background:var(--panel2)}
.fname{color:var(--ink);word-break:break-all;font-weight:500}
.stat{white-space:nowrap;font-size:12px;font-variant-numeric:tabular-nums}.stat .p{color:var(--ok)}.stat .m{color:var(--bad)}.stat .mut{color:var(--mut)}
pre.diff{margin:0;overflow-x:auto;font:12px/1.45 "SF Mono",ui-monospace,Menlo,Consolas,monospace;border-top:1px solid var(--line)}
pre.diff .ln{display:block;padding:0 13px;white-space:pre}
.ln.add{background:color-mix(in srgb,var(--ok) 14%,transparent);color:var(--ink)}.ln.add::before{content:"+ ";color:var(--ok)}
.ln.del{background:color-mix(in srgb,var(--bad) 14%,transparent);color:var(--ink)}.ln.del::before{content:"- ";color:var(--bad)}
.ln.hunk{color:var(--accent);background:color-mix(in srgb,var(--accent) 10%,var(--panel))}.ln.meta{color:var(--mut)}
.ln.ctx{color:var(--mut)}.ln.ctx::before{content:"  "}
.empty{color:var(--mut);font-style:italic}
.dup{color:var(--mut);font-size:11px;font-style:italic}
</style></head><body><div class="wrap">
HEAD_HTML

echo "<h1>Working-tree diff &mdash; by body of work</h1>"
echo "<div class=\"sub\">${TS} · ${#ALL_CHANGED[@]} changed file(s) · base: <code>$(printf '%s' "$BASE" | esc)</code></div>"
echo '<div class="controls"><button onclick="document.querySelectorAll('\''details.file'\'').forEach(d=>d.open=true)">Expand all</button><button onclick="document.querySelectorAll('\''details.file'\'').forEach(d=>d.open=false)">Collapse all</button></div>'

if [ "${#ALL_CHANGED[@]}" -eq 0 ]; then
  echo '<p class="empty">No changes.</p>'
else
  # TOC: groups then fallback
  echo '<div class="toc"><b>Bodies of work:</b> '
  gi=0
  if [ "${#GROUP_TITLES[@]}" -gt 0 ]; then
    for t in "${GROUP_TITLES[@]}"; do
      [ -z "$t" ] && continue
      echo "<a href=\"#g${gi}\">$(printf '%s' "$t" | esc)</a>"; gi=$((gi+1))
    done
  fi
  unl=0; for f in "${ALL_CHANGED[@]}"; do is_labeled "$f" || unl=$((unl+1)); done
  [ "$unl" -gt 0 ] && echo "<a href=\"#gfallback\">Uncategorized — by type (${unl})</a>"
  echo '</div>'

  # Labeled bodies of work
  gi=0
  idx=0
  while [ "$idx" -lt "${#GROUP_TITLES[@]}" ]; do
    title="${GROUP_TITLES[$idx]}"
    files_blob="${GROUP_FILES[$idx]}"
    echo "<div class=\"group\" id=\"g${gi}\"><h2>$(printf '%s' "$title" | esc)</h2>"
    n=0; while IFS= read -r f; do [ -n "$f" ] && n=$((n+1)); done <<< "$files_blob"
    echo "<div class=\"gmeta\">${n} file(s)</div></div>"
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      if printf '%s\n' "${ALL_CHANGED[@]}" | grep -qxF "$f"; then
        others=0; for gf in "${GROUP_FILES[@]}"; do printf '%s' "$gf" | grep -qxF "$f" && others=$((others+1)); done
        emit_file "$f"
        [ "$others" -gt 1 ] && echo "<div class=\"dup\">↑ also shown under $((others-1)) other group(s)</div>"
      else
        echo "<div class=\"empty\">$(printf '%s' "$f" | esc) — no changes (listed but unmodified)</div>"
      fi
    done <<< "$files_blob"
    gi=$((gi+1)); idx=$((idx+1))
  done

  # Fallback: unlabeled files, bucketed by type
  if [ "$unl" -gt 0 ]; then
    echo '<div class="group fallback" id="gfallback"><h2>Uncategorized — by type</h2><div class="gmeta">best-effort fallback: changed files not assigned to a labeled body of work, bucketed by file type</div></div>'
    for cat in Specs Rust Python C/C++ JS/TS Shell Config Memory Docs Other; do
      catfiles=()
      for f in "${ALL_CHANGED[@]}"; do
        is_labeled "$f" && continue
        [ "$(categorize "$f")" = "$cat" ] && catfiles+=("$f")
      done
      [ "${#catfiles[@]}" -eq 0 ] && continue
      echo "<div class=\"subcat\">${cat} (${#catfiles[@]})</div>"
      for f in "${catfiles[@]}"; do emit_file "$f"; done
    done
  fi
fi

echo '</div><script src="/manifest-link.js"></script><script src="/ann.js"></script></body></html>'
} > "$OUT"

echo "$OUT"
