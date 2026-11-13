#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
AUR review-document infrastructure — shared install logic.

Single authority for everything Part B ("Review Document Infra") installs, so
the CLI (`install.py`) and the GUI (`install-gui.py`) cannot drift apart. They
previously carried byte-identical copies of this logic, which is exactly how the
Alt+D launch design got fixed in one downstream project and nowhere else.

Nothing here prompts or prints on its own: every entry point takes a `log`
callable so each front end controls its own formatting.
"""

import json
import os
import shutil
import stat
import sys
from pathlib import Path

# ── Resource resolution ───────────────────────────────────────────────────────

def resource_root():
    """Directory holding the bundled payload (server/, templates/, patches/, …).

    Under a PyInstaller --onefile build `__file__` points inside the ephemeral
    extraction directory, so the payload must be found via `sys._MEIPASS`; the
    .spec files bundle those trees as `datas`. Running from a source checkout,
    it is simply this file's directory.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else Path(__file__).resolve().parent

# ── IDE detection ─────────────────────────────────────────────────────────────

# The ONE authority for where a host IDE's Simple Browser lives. patches/
# scripts and both installer front ends import this list; a fourth copy is how
# the last "kept in sync" comment drifted.
SIMPLEBROWSER_CANDIDATES = [
    Path("/usr/share/antigravity/resources/app/extensions/simple-browser/dist/extension.js"),
    Path("/usr/share/code/resources/app/extensions/simple-browser/dist/extension.js"),
    Path("/snap/code/current/usr/share/code/resources/app/extensions/simple-browser/dist/extension.js"),
]

# The ONE authority for where the host IDE's main-process bundle lives —
# the integrated browser's context menu is built there (A4). Antigravity is
# deliberately absent: its build predates the integrated browser, and listing
# a target whose anchor cannot exist turns "feature absent" into a reported
# failure; if a later build ships the same browser, its path joins this list.
VSCODE_MAINJS_CANDIDATES = [
    Path("/usr/share/code/resources/app/out/main.js"),
    Path("/snap/code/current/usr/share/code/resources/app/out/main.js"),
]

# ── Elevation ─────────────────────────────────────────────────────────────────
# GUI askpass helpers (first present wins), so sudo can prompt even with no
# tty — the GUI installer, an IDE task, an agent session. On a tty the helper
# still pops a GUI dialog: slightly nicer than a terminal prompt, and the same
# behaviour in every front end.

ASKPASS_CANDIDATES = [
    "/usr/libexec/seahorse/ssh-askpass",
    "/usr/lib/seahorse/ssh-askpass",
    "/usr/bin/ssh-askpass",
    "/usr/lib/openssh/gnome-ssh-askpass",
    "/usr/lib/openssh/ssh-askpass",
    "/usr/libexec/openssh/ssh-askpass",
]

def find_askpass():
    for p in ASKPASS_CANDIDATES:
        if os.access(p, os.X_OK):
            return p
    return None

def sudo_argv():
    """(argv_prefix, env) for an elevated subprocess call.

    Prefers `sudo -A` with a GUI askpass so the call works with or without a
    tty; falls back to plain `sudo` (tty prompt) when no helper is installed.
    `env` is a full environment dict when askpass is used, else None.
    """
    askpass = find_askpass()
    if askpass:
        env = dict(os.environ)
        env["SUDO_ASKPASS"] = askpass
        return ["sudo", "-A", "--"], env
    return ["sudo", "--"], None

# ── Patch dispatch — the installer is its own worker ──────────────────────────
# The patches live in patches/*.py with hyphenated names, run as scripts. From a
# source checkout an installer could spawn `python3 patches/x.py`; in a frozen
# (PyInstaller onefile) build there is no Python to spawn — sys.executable IS
# the installer. So both front ends carry a `--run-patch NAME` worker mode that
# runs the script in-process, and run_patch() re-invokes sys.executable in that
# mode. One code path, both deployments; and because A3 may need root, the
# elevated call is that same argv behind `sudo -A`, which is the only way a
# frozen binary can elevate at all.

def patch_dir():
    return resource_root() / "patches"

def patch_names():
    """The accepted --run-patch set: every patches/*.py except the orchestrator."""
    return sorted(p.stem for p in patch_dir().glob("*.py") if p.stem != "apply-all")

def run_patch_worker(name, args):
    """Run patches/<name>.py as __main__ with `args`; return its exit status.

    Validates `name` first (P14): an unknown name is refused with the accepted
    set, never resolved to a path and executed.
    """
    import runpy
    if name not in patch_names():
        print(f"unknown patch '{name}'; accepted: {', '.join(patch_names())}",
              file=sys.stderr)
        return 2
    script = patch_dir() / f"{name}.py"
    saved = sys.argv
    sys.argv = [str(script)] + list(args)
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as e:
        code = e.code
        return code if isinstance(code, int) else (0 if code is None else 1)
    finally:
        sys.argv = saved
    return 0

def patch_argv(name, *args):
    """argv that re-invokes THIS installer in worker mode for patch `name`."""
    self_argv = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, sys.argv[0]]
    return self_argv + ["--run-patch", name] + list(args)

def installer_argparser(doc):
    """The one accepted argument set shared by both installer front ends.

    Ordinary use takes no arguments (everything is asked interactively);
    --run-patch is the worker mode above. Returns (parser, args); the caller
    dispatches on args.run_patch before doing anything else.
    """
    import argparse
    ap = argparse.ArgumentParser(description=doc.strip().splitlines()[0])
    ap.add_argument("--run-patch", metavar="NAME",
                    help="worker mode: run patches/NAME.py in-process and exit with its "
                         f"status (NAME: {', '.join(patch_names())})")
    ap.add_argument("patch_args", nargs="*", help="arguments passed to the patch in --run-patch mode")
    args = ap.parse_args()
    if args.patch_args and not args.run_patch:
        ap.error(f"unexpected argument(s): {' '.join(args.patch_args)}")
    return ap, args

def detect_ides():
    ides = []
    if Path("~/.antigravity").expanduser().exists():
        ides.append("antigravity")
    if Path("~/.vscode").expanduser().exists():
        ides.append("vscode")
    return ides

def keybindings_path(ide):
    if ide == "antigravity":
        return Path("~/.config/Antigravity/User/keybindings.json").expanduser()
    return Path("~/.config/Code/User/keybindings.json").expanduser()

# ── Task labels ───────────────────────────────────────────────────────────────
# The keybinding invokes the ensure-task BY LABEL, so these must match the
# labels in server/tasks.json.template exactly.

START_TASK_LABEL = "Start AUR doc server"
ENSURE_TASK_LABEL = "Ensure doc server"

# ── Keybinding ────────────────────────────────────────────────────────────────

def _strip_line_comments(text):
    """Strip // comments for JSONC parsing. Does not handle block comments."""
    result, in_string, i = [], False, 0
    while i < len(text):
        if in_string:
            if text[i] == "\\" and i + 1 < len(text):
                result += [text[i], text[i + 1]]
                i += 2
                continue
            if text[i] == '"':
                in_string = False
            result.append(text[i])
        else:
            if text[i] == '"':
                in_string = True
                result.append(text[i])
            elif text[i : i + 2] == "//":
                while i < len(text) and text[i] != "\n":
                    i += 1
                continue
            else:
                result.append(text[i])
        i += 1
    return "".join(result)

def manifest_url(port):
    return f"http://localhost:{port}/manifest.html"

def keybinding_entry(port):
    """The Alt+D binding: ensure the server is up, THEN open the manifest.

    Two phases via `runCommands`, not a bare `simpleBrowser.show`: opening the
    browser directly races the folderOpen auto-start task and shows
    connection-refused when the server has not bound yet.

    It must be the `simpleBrowser.show` COMMAND rather than a `vscode://` URI —
    Antigravity ships the simple-browser extension but registers no
    `vscode://vscode.simpleBrowser/show` URI handler.
    """
    return {
        "key": "alt+d",
        "command": "runCommands",
        "args": {
            "commands": [
                {"command": "workbench.action.tasks.runTask", "args": ENSURE_TASK_LABEL},
                {"command": "simpleBrowser.show", "args": manifest_url(port)},
            ]
        },
    }

def _is_altd_doc_binding(item):
    """Is `item` one of OUR alt+d bindings, in either the current or legacy form?

    Matching both is what makes re-running the installer an in-place UPGRADE.
    Keying only on the legacy `command == "simpleBrowser.show"` (as this
    installer used to) fails to recognise an already-migrated `runCommands`
    binding and appends a second alt+d entry — last-one-wins in VSCode, so the
    racy legacy binding silently shadows the fixed one.
    """
    if not isinstance(item, dict) or item.get("key") != "alt+d":
        return False
    cmd = item.get("command")
    if cmd == "simpleBrowser.show":
        return True
    if cmd == "runCommands":
        subs = item.get("args", {}).get("commands", [])
        return any(
            isinstance(s, dict) and s.get("command") == "simpleBrowser.show"
            for s in subs
        )
    return False

def install_keybinding(ide, port, log=print):
    path = keybindings_path(ide)
    path.parent.mkdir(parents=True, exist_ok=True)

    entry = keybinding_entry(port)

    data = []
    if path.exists():
        try:
            data = json.loads(_strip_line_comments(path.read_text()))
        except (json.JSONDecodeError, ValueError):
            log(f"WARNING: could not parse {path} — will create a new file")
            data = []
    if not isinstance(data, list):
        log(f"WARNING: {path} is not a keybindings array — will create a new file")
        data = []

    matches = [i for i, item in enumerate(data) if _is_altd_doc_binding(item)]

    if matches:
        # Upgrade the first in place; drop any extras a previous buggy run
        # appended, so exactly one alt+d doc binding survives.
        data[matches[0]] = entry
        for i in reversed(matches[1:]):
            del data[i]
        action = "Updated" if len(matches) == 1 else f"Updated (removed {len(matches) - 1} duplicate)"
    else:
        data.append(entry)
        action = "Added"

    path.write_text(json.dumps(data, indent=2) + "\n")
    log(f"{action} Alt+D keybinding → {path}")

# ── tasks.json ────────────────────────────────────────────────────────────────

def _with_port(task, port):
    """Prefix AUR_DOC_PORT onto a task command for a non-default port.

    The ensure-task needs it too: open-manifest.sh falls back to
    $AUR_DOC_PORT when aur/server.port does not exist yet.
    """
    if port != 7432:
        task = dict(task)
        task["command"] = f"AUR_DOC_PORT={port} " + task["command"]
    return task

def install_task(project_dir, port, log=print):
    vscode_dir = Path(project_dir) / ".vscode"
    tasks_path = vscode_dir / "tasks.json"
    vscode_dir.mkdir(exist_ok=True)

    template = resource_root() / "server" / "tasks.json.template"
    # ALL template tasks, not just the first: the Alt+D binding is useless
    # without the ensure-task it invokes by label.
    new_tasks = [_with_port(t, port) for t in json.loads(template.read_text())["tasks"]]

    if not tasks_path.exists():
        tasks_path.write_text(
            json.dumps({"version": "2.0.0", "tasks": new_tasks}, indent=2) + "\n"
        )
        log(f"Created {tasks_path}")
        return

    try:
        data = json.loads(_strip_line_comments(tasks_path.read_text()))
    except (json.JSONDecodeError, ValueError):
        log(f"WARNING: could not parse {tasks_path} — skipping task install")
        return

    tasks = data.get("tasks", [])
    existing = {t.get("label") for t in tasks if isinstance(t, dict)}
    added = [t for t in new_tasks if t["label"] not in existing]
    skipped = [t["label"] for t in new_tasks if t["label"] in existing]

    if added:
        tasks.extend(added)
        data["tasks"] = tasks
        data.setdefault("version", "2.0.0")
        tasks_path.write_text(json.dumps(data, indent=2) + "\n")
        log(f"Merged {len(added)} task(s) into {tasks_path}: "
            + ", ".join(t["label"] for t in added))
    for label in skipped:
        log(f"Task '{label}' already present — skipping")

# ── Review infrastructure payload ─────────────────────────────────────────────

def install_review_infra(project_dir, do_claude_md, log=print):
    project_dir = Path(project_dir)
    here = resource_root()
    if (here / "aurshare.py").is_file() and (here / "server" / "libaur.rs").is_file():
        import sys
        if str(here) not in sys.path:
            sys.path.insert(0, str(here))
        import aurshare
        aurshare.ensure_built(here, names=["libaur.so"], log=log)
    # NOT `.claude/`. That directory is the agent's own configuration — its
    # settings, hooks and permission allow-lists — and writes there require a
    # human confirmation that no configuration can waive, by design: an agent
    # able to write it unattended could grant itself any permission. Tooling
    # parked inside it pays that toll on every edit, forever. `aur/` is the
    # project's instance of AUR-provided infrastructure, and it is ordinary.
    aur_dir = project_dir / "aur"
    docs_dir = project_dir / "aur-docs"
    aur_dir.mkdir(exist_ok=True)
    docs_dir.mkdir(exist_ok=True)

    files = [
        (here / "server" / "server.py",         aur_dir / "server.py"),
        (here / "server" / "libaur.so",         aur_dir / "libaur.so"),
        (here / "server" / "open-manifest.sh",  aur_dir / "open-manifest.sh"),
        (here / "server" / "manifest-link.js",  docs_dir / "manifest-link.js"),
        (here / "server" / "ann.css",           docs_dir / "ann.css"),
        (here / "server" / "ann.js",            docs_dir / "ann.js"),
        (here / "server" / "doc.css",           docs_dir / "doc.css"),
        (here / "templates" / "manifest.html",  docs_dir / "manifest.html"),
    ]
    missing = [str(src) for src, _ in files if not src.is_file()]
    if missing:
        raise FileNotFoundError(
            "cannot install review infrastructure; missing asset(s): "
            + ", ".join(missing)
        )
    for src, dst in files:
        shutil.copy2(src, dst)
        log(f"{src.name} → {dst}")

    # The ensure-task runs this via `bash`, but set the bit explicitly: a
    # PyInstaller extraction does not necessarily preserve source permissions.
    script = aur_dir / "open-manifest.sh"
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    if do_claude_md:
        dst = project_dir / "CLAUDE.md"
        if dst.exists():
            log("CLAUDE.md already exists — not overwriting")
            log(f"  (template is at {here / 'templates' / 'CLAUDE.md.template'})")
        else:
            shutil.copy2(here / "templates" / "CLAUDE.md.template", dst)
            log(f"CLAUDE.md.template → {dst}")
            log(f"!! Edit {dst} — add project context at the bottom")
