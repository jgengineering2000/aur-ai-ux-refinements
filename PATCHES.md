# AUR — Patch Specifications

Each patch modifies extension assets in-place. All patches are idempotent (safe to
re-run), create `.bak` backups before writing, and detect already-applied state.

---

## A1 — Chat Panel Layout

**File:** `patches/claude-ui-layout.py`
**Targets:** `~/.antigravity/extensions/anthropic.claude-code-*/webview/index.css`
            `~/.vscode/extensions/anthropic.claude-code-*/webview/index.css`
**Elevation:** Not required.

### Changes applied

| CSS rule | Before | After | Reason |
|---|---|---|---|
| Container padding | `20px 20px 40px` | `20px 8px 40px` | Narrow panel wastes horizontal space |
| Sticky container padding | `0 20px 40px` | `0 8px 40px` | Consistent with above |
| Scrollbar mode | `overflow-y:auto` | `overflow-y:overlay` | Auto reserves gutter even when hidden |
| Assistant message indent | `padding-left:30px` | `padding-left:14px` | Excess indent on left-aligned panel |
| Timeline dot position | `left:9px` | `left:1px` | Aligns dot with reduced margin |
| Vertical line position | `left:12px` | `left:4px` | Keeps line centred on dot |
| Input box inset | `16px` | `8px` | Matches container padding reduction |

### Detection

Each rule is matched by its full minified CSS value. If the "after" value is already
present, the patch reports "already applied" and skips. If neither "before" nor "after"
is found, the patch reports "NOT FOUND" — this indicates the extension updated and the
pattern changed; inspect the new `index.css` and update `PATCHES` list.

---

## A2 — Processing Vocabulary

**File:** `patches/claude-ui-vocabulary.py`
**Targets:** `~/.antigravity/extensions/anthropic.claude-code-*/webview/index.js`
            `~/.vscode/extensions/anthropic.claude-code-*/webview/index.js`
**Elevation:** Not required.

### What it does

Claude Code cycles through a large set of synonyms ("Germinating", "Spelunking",
"Wibbling", …) while processing — 84 words as of extension 2.1.158. The patch replaces
the entire array with a single configurable word.

### Anchor pattern

The patch locates the array by searching for `"Germinating"` (the first word in the
list) and then finding the containing `[…]` array boundaries. This is more robust than
matching the whole array and survives minor ordering changes.

### Replacement options

| Argument | Result |
|---|---|
| *(none)* | `"Working"` |
| `"..."` | Ellipsis |
| `""` | Empty string (hides the label) |
| Any other string | That word |

### Detection

Already-patched detection checks for `["Working"]` or the configured replacement word
in the source. If found, reports "already patched" and skips.

---

## A3 — Simple Browser / Jetski Tab Title

**File:** `patches/simplebrowser-title.py`
**Targets (auto-detected):**
- `/usr/share/antigravity/resources/app/extensions/simple-browser/dist/extension.js`
- `/usr/share/code/resources/app/extensions/simple-browser/dist/extension.js`
- `/snap/code/current/usr/share/code/resources/app/extensions/simple-browser/dist/extension.js`
**Elevation:** Required if target is root-owned (system apt/dpkg install).
              Not required for user-local installs (AppImage, `~/.local/`).

### What it does

VS Code's Simple Browser (and Antigravity's Jetski) sets the panel tab title once on
first open and never updates it as the URL changes. All tabs show "Simple Browser" or
"Jetski Preview" regardless of the loaded document.

The patch intercepts the `show()` method to extract the filename from the URL and set
`_webviewPanel.title` before revealing the panel.

### Pattern matched

```js
// Before
show(t,e){this._webviewPanel.webview.html=this.getHtml(t),
  this._webviewPanel.reveal(e?.viewColumn,e?.preserveFocus)}

// After (condensed)
show(t,e){const _p=t.split("/").pop().replace(/\.[^.]+$/,"")||"Preview";
  this._webviewPanel.title=_p;
  this._webviewPanel.webview.html=this.getHtml(t),
  this._webviewPanel.reveal(e?.viewColumn,e?.preserveFocus)}
```

Title displayed is the filename without extension (e.g. `audit-2026-05-15-132500`).

### Elevation check

The script checks `os.access(path, os.W_OK)` before attempting the write. If the target
is not writable, it exits with a clear message rather than failing silently with a
permissions error.

---

## Patch robustness across extension updates

All three patches use **content patterns** (exact CSS value strings or JS code
substrings) rather than byte offsets or hashed class names. This makes them moderately
robust across minor Claude Code updates that don't change the targeted behaviour.

When an update breaks a patch:
1. The patch reports which pattern it could not find.
2. Examine the new extension file and identify the equivalent CSS rule or JS function.
3. Update the pattern constants in the patch script (`PATCHES` list for A1,
   `ANCHOR`/`OLD` constants for A2/A3).
4. Re-run the patch.

---

## Auto-reapply after extension updates

A Claude Code extension update installs a **new versioned directory** (e.g.
`anthropic.claude-code-2.1.162-linux-x64`) alongside the old one and switches to it.
The new webview is pristine, so the patches silently revert — the source patches are
never lost (they live here in AUR), but their *applied* state is gone until re-run.

`systemd/` wires this up automatically:

- **`aur-reapply.path`** — a user path unit watching `~/.antigravity/extensions/`.
- **`aur-reapply.service`** — a oneshot that runs `patches/apply-all.py --no-browser`
  when the watcher fires.
- **`install-autoreapply.py`** — renders the service with this AUR checkout's absolute
  path, installs both units into `~/.config/systemd/user/`, and enables the watcher.
  Run once: `python3 systemd/install-autoreapply.py` (`--uninstall` to remove).

Known limitation: the watcher re-patches the **files**, but a running webview won't
reflect it until the extension host reloads. After an update + reload the margins may
briefly return; by then the service has re-patched on disk, so one more reload settles
it. File-patching an extension can't be made atomic with the host reload.
