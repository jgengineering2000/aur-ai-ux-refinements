# Shared root — `~/.local/aur/`

**Design note. Owner-directed 2026-08-08; assistant-authored.** Lowercase filename
because this is not an owner spec — it records a design the owner specified and the
reasoning behind each decision.

---

## The defect this fixes

Every installed project's shared assets are relative symlinks into
`~/develop/aur/` — the **source checkout**. That makes a working tree a runtime
dependency of every project that has ever been installed. Move it, rename it, or
work on a machine that has no checkout, and every project breaks.

The symptom that surfaced it: cross-project discovery in the doc manifest had to
scan `develop/*/` for sibling instances, which is one person's directory
convention. That was not an assumption the implementation invented — it was one
the architecture already required.

A second, older fork sits beside it. `docinfra.py` **copies** assets into a
project; local projects **symlink** them. Copies drift; links couple to a
checkout. The shared root removes the choice: everyone links, and the target is
installed rather than developed.

## Layout

Assets live directly in the root, each stored under a version-suffixed name with
a stable symlink beside it:

```
~/.local/aur/
  projects.tsv                       registry of installed projects
  VERSION                            version of the last successful deployment
  ai_common.md      -> ai_common.md.2.3.2
  ai_common.md.2.3.2
  ai_process.md     -> ai_process.md.2.3.2
  ai_process.md.2.3.2
  server.py         -> server.py.2.3.2
  server.py.2.3.2
  open-manifest.sh  -> open-manifest.sh.2.3.2
  ann.css           -> ann.css.2.3.2
  ann.js            -> ann.js.2.3.2
  manifest-link.js  -> manifest-link.js.2.3.2
  manifest.html     -> manifest.html.2.3.2
  skills            -> skills.2.3.2/
  skills.2.3.2/
```

A project links to the **stable name**, never to a version:

```
<project>/aur/server.py       -> /home/<user>/.local/aur/server.py
<project>/aur/ai_common.md    -> /home/<user>/.local/aur/ai_common.md
```

Two hops, resolved transparently by every consumer. Upgrading repoints one
symlink in the shared root and every project follows without being touched.

**Per-file versioning, not a `current/` tree.** An asset that did not change keeps
its version. A rules-only release does not republish `server.py`, so "what
actually changed" stays answerable from the filesystem, and each artifact states
its own version without anyone reading source.

**Project links are absolute.** Both ends used to live under `develop/`, so
relative links worked. A project may now live anywhere, so `~` is expanded at
creation time and never stored.

## Version comparison

**Versions compare as integer tuples, never as strings.** `"2.10" < "2.9"` is true
lexically. A downgrade guard that compares filename suffixes as text will install
2.9 over 2.10 and report success. The failure appears only after ten point
releases, by which time nobody connects reverted rules to a string sort.

`parse_version("2.3.2") -> (2, 3, 2)`. Non-numeric components sort as `-1` so a
malformed suffix always loses to a well-formed one rather than throwing.

## Registry

`projects.tsv` — tab-separated, one project per line, `#`-prefixed header:

| Column | Meaning |
|---|---|
| `path` | absolute project root |
| `layout` | `aur` (installed project) or `server` (an AUR checkout self-hosting) |
| `version` | version deployed when the project was registered |

TSV rather than JSON: the data is flat, the file is greppable and hand-editable,
appending is safe, and it needs no parser. A path containing a tab is **refused at
write time** rather than silently corrupting a row.

The registry is the *only* source of cross-project discovery. Nothing scans a
directory layout.

**"Search from…" is the fallback, not the mechanism.** For projects installed
before the registry existed, or wired by hand, a user-supplied root is scanned
once for `*/aur/server.py` and the results are **written into the registry**. The
scan happens on demand, never on page load.

## Sanity check and self-heal

**Runs at every deployment and at every server start.** Cheap, and the failure it
prevents is silent.

| Condition | Action |
|---|---|
| Root missing | Create it |
| Stable name dangling | Repoint to the highest valid version present |
| Stable name is a real file, not a symlink | Move aside to `<name>.orphaned`, repoint |
| Versioned file present, no stable name | Create the stable name at the highest version |
| Stable name points below an available higher version | Leave it — this is legitimate; only deployment advances a version |
| Registry row whose path is gone | Prune |
| Registry row whose instance is gone | Prune |
| Duplicate registry rows | Collapse, newest version wins |

Healing is reported, never silent: a repair that leaves no trace is
indistinguishable from a system that was never broken, and the next occurrence
gets diagnosed from scratch.

## Deployment

1. Verify and heal the root.
2. For each asset, compare incoming version to the installed one **as tuples**.
   Refuse the asset if the incoming version is lower; report and continue.
3. Write `<asset>.<version>` for every asset that is advancing.
4. Repoint stable names **after** all writes succeed, so a partial failure leaves
   the previous coherent set intact.
5. Write `VERSION`.
6. Register the project.

Mixed versions across assets are **legitimate** — that is what per-file versioning
buys. Step 4 is what distinguishes a legitimate mix from a half-failed install.

## What stays per-project

`<project>/aur/server.config.json` (project values, tracked by the project),
`<project>/aur/server.port` (runtime state), `<project>/.vscode/tasks.json`
(generated), and `<project>/aur-docs/` (the product). Everything else links.

## AUR developing itself

Commit `379bfe0` made AUR self-host from canonical `server/` so there would be one
physical file and no copy to drift. That is preserved for free: on a developer's
machine the shared root's stable names may point at the checkout instead of a
deployed version. One mechanism, two configurations, and AUR stops being a special
case in the code.

## Out of scope, and larger than it looks

`ai_common.md` is currently `ai_common.md`, which is threaded with
Claude-Code-specific mechanics — Alt+D, skills, hooks, the doc server. Making the
shared configuration genuinely vendor-neutral is editorial work on the **text**,
separable from everything above, and should not be assumed free because a file
moved. The layout here supports it; it does not accomplish it.
