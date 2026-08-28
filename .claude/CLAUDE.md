# AUR — Claude Instructions

## Inheritance

Universal engineering principles, collaboration rules, spec-doc rule,
communication style, and patent-IP whitelist live in `ai_common.md` at
this repo root, and are auto-loaded into every session via the
`~/.claude/CLAUDE.md` symlink. This file contains only AUR-specific guidance.

AUR is the authoritative source: edits to cross-project rules go into
`ai_common.md`, not here.

## Generated Documents

Write structured output (audits, plans, reviews) as HTML to `aur-docs/`, post a one-paragraph summary + link in chat, tell the user **Alt+D**. Never paste document content into chat.

- `aur/` — server, launcher, config, port file (infrastructure). **Never `.claude/`**: that
  directory is the agent's own configuration, so writes there require a human confirmation
  that no configuration can waive, and tooling installed inside it pays that toll forever.
- `aur-docs/` — all generated HTML documents and annotations (output/product)

Shared assets in both directories are symlinks into `~/.local/aur/`, the shared root — so
nothing depends on an AUR source checkout. Per-project files (`server.config.json`,
`server.port`) stay local.

1. Write `aur-docs/<name>-<date>-<HHMMSS>.html` (seconds precision avoids multi-session collisions)
2. The Alt+D keybinding's URL is kept current by `aur/open-manifest.sh` — it reads
   `aur/server.port` and re-points the binding. Never hand-edit the port.
3. Server auto-starts via the `Start AUR doc server` VSCode task; Alt+D re-ensures it;
   manual fallback: `python3 aur/server.py &`

Every HTML doc must include, just before `</body>`:

```html
<script src="/manifest-link.js"></script>
<script src="/ann.js"></script>
```

and `<link rel="stylesheet" href="/ann.css">` in `<head>`. All three are served from
`aur-docs/` and resolve through the shared root, so there is nothing to copy and
nothing to keep in sync.

## Prompt Library

`PROMPTS.md` is the canonical specification for all quick-prompt triggers. When the user
enters a trigger phrase, execute the full spec from PROMPTS.md for that trigger.

The prompt bar in `aur-docs/manifest.html` copies these trigger phrases to the clipboard.
The `PROMPTS` array in manifest.html must stay in sync with the triggers defined in PROMPTS.md.

## Release Process

When the user triggers `push release`, follow RELEASE.md exactly. Key steps:

1. **Build binary** — `pyinstaller aur-installer.spec` (the tracked spec: it
   carries the bundled data trees and hidden imports a bare CLI build omits).
   Output at `dist/aur-installer`. Do not commit it. Smoke-test it per
   RELEASE.md §1 before uploading.
2. **Source zip** — zip `patches/ server/ templates/ keybindings/ install.py install-gui.py
   README.md LICENSE PROJECT.md PROMPTS.md PATCHES.md WORKFLOW.md RELEASE.md`
   excluding `*.bak __pycache__/ *.pyc`. Save to `/tmp/aur-refined-vX.Y.Z-source.zip`.
3. **Tag** — `git tag vX.Y.Z && git push origin vX.Y.Z`
4. **Release** — `gh release create vX.Y.Z --title "..." --notes "..."`
5. **Upload** — `gh release upload vX.Y.Z dist/aur-installer /tmp/aur-refined-vX.Y.Z-source.zip`
6. **Verify** — `gh release view vX.Y.Z --json assets --jq '.assets[].name'`

Gate: confirm version, changelog, and pre-flight checks before any tag/push/upload.
See RELEASE.md for the full procedure including release notes template.

## Project Context

- **Language:** Python 3 (patches, server, installer)
- **Config dir:** `~/.config/Antigravity/User/` (not `~/.config/Code/`)
- **Doc server:** port in `.claude/server.port` (default 7432)
- **Keybinding:** `Alt+D` → Antigravity Simple Browser / VS Code Simple Browser
