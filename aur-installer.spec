# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
#
# PyInstaller recipe for the public Linux binary, `aur-installer` — the
# artefact RELEASE.md attaches to every GitHub release. Build with
#
#     pyinstaller aur-installer.spec        # → dist/aur-installer
#
# never with a bare `pyinstaller install-gui.py`: the installer copies files
# out of server/, templates/, patches/ and systemd/ at run time, so those trees
# must ride inside the onefile binary as datas — with none, the frozen build
# resolves them under sys._MEIPASS, finds nothing, and dies on the first copy.
# docinfra.resource_root() reads them back out of _MEIPASS.
#
# This spec is TRACKED (an exception to the *.spec ignore rule) because it is
# the only complete statement of what the binary contains; a recipe that lives
# in a gitignored file is a recipe the next release cannot reproduce.

a = Analysis(
    ['install-gui.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('server', 'server'),
        ('templates', 'templates'),
        ('patches', 'patches'),
        ('systemd', 'systemd'),
    ],
    # The patch scripts under patches/ are executed at run time via
    # runpy (docinfra.run_patch_worker), not imported — so PyInstaller's
    # import analysis never sees their stdlib dependencies. Name them here
    # or the frozen worker dies with ModuleNotFoundError on the first patch.
    hiddenimports=['argparse', 'glob', 're', 'runpy', 'shutil'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='aur-installer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
