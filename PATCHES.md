# AUR — Patch Specifications

Each patch modifies host-IDE assets in-place — extension files (A1–A3) or the
application bundle itself (A4). All patches are idempotent (safe to re-run),
create `.bak` backups before writing, and detect already-applied state.

---

## A1 — Chat Panel Layout

**File:** `patches/claude-ui-layout.py`
**Targets:** `~/.vscode/extensions/anthropic.claude-code-*/webview/index.css`
            `~/.antigravity/extensions/anthropic.claude-code-*/webview/index.css`
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
**Targets:** `~/.vscode/extensions/anthropic.claude-code-*/webview/index.js`
            `~/.antigravity/extensions/anthropic.claude-code-*/webview/index.js`
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
- `/usr/share/code/resources/app/extensions/simple-browser/dist/extension.js`
- `/usr/share/antigravity/resources/app/extensions/simple-browser/dist/extension.js`
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

Matched **structurally**, by regex, not by a literal — the two parameter names are
assigned by the minifier and differ between builds of the same source (Antigravity
emits `show(t,e)`, VS Code `show(e,t)`). A literal encoding one spelling reports
"pattern not found" against the other, which reads as a behavioural change when
nothing changed. The names are captured and reused in the replacement, and
backreferences keep the match self-consistent — the first parameter must be the one
passed to `getHtml()`, the second the one whose `viewColumn` is read.

```js
// Before (params shown as p1,p2 — whatever the build named them)
show(p1,p2){this._webviewPanel.webview.html=this.getHtml(p1),
  this._webviewPanel.reveal(p2?.viewColumn,p2?.preserveFocus)}

// After (condensed)
show(p1,p2){const _p=p1.split("/").pop().replace(/\.[^.]+$/,"")||"Preview";
  this._webviewPanel.title=_p;
  this._webviewPanel.webview.html=this.getHtml(p1),
  this._webviewPanel.reveal(p2?.viewColumn,p2?.preserveFocus)}
```

Title displayed is the filename without extension (e.g. `audit-2026-05-15-132500`).

### Elevation check

The script checks `os.access(path, os.W_OK)` before attempting the write. If the target
is not writable, it exits with a clear message rather than failing silently with a
permissions error.

---

## A4 — Integrated Browser Spellcheck Suggestions

**File:** `patches/vscode-spellcheck-menu.py`
**Targets (`docinfra.VSCODE_MAINJS_CANDIDATES`):**
- `/usr/share/code/resources/app/out/main.js`
- `/snap/code/current/usr/share/code/resources/app/out/main.js`
**Elevation:** Required on system (apt/dpkg) installs — the file is root-owned.
**Activation:** Full VS Code relaunch. `main.js` is the **main process**: an
extension-host restart or window reload never re-reads it.

### What it does

VS Code's integrated browser (`workbench.action.browser.open` — where
`simpleBrowser.show` forwards localhost URLs since ~1.13x) runs Chromium's
spellchecker, so misspelled words in a page's text fields are underlined — but
its right-click menu never offers the corrections. The menu is built in
`main.js`'s `showContextMenu(view, params)`; `params` is Electron's full
ContextMenuParams, whose `misspelledWord` and `dictionarySuggestions` arrive on
every right-click and are dropped unread (neither token occurs anywhere in the
shipped app — verified 1.135.0).

The patch inserts, at the head of the existing `isEditable` branch and above
Cut/Copy/Paste: one item per dictionary suggestion clicking
`webContents.replaceMisspelling()` — Chromium's own replacement primitive — and
an "Add to Dictionary" item calling `session.addWordToSpellCheckerDictionary()`.
The dictionary, the checker and the replacement all belong to the mechanism
that draws the underline; the menu rows are the only addition.

### Pattern matched

Structural, two-stage, all minified names captured and reused via backreference
(the A3 lesson): stage 1 matches the `showContextMenu` head and captures the
params and webContents names; stage 2 matches the `isEditable?(…role:"cut"…`
branch head **using the captured params name**, so the stages cannot pair
across different functions. Insertion happens inside the branch's
comma-sequence, which keeps the JS valid without restructuring anything.

### Detection and self-retirement

Already-patched detection is the presence of `replaceMisspelling` — which also
stands the patch down automatically if a future VS Code consumes the
suggestions itself, rather than doubling the menu.

`out/main.js` is **not** in `product.json`'s `checksums` list (verified
1.135.0), so no "installation appears corrupt" handling is needed. If a future
build adds it, the symptom is a corrupt-install badge after patching; the fix
is updating that checksum entry alongside the patch.

`--selftest` gates the matcher against synthetic fixtures (of our own
construction, in two different minifier namings) without touching any target;
`--check` reports would-patch/already against the real files without writing.

### Root auto-repair after VS Code app updates

The `aur-reapply` watcher watches the **extensions** directories and cannot
elevate; a VS Code application update replaces root-owned `main.js` without
touching those. So A4 carries its own repair, on the root side —
`systemd/install-root-repatch.py` installs it (one askpass), and `install.py`
offers it with A4:

- **Root half**: `aur-vscode-repatch.path` (a *system* unit) watches
  `/usr/share/code/resources/app/out/main.js`; its oneshot runs **root-owned
  copies** under `/usr/local/lib/aur/` — never the user-writable checkout,
  which is what makes unattended elevation safe to grant. Boot-time run as a
  backstop for updates applied while the watcher was down; idempotent, so a
  no-op repair is silent.
- **User half**: when a repair actually changed something (or failed), the
  root half publishes `/var/lib/aur/vscode-repatch-event`, and the user units
  (`aur-repatch-notify.{path,service}`) raise a session dialog explaining
  what happened — including a re-apply *failure*, which names the journal to
  read — with an **"Disable auto-repair"** button.
- **Opt-out contract**: that button (or the user, preemptively, by hand)
  creates `~/.config/aur/vscode-repatch.optout`; the root service's
  `ConditionPathExists=!` refuses to run while it exists. Delete the flag to
  re-enable. A user-owned flag can only suppress the repair, never grant
  anything, which is why root may trust it.
- **Refreshing the root copies**: after the patch script changes in the
  checkout, re-run `install-root-repatch.py` — the copies do not track the
  checkout by themselves.

The snap install stays out of scope on both sides: squashfs is read-only even
for root, so a snap VS Code cannot be patched in place at all. The tab-title
patch A3, whose system-install target has the same root ownership, still takes
the manual `apply-all.py` route after updates.

---

## Patch robustness across extension updates

All four patches use **content patterns** (exact CSS value strings, or for A3/A4
structural regexes) rather than byte offsets or hashed class names. This makes them
moderately robust across minor updates that don't change the targeted behaviour.

A1 and A2 still match literals, and that is a known limit: anything the minifier is
free to rename can break them without the behaviour changing. A3 was moved to a
structural match after exactly that happened — see its section; A4 was structural
from the start for the same reason.

**Exit status is the contract.** Every patch script exits 0 only when each
target is patched or already patched, and 1 when a target is missing, unwritable,
or no longer matches — and `apply-all.py`, both installers and therefore the
systemd oneshot propagate that. So `systemctl --user status aur-reapply.service`
reading `success` means the patches are on disk, not merely that a script ran.
Every entry point also refuses an unrecognised argument with usage and exit 2
before touching anything, and accepts `--help`.

When an update breaks a patch:
1. The patch reports which pattern it could not find, and exits 1.
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

- **`aur-reapply.path`** — a user path unit watching **both** `~/.vscode/extensions/`
  and `~/.antigravity/extensions/`. Both unconditionally: a watch list derived from
  which IDE is installed at install time is a snapshot that goes stale the moment the
  other one is added, and the resulting failure is silent — patches simply stop being
  re-applied there. Watching an absent directory costs nothing.
- **`aur-reapply.service`** — a oneshot that runs `patches/apply-all.py --no-browser`
  when the watcher fires. `--no-browser` skips A3 and A4, the two patches that may
  need sudo — an unattended oneshot cannot answer an askpass prompt; those two are
  re-applied by running `apply-all.py` by hand after the relevant update (see A4's
  known-limitation section).
- **`install-autoreapply.py`** — renders the service with this AUR checkout's absolute
  path, installs both units into `~/.config/systemd/user/`, and enables the watcher.
  Run once: `python3 systemd/install-autoreapply.py` (`--uninstall` to remove).

Known limitation: the watcher re-patches the **files**, but a running webview won't
reflect it until the extension host reloads. After an update + reload the margins may
briefly return; by then the service has re-patched on disk, so one more reload settles
it. File-patching an extension can't be made atomic with the host reload.
