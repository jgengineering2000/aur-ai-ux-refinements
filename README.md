# AUR — AI UX Refinements

Quality-of-life patches and workflow tooling for
[Claude Code](https://claude.ai/code), Anthropic's AI coding assistant.

Two independent layers — use either or both:

- **A. IDE Extension Patches** — improve the chat panel UI
  (VS Code and any VS Code-based fork)
- **B. Review Document Infrastructure** — generate, view, and
  annotate structured HTML documents from within the IDE
  (works with any Claude Code environment)

---

## Installation

### Linux — clickable installer

1. Download `aur-installer` from the
   [latest release](https://github.com/jgengineering2000/claude-ux-refined/releases/latest)
2. Mark it executable:
   ```bash
   chmod +x aur-installer
   ```
3. Double-click it in your file manager, or run `./aur-installer`

A GUI window opens with checkboxes for each feature. No Python installation required.

### Windows / macOS — source install

1. Download `aur-refined-vX.Y.Z-source.zip` from the
   [latest release](https://github.com/jgengineering2000/claude-ux-refined/releases/latest)
2. Extract the zip and open a terminal in the folder
3. Run the CLI installer:
   ```
   python3 install.py
   ```
   or the GUI installer (requires Python with tkinter):
   ```
   python3 install-gui.py
   ```

### From source (any platform)

```bash
git clone https://github.com/jgengineering2000/claude-ux-refined.git
cd aur-refined
python3 install.py
```

---

## A. IDE Extension Patches

These patch the Claude Code VS Code extension's webview assets.
They target the user-owned extension directory under `~/.vscode/` or
`~/.antigravity/` — **no sudo required** for the chat panel patches.

### A1. Chat Panel Layout

The default panel wastes horizontal space in a narrow side panel:

| Before | After |
|---|---|
| 30px left indent on assistant messages | 14px |
| 20px left/right container padding | 8px |
| Scrollbar reserves space even when hidden | Overlay mode |
| Timeline dot and vertical line misaligned | Aligned |

```bash
python3 patches/claude-ui-layout.py
```

### A2. Processing Vocabulary

Claude Code cycles through ~50 synonyms while thinking
("Spelunking", "Wibbling", "Shimmying"…).
This replaces the list with a single configurable word.

```bash
python3 patches/claude-ui-vocabulary.py            # "Working"
python3 patches/claude-ui-vocabulary.py "..."      # ellipsis
python3 patches/claude-ui-vocabulary.py ""         # hide it
```

### A3. Simple Browser Tab Title

VS Code's Simple Browser and Antigravity's "Jetski" both display a
static label ("Simple Browser" / "Jetski Preview") in the tab,
regardless of the page loaded. When you open multiple documents the
tabs are indistinguishable.

This patch makes the tab title reflect the filename of the page.

```bash
python3 patches/simplebrowser-title.py
```

Targets auto-detected from:
- VS Code: `/usr/share/code/…`
- VS Code (snap): `/snap/code/…`
- Antigravity: `/usr/share/antigravity/…`

The script checks whether the target file is writable before proceeding.
**System-installed** VS Code / Antigravity (apt/dpkg) store the extension
under `/usr/share/` (root-owned), so `sudo` is required.
**User-local installs** (AppImage, `~/.local/`) are writable without
elevation — run without `sudo`.

```bash
# system install (apt/dpkg) — target is root-owned
sudo python3 patches/simplebrowser-title.py

# user-local install (AppImage, ~/.local/) — no sudo needed
python3 patches/simplebrowser-title.py
```

If elevation is needed and not provided, the script exits with a clear
message rather than failing silently.

### Apply All

```bash
# Layout + vocabulary only (never needs sudo)
python3 patches/apply-all.py

# Include Simple Browser title fix:
#   system install (apt/dpkg)
sudo python3 patches/apply-all.py --browser
#   user-local install
python3 patches/apply-all.py --browser
```

After any patch: `Ctrl+Shift+P` → **Developer: Restart Extension Host**

> **Note:** Extension updates overwrite patches. Re-run after updating
> Claude Code. Each script detects already-patched files and is safe to
> re-run.

---

## B. Review Document Infrastructure

When Claude Code generates audits, reviews, or implementation plans,
the default is to paste the full content into the chat window. This
infrastructure redirects that output to styled HTML documents that
open inside the IDE.

Works with **any Claude Code environment** — VS Code, Antigravity,
or the CLI with any browser.

### What You Get

- Dark-themed HTML documents with colour-coded finding cards
- Per-finding annotation system, auto-saved to local JSON
- Document manifest hub with quick-prompt bar (Alt+D)
- Unread document tracking — new docs shown in bold until opened
- Annotations stored as plain JSON alongside the document —
  version-controllable, readable by Claude in future sessions

### Setup

The interactive installer handles all of this. For manual setup:

**1. Deploy the shared assets** (once per machine):

```bash
python3 aurshare.py deploy . "$(cat VERSION)"
```

This populates `~/.local/aur/` — the **shared root**. Assets are stored
version-suffixed (`server.py.2.4.1`) with a stable symlink beside them
(`server.py`), so a later upgrade repoints one link and every installed project
follows without being touched. Downgrades are refused per asset, comparing
versions as integer tuples so `2.10.0` correctly outranks `2.9.0`.

**2. Install into your project:**

```bash
python3 aurshare.py install yourproject
```

This links `yourproject/aur/` and `yourproject/aur-docs/` at the shared root,
writes a minimal `server.config.json` and a `.vscode/tasks.json`, and records the
project in `~/.local/aur/projects.tsv`. **Nothing is copied**, so nothing drifts,
and no project depends on this checkout — move or delete it and installed projects
keep working.

`open-manifest.sh` is the health-check the Alt+D keybinding runs before opening
the browser (step 4). It keeps that binding's URL pointed at the live port, and
it verifies the responder is *this* project's server rather than merely that
something is listening — otherwise a second project falls back to the default
7432, finds the first project's server, and silently opens the wrong manifest.

**3. Start it:**

```bash
python3 yourproject/aur/server.py &
# Serves on http://localhost:7432/, scanning upward if that port is taken
```

**4. Add the keybinding** (VS Code / Antigravity) to your IDE's `keybindings.json`:

```json
{
  "key": "alt+d",
  "command": "runCommands",
  "args": {
    "commands": [
      { "command": "workbench.action.tasks.runTask", "args": "Ensure doc server" },
      { "command": "simpleBrowser.show", "args": "http://localhost:7432/manifest.html" }
    ]
  }
}
```

Two phases, deliberately. Phase 1 runs the `Ensure doc server` task
(`aur/open-manifest.sh`), which confirms *this project's* server is answering and
starts it if not; phase 2 opens the manifest. Binding `simpleBrowser.show`
directly races the `folderOpen` auto-start task and shows connection-refused when
the server has not bound yet.

With several projects installed, the manifest also carries a **project menu** built
from the registry. Clicking through starts the target project's server if it is not
running — which is why discovery is a registry and not a port scan: a scan can only
find servers that are already up.

`open-manifest.sh` also rewrites this URL to match `aur/server.port`, so the
binding follows the server if it scans past a busy 7432.

**5. Add `CLAUDE.md` to your project:**

Copy `templates/CLAUDE.md.template` to your project root as `CLAUDE.md`
and fill in the project-specific section at the bottom.

### Annotation Storage

Annotations are saved to `aur-docs/annotations/<docname>.json` via
the local server. They load automatically when the page is opened.
The JSON files are plain text and can be committed to git or read by
Claude directly.

---

## Prompt Library

`PROMPTS.md` defines eight standard trigger phrases and their full
optimised instruction specs:

| Trigger | Purpose |
|---|---|
| `session close` | Consolidate session knowledge, gate promotions to spec docs |
| `bench plan` | Specification-first performance baseline (no results yet) |
| `bench results` | Document measured values against the plan |
| `project audit` | Full spec-vs-implementation gap analysis and roadmap |
| `code quality` | Five-dimension code review — no changes made |
| `debug plan` | Hypothesis-driven debug plan — no fixes attempted |
| `debug results` | Confirm/rule-out hypotheses, before/after measurements |
| `push release` | Pre-flight check, changelog, gated tag/push/publish |

The manifest hub's quick-prompt bar copies any trigger to the clipboard
with one click.

---

## Compatibility

| Feature | VS Code | Antigravity | CLI + browser |
|---|---|---|---|
| Layout patch (A1) | ✔ | ✔ | — |
| Vocabulary patch (A2) | ✔ | ✔ | — |
| Simple Browser title (A3) | ✔ (sudo if system-installed) | ✔ (sudo if system-installed) | — |
| Doc server (B) | ✔ | ✔ | ✔ |
| CLAUDE.md workflow (B) | ✔ | ✔ | ✔ |
| Alt+D keybinding (B) | ✔ | ✔ | — |
| GUI installer | ✔ Linux binary | ✔ Linux binary | Python source |

---

## File Layout

```
aur/
├── patches/
│   ├── apply-all.py               apply all IDE patches
│   ├── claude-ui-layout.py        chat panel margins, dot, scrollbar
│   ├── claude-ui-vocabulary.py    replace cycling word list
│   └── simplebrowser-title.py     tab title fix (VS Code + forks)
├── server/
│   ├── server.py                  annotation-capable doc server
│   ├── open-manifest.sh           Alt+D health-check, start, port-sync
│   ├── manifest-link.js           injected bar script for all docs
│   └── tasks.json.template        VS Code auto-start + ensure tasks
├── templates/
│   ├── CLAUDE.md.template         project instructions for Claude
│   ├── manifest.html              document hub with prompt bar
│   └── review-template.html       styled HTML review doc template
├── keybindings/
│   └── antigravity.json           Alt+D binding example
├── install.py                     CLI installer
├── install-gui.py                 GUI installer (tkinter)
├── docinfra.py                    shared doc-infra install logic (both installers)
├── PROJECT.md                     vision, goals, roadmap
├── PROMPTS.md                     canonical prompt library
├── PATCHES.md                     patch technical specifications
├── WORKFLOW.md                    review document workflow spec
└── RELEASE.md                     release and distribution process
```

---

MIT License — Copyright (c) 2026 Jonathan George

*Contributions welcome. Patches tested on Linux; the layout and
vocabulary scripts should work on macOS and Windows with the
appropriate extension paths.*
