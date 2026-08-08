# AUR — Release and Distribution Process

## Version numbering

Semver: `MAJOR.MINOR.PATCH`

| Increment | When |
|---|---|
| PATCH | Bug fixes, minor copy changes, patch pattern updates |
| MINOR | New features — new patches, new prompts, new installer options |
| MAJOR | Breaking changes, major restructures, renamed/removed components |

Current release series: **v2.x.x** (AUR restructure from v1 claude-ux-refined)

---

## Release artefacts

Every release attaches three artefacts to the GitHub release:

| Artefact | Description |
|---|---|
| `aur-installer` | Linux x86-64 self-contained binary (no Python required) |
| `aur-refined-vX.Y.Z-source.zip` | Source archive for manual install on any platform |
| Source code (auto) | GitHub-generated zip/tar from the tag |

---

## Pre-release checklist

1. All changes committed and pushed to `main`.
2. Version updated wherever it appears (release notes, README if pinned).
3. `install.py` and `install-gui.py` tested manually — run through at
   least one A-only and one B-only install on a clean target directory.
4. No uncommitted changes: `git status` is clean.
5. Patch patterns verified against the current Claude Code extension version.

---

## Step-by-step release procedure

### 0. Set the version

`VERSION` at the repo root is the single source of truth. Bump it first; every
later step reads it rather than repeating the number.

```bash
echo 2.5.0 > VERSION
```

Versions are compared as integer tuples throughout (`aurshare.parse_version`),
so `2.10.0` correctly outranks `2.9.0`. Never compare version strings.

### 1. Build the Linux GUI binary

```bash
cd <aur-checkout>
pyinstaller --onefile --windowed --name aur-installer install-gui.py
# Output: dist/aur-installer
```

`dist/` and `build/` are in `.gitignore` — do not commit the binary.

### 2. Create the source zip

```bash
VERSION=vX.Y.Z   # e.g. v2.1.0
zip -r /tmp/aur-refined-${VERSION}-source.zip \
  patches/ server/ templates/ keybindings/ systemd/ \
  install.py install-gui.py docinfra.py README.md LICENSE \
  PROJECT.md PROMPTS.md PATCHES.md WORKFLOW.md RELEASE.md \
  -x "*.bak" -x "__pycache__/*" -x "*.pyc"
```

### 3. Tag and push

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

### 4. Create the GitHub release

```bash
gh release create vX.Y.Z \
  --title "vX.Y.Z — short description" \
  --notes "$(cat <<'EOF'
## What's new

- Bullet summary of changes

## Installation

### Linux
1. Download `aur-installer`
2. `chmod +x aur-installer`
3. Double-click or `./aur-installer`

### Windows / macOS
Download `aur-refined-vX.Y.Z-source.zip`, extract, run `python3 install.py`

## Requirements
- Python 3.8+ (source install only)
- Claude Code VS Code extension
- VS Code or any VS Code fork
EOF
)"
```

### 5. Upload artefacts

```bash
gh release upload vX.Y.Z \
  dist/aur-installer \
  /tmp/aur-refined-${VERSION}-source.zip
```

### 6. Verify

```bash
gh release view vX.Y.Z --json assets --jq '.assets[].name'
# Should list: aur-installer, aur-refined-vX.Y.Z-source.zip
```

---

## Future platform targets

| Platform | Planned approach |
|---|---|
| Windows | PyInstaller `--onefile --windowed` on a Windows host or via GitHub Actions |
| macOS | PyInstaller on macOS; optionally a `.app` bundle via `py2app` |
| Linux .deb | `fpm` or `dpkg-deb` wrapping the PyInstaller binary |

Windows and macOS binaries will be added to releases as additional artefacts
using the same `gh release upload` step with platform-specific filenames
(e.g. `aur-installer.exe`, `aur-installer-macos`).

---

## GitHub repository settings

- **Topics:** `claude-code`, `claude-ai`, `vscode-extension`, `developer-tools`
- **Default branch:** `main`
- **Releases:** all tagged with `vMAJOR.MINOR.PATCH`
- **Remote:** `https://github.com/jgengineering2000/claude-ux-refined.git`
