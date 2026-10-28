---
name: doc-generation
description: Read BEFORE writing any structured output document (audit, plan, review, status, model) — HTML doc conventions, aur-docs/ layout, the mandatory annotation system boilerplate, the doc server, and the Alt+D keybinding contract.
---

# Generated Documents

Write structured output (audits, plans, reviews) as HTML to `aur-docs/`, post a
one-paragraph summary in chat, tell the user **Alt+D**, and end the turn with the
document's link: its full absolute path as the link's text and a short note of what
they should see there (ai_common's file-to-review block, owner 2026-10-18). Never
paste document content into chat.

Nothing here is project-specific. The document system — its locations, formats,
links, tabs, capabilities and appearance — is defined once and inherited. A project
that needs a different *value* sets it in `aur/server.config.json`; a project that
appears to need different *behaviour* has found a gap in AUR, not a reason to fork the document system.

- `aur/` — server, launcher, config, port file (infrastructure). **Never
  `.claude/`**: that is the agent's own configuration, so writes there require a
  human confirmation no configuration can waive, and tooling installed inside it
  pays that toll forever.
- `aur-docs/` — all generated HTML documents and annotations (output/product)

1. Write `aur-docs/<name>-<date>-<HHMMSS>.html` (seconds precision avoids
   multi-session collisions).
2. **Alt+D keybinding** — in the IDE's `keybindings.json`
   (`~/.config/Antigravity/User/` or `~/.config/Code/User/`, whichever
   `docinfra.detect_ides()` found):
   ```json
   [{"key":"alt+d","command":"runCommands","args":{"commands":[
     {"command":"workbench.action.tasks.runTask","args":"Ensure doc server"},
     {"command":"simpleBrowser.show","args":"http://localhost:7432/manifest.html"}
   ]}}]
   ```
   `runCommands` chains two steps: (1) the `Ensure doc server` task
   (`aur/open-manifest.sh`) confirms **this project's** server is answering — via
   `/meta`, not a bare port probe, since another project may hold the default port
   — and starts `server.py` detached if not; (2) `simpleBrowser.show` opens
   `/manifest.html` (stable entry point; lists all docs by date, so no per-doc
   keybinding update). It must be the command, not a `vscode://` URI — Antigravity
   ships `vscode.simple-browser` but registers no
   `vscode://vscode.simpleBrowser/show` handler, so `--open-url vscode://...`
   fails with "extension … not found".
   The `:7432` above is only the default: step (1) also *syncs* this binding's
   `simpleBrowser.show` URL to the actually-bound `aur/server.port` (the server
   scans upward when its preferred port is busy), so you never hand-edit the port.
   A port change is one Alt+D behind (the run that changes the port rewrites the
   file; the next press opens it) — in steady state the URL is always correct. If
   the file is wiped to `[]` (a settings-sync reset — the known failure mode),
   restore the block above verbatim; the updater only rewrites an existing
   binding, it never fabricates or clobbers one.
3. Server auto-starts via the `Start AUR doc server` VSCode task (runOn
   folderOpen); Alt+D re-ensures it on demand; manual fallback:
   `python3 aur/server.py &`. Port lives in `aur/server.port` (default 7432).
   **Task labels are part of the contract** — the keybinding invokes
   `Ensure doc server` by name, so a project that renames either task breaks the
   binding for itself alone and gains nothing.
4. **Shared doc-system assets are SYMLINKS into the shared root — never copies.**
   `aur/{server.py,open-manifest.sh,aurshare.py}` and
   `aur-docs/{manifest.html,manifest-link.js,ann.js,ann.css}` all point at
   **`~/.local/aur/`**, where each asset is stored version-suffixed
   (`ann.js.2.4.1`) behind a stable name (`ann.js`). One physical file per asset,
   so drift is structurally impossible — nothing to re-copy, no gate to run.
   Links are **absolute**: no project depends on an AUR *source checkout* being
   anywhere in particular, so moving or deleting it breaks nothing. Where AUR is
   developed, `aurshare.py devlink` points the shared root's stable names at the
   working tree, so a fix made **in AUR** is seen immediately; elsewhere the same
   names front deployed copies. One mechanism, two configurations.
   `server.py` (`SimpleHTTPRequestHandler`) follows the chain, and
   `open-manifest.sh`'s `BASH_SOURCE`-derived `HERE` still resolves to the
   *project's* `aur/` (hence the project's port), because bash keeps the
   invocation path, not the link target.
   **There is no per-project fork.** The console, the Specs and AI-Context tabs and the
   transcript viewer are canonical; the handful of things that genuinely differ
   per project — daemon directory, console registry, project name, one-shot
   prefixes, sysinfo binary, AI-context exclusions and pinned header links — live
   in `aur/server.config.json`, which is the project's and is tracked, beside
   `aur/server.port` (runtime state). Change behaviour in AUR; change *this
   project's* values in the config.
   **Cross-project navigation:** the manifest header carries a menu of every
   registered project (`~/.local/aur/projects.tsv`), and clicking one starts its
   server if it is down. Discovery is the registry, never a port scan — a scan can
   only find servers already running, which is the case the menu exists for.

## Annotation system (mandatory in every HTML doc)

Include via external files (served from `aur-docs/`). Four targets via relative
`/annotations/${DOC}`:
- `.card` divs and `h2`/`h3` headings → `✎ Annotate` button
- Table section rows (single `<td colspan>`) → `✎ Annotate` button
- `<li>` and data `<tr>` items → visible ID badge (e.g. `DG1`) + `✎` icon; stored
  as `item-{hi}-{n}`

**Never build a layout mock from `<table>`/`<tr>`/`<td>`.** `ann.js` claims *every*
`<tr>` with more than one cell as a data row and injects an ID badge into its first
cell and a `✎` button into its last. For a UI mock — where the alignment IS the
content being reviewed — that silently rewrites the thing under review, and edits
to its CSS appear to do nothing because the injected widgets dominate. Use `div` +
CSS grid (`display:grid; grid-template-columns: max-content …`), which the
annotation system does not touch, and which gives per-group independent column
sizing for free. Only genuine data tables get `<tr>`.

Add `<link rel="stylesheet" href="/ann.css">` in `<head>`. Just before `</body>`:

```html
<script src="/manifest-link.js"></script>
<script src="/ann.js"></script>
```

## Styling: link the theme, define no colours

**A document never carries a palette.** Link `/doc.css` in `<head>` alongside
`/ann.css`, and write no colour of your own — no hex literal, no named colour,
no `:root` block. Everything comes from the shared sheet:

```html
<link rel="stylesheet" href="/ann.css">
<link rel="stylesheet" href="/doc.css">
```

Available: `--bg --panel --panel2 --ink --mut --line --accent --ok --warn --bad
--gen`. Use `var(--…)` for every colour you need, including inside a
document-specific rule.

This exists because the corpus reached **93 separate inline stylesheets across
114 documents, drifted into 20 palettes, ten of them light** — each written by a
session that had no shared sheet to reach for and so invented one. Nothing
prevented it because nothing was shared: `ann.css` styles the annotation widgets
and never governed a document's own appearance. The cost was not ugliness, it was
that no two documents could be read side by side.

- **Structure is yours; colour is not.** Layout, spacing and any
  document-specific component still belong in the page. `doc.css` deliberately
  contains no layout at all, because the same file is injected over documents
  written before it existed — repainting those is safe, relaying them out would
  break the ones that took the most care with their columns.
- **Older documents are repaired automatically.** `manifest-link.js` appends
  `doc.css` after the document's own `<style>`, so it wins the cascade. A rule
  more specific than a bare element selector still wins locally — that is the
  accepted limit of a non-aggressive sheet, not a bug to route around.
- **Changing the theme means editing `server/doc.css` in AUR**, once. If a
  document needs a colour the palette lacks, that is a request to extend the
  palette, never a licence to hardcode one.
