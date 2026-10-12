#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Claude Doc Server — serves .claude/ files and persists annotations to JSON.

GET  /                          → directory listing
GET  /<file>                    → serve static file
GET  /annotations/<docname>     → read saved annotations for a doc
POST /annotations/<docname>     → save annotations JSON for a doc

Port selection (first match wins):
  1. AUR_DOC_PORT environment variable
  2. Default 7432
  3. Next free port scanning upward — handles multiple concurrent
     project instances without manual configuration

The bound port is written to server.port so the IDE keybinding updater
and CLAUDE.md workflow can discover it without hardcoding. Cleaned up on
normal exit and SIGTERM.
"""

import ctypes
import http.server
import json
import os
import re
import select
import shutil
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

for _cand in (Path(__file__).parent, Path(__file__).parent.parent):
    if (_cand / "aurshare.py").exists():
        sys.path.insert(0, str(_cand))
        break
try:
    import aurshare
except ImportError:                      # pre-shared-root install
    aurshare = None

PREFERRED_PORT = int(os.environ.get("AUR_DOC_PORT", "7432"))
_HERE = Path(__file__).parent          # aur/ — AUR-provided infra, per project
ROOT = _HERE.parent / "aur-docs"    # ../aur-docs/ — served output
PORT_FILE = _HERE / "server.port"      # sits beside the server that binds it
ANNOTATIONS_DIR = ROOT / "annotations"
ROOT.mkdir(exist_ok=True)
ANNOTATIONS_DIR.mkdir(exist_ok=True)
READS_FILE = ANNOTATIONS_DIR / "reads.json"
_doc_lock = threading.Lock()

# ── Per-project configuration ───────────────────────────────────────────────
#
# This server is AUR infrastructure: one physical file, inherited by every
# project. The handful of things that genuinely differ between projects live
# HERE, in an optional `server.config.json` beside it, so a project never needs
# to fork the code to change a path or a title. A project that wants the
# defaults ships no file at all.
#
# The seam is deliberately four values and not a plugin system. Nothing today
# needs to add a tab or a route, and a extension mechanism built for one
# consumer is machinery with no second opinion to keep it honest.

_CONFIG_DEFAULTS = {
    # Where the project's daemons write their logs, and where console sessions
    # register themselves. Both default under the project's own name.
    "daemon_dir": None,
    "registry": None,
    # Shown on the daemon group and in the spec page title.
    "project_name": None,
    # Prefixes of one-shot artifacts that live in the log directory but are not
    # streams — crash reports and the like, named by a project-owned convention.
    "oneshot_prefixes": [],
    # A binary emitting machine readings as JSON for the system row. Absent is
    # fine: the row simply does not render.
    "sysinfo_bin": None,
    # Directories under .claude/ to keep out of the AI Context tab.
    "ai_context_exclude": ["__pycache__"],
    # Documents pinned into the manifest header, as {"label", "href"} pairs.
    "header_links": [],
    # Regexes pulling columns out of a one-shot report's own header, as
    # {column: pattern}. The report format is the project's; the table is not.
    "oneshot_fields": {},
}


def _load_config():
    """Project settings from `server.config.json`, defaults where it is silent.

    Read once at import. A malformed file is reported and ignored rather than
    fatal — the doc server going dark because a key was mistyped would cost the
    owner the surface they would use to read about the mistake.
    """
    cfg = dict(_CONFIG_DEFAULTS)
    path = _HERE / "server.config.json"
    try:
        loaded = json.loads(path.read_text())
    except FileNotFoundError:
        return cfg
    except (OSError, ValueError) as exc:
        print(f"[docserver] ignoring {path}: {exc}", file=sys.stderr)
        return cfg
    unknown = sorted(set(loaded) - set(_CONFIG_DEFAULTS))
    if unknown:
        print(f"[docserver] {path}: unknown key(s) {', '.join(unknown)} — "
              f"accepted: {', '.join(sorted(_CONFIG_DEFAULTS))}", file=sys.stderr)
    cfg.update({k: v for k, v in loaded.items() if k in _CONFIG_DEFAULTS})
    return cfg


CONFIG = _load_config()
# The project's own name, for labels. The directory the project lives in is the
# answer when nothing says otherwise — it is what the owner calls it.
PROJECT_NAME = CONFIG["project_name"] or ROOT.parent.name


def _cfg_path(key, default=None):
    """A configured path: `~` expanded, relative resolved against the project.

    Resolving relative paths against the PROJECT ROOT rather than the process's
    working directory is what lets a config file be checked in and still be
    right — the server is started from a task, a shell and a keybinding, none of
    which agree on a cwd.
    """
    raw = CONFIG.get(key)
    if not raw:
        return default
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (ROOT.parent / p)

# Inherited AUR specs surfaced read-only in the Specs tab for IDE convenience.
# Explicit allowlist (NOT a glob) of files OUTSIDE the project tree — keyed by the
# basename the client requests under /spec-aur/<name>, mapped to an absolute path.
# Each path is resolved once here; the request handler only ever indexes this dict,
# so no client-supplied string is joined to a directory and the traversal guard is
# never relaxed. ai_common.md + PROCESS.md are the inherited engineering specs.
_AUR_DIR = Path.home() / "develop" / "aur"
AUR_SPECS = {
    "ai_common.md": _AUR_DIR / "ai_common.md",
    "PROCESS.md":       _AUR_DIR / "PROCESS.md",
}

# AI Context tab: the instruction/memory files that drive Claude's behavior in
# this project, surfaced read-only for the owner. Rooted at .claude/ (sibling
# of aur-docs/); excludes checkpoints-archive/ (stale trail) and __pycache__.
AI_CONTEXT_ROOT = ROOT.parent / ".claude"
AI_CONTEXT_EXCLUDE_DIRS = set(CONFIG["ai_context_exclude"])


# --------------------------------------------------------------------------
# Console streams — live output, as opposed to the dated snapshots everything
# else on this server serves.
#
# The AI harness writes EVERY command's output to a file under
# <session>/tasks/ and appends to it as the command runs — foreground commands
# included; the earlier claim here that they "do not get that file until they
# exit" was measured and is false. What is true is the opposite and worse: a
# foreground command's file is UNLINKED the moment it exits, and only
# backgrounded commands leave one behind. Retention, not streaming, is what
# loses the output, so the producer-side contract is not "background your long
# jobs" — it is the spool below, which keeps a second name for the inode before
# the harness drops the first.
#
# Sessions are grouped from their transcripts: `ai-title` carries the session
# title and the first entry carries the start time. Daemon logs are NOT filed
# under a session — a daemon's log belongs to the daemon, and hanging it under
# whichever AI session happened to be open would encode a false ownership.
_PROJECT_SLUG = "-" + str(ROOT.parent).lstrip("/").replace("/", "-")

CONSOLE_CHUNK_CAP = 128 * 1024      # most bytes returned by one poll
CONSOLE_FIRST_TAIL = 64 * 1024      # opening view shows the tail, not the whole file
# Two days of sessions, not fifteen minutes. The old window answered "what is
# running right now", which is a question the daemon rows already answer; the
# question a reader actually brings to this tab is "where was I", and that one
# spans yesterday. Measured on this machine: 15 minutes lists 1 session, 48
# hours lists 15 — a readable list, not a flood.
CONSOLE_LIVE_WINDOW_S = 48 * 3600
CONSOLE_DAEMON_WINDOW_S = 86400     # a daemon log is a "stream" if it moved this recently
CONSOLE_DAEMON_CAP = 20             # most daemon chips shown before the rest go to archive

# Spooled output is deleted after this long. Retention used to be UNBOUNDED —
# not by decision but by oversight, and invisible because only 15 minutes of it
# was ever on screen. Widening the window to 48h is what makes an unbounded
# spool something a reader would meet, so the bound lands with it.
CONSOLE_SPOOL_RETENTION_S = 72 * 3600

# A runaway command — an `ltrace`, a debug build with tracing left on — can write
# faster than anyone notices. These are ALERT thresholds, never caps: capture is
# not stopped and output is never dropped, because silently losing output is the
# exact defect the spool was built to fix. The row goes amber and says which
# command is responsible; the operator decides.
CONSOLE_SPOOL_ALERT_TOTAL = 1024 * 1024 * 1024      # 1G across the whole spool
CONSOLE_SPOOL_ALERT_SINGLE = 200 * 1024 * 1024      # 200M from one command

# One-shot artifacts that live in the log directory but are not streams: written
# once by a tool that then exits, never appended to again. Age cannot filter
# them — crashes happen daily, so recent dumps sit permanently inside any window
# (measured: 16 of them buried all 4 real logs). These are named by a
# project-owned convention, so matching the prefix respects a contract rather
# than guessing. Declared by the project in `server.config.json`; they remain
# openable through the archive.
CONSOLE_ONESHOT_PREFIXES = tuple(CONFIG["oneshot_prefixes"])

_DEFAULT_DAEMON_DIR = Path.home() / ".local" / "share" / PROJECT_NAME
CONSOLE_ROOTS = {
    "tmp_root":   Path(f"/tmp/claude-{os.getuid()}") / _PROJECT_SLUG,
    "projects":   Path.home() / ".claude" / "projects" / _PROJECT_SLUG,
    "daemon_dir": _cfg_path("daemon_dir", _DEFAULT_DAEMON_DIR),
    "registry":   _cfg_path("registry", _DEFAULT_DAEMON_DIR / "consoles"),
}

# The ONLY client-supplied string that goes anywhere near a path. Components are
# validated against these and the path is then CONSTRUCTED from known roots —
# no client string is ever joined to a directory, and the result is realpath-
# contained before it is opened (same discipline as AUR_SPECS above).
_RE_UUID = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
_RE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _console_roots(**over):
    roots = dict(CONSOLE_ROOTS)
    roots.update({k: v for k, v in over.items() if v is not None})
    return roots


def _contained(path: Path, root: Path):
    """Realpath containment — the last guard before any file is opened."""
    try:
        rp = os.path.realpath(path)
        rr = os.path.realpath(root)
    except OSError:
        return None
    if rp == rr or rp.startswith(rr + os.sep):
        return Path(rp)
    return None


# --------------------------------------------------------------------------
# Console spool — making foreground output outlive the command that wrote it.
#
# MEASURED, because the model this surface was built on is inverted. The
# harness gives EVERY Bash command a live-appended `<taskid>.output` under
# <session>/tasks/ — and unlinks it the instant a FOREGROUND command exits.
# Only backgrounded commands leave a file behind. Sampled at 0.5s against a
# 6-second foreground command: the file appeared, grew 31 → 186 bytes one line
# at a time, and was gone within 500ms of the return. Across one real session
# that is 229 commands run against 19 files surviving — 92% of a night's output
# unrecoverable by morning, which is precisely what the Console tab was
# reported to be missing.
#
# THE FIX IS A HELD DESCRIPTOR, NOT A LINK. Capture opens each task file the
# moment it appears, HOLDS the fd, and tails new bytes into a per-command
# spool file as they arrive; the deletion of the source name — the only
# terminator the harness gives — is answered by draining to EOF, one grace
# re-read, and closing both files. Because the writer's fd outlives the
# unlink, so does ours, and the post-unlink bytes drain like any others.
#
# An earlier design hard-linked the task file into the spool instead — one
# syscall, zero copies, and correct in isolation — and it is the one design
# this file must never return to. The Claude Code harness (>= 2.1.252)
# REFUSES to consume any task output whose link count is not 1 ("task output
# swap refused (not a regular nlink-1 file)") and can SIGKILL the running
# command ("output file identity changed") — its error text wrongly blames
# rival-session deletion, which cost four sessions of misdiagnosis. Every
# Bash command raced the ~15ms linker, so captures died intermittently in
# every project whose server ran (proven 2026-09-01 with an inotify watch
# instrumenting the tasks dir). The nlink-1 check is an unconfigurable boundary on
# the harness's side (P15, privileged-paths class): AUR adapts. Cost
# accepted: one fd per live command and each byte copied once — the spool
# alert thresholds below already watch runaways.
#
# Winning the race needs the kernel's own signal rather than a poll. A `git
# status` lives ~50ms, so any polling interval cheap enough to run all day is
# one that misses most commands — the readiness signal the resource itself
# publishes is the only honest wait (AUR P15). inotify is that signal, reached
# through ctypes because it has no stdlib binding and a pip dependency for
# three syscalls would be gratuitous (P6).
#
# What this deliberately does NOT do is survive the server being down. Capture
# runs while the server runs; a command that both started and finished during a
# gap is gone. That is an accepted constraint, not an oversight — but an
# accepted constraint that is invisible is indistinguishable from a bug, so the
# spool records when it started and the Console tab states it (P11).
_IN_MODIFY = 0x00000002
_IN_CREATE = 0x00000100
_IN_MOVED_TO = 0x00000080
_IN_DELETE = 0x00000200
_IN_CLOSE_WRITE = 0x00000008
_IN_ISDIR = 0x40000000
_IN_CLOEXEC = 0o2000000
_INOTIFY_HDR = struct.Struct("iIII")


class _Tail:
    """One live capture: the held source fd and the spool file it feeds."""
    __slots__ = ("uuid", "stem", "src_fd", "dst_fd", "last_sweep_check", "last_sweep_mtime")

    def __init__(self, uuid, stem, src_fd, dst_fd):
        self.uuid = uuid
        self.stem = stem
        self.src_fd = src_fd
        self.dst_fd = dst_fd
        self.last_sweep_check = 0.0
        self.last_sweep_mtime = 0.0


_FDUSER_BIN = shutil.which("fduser") or str(Path.home() / ".local" / "bin" / "fduser")
if not os.path.exists(_FDUSER_BIN):
    _FDUSER_BIN = None

_LIB_FDUSER = None
for _so_name in ("libfduser.so", "libfileholder.so"):
    _cand = _HERE / _so_name
    if _cand.exists():
        try:
            _lib = ctypes.CDLL(str(_cand))
            fn = getattr(_lib, "fduser_has_writer", None) or getattr(_lib, "fileholder_has_writer", None)
            if fn is not None:
                fn.argtypes = [ctypes.c_char_p]
                fn.restype = ctypes.c_int
                _LIB_FDUSER = fn
                break
        except OSError:
            pass


class ConsoleSpool:
    """Tails each task output into the registry over a held descriptor.

    The source file's link count is never touched — the harness refuses any
    task output whose nlink is not 1 (see the block comment above) — so
    capture holds an fd instead: open on CREATE, drain on MODIFY, and on
    DELETE of the source name drain to EOF, take one grace re-read, and close.
    Holding the fd is also the liveness signal every reader consults, which is
    deliberately in-process state: after a server restart nothing holds a
    descriptor, and not-live is the true answer for every command that
    predates the restart.

    One watcher, one inotify fd, every session — the tasks directories are all
    under one root, so there is nothing per-session to spawn, reap or orphan.
    Watches are nested because the tree is built lazily: the session directory
    appears at session start and its `tasks/` only at the FIRST command (this
    session: 07:59:35 and 08:00:51, 76 seconds apart), so a watch placed on a
    path that does not exist yet would silently never fire.
    """

    POLL_S = 0.05                 # fallback cadence; the floor on what it can see
    SWEEP_S = 2.0                 # orphan-tail backstop cadence under inotify
    GRACE_S = 0.02                # pause before the one grace re-read at DELETE

    def __init__(self, tmp_root: Path, registry: Path):
        self._tmp_root = Path(tmp_root)
        self._registry = Path(registry)
        self._lock = threading.Lock()
        self._wd = {}                 # wd -> (kind, uuid)
        self._tails = {}              # (uuid, stem) -> _Tail while the fd is held
        self._settle = {}             # sentinel name -> Event
        self._seq = 0
        self._thread = None
        self._stopping = False
        self._ino = None
        self._cycle = 0               # completed poll passes, for settle()
        self._seen = {}               # uuid -> tasks-dir mtime_ns at last listing
        self._woke = threading.Condition(self._lock)
        self.mode = None              # "inotify" | "poll" — reported to the reader
        self.started = time.time()
        self.missed = 0               # races lost — a command gone before the open

    # -- liveness ----------------------------------------------------------
    def is_live(self, uuid, stem):
        """True while this tailer holds the source fd — the liveness signal."""
        with self._lock:
            return (uuid, stem) in self._tails

    def session_live(self, uuid):
        """True while ANY command in the session is still being tailed."""
        with self._lock:
            return any(u == uuid for u, _ in self._tails)

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        """inotify if the kernel will give us an instance, polling if it will not.

        MEASURED HAZARD, not a hypothetical: this desktop was already holding
        145 inotify instances against `fs.inotify.max_user_instances = 128`
        (language servers, trackers, gvfs), so `inotify_init1` returns EMFILE
        and the clean path is simply unavailable until the cap is raised. A
        capture layer that failed here would fail exactly the way the defect it
        replaces failed — silently — so it degrades to polling instead and says
        which mode it is in (P15: contention for a shared resource is a
        planning input; P10: the degradation is logged, not swallowed).

        The fallback is honest about what it loses. Polling catches every
        command that outlives one interval and misses ones shorter than it,
        where inotify misses nothing; that difference is reported to the reader
        rather than left to be discovered.
        """
        try:
            self._registry.mkdir(parents=True, exist_ok=True)
            self._tmp_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"[console-spool] REFUSING: cannot create task root "
                  f"{self._tmp_root} / registry {self._registry}: {exc}",
                  file=sys.stderr)
            return False
        try:
            self._ino = _Inotify()
            self.mode = "inotify"
            self._watch(self._tmp_root, "root", None)
        except OSError as exc:
            self._ino = None
            self.mode = "poll"
            print(f"[console-spool] inotify unavailable ({exc.strerror or exc}); "
                  f"falling back to {self.POLL_S * 1000:.0f}ms polling — commands "
                  f"shorter than that may be missed. Raise "
                  f"fs.inotify.max_user_instances to restore exact capture.",
                  file=sys.stderr)
        self.scan()
        self._thread = threading.Thread(target=self._run, name="console-spool",
                                        daemon=True)
        self._thread.start()
        # The capturing object registers ITSELF as the one every reader
        # consults. Liveness is "a tailer holds this fd", so the answer lives
        # in the object that holds it — and binding that to one call site in
        # __main__ instead meant any other consumer (the contract test, an
        # embedding) silently read every command as ended, with no error to
        # say why (P16: discover the running instance, never require a second
        # place to declare it).
        global _SPOOL
        _SPOOL = self
        return True

    def stop(self):
        self._stopping = True
        # Deregister first: a reader arriving during teardown must not be told
        # a command is live by a spool that is releasing every fd it holds.
        global _SPOOL
        if _SPOOL is self:
            _SPOOL = None
        if self._ino is not None:
            self._ino.close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        # A server shutting down mid-command still owns every held fd (P8 —
        # every exit path releases every owned resource): drain what has been
        # written and close. The successor's startup scan re-reads the source
        # from byte 0, so nothing is lost across the restart.
        with self._lock:
            tails = list(self._tails.values())
        for tail in tails:
            self._finish(tail)

    def settle(self, timeout=5.0):
        """Block until every event queued before this call has been handled.

        A sleep here would be racy in exactly the way the surface under test is
        racy. Under inotify the sentinel rides the SAME queue: inotify
        preserves order, so once the watcher has seen it, everything created
        before it has necessarily been handled already. Under polling there is
        no queue to ride, so it waits for two full passes — one that may have
        been already in flight when the caller created its file, and one that
        is therefore guaranteed to have seen it.
        """
        if self._thread is None:
            return True
        if self.mode == "poll":
            deadline = time.monotonic() + timeout
            with self._lock:
                target = self._cycle + 2
                while self._cycle < target:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._woke.wait(remaining)
            return True
        with self._lock:
            self._seq += 1
            token = f".settle-{self._seq}"
            ev = threading.Event()
            self._settle[token] = ev
        probe = self._tmp_root / token
        try:
            probe.touch()
        except OSError:
            return False
        ok = ev.wait(timeout)
        with self._lock:
            self._settle.pop(token, None)
        try:
            probe.unlink()
        except OSError:
            pass
        return ok

    # -- capture -----------------------------------------------------------
    def scan(self):
        """Startup recovery — adopt whatever is already in flight.

        A task file on disk is COMPLETE from byte 0, because the unlink happens
        only at command exit. So a server starting mid-session captures every
        command still running in full, and loses only those that had already
        finished. That is what makes a late start cost commands rather than
        sessions.
        """
        try:
            sessions = [d for d in os.scandir(self._tmp_root) if d.is_dir()]
        except OSError:
            return
        now = time.time()
        for sd in sessions:
            if not _RE_UUID.match(sd.name):
                continue
            self._watch(Path(sd.path), "session", sd.name)
            tasks = Path(sd.path) / "tasks"
            try:
                st = tasks.stat()
            except OSError:
                continue
            # A directory's mtime moves whenever an entry is created or removed,
            # so it is the change signal that keeps the poll cheap: this loop
            # runs 20 times a second against ~99 session directories, and
            # listing all of them every pass would be ~2000 scandirs/sec to
            # discover nothing. One stat each, and the listing happens only for
            # a directory that actually changed (P7 — a cost pathological for
            # the work done is a defect, not a tradeoff).
            if now - st.st_mtime > CONSOLE_LIVE_WINDOW_S:
                continue
            key = (sd.name, st.st_mtime_ns)
            if self._seen.get(sd.name) == st.st_mtime_ns:
                continue
            self._watch(tasks, "tasks", sd.name)
            try:
                for f in os.scandir(tasks):
                    self._capture(sd.name, f.name)
            except OSError:
                continue
            self._seen[key[0]] = key[1]

    def _watch(self, path, kind, uuid):
        if self._ino is None:
            return
        mask = _IN_CREATE | _IN_MOVED_TO
        if kind == "tasks":
            # Leaf directories carry file traffic: MODIFY drives the
            # drain, and CLOSE_WRITE (process closed output) or DELETE
            # is the command's terminator.
            mask |= _IN_MODIFY | _IN_DELETE | _IN_CLOSE_WRITE
        wd = self._ino.add(path, mask)
        if wd is not None:
            with self._lock:
                self._wd[wd] = (kind, uuid)

    def _has_writer(self, tail):
        """Check if any process on the machine has this task file open for writing."""
        src_path = self._tmp_root / tail.uuid / "tasks" / f"{tail.stem}.output"
        target = str(src_path) if src_path.exists() else f"/proc/{os.getpid()}/fd/{tail.src_fd}"

        # 1. Ultra-fast path: in-process C shared library (zero IPC, GIL released, ~3.8ms)
        if _LIB_FDUSER is not None:
            try:
                res = _LIB_FDUSER(target.encode())
                if res in (0, 1):
                    return res == 1
            except Exception:
                pass

        # 2. CLI fallback path: native fduser binary
        if _FDUSER_BIN:
            try:
                res = subprocess.run([_FDUSER_BIN, "-q", "-w", target], check=False)
                if res.returncode in (0, 1):
                    return res.returncode == 0
            except OSError:
                pass

        # 3. Fallback path: procfs scan in Python
        target_suffix = f"/{tail.uuid}/tasks/{tail.stem}.output"
        my_uid = os.getuid()
        try:
            pids = [d for d in os.listdir("/proc") if d.isdigit()]
        except OSError:
            return False
        for pid in pids:
            try:
                if os.stat(f"/proc/{pid}").st_uid != my_uid:
                    continue
                fds = os.listdir(f"/proc/{pid}/fd")
            except OSError:
                continue
            for fd in fds:
                try:
                    dest = os.readlink(f"/proc/{pid}/fd/{fd}")
                    if dest.endswith(target_suffix):
                        try:
                            with open(f"/proc/{pid}/fdinfo/{fd}", "r") as fh:
                                for line in fh:
                                    if line.startswith("flags:"):
                                        flags = int(line.split()[1], 8)
                                        if (flags & 3) in (os.O_WRONLY, os.O_RDWR):
                                            return True
                        except OSError:
                            return True
                except OSError:
                    pass
        return False

    def _capture(self, uuid, name):
        """CREATE: open the source, hold the fd, start the per-command spool file."""
        if not name.endswith(".output"):
            return
        stem = name[:-len(".output")]
        with self._lock:
            if (uuid, stem) in self._tails:
                return
        src = self._tmp_root / uuid / "tasks" / name
        # A SYMLINKED task entry is a subagent's transcript, not console output:
        # the harness points those at ~/.claude/projects/…/subagents/*.jsonl.
        # Following one would tail a growing JSONL transcript into the console
        # and offer its first line — raw JSON, quotes and all — as a chip
        # label. It is not output, and it is skipped.
        try:
            if src.is_symlink():
                return
        except OSError:
            return
        try:
            src_fd = os.open(src, os.O_RDONLY | os.O_CLOEXEC)
        except FileNotFoundError:
            # Lost the race: the command finished and the harness unlinked the
            # name before the open landed, so the bytes are unrecoverable.
            # Counted, because a silently dropped stream is the defect this
            # class exists to end.
            self.missed += 1
            return
        except OSError as exc:
            print(f"[console-spool] open failed for session={uuid} "
                  f"task={name}: {exc}", file=sys.stderr)
            return
        dst_dir = self._registry / uuid / "spool"
        dst = dst_dir / (stem + ".log")
        try:
            dst_dir.mkdir(parents=True, exist_ok=True)
            # RESUME, never clobber. The source is complete from byte 0 for as
            # long as its name exists, so adoption has to reconcile against
            # whatever a previous generation already spooled: skip the bytes
            # already captured and append only the remainder. O_TRUNC here
            # instead — which this did until 2026-09-02 — rewrites a file this
            # process does not own, and the hardlink design it replaced
            # explicitly refused to (`if dst.exists(): return`). The damage is
            # invisible in the happy case, because re-copying a complete source
            # reproduces identical bytes; what it destroys is every spool file
            # whose source no longer matches it, and it restamps the mtime of
            # every one it touches — which is the start-order key the merged
            # history sorts on.
            already = dst.stat().st_size if dst.exists() else 0
            try:
                st_src = os.fstat(src_fd)
                source_size = st_src.st_size
                source_mtime = st_src.st_mtime
            except OSError:
                source_size = 0
                source_mtime = 0

            # If the file is already completely captured and was not modified recently,
            # it is a finished command from a previous run. Do not hold it open.
            if already >= source_size and already > 0 and (time.time() - source_mtime > 5.0):
                os.close(src_fd)
                return

            if already > source_size:
                # The spool holds MORE than the source: the source was replaced
                # or truncated under us, so the prior capture describes a
                # different stream. Start it over rather than appending a
                # second stream's bytes onto the first.
                already = 0
                flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC
            else:
                flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC
            dst_fd = os.open(dst, flags, 0o644)
            if already:
                os.lseek(src_fd, already, os.SEEK_SET)
        except OSError as exc:
            os.close(src_fd)
            print(f"[console-spool] spool create failed for session={uuid} "
                  f"task={name}: {exc}", file=sys.stderr)
            return
        tail = _Tail(uuid, stem, src_fd, dst_fd)
        with self._lock:
            self._tails[(uuid, stem)] = tail
        self._drain(tail)

        # If this capture caught up on an inactive file that stopped writing > 5s ago,
        # finish and close descriptors immediately rather than holding them open.
        if (time.time() - source_mtime > 5.0) and not self._has_writer(tail):
            self._finish(tail)

    def _drain(self, tail):
        """Copy every byte the source has that the spool does not."""
        while True:
            try:
                chunk = os.read(tail.src_fd, 65536)
            except OSError as exc:
                print(f"[console-spool] read failed for session={tail.uuid} "
                      f"task={tail.stem}: {exc}", file=sys.stderr)
                return
            if not chunk:
                return
            try:
                os.write(tail.dst_fd, chunk)
            except OSError as exc:
                print(f"[console-spool] spool write failed for "
                      f"session={tail.uuid} task={tail.stem}: {exc}",
                      file=sys.stderr)
                return

    def _touch(self, uuid, name):
        """MODIFY: new bytes on a tailed source — read them off the held fd."""
        if not name.endswith(".output"):
            return
        with self._lock:
            tail = self._tails.get((uuid, name[:-len(".output")]))
        if tail is None:
            # A source writing that nothing tails: its CREATE was lost (queue
            # overflow, or it predates this watch). MODIFY proves it is alive
            # now, so adopt it — the file is complete from byte 0.
            self._capture(uuid, name)
            return
        self._drain(tail)

    def _ended(self, uuid, name):
        """CLOSE_WRITE or DELETE of the source name — command has completed."""
        if not name.endswith(".output"):
            return
        with self._lock:
            tail = self._tails.get((uuid, name[:-len(".output")]))
        if tail is not None:
            self._finish(tail)

    def _finish(self, tail):
        """Drain to EOF, one grace re-read, close both — the end-of-command stamp.

        The writer's fd can outlive the unlink, so after the first drain a
        short pause and one re-read catch a final in-flight write. Closing is
        the stamp itself: liveness reads "ended" from the dropped fd, and the
        spool file's own mtime — its last write — is the end time the history
        already reports.
        """
        self._drain(tail)
        time.sleep(self.GRACE_S)
        self._drain(tail)
        with self._lock:
            self._tails.pop((tail.uuid, tail.stem), None)
        for fd in (tail.src_fd, tail.dst_fd):
            try:
                os.close(fd)
            except OSError:
                pass

    def _sweep(self):
        """Backstop for lost events: finish any tail whose source name is gone or writer closed.

        inotify can drop events under queue overflow, and polling has no
        DELETE event at all. `st_nlink == 0` on the HELD fd is the truthful
        test — the name is gone whatever notified or failed to. Draining
        first also mops up any MODIFY the queue lost.
        """
        with self._lock:
            tails = list(self._tails.values())
        now = time.time()
        for tail in tails:
            self._drain(tail)
            try:
                st = os.fstat(tail.src_fd)
                gone = st.st_nlink == 0
                inactive = (now - st.st_mtime) > 10.0
            except OSError:
                gone = True
                inactive = True
            if gone:
                self._finish(tail)
            elif inactive:
                # If inactive, only re-scan /proc if the file was modified since
                # the last check, or after a backoff interval (15s) has elapsed.
                if (st.st_mtime != tail.last_sweep_mtime) or (now - tail.last_sweep_check >= 15.0):
                    tail.last_sweep_check = now
                    tail.last_sweep_mtime = st.st_mtime
                    if not self._has_writer(tail):
                        self._finish(tail)

    def _run(self):
        if self.mode == "poll":
            return self._run_poll()
        last_sweep = time.monotonic()
        while not self._stopping:
            try:
                events = self._ino.read(timeout=self.SWEEP_S)
            except OSError:
                return
            for wd, mask, name in events:
                with self._lock:
                    entry = self._wd.get(wd)
                    waiter = self._settle.get(name)
                if waiter is not None:
                    waiter.set()
                    continue
                if entry is None:
                    continue
                kind, uuid = entry
                if kind == "root" and (mask & _IN_ISDIR) and _RE_UUID.match(name):
                    sd = self._tmp_root / name
                    self._watch(sd, "session", name)
                    if (sd / "tasks").is_dir():
                        self._watch(sd / "tasks", "tasks", name)
                elif kind == "session" and name == "tasks":
                    self._watch(self._tmp_root / uuid / "tasks", "tasks", uuid)
                elif kind == "tasks":
                    if mask & (_IN_DELETE | _IN_CLOSE_WRITE):
                        self._ended(uuid, name)
                    elif mask & _IN_MODIFY:
                        self._touch(uuid, name)
                    else:
                        self._capture(uuid, name)
            now = time.monotonic()
            if now - last_sweep >= self.SWEEP_S:
                self._sweep()
                last_sweep = now

    def _run_poll(self):
        """Re-scan the live task directories on a short interval.

        Cheap because it is bounded by what is actually running: only session
        directories whose task dir exists are visited, which is a handful of
        entries per pass, not the ~490-file daemon directory. `scan()` already
        skips anything already tailed, so a pass over an idle tree does no work
        beyond one `scandir` each; the sweep is what drains live tails and
        finishes unlinked ones, since polling has no per-file events.
        """
        while not self._stopping:
            self.scan()
            self._sweep()
            with self._lock:
                self._cycle += 1
                self._woke.notify_all()
            time.sleep(self.POLL_S)


class _Inotify:
    """Three syscalls' worth of binding — init, add_watch, and a read loop."""

    def __init__(self):
        self._libc = ctypes.CDLL("libc.so.6", use_errno=True)
        fd = self._libc.inotify_init1(_IN_CLOEXEC)
        if fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1")
        self.fd = fd

    def add(self, path, mask):
        wd = self._libc.inotify_add_watch(self.fd, os.fsencode(str(path)),
                                          ctypes.c_uint32(mask))
        return wd if wd >= 0 else None

    def read(self, timeout=None):
        """Next event batch; with a timeout, [] when nothing arrived in time.

        The timeout exists for the caller's sweep cadence — a read that blocked
        forever would let an orphaned tail (its DELETE lost to queue overflow)
        hold its fd until the next unrelated filesystem event.
        """
        if timeout is not None:
            ready, _, _ = select.select([self.fd], [], [], timeout)
            if not ready:
                return []
        buf = os.read(self.fd, 65536)
        out, off = [], 0
        while off + _INOTIFY_HDR.size <= len(buf):
            wd, mask, _cookie, ln = _INOTIFY_HDR.unpack_from(buf, off)
            off += _INOTIFY_HDR.size
            raw = buf[off:off + ln].split(b"\0", 1)[0]
            off += ln
            out.append((wd, mask, raw.decode("utf-8", "replace")))
        return out

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


_SPOOL = None                      # the running watcher, for the gap indicator


def _spool_live(uuid, stem):
    """Whether the tailer still holds this command's source fd.

    THE liveness signal for spooled streams, replacing the retired nlink test
    (a link count above 1 is now impossible by design — see ConsoleSpool). It
    is in-process state on purpose: after a server restart nothing holds a
    descriptor, and not-live is the true answer for every command that
    predates the restart.
    """
    return _SPOOL is not None and _SPOOL.is_live(uuid, stem)


_SPOOL_STATS_CACHE = {}  # session_name -> (st_mtime_ns, sess_sizes)
_spool_stats_lock = threading.Lock()


def spool_stats(registry: Path, alert_total=CONSOLE_SPOOL_ALERT_TOTAL,
                alert_single=CONSOLE_SPOOL_ALERT_SINGLE):
    """Total spooled bytes, and the largest single command, with alert flags.

    Cached per session directory mtime: dormant/closed sessions (the vast
    majority) do not change and are reused instantly without stat'ing thousands
    of historical files on every 3-second beat. Sessions with live commands are
    always refreshed.
    """
    sizes = []
    try:
        sessions = list(Path(registry).iterdir())
    except OSError:
        sessions = []
    with _spool_stats_lock:
        for sd in sessions:
            spool = sd / "spool"
            try:
                st = spool.stat()
            except OSError:
                continue
            is_live = _SPOOL is not None and _SPOOL.session_live(sd.name)
            cached = _SPOOL_STATS_CACHE.get(sd.name)
            if not is_live and cached is not None and cached[0] == st.st_mtime_ns:
                sizes.extend(cached[1])
                continue
            sess_sizes = []
            try:
                entries = list(spool.glob("*.log"))
            except OSError:
                continue
            for f in entries:
                try:
                    sess_sizes.append(f.stat().st_size)
                except OSError:
                    continue
            _SPOOL_STATS_CACHE[sd.name] = (st.st_mtime_ns, sess_sizes)
            sizes.extend(sess_sizes)

    total = sum(sizes)
    biggest = max(sizes, default=0)
    return {
        "bytes": total,
        "files": len(sizes),
        # min/avg/max rather than the single largest and its name. One number
        # cannot say whether it is an outlier: a 185K maximum reads alarming
        # beside nothing and unremarkable beside a 7K mean. The distribution is
        # what makes a runaway command obvious WHILE it is still writing, which
        # is the whole point of showing this continuously. The filename is
        # dropped — it identified a command the reader cannot act on from here,
        # and cost the width the three figures now use.
        "min": min(sizes, default=0),
        "avg": (total // len(sizes)) if sizes else 0,
        "max": biggest,
        "alert_total": alert_total,
        "alert_single": alert_single,
        "over": total > alert_total or biggest > alert_single,
    }


def prune_spool(registry: Path, retention_s=CONSOLE_SPOOL_RETENTION_S, now=None):
    """Delete spooled output older than the retention bound; report what went.

    Retention was UNBOUNDED — not by decision but by oversight, and invisible
    because only fifteen minutes of it was ever on screen. Widening the listing
    to 48 hours is what makes an unbounded spool something a reader would meet,
    so the bound lands with it.

    A session directory is judged by its NEWEST file, never per-file: a session
    is one conceptual unit, and expiring half its console while the other half
    survives produces a history with holes in the middle, which is worse than
    either keeping or dropping the whole thing.
    """
    now = time.time() if now is None else now
    removed_dirs, removed_bytes = 0, 0
    try:
        sessions = list(Path(registry).iterdir())
    except OSError:
        return {"dirs": 0, "bytes": 0}
    for sd in sessions:
        spool = sd / "spool"
        try:
            entries = [(f, f.stat()) for f in spool.glob("*.log")]
        except OSError:
            continue
        if not entries:
            continue
        newest = max(st.st_mtime for _, st in entries)
        if now - newest <= retention_s:
            continue
        # A command the tailer still holds a source fd for is RUNNING. Age
        # says it should go; a live writer says it must not, and the live
        # writer wins — deleting under the tailer's open descriptors would
        # strand a capture appending into a file no reader can ever reach.
        if _SPOOL is not None and _SPOOL.session_live(sd.name):
            continue
        size = sum(st.st_size for _, st in entries)
        try:
            shutil.rmtree(sd)
        except OSError as exc:
            print(f"[console-spool] cannot prune {sd.name}: {exc}",
                  file=sys.stderr, flush=True)
            continue
        removed_dirs += 1
        removed_bytes += size
    if removed_dirs:
        print(f"[console-spool] pruned {removed_dirs} session(s), "
              f"{removed_bytes / 1048576:.1f}M older than "
              f"{retention_s / 3600:.0f}h", flush=True)
    return {"dirs": removed_dirs, "bytes": removed_bytes}


_SPOOLED_CACHE = {}  # uuid -> (st_mtime_ns, spooled_entries)
_spooled_lock = threading.Lock()


def _spooled(registry: Path, uuid: str):
    """Per-command streams the spool captured, newest first.

    One chip per command, which is what retires the old `stdout` id: that one
    resolved to whichever task file had the newest mtime, so it re-pointed at a
    stale survivor the moment a command exited, and flapped between two streams
    whenever two ran at once — resetting the reader's offset on every flip.
    Naming each command separately removes the expression that produced both.

    Cached by session spool directory mtime for closed sessions, avoiding
    re-reading dozens of completed files on every console listing poll.
    """
    d = registry / uuid / "spool"
    try:
        st = d.stat()
    except OSError:
        return []

    is_live = _SPOOL is not None and _SPOOL.session_live(uuid)
    with _spooled_lock:
        cached = _SPOOLED_CACHE.get(uuid)
        if not is_live and cached is not None and cached[0] == st.st_mtime_ns:
            return list(cached[1])

    out = []
    try:
        entries = [(f, f.stat()) for f in d.glob("*.log")]
    except OSError:
        return out
    for f, f_st in sorted(entries, key=lambda e: -e[1].st_mtime):
        out.append({
            "id":    f"session:{uuid}:spool:{f.stem}",
            "label": _spool_label(f, f_st),
            "path":  str(f),
            "size":  f_st.st_size,
            # The harness redirects BOTH streams into one file — verified by
            # emitting to each and finding the lines interleaved. The pane says
            # so, because a reader who assumes otherwise goes hunting for an
            # error stream that was in front of them all along.
            "carries": "stdout+stderr",
            # Still named by SOMETHING: the harness never records the command
            # text next to the task id, and inventing a mapping from timestamp
            # order would be a guess presented as fact.
            "live":  _spool_live(uuid, f.stem),
            # Raw epoch, for the session sort's fallback. The formatted stamps
            # elsewhere are for reading; this one is for comparing.
            "mtime_epoch": f_st.st_mtime,
        })
    with _spooled_lock:
        if not is_live:
            _SPOOLED_CACHE[uuid] = (st.st_mtime_ns, list(out))
    return out


def console_history(registry: Path, uuid: str, cursor=None,
                    cap=CONSOLE_CHUNK_CAP):
    """Every command in one continuous console, which is the point of the tab.

    Per-command chips answered "keep the output" and made the thing they were
    built for WORSE: watching a long build meant guessing which of a growing row
    of chips was the build, and a pane pinned to one command showed nothing
    while another produced everything. One merged stream is what monitoring
    actually wants — the build's output arrives as it is written, without
    anyone choosing a stream first.

    The cursor is a byte offset PER command file, carried by the client. Each
    poll reads whatever is new from each file in start order and tags it, so
    two commands running at once interleave visibly instead of one silently
    replacing the other. Where a chunk's source changes, the client draws the
    boundary — the marker is the reader's cue that a command began or ended.
    """
    cursor = dict(cursor or {})
    d = registry / uuid / "spool"
    try:
        files = [(f, f.stat()) for f in d.glob("*.log")]
    except OSError:
        return {"chunks": [], "cursor": cursor}
    # Start order, not mtime: mtime reorders the row every time an older
    # command writes another line, which would shuffle the history under the
    # reader. st_ctime is when the spool file was created, i.e. when the
    # command began.
    files.sort(key=lambda e: e[1].st_ctime)

    chunks, budget = [], cap
    for f, st in files:
        key = f.stem
        seen = cursor.get(key)
        first = seen is None
        start = 0 if first else seen
        if st.st_size < start:                     # truncated — re-read
            start, first = 0, True
        if st.st_size == start and not first:
            continue
        if budget <= 0:
            break
        try:
            with f.open("rb") as fh:
                fh.seek(start)
                data = fh.read(min(budget, st.st_size - start))
        except OSError:
            continue
        cursor[key] = start + len(data)
        budget -= len(data)
        # A command whose source fd the tailer still holds has not exited.
        # That is the honest end-marker here, where there is no pid to ask and
        # a file that merely stopped growing is indistinguishable from a slow
        # one.
        ended = not _spool_live(uuid, key) and cursor[key] >= st.st_size
        if not data and not first and not ended:
            continue
        chunk = {
            "src":   key,
            "label": _spool_label(f, st),
            "text":  data.decode("utf-8", errors="replace"),
            "start": first,
            "end":   ended,
            "at":    datetime.fromtimestamp(st.st_ctime).strftime("%H:%M:%S"),
        }
        # The closing marker carries what the command COST, which is the half a
        # reader scrolling back through a night's work is actually looking for.
        # Both come from the stat already taken: the command ran from the moment
        # its link was made to its last write, and produced the bytes on disk.
        if ended:
            chunk["dur_s"] = max(0.0, round(st.st_mtime - st.st_ctime, 1))
            chunk["size"] = st.st_size
        chunks.append(chunk)
    return {"chunks": chunks, "cursor": cursor}


def console_transcript(projects: Path, uuid: str, after=0, cap=400):
    """The conversation, as turns — not the raw JSONL.

    A transcript is roughly four-fifths tool traffic, and dumping it whole
    reproduces exactly the wall of text a reader opens this to escape. So each
    record is reduced to what a person would want to see again:

      * `user` / `assistant` text, which is the conversation;
      * tool CALLS as one line each — name plus a short subject — because "what
        did it do here" is answerable from the name and the target;
      * tool RESULTS collapsed behind their call, never dropped. For `Bash` they
        are genuinely redundant: those same bytes are the session console, shown
        better there. For `Read`, `Edit`, `Grep` and the rest they exist nowhere
        else, so discarding them would lose the only copy.

    `after` is a record index, so a reader who scrolls back does not re-fetch
    what they already hold.
    """
    jl = Path(projects) / f"{uuid}.jsonl"
    out, idx = [], 0
    try:
        fh = jl.open("r", errors="replace")
    except OSError:
        return {"turns": [], "next": after, "total": 0}
    with fh:
        for line in fh:
            idx += 1
            if idx <= after:
                continue
            if len(out) >= cap:
                break
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") not in ("user", "assistant"):
                continue
            content = (rec.get("message") or {}).get("content")
            if isinstance(content, list):
                blocks = content
            elif isinstance(content, str):
                blocks = [{"type": "text", "text": content}]
            else:
                blocks = []
            text, calls, results = [], [], []
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                t = b.get("type")
                if t == "text" and b.get("text"):
                    text.append(b["text"])
                elif t == "tool_use":
                    calls.append({"name": b.get("name") or "tool",
                                  "subject": _tool_subject(b.get("input"))})
                elif t == "tool_result":
                    results.append(_result_text(b.get("content"))[:4000])
            if not (text or calls or results):
                continue
            # The role is what the record CARRIES, not the envelope it arrived
            # in. Tool results come back inside `user` records — that is the
            # API's turn structure, not a claim about who spoke — so reading the
            # envelope labelled them "You". On a real transcript that was 857
            # records against 378 the person actually wrote: more than two
            # thirds of everything attributed to the reader was a tool
            # answering.
            role = rec.get("type")
            if role == "user" and results and not text:
                role = "tool"
            out.append({
                "i": idx,
                "role": role,
                "text": "\n".join(text),
                "calls": calls,
                "results": results,
                "at": rec.get("timestamp"),
            })
    return {"turns": out, "next": idx, "total": idx}


def _tool_subject(inp):
    """One short phrase naming what a tool call acted on.

    The call's own arguments are the honest label — a path, a command, a
    pattern. Guessing from the tool name alone would produce "Bash" four hundred
    times over, which names nothing.
    """
    if not isinstance(inp, dict):
        return ""
    for key in ("command", "file_path", "path", "pattern", "url", "query",
                "prompt", "description"):
        v = inp.get(key)
        if isinstance(v, str) and v.strip():
            return re.sub(r"\s+", " ", v.strip())[:120]
    return ""


def _result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                          if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _spool_label(path: Path, st=None):
    """The first non-empty line of a command's output — what a reader scans for.

    The task id alone (`b9qckl28g`) names nothing, and the command text is not
    recorded anywhere the spool can reach.

    NO timestamp. This label is rendered inside a boundary marker that already
    carries one, and the two disagreed: the marker prints when the command
    STARTED while this carried the file's last-write time, so a banner read
    `▶ 18:30:20  18:30:14 …` — two clocks, neither explained. The marker owns
    the time; this owns the name.
    """
    head = ""
    try:
        with path.open("r", errors="replace") as fh:
            for line in fh:
                if line.strip():
                    head = line[:400]
                    break
    except OSError:
        pass
    # Neutralized HERE, not only at the renderer. This is arbitrary command
    # output on its way to becoming a UI string, and a label that depends on
    # every downstream consumer escaping correctly is one consumer away from
    # the DOM corruption this already caused once. Control characters (a
    # progress bar's \r, an ANSI escape) are dropped and runs of whitespace
    # collapsed, so the label stays one line whatever the command emitted.
    head = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", head)
    head = re.sub(r"[\x00-\x1f\x7f]+", " ", head)
    head = re.sub(r"\s+", " ", head).strip()[:60]
    # Still named by SOMETHING when a command has emitted nothing yet: the task
    # id is a poor name but a present one, where an empty banner would read as
    # a command that did not start.
    return head or path.stem


_SESSION_META_CACHE = {}
_meta_lock = threading.Lock()


def _session_meta(jsonl: Path):
    """Title, start time and branch for one session, from its transcript.

    `ai-title` entries are interspersed rather than trailing and the LAST one is
    the current title, so the answer needs the whole file — but only ONCE. The
    listing is polled every 10 seconds for as long as the tab is open and a
    transcript reaches several MB, so each call resumes from where the last one
    stopped and parses only what was appended since.

    Only whole lines are consumed: the file is being appended to while we read
    it, and a torn final line consumed now would never be re-read.
    """
    key = str(jsonl)
    try:
        size = jsonl.stat().st_size
    except OSError:
        return None

    with _meta_lock:
        cached = _SESSION_META_CACHE.get(key)
    if cached and size >= cached["read"]:
        state = dict(cached)
    else:
        # Shrank: rotated or rewritten, so the cached offset is meaningless.
        state = {"read": 0, "title": None, "started": None, "branch": None,
                 "last_human": None}

    if size > state["read"]:
        try:
            with jsonl.open("rb") as fh:
                fh.seek(state["read"])
                data = fh.read()
        except OSError:
            return None
        end = data.rfind(b"\n")
        if end != -1:
            chunk = data[:end + 1].decode("utf-8", errors="replace")
            for line in chunk.splitlines():
                # Substring test before json.loads — cheap over MBs, where a
                # parse per line is not.
                if state["started"] is None and '"timestamp"' in line:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    state["started"] = d.get("timestamp")
                    state["branch"] = state["branch"] or d.get("gitBranch")
                elif '"ai-title"' in line:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if d.get("aiTitle"):
                        state["title"] = d["aiTitle"]
                elif '"type":"user"' in line or '"type": "user"' in line:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if _is_substantial(d):
                        state["last_human"] = d.get("timestamp") or state["last_human"]
            state["read"] += end + 1

    with _meta_lock:
        _SESSION_META_CACHE[key] = dict(state)
    return {"title": state["title"], "started": state["started"],
            "branch": state["branch"], "last_human": state["last_human"]}


def _is_substantial(rec):
    """Did a person actually say something in this record?

    The sort key this feeds exists because ordering on the transcript's mtime
    ranked sessions by "was this file touched", and merely CLOSING a long-dead
    session touches it — so the session a reader had finished with jumped to the
    top of the list, above the one they were working in.

    Three things write `user` records and only one of them is a person:
      * a `text` block — typed input, the only case that counts;
      * `tool_result` blocks — the harness feeding output back, which outnumber
        real turns roughly twenty to one;
      * a slash command — a control gesture, not a conversation. The owner's
        ruling: a session where nothing happened but `/compact` has not been
        interacted with.

    Anything unparseable counts as NOT substantial, which is the safe direction:
    a session is then ordered by an older, real interaction rather than promoted
    on evidence we could not read.
    """
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    else:
        return False
    text = text.strip()
    if not text:
        return False
    # A slash command, with or without the harness's markup around it. `/loop 5m
    # do the thing` is still a command; a message that merely MENTIONS a command
    # further in is a real one, so only the opening token is examined.
    if text.startswith("<command-name>") or text.startswith("<command-message>"):
        return False
    return not text.startswith("/")


def _local_stamp(iso, fallback_mtime):
    """Transcripts store UTC; the header shows local time or it misleads."""
    if iso:
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return dt.astimezone().strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return datetime.fromtimestamp(fallback_mtime).strftime("%Y-%m-%d %H:%M")


def _epoch_of(iso, fallback):
    """Transcript timestamps are ISO/UTC; comparing them to a spool start needs
    seconds, and a session whose first line is unparseable falls back to mtime
    rather than reporting a gap it cannot substantiate."""
    if iso:
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return fallback


def _newest_task(tasks_dir: Path):
    try:
        outs = [p for p in tasks_dir.iterdir() if p.suffix == ".output"]
    except OSError:
        return None
    return max(outs, key=lambda p: p.stat().st_mtime, default=None)


def _registered(registry: Path, uuid: str):
    """Process streams a session explicitly registered (console-register.sh)."""
    out = []
    d = registry / uuid
    try:
        entries = sorted(d.glob("*.json"))
    except OSError:
        return out
    for f in entries:
        try:
            desc = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        out.append({
            "id":    f"session:{uuid}:proc:{f.stem}",
            "label": desc.get("label") or f.stem,
            "path":  desc.get("path"),
            "pid":   desc.get("pid"),
            "rc":    desc.get("rc"),
        })
    return out


# ── The two-log framework ───────────────────────────────────────────────────
#
# A daemon writes on two channels, and they are different things:
#
#   captured   what it printed on stdout/stderr, redirected into a file by
#              whoever launched it — a shell, a service unit, a test harness.
#              The daemon does not know the file exists.
#   own log    the file the daemon opened for itself and writes its levelled
#              product log to.
#
# They are told apart by FILE DESCRIPTOR NUMBER, which is evidence rather than
# convention: fds 1 and 2 ARE stdout and stderr by definition, so a regular file
# sitting on one of them is a captured stream whatever it is named, and a `.log`
# held on any other descriptor is a log the process opened itself. This needs no
# product name, no path template and no cooperation from the daemon — it is what
# `lsof` reads, without the subprocess.
#
# Naming convention is the fallback for a daemon that is NOT running: with no
# pid there are no descriptors to read, so the filename is the only evidence
# left. `<stem>.log` is the own log and `<stem>_err.log` (or -err, .err, _stderr,
# _out, _stdout) is the captured stream, and the two fold onto one row because
# they name one daemon.
#
# Nothing here is specific to any project: the directory is a parameter, the
# classification is a kernel fact, and the convention is over separators rather
# than over names.

_CAPTURED_MARKERS = ("err", "errors", "stderr", "out", "stdout")

# A marker counts only when a separator introduces it. Without that anchor
# `layout.log` loses its tail to `out` and files itself under `lay`.
_RE_CAPTURED = re.compile(
    r"^(.*[^-_.])[-_.](?:%s)$" % "|".join(_CAPTURED_MARKERS), re.I)

# A crash log is a daemon's THIRD channel, not a daemon of its own. Without
# this, `derivemeta-crash.log` reduced to the key `derivemeta-crash` and stood
# as a separate row — a phantom process with no pid, no cpu and no memory,
# one per executable that had ever started, while the backtraces it held sat
# under a name matching nothing on the system.
_CRASH_MARKERS = ("crash",)
_RE_CRASH = re.compile(
    r"^(.*[^-_.])[-_.](?:%s)$" % "|".join(_CRASH_MARKERS), re.I)


def _daemon_key(stem: str) -> str:
    """Grouping identity for a daemon — separator- and case-insensitive.

    `mti-sidecar.log` and `mti_sidecar_err.log` are one daemon's two channels
    even though they disagree on a separator, so both must reduce to one key.
    The same normaliser runs over a live process's name, which is how an
    evidence row and a convention row for one daemon meet on one line.
    """
    return re.sub(r"[-_.\s]+", "-", stem).strip("-").lower()


def _split_channel(filename: str):
    """(key, channel) for a log filename, by naming convention alone."""
    stem = filename[:-4] if filename.endswith(".log") else filename
    # Crash first: it is the more specific claim, and a name ending in both
    # markers must not be filed by the weaker one.
    m = _RE_CRASH.match(stem)
    if m:
        return _daemon_key(m.group(1)), "crash"
    m = _RE_CAPTURED.match(stem)
    if m:
        return _daemon_key(m.group(1)), "captured"
    return _daemon_key(stem), "own"


def _proc_name(pid: int):
    """What to call the process: argv[0]'s basename, falling back to comm.

    `comm` is the FALLBACK rather than the source because the kernel truncates
    it to 15 characters, and a name that silently loses its tail cannot be
    matched against the log filenames it has to group with.
    """
    try:
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        argv = []
    if argv and argv[0]:
        base = os.path.basename(argv[0].decode("utf-8", "replace")).strip()
        if base:
            return base
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip() or None
    except OSError:
        return None


_O_ACCMODE = 3          # O_RDONLY=0, O_WRONLY=1, O_RDWR=2


def _fd_writable(pid, fd) -> bool:
    """Was this descriptor opened for writing?

    Holding a log open is only evidence of OWNING it if the holder can write to
    it. Without this check every reader becomes a daemon: `tail -f mti-fuse.log`
    would be listed as the process producing that log, and so would this very
    server the moment a pane was opened on one. The access mode is in fdinfo's
    octal `flags`, which is the kernel's own answer rather than an inference
    from the process name.
    """
    try:
        with open(f"/proc/{pid}/fdinfo/{fd}") as fh:
            for line in fh:
                if line.startswith("flags:"):
                    return int(line.split()[1], 8) & _O_ACCMODE in (1, 2)
    except (OSError, ValueError, IndexError):
        return False
    return False


_LOG_CHANNELS_CACHE = {}
_LOG_CHANNELS_TTL = 3.0  # seconds
_log_channels_lock = threading.Lock()


def _log_channels(daemon_dir: Path, registry: Path = None):
    """Every log under `daemon_dir` a process holds open, classified by fd.

    `registry` names the console spool's own tree, which is EXCLUDED. The
    spool captures by holding each command's spool file open for writing, and
    that tree lives under the daemon directory — so without this every command
    being captured surfaced here as a daemon channel named after its task id,
    offered as a chip that then 404'd ("stream no longer served") because no
    daemon log of that name exists. The spool's descriptors are capture
    machinery, not a daemon writing its log.

    Returns `(owners, holder)`:
      owners  pid -> {"name", "captured": {path…}, "own": {path…}}
      holder  path -> pid, the truthful answer to "is this file live", where
              mtime is only a proxy: a log nothing has open is finished no
              matter how recently it was touched, and a log held open is live
              no matter how long it has been quiet.

    One pass over /proc; other users' processes raise PermissionError and are
    skipped, which is correct — we only care about this user's daemons.
    """
    root = os.path.realpath(daemon_dir)
    spool_root = os.path.realpath(
        registry if registry is not None else CONSOLE_ROOTS["registry"])
    now = time.monotonic()
    with _log_channels_lock:
        cached = _LOG_CHANNELS_CACHE.get((root, spool_root))
        if cached is not None and (now - cached[0]) < _LOG_CHANNELS_TTL:
            cached_owners, cached_holder = cached[1]
            still_valid = True
            for pid in cached_owners:
                try:
                    os.kill(pid, 0)
                except OSError:
                    still_valid = False
                    break
            if still_valid:
                return ({p: {k: set(v) if isinstance(v, set) else v for k, v in r.items()}
                         for p, r in cached_owners.items()}, dict(cached_holder))

    owners, holder = {}, {}
    try:
        pids = [d for d in os.listdir("/proc") if d.isdigit()]
    except OSError:
        return owners, holder
    my_uid = os.getuid()
    for pid in pids:
        try:
            if os.stat(f"/proc/{pid}").st_uid != my_uid:
                continue
        except OSError:
            continue
        fd_dir = f"/proc/{pid}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(f"{fd_dir}/{fd}")
            except OSError:
                continue
            # The kernel suffixes an unlinked target with " (deleted)".
            # Such an fd is NOT the file of that name on disk — it is a
            # rotated generation the writer never reopened — so it must not
            # mark the current file live. Stripping the suffix and matching
            # anyway would attribute a process to a file it is not writing
            # to, which is exactly the lie this column exists to prevent.
            if target.endswith(" (deleted)"):
                continue
            if not target.startswith(root + os.sep):
                continue
            # The console spool's own capture descriptors (see the docstring).
            if target.startswith(spool_root + os.sep):
                continue
            # The crash log is identified by NAME before fd number, because
            # the handler holds it on an ordinary descriptor for the process's
            # whole life — by fd alone it looks exactly like the daemon's own
            # log and would compete with it for the same cell.
            if _split_channel(os.path.basename(target))[1] == "crash":
                channel = "crash"
            elif fd in ("1", "2"):
                channel = "captured"
            elif target.endswith(".log"):
                channel = "own"
            else:
                # Held open, but not a log: a database, a WAL, a lock file.
                # `mtid` alone holds ninety descriptors on its sqlite file, so
                # without this the process row would be almost entirely noise.
                continue
            # Checked only AFTER the cheap filters above, so the extra read
            # happens for the handful of real candidates rather than for every
            # descriptor in the directory.
            if not _fd_writable(pid, fd):
                continue
            ipid = int(pid)
            rec = owners.get(ipid)
            if rec is None:
                rec = owners[ipid] = {"name": None, "captured": set(),
                                      "own": set(), "crash": set()}
            rec[channel].add(target)
            holder.setdefault(target, ipid)
    for pid, rec in owners.items():
        rec["name"] = _proc_name(pid) or str(pid)
    with _log_channels_lock:
        _LOG_CHANNELS_CACHE[(root, spool_root)] = (
            now,
            ({p: {k: set(v) if isinstance(v, set) else v for k, v in r.items()}
              for p, r in owners.items()}, dict(holder))
        )
    return owners, holder


def _daemon_rows(daemon_dir: Path, owned=frozenset(), registry: Path = None):
    """One row per daemon: its process when running, its channels either way.

    The row is the DAEMON, not the file. A daemon with two logs used to be two
    unrelated rows, sorted apart from each other, neither saying which process
    it belonged to. Liveness is carried per CHANNEL rather than per row because
    the two are independently live: a daemon can be writing its own log long
    after the shell that captured its stderr exited.

    Evidence wins where it exists and convention fills in behind it — a file a
    process holds open is attributed to that process and to nothing else, and
    only files no one holds are grouped by name. Rows are keyed on the
    normalised identity so an evidence row and a convention row for one daemon
    merge, with a pid suffix when two live processes share a name: folding two
    processes onto one line would be a lie no column could correct.
    """
    owners, holder = _log_channels(daemon_dir, registry)
    rows, by_key = [], {}

    def _row(key, name):
        r = by_key.get(key)
        if r is None:
            r = by_key[key] = {"key": key, "name": name, "pid": None,
                               "captured": None, "own": None, "crash": None}
            rows.append(r)
        return r

    def _chan(path):
        try:
            st = os.stat(path)
        except OSError:
            return None
        return {"id": f"daemon:{os.path.basename(path)}",
                "label": os.path.basename(path),
                "size": st.st_size, "mtime": st.st_mtime,
                "live": path in holder}

    # Evidence first, so a held file is never re-attributed by name below.
    for pid, rec in sorted(owners.items()):
        key = _daemon_key(rec["name"])
        if key in by_key and by_key[key]["pid"] not in (None, pid):
            key = f"{key}#{pid}"
        r = _row(key, rec["name"])
        r["pid"] = pid
        for channel in ("captured", "own", "crash"):
            for path in sorted(rec[channel]):
                ch = _chan(path)
                # Two files on one channel — a process holding fd 1 and fd 2 on
                # different captures. The one being written to now is the one
                # worth showing.
                if ch and (r[channel] is None
                           or ch["mtime"] > r[channel]["mtime"]):
                    r[channel] = ch

    # Convention behind it, for everything nothing holds open.
    try:
        names = sorted(p.name for p in daemon_dir.glob("*.log"))
    except OSError:
        names = []
    for name in names:
        path = os.path.realpath(daemon_dir / name)
        if path in holder or path in owned:
            continue
        if name.startswith(CONSOLE_ONESHOT_PREFIXES):
            continue
        ch = _chan(path)
        if ch is None:
            continue
        key, channel = _split_channel(name)
        r = _row(key, key)
        if r[channel] is None or ch["mtime"] > r[channel]["mtime"]:
            r[channel] = ch

    for r in rows:
        chans = [r[c] for c in ("captured", "own", "crash") if r[c]]
        r["live"] = any(c["live"] for c in chans)
        r["mtime"] = max((c["mtime"] for c in chans), default=0)
        # An empty crash log is dropped HERE, once, rather than by each reader:
        # the listing and the 3-second numbers poll must agree about which
        # channels exist, and they don't share a code path. Suppressed in one
        # and not the other, the poll writes a size into the cell the listing
        # drew as a dash. Liveness and mtime are taken FIRST — a live process
        # holding an empty crash log is still live evidence.
        if r["crash"] and not r["crash"]["size"]:
            r["crash"] = None
    # A crash log is created when the handler INSTALLS — at startup, before any
    # crash — because open() is not async-signal-safe and the descriptor has to
    # exist before the SIGSEGV that uses it. So an empty one is evidence of a
    # clean run, not a daemon to list, and a row holding nothing else describes
    # no process, no output and no fault: six such rows were being drawn, one
    # per executable that had ever started. A row with a live pid stays whatever
    # its channels look like — that one IS a running daemon.
    return [r for r in rows
            if r["pid"] is not None or r["captured"] or r["own"] or r["crash"]]


_HZ = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
_PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
_CPU_CACHE = {}          # pid -> (cpu_ticks, monotonic) from the previous poll
_LAST_LOAD = {}          # pid -> last measured %CPU, for non-sampling readers
_cpu_lock = threading.Lock()


# ── One-shot reports ────────────────────────────────────────────────────────
#
# A crash report is a one-shot artifact: written once by a tool that then exits,
# never appended to. That is what makes it safe to cache forever on (mtime,
# size) — the real directory holds ~495 of them totalling 121 MB, and reparsing
# that on a ten-second poll would make the tab unusable.
#
# What can be read generically is the same for any project: name, size, age. The
# useful columns — which process died and why — live inside the file in a
# project-owned format, so the patterns come from `server.config.json` rather
# than being baked in here. `ps` output is the one exception worth building in:
# it is a standard format, not a project convention, so resolving a pid to its
# command line through it is generic even though the report that embeds it
# is not.

_ONESHOT_CACHE = {}
_oneshot_lock = threading.Lock()

# A `ps` row: USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND. Tree output
# prefixes the command with drawing characters, which are stripped — they encode
# a parent relationship the table does not show.
# re.M is load-bearing: without it `^` anchors to the start of the whole file
# and this never matches a single row, silently handing every lookup to the
# apport fallback — which yields a bare binary path with no arguments and looks
# like a working column.
_RE_PS_ROW = re.compile(
    r"^\S+\s+(\d+)\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+"
    r"\S+\s+\S+\s+(.*)$", re.M)
_RE_TREE_ART = re.compile(r"^[|\\_\s`-]+")


def _command_for_pid(text: str, pid: str):
    """The command line of `pid`, from any embedded `ps` output in `text`.

    Ordered by trustworthiness rather than by position: a row whose own pid
    field matches is the process itself, while an `apport` row merely mentions
    it as an argument and encodes the path with `!` separators. The first is
    evidence, the second is a fallback used only when the process was already
    gone by the time the report ran.
    """
    for m in _RE_PS_ROW.finditer(text):
        if m.group(1) == pid:
            cmd = _RE_TREE_ART.sub("", m.group(2)).strip()
            if cmd:
                return cmd
    m = re.search(r"apport\b[^\n]*?--\s+(\S+)", text)
    if m:
        return m.group(1).replace("!", "/")
    return None


def _parse_oneshot(path: str, st, fields):
    """Header fields and the triggering command for one report."""
    try:
        with open(path, errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    out = {}
    for name, pattern in fields.items():
        try:
            m = re.search(pattern, text, re.M)
        except re.error:
            continue
        if m:
            out[name] = (m.group(1) if m.groups() else m.group(0)).strip()
    pid = out.get("pid")
    if pid and pid.isdigit():
        out["command"] = _command_for_pid(text, pid)
    return out


def console_oneshots(*, daemon_dir=None, now=None, **_ignored):
    """Every one-shot report, newest first.

    Never pruned automatically. These are the record of something going wrong,
    and a table that quietly deletes them is worse than no table — deletion is
    the reader's decision, taken per row.
    """
    r = _console_roots(daemon_dir=daemon_dir)
    now = now or time.time()
    fields = CONFIG.get("oneshot_fields") or {}
    rows = []
    if not CONSOLE_ONESHOT_PREFIXES:
        return {"rows": [], "bytes": 0}
    try:
        names = sorted(r["daemon_dir"].glob("*.log"))
    except OSError:
        names = []
    total = 0
    for pth in names:
        if not pth.name.startswith(CONSOLE_ONESHOT_PREFIXES):
            continue
        try:
            st = pth.stat()
        except OSError:
            continue
        total += st.st_size
        key = str(pth)
        with _oneshot_lock:
            hit = _ONESHOT_CACHE.get(key)
            if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
                parsed = hit[2]
            else:
                parsed = _parse_oneshot(key, st, fields) or {}
                _ONESHOT_CACHE[key] = (st.st_mtime, st.st_size, parsed)
        rows.append({
            "id": f"daemon:{pth.name}", "name": pth.name,
            "size": st.st_size, "age_s": int(now - st.st_mtime),
            "when": _local_stamp(None, st.st_mtime),
            "pid": parsed.get("pid"), "reason": parsed.get("reason"),
            "command": parsed.get("command"),
        })
    rows.sort(key=lambda x: -(x["age_s"] * -1))
    rows.sort(key=lambda x: x["age_s"])
    return {"rows": rows, "bytes": total}


def console_delete_oneshots(names, *, daemon_dir=None):
    """Delete named one-shot reports. Returns what actually went.

    Every name is validated and the path CONSTRUCTED from the known root — the
    same discipline as every other id here — and the prefix is re-checked at
    delete time so this endpoint can never be talked into removing a daemon's
    live log.
    """
    r = _console_roots(daemon_dir=daemon_dir)
    gone, refused = [], []
    for name in names or []:
        if (not isinstance(name, str) or not _RE_NAME.match(name)
                or not name.endswith(".log")
                or not name.startswith(CONSOLE_ONESHOT_PREFIXES)):
            refused.append(name)
            continue
        path = _contained(r["daemon_dir"] / name, r["daemon_dir"])
        if path is None or not path.is_file():
            refused.append(name)
            continue
        try:
            path.unlink()
        except OSError:
            refused.append(name)
            continue
        with _oneshot_lock:
            _ONESHOT_CACHE.pop(str(path), None)
        gone.append(name)
    return {"deleted": gone, "refused": refused}


def _gate_state_dirs():
    """The tmpfs directories a project's admission gates (a `rustc-gate`,
    `cc-gate`, or a project's own resource gate — each project's launcher
    crate owns its `GateState`) write their per-pid blocked-reason files
    under: `/dev/shm` when it is a writable directory, `/tmp` otherwise, then
    every `<project>-gate-<user>` directory there.

    Inferred the SAME way the Rust side chooses the base (its `tmpfs_base`),
    never configured — the answer is always derivable from the running system
    (AUR P16) — and discovered by shape, never by a project's name, so one doc
    server reads every project's gates and a second, independently-tunable
    copy of this choice cannot drift.
    """
    shm = Path("/dev/shm")
    base = shm if shm.is_dir() and os.access(shm, os.W_OK) else Path("/tmp")
    user = os.environ.get("USER", "default")
    return sorted(d for d in base.glob(f"*-gate-{user}") if d.is_dir())


def _read_gate_entries(dir_path=None):
    """Every live blocked-gate entry under `_gate_state_dirs()`, pruning
    (deleting) any file whose pid is no longer alive and skipping — logged,
    left in place — anything that fails to parse.

    This is the Python half of the rule the writer's own reader states on the
    Rust side (`read_gate_entries` in the project's launcher crate): same
    schema, same pruning-by-pid-liveness rule, so a monitor reading this
    endpoint and the project's own tests can never disagree about which
    entries are live. Only the read is reimplemented here — the gates
    themselves, and the only writers of these files, are the Rust binaries.
    """
    dirs = [Path(dir_path)] if dir_path is not None else _gate_state_dirs()
    out = []
    for path in sorted(p for d in dirs if d.is_dir() for p in d.glob("*.json")):
        try:
            entry = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            print(f"[gate-state] malformed entry {path}: {exc}", file=sys.stderr)
            continue
        pid = entry.get("pid")
        if not isinstance(pid, int):
            print(f"[gate-state] malformed entry {path}: missing/bad pid",
                  file=sys.stderr)
            continue
        if not Path(f"/proc/{pid}").exists():
            # The owning process is gone without running its own cleanup
            # (kill -9) — this reader is the one place that reaps the file,
            # same as the Rust reader, rather than leaving a stale reading for
            # a human to notice.
            try:
                path.unlink()
            except OSError:
                pass
            continue
        out.append({
            "gate": entry.get("gate", ""),
            "pid": pid,
            "verb": entry.get("verb", ""),
            "target": entry.get("target", ""),
            "since": entry.get("since", 0),
            "waited_s": entry.get("waited_s", 0),
            "blocked_by": entry.get("blocked_by", ""),
            "demand": entry.get("demand", ""),
            "criteria": entry.get("criteria", ""),
        })
    return out


def console_procstats(*, daemon_dir=None, tmp_root=None, projects=None,
                      registry=None):
    """Just the numbers for the daemon rows, refreshed on top(1)'s cadence.

    Separate from /consoles because the two answer different questions at
    different rates: the group listing is structure (which sessions exist, which
    logs are streams) and rebuilding it re-renders the list, which at a 3-second
    beat would re-attach every open pane and fight the reader's scroll. This
    returns values only, and the client writes them into the existing cells.
    """
    r = _console_roots(daemon_dir=daemon_dir, tmp_root=tmp_root,
                       projects=projects, registry=registry)
    # The SAME row builder the listing uses. Keying these numbers off a second,
    # independent scan is how the 3-second beat and the 10-second rebuild would
    # drift into disagreeing about which process owns which log — the client
    # writes into cells the listing created, so both must derive identity the
    # one way.
    out = {}
    for row in _daemon_rows(r["daemon_dir"], registry=r["registry"]):
        # `out`/`log` — the SAME channel vocabulary the listing uses. Two names
        # for one channel across two endpoints is a drift waiting to happen,
        # and the client would have to translate between them per poll.
        entry = {"live": row["live"],
                 "ch": {name: {"size": ch["size"], "live": ch["live"]}
                        for name, ch in (("out", row["captured"]),
                                         ("log", row["own"]),
                                         ("crash", row["crash"])) if ch}}
        if row["pid"] is not None:
            entry.update(_proc_stats(row["pid"]) or {"pid": row["pid"]})
        out[f"dmn:{row['key']}"] = entry
    # Rides the same response as the daemon numbers: one sampler owns the
    # 3-second interval, and a second caller taking its own sample would close
    # each delta over whatever gap happened to separate the two requests.
    # The spool reading rides on the 3-second poll that already exists, rather
    # than a timer of its own: a runaway command is only worth watching while
    # someone is watching, and this is the poll that means they are.
    return {"rows": out, "system": system_stats(),
            "top": top_process_groups(),
            "spool": spool_stats(r["registry"]),
            "gates": _read_gate_entries()}


_sys_lock = threading.Lock()

# `mti-tier sysinfo` is the one authority for machine readings AND for the
# thresholds they are judged against — the same `resources::Policy` the
# admission gates apply, so a highlighted field here means exactly what would
# make `rustc-gate` hold a compile back. A second set of numbers parsed out of
# /proc, or a threshold retyped in this file, would be a second definition of
# the policy and would drift the moment a knob moved (AUR P3).
#
# LONG-LIVED, one spawn, because the CPU figures are a delta between two reads
# separated in time: a process per sample could only produce them by sleeping
# inside every poll.
SYSINFO_BIN = _cfg_path("sysinfo_bin")
SYSINFO_INTERVAL_MS = 3000
# Past this, the last line we hold is history, not status. A monitoring row that
# keeps presenting a stale reading as current is the exact failure this whole
# surface exists to stop telling (P11).
SYSINFO_STALE_S = 12.0

_SYSINFO = {"at": 0.0, "fields": None, "error": None}


class SysinfoStream:
    """Reads `mti-tier sysinfo` for as long as the server lives."""

    def __init__(self, binary=None, interval_ms=SYSINFO_INTERVAL_MS):
        chosen = binary or SYSINFO_BIN
        # None is a legitimate configuration, not an error: a project that
        # ships no sysinfo binary simply gets no machine row. `Path(None)`
        # would raise, taking the whole doc server down with it.
        self._bin = Path(chosen) if chosen else None
        self._interval = interval_ms
        self._proc = None
        self._thread = None
        self._stopping = False

    def start(self):
        if self._bin is None:
            _set_sysinfo_error("no sysinfo binary configured — set "
                               "`sysinfo_bin` in server.config.json")
            return False
        if not self._bin.is_file():
            _set_sysinfo_error(f"{self._bin} not built")
            return False
        try:
            self._proc = subprocess.Popen(
                [str(self._bin), "sysinfo", "--interval", str(self._interval)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1)
        except OSError as exc:
            _set_sysinfo_error(f"cannot start {self._bin}: {exc}")
            return False
        self._thread = threading.Thread(target=self._run, name="sysinfo",
                                        daemon=True)
        self._thread.start()
        return True

    def _run(self):
        for line in self._proc.stdout:
            fields = {}
            for tok in line.split():
                k, _, v = tok.partition("=")
                if not v:
                    continue
                try:
                    fields[k] = float(v) if "." in v else int(v)
                except ValueError:
                    continue
            if fields:
                with _sys_lock:
                    _SYSINFO.update(at=time.time(), fields=fields, error=None)
        # The stream ended. Whether the binary died or was killed, the row must
        # stop claiming to be live rather than freeze on its last good value.
        if not self._stopping:
            rc = self._proc.poll()
            _set_sysinfo_error(f"sysinfo sampler exited rc={rc}")

    def stop(self):
        """Owned end to end: the child does not outlive this server on any exit
        path, including the panic one (AUR P8)."""
        self._stopping = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)


def _set_sysinfo_error(msg):
    print(f"[sysinfo] {msg}", file=sys.stderr, flush=True)
    with _sys_lock:
        _SYSINFO.update(error=msg)


def system_stats():
    """The one-line machine reading under the daemon list.

    A job's own log answers "where is it"; it cannot answer "what is this doing
    to the machine", and that is a separate defect class — work running unniced,
    memory draining toward the OOM killer's choice of victim, a disk filling.
    None of it appears in the job's output because none of it is the job's
    business.

    Every number here — and every threshold it is judged against — comes from
    `mti-tier sysinfo`, i.e. from `resources.rs`, i.e. from the same policy the
    admission gates admit work on. This function only decides whether what it
    holds is still current.
    """
    with _sys_lock:
        at, fields, error = _SYSINFO["at"], _SYSINFO["fields"], _SYSINFO["error"]
    age = time.time() - at if at else None
    # An error OUTRANKS staleness, because it is the actionable half. `error` is
    # cleared by every good sample, so its presence means the stream is broken
    # now and these numbers will never refresh — "stale 17s" would describe the
    # symptom while withholding the cause, which is the reader's whole next step
    # ("not built — run `make gates`"). Any fields we still hold ride along, so
    # the row can show WHAT froze rather than going blank.
    if error:
        out = {"status": "unavailable", "error": error}
        if fields:
            out.update(age_s=round(age, 1) if age else None, **fields)
        return out
    if fields is None:
        return {"status": "starting"}
    if age is not None and age > SYSINFO_STALE_S:
        # Held, but flagged. The values are shown so a reader can see WHAT went
        # stale, never presented as current.
        return {"status": "stale", "age_s": round(age, 1), **fields}
    return {"status": "live", "age_s": round(age or 0.0, 1), **fields}


def _proc_stats(pid, sample=True):
    """Priority, load, CPU time and resident size for one pid.

    Load is a DELTA against the previous poll, not a lifetime average: a daemon
    that burned a minute of CPU an hour ago and is idle now would otherwise read
    as permanently busy, which is the opposite of what the row is for. The first
    poll after a pane opens therefore has no load to report and says so rather
    than printing a zero it cannot justify.

    Fields are taken after the LAST ')' — a process whose comm contains spaces
    or parentheses shifts every positional index otherwise.
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    cut = raw.rfind(")")
    if cut < 0:
        return None
    f = raw[cut + 2:].split()
    if len(f) < 22:
        return None
    try:
        utime, stime = int(f[11]), int(f[12])
        nice = int(f[16])
        state = f[0]
    except (ValueError, IndexError):
        return None

    # RES comes from statm, NOT from stat's field 24. proc(5) documents stat's
    # rss as inaccurate and defers to statm, and top reads statm — measured on
    # this machine at one instant, the two disagreed by 1.5 MB (155432 vs
    # 156976 KiB). Reporting a number that merely looks like top's is worse than
    # not reporting one, because it cannot be checked against top side by side.
    try:
        rss_pages = int(Path(f"/proc/{pid}/statm").read_text().split()[1])
    except (OSError, ValueError, IndexError):
        return None

    ticks = utime + stime
    now = time.monotonic()
    # ONE sampler owns the interval. /procstats beats at top's 3s; letting the
    # 10s structure poll also take a sample would close a delta over whatever
    # gap happened to separate the two calls — sometimes 200ms — and the load
    # column would jitter for no reason the reader could see. Non-sampling
    # callers report the last value the real sampler measured.
    with _cpu_lock:
        if not sample:
            load = _LAST_LOAD.get(pid)
        else:
            prev = _CPU_CACHE.get(pid)
            _CPU_CACHE[pid] = (ticks, now)
            load = None
            if prev and now > prev[1]:
                load = max(0.0, (ticks - prev[0]) / _HZ / (now - prev[1]) * 100.0)
                _LAST_LOAD[pid] = load
            else:
                load = _LAST_LOAD.get(pid)

    return {"pid": pid, "state": state, "nice": nice, "load": load,
            "cputime": ticks / _HZ, "rss": rss_pages * _PAGE}


_TOP_PROC_CACHE = {}      # pid -> (ticks, monotonic)
_TOP_PROC_RESULT_CACHE = {"at": 0.0, "data": []}
_TOP_PROC_INTERVAL_S = 2.0
_top_proc_lock = threading.Lock()


def top_process_groups():
    """Top 2 CPU load process groups, plus top 1 RSS group (or 3rd CPU group if covered).

    Aggregates processes sharing the same executable name:
      count, executable name, representative nice, total %CPU, total CPU time (TIME+), total RSS.
    Example line: 7 rustc +11 478% 59:25.33 19G
    """
    now = time.monotonic()
    with _top_proc_lock:
        if (now - _TOP_PROC_RESULT_CACHE["at"]) < _TOP_PROC_INTERVAL_S:
            return list(_TOP_PROC_RESULT_CACHE["data"])

    try:
        pids = [int(d) for d in os.listdir("/proc") if d.isdigit()]
    except OSError:
        return []

    raw_procs = []
    for pid in pids:
        try:
            with open(f"/proc/{pid}/stat", "r") as fh:
                raw = fh.read()
            cut = raw.rfind(")")
            if cut < 0:
                continue
            comm = raw[raw.find("(") + 1:cut]
            parts = raw[cut + 2:].split()
            if len(parts) < 17:
                continue
            utime = int(parts[11])
            stime = int(parts[12])
            nice = int(parts[16])
            ticks = utime + stime

            rss_pages = int(parts[21]) if len(parts) >= 22 else 0
            rss_bytes = rss_pages * _PAGE
            raw_procs.append((pid, comm, nice, ticks, rss_bytes))
        except (OSError, IndexError, ValueError):
            continue

    with _top_proc_lock:
        current_pids = set()
        groups = {}
        for pid, comm, nice, ticks, rss in raw_procs:
            current_pids.add(pid)
            prev = _TOP_PROC_CACHE.get(pid)
            _TOP_PROC_CACHE[pid] = (ticks, now)
            load = 0.0
            if prev and now > prev[1]:
                delta_ticks = ticks - prev[0]
                delta_time = now - prev[1]
                if delta_ticks > 0 and delta_time > 0:
                    load = (delta_ticks / _HZ) / delta_time * 100.0

            g = groups.get(comm)
            if g is None:
                g = groups[comm] = {
                    "name": comm,
                    "count": 0,
                    "nices": [],
                    "load": 0.0,
                    "ticks": 0,
                    "rss": 0,
                }
            g["count"] += 1
            g["nices"].append(nice)
            g["load"] += load
            g["ticks"] += ticks
            g["rss"] += rss

        # Evict dead PIDs to prevent unbounded memory growth
        dead_pids = set(_TOP_PROC_CACHE.keys()) - current_pids
        for dead in dead_pids:
            _TOP_PROC_CACHE.pop(dead, None)

    by_cpu = sorted(groups.values(), key=lambda x: (x["load"], x["ticks"]), reverse=True)
    by_rss = sorted(groups.values(), key=lambda x: x["rss"], reverse=True)

    g1 = by_cpu[0] if len(by_cpu) > 0 else None
    g2 = by_cpu[1] if len(by_cpu) > 1 else None

    covered = {g["name"] for g in (g1, g2) if g}
    top_rss = by_rss[0] if len(by_rss) > 0 else None

    if top_rss and top_rss["name"] not in covered:
        g3 = top_rss
        g3_tag = "rss#1"
    else:
        g3 = by_cpu[2] if len(by_cpu) > 2 else None
        g3_tag = "cpu#3"

    def _fmt(tag, g):
        total_sec = g["ticks"] / _HZ
        m = int(total_sec // 60)
        s = total_sec % 60
        unique_nices = sorted(set(g["nices"]))
        ni_str = ",".join(f"+{n}" if n > 0 else str(n) for n in unique_nices) if unique_nices else "0"
        return {
            "rank": tag,
            "count": g["count"],
            "name": g["name"],
            "nice": ni_str,
            "load": g["load"],
            "cputime": f"{m}:{s:05.2f}",
            "cputime_s": total_sec,
            "rss": g["rss"],
        }

    out = []
    if g1:
        out.append(_fmt("cpu#1", g1))
    if g2:
        out.append(_fmt("cpu#2", g2))
    if g3:
        out.append(_fmt(g3_tag, g3))
    with _top_proc_lock:
        _TOP_PROC_RESULT_CACHE["at"] = now
        _TOP_PROC_RESULT_CACHE["data"] = list(out)
    return out


def _live_sessions(r, now, live_window):
    """AI sessions active within the window, newest first.

    Liveness is transcript mtime: a live session does NOT hold its transcript
    open (checked against running processes — every one had it closed), so
    there is no fd to test and recency is the honest signal available.
    """
    sessions = []
    try:
        transcripts = list(r["projects"].glob("*.jsonl"))
    except OSError:
        transcripts = []
    for jl in transcripts:
        try:
            mtime = jl.stat().st_mtime
        except OSError:
            continue
        if now - mtime > live_window:
            continue
        uuid = jl.stem
        if not _RE_UUID.match(uuid):
            continue
        meta = _session_meta(jl) or {}
        # ONE console per session. The per-command chips that used to follow are
        # DELETED, not demoted: a session that runs two hundred commands grew
        # two hundred buttons, and the reader still had to guess which of them
        # was the build they were waiting on. Filtering the merged history is
        # the "isolate one command" affordance; a row of buttons never was one.
        #
        # `_registered` streams stay. Those are named long-lived processes, not
        # one entry per command, so they are a list a reader can actually hold
        # in their head.
        spooled = _spooled(r["registry"], uuid)
        streams = []
        if spooled:
            streams.append({"id": f"session:{uuid}:history",
                            "label": f"session console ({len(spooled)})",
                            "carries": "stdout+stderr",
                            "primary": True,
                            "live": any(s.get("live") for s in spooled)})
        # The transcript itself, openable. A session closed by accident is
        # otherwise unrecoverable from this tab: the console shows what COMMANDS
        # did, and says nothing about what was being discussed when it ended.
        streams.append({"id": f"session:{uuid}:transcript",
                        "label": "AI transcript",
                        "carries": "conversation",
                        "transcript": True,
                        "live": False})
        streams.extend(_registered(r["registry"], uuid))
        # The ordering key, deliberately NOT the transcript mtime. Those answer
        # different questions: mtime says "this file was touched", which merely
        # CLOSING a finished session does — so a session the reader was done
        # with jumped above the one they were working in. This says "a person
        # spoke here", and falls back through the newest console write to mtime,
        # so a session always sorts on something real.
        active = _epoch_of(meta.get("last_human"), 0)
        if not active and spooled:
            active = max((s.get("mtime_epoch") or 0) for s in spooled)
        if not active:
            active = mtime

        sess = {
            "kind": "session",
            "agent": "Claude",
            "session_id": uuid,
            "title": meta.get("title") or "(untitled session)",
            "started": _local_stamp(meta.get("started"), mtime),
            "branch": meta.get("branch"),
            "mtime": mtime,
            "active": active,
            "streams": streams,
        }
        # The accepted constraint, stated rather than left to be discovered:
        # capture runs only while the server does, so a session that began
        # before it started has a hole at the front and the reader must be told
        # where it ends (P11 — an unstated gap reads as "nothing happened").
        if _SPOOL is not None:
            sess["capture_since"] = _local_stamp(None, _SPOOL.started)
            sess["capture_gap"] = _SPOOL.started > _epoch_of(meta.get("started"),
                                                            mtime)
            if _SPOOL.missed:
                sess["capture_missed"] = _SPOOL.missed
        sessions.append(sess)
    sessions.sort(key=lambda s: s["active"], reverse=True)
    return sessions


def console_groups(*, tmp_root=None, projects=None, daemon_dir=None,
                   registry=None, live_window=CONSOLE_LIVE_WINDOW_S, now=None,
                   include_archive=False):
    """Daemons pinned first, then LIVE sessions newest-first.

    One row per DAEMON rather than per file — see `_daemon_rows`. Rows are
    filtered by AGE, not by name: a daemon whose logs stopped moving days ago is
    not a stream in any useful sense, and age needs no maintenance as new log
    kinds appear where a name blocklist would rot. Quiet rows stay openable
    through `archive`, which is built only when asked for.
    """
    r = _console_roots(tmp_root=tmp_root, projects=projects,
                       daemon_dir=daemon_dir, registry=registry)
    now = now or time.time()
    groups = []

    # Sessions are collected first: a log some session registered belongs to
    # that session, and must not appear a second time as an unowned daemon log.
    sessions = _live_sessions(r, now, live_window)
    owned = {os.path.realpath(s["path"])
             for sess in sessions for s in sess["streams"] if s.get("path")}

    rows = _daemon_rows(r["daemon_dir"], owned, registry=r["registry"])

    recent, older = [], []
    for row in rows:
        # Held open beats every heuristic: something is writing to it right now.
        if row["live"] or now - row["mtime"] <= CONSOLE_DAEMON_WINDOW_S:
            recent.append(row)
        else:
            older.append(row)

    # Live first, then by recency — the thing being written to is what you came
    # to watch, and it must never be pushed off the row by a quiet log.
    recent.sort(key=lambda row: (not row["live"], -row["mtime"]))
    older.sort(key=lambda row: -row["mtime"])
    shown = recent[:CONSOLE_DAEMON_CAP]

    # Counted apart from the rows because it is a different fact, and one the
    # old lumped-under-"older" count got wrong: these are one-shot crash
    # reports, not quiet daemons, and most of them were written recently.
    # `_daemon_rows` drops them, so they are collected here — each is a single
    # closed file with no process behind it, which is a `log` channel and
    # nothing else. They stay reachable through `archive` until the crash-report
    # table takes them; removing them before it exists would make ~490 files
    # unreachable from the page for the width of one commit.
    oneshots = []
    try:
        for p in sorted(r["daemon_dir"].glob("*.log")):
            if not p.name.startswith(CONSOLE_ONESHOT_PREFIXES):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            oneshots.append({
                # Every channel key the row shape defines, present and null —
                # this dict is built by hand rather than by `_row`, so a channel
                # added there and forgotten here is a KeyError in the reader.
                "key": p.name, "name": p.name[:-4], "pid": None, "live": False,
                "mtime": st.st_mtime, "captured": None, "crash": None,
                "own": {"id": f"daemon:{p.name}", "label": p.name,
                        "size": st.st_size, "mtime": st.st_mtime,
                        "live": False}})
    except OSError:
        pass
    oneshots.sort(key=lambda row: -row["mtime"])

    def _entry(row):
        e = {"id": f"dmn:{row['key']}", "name": row["name"],
             "label": row["name"], "live": row["live"],
             "mtime": _local_stamp(None, row["mtime"])}
        # `out` is the captured stdout/stderr, `log` the daemon's own product
        # log — named for the columns they land in. A channel that does not
        # exist is null rather than absent, so the client renders a dash in a
        # real cell instead of collapsing the row's column count.
        for key, ch in (("out", row["captured"]), ("log", row["own"]),
                        ("crash", row["crash"])):
            e[key] = None if ch is None else {
                "id": ch["id"], "label": ch["label"],
                "size": ch["size"], "live": ch["live"]}
        if row["pid"] is not None:
            e["proc"] = (_proc_stats(row["pid"], sample=False)
                         or {"pid": row["pid"]})
        return e

    daemons = {
        "kind": "daemons",
        "agent": "Daemons",
        "title": f"{PROJECT_NAME} daemon and tool logs",
        # The listing window, carried so the sessions header can state it. A
        # number typed into the page would be a second definition that drifts
        # the moment the constant here moves.
        "window_hours": round(live_window / 3600),
        "streams": [_entry(row) for row in shown],
        "hidden_old": (len(recent) - len(shown)) + len(older),
        "hidden_oneshot": len(oneshots),
    }
    if include_archive:
        daemons["archive"] = [_entry(row) for row
                              in recent[CONSOLE_DAEMON_CAP:] + older + oneshots]
    groups.append(daemons)

    groups.extend(sessions)
    return groups


def console_resolve(sid, *, tmp_root=None, projects=None, daemon_dir=None,
                    registry=None):
    """Stream id → a real file, or None. Validate, then CONSTRUCT the path."""
    r = _console_roots(tmp_root=tmp_root, projects=projects,
                       daemon_dir=daemon_dir, registry=registry)
    if not sid or not isinstance(sid, str):
        return None
    parts = sid.split(":")

    if parts[0] == "daemon":
        if len(parts) != 2 or not _RE_NAME.match(parts[1]) or not parts[1].endswith(".log"):
            return None
        path = _contained(r["daemon_dir"] / parts[1], r["daemon_dir"])
        if path is None or not path.is_file():
            return None
        return {"path": path, "label": parts[1], "stream_key": parts[1],
                "pid": None, "rc": None, "kind": "daemon"}

    if parts[0] == "session" and len(parts) >= 3:
        uuid = parts[1]
        if not _RE_UUID.match(uuid):
            return None

        # `stdout` is deliberately absent. It resolved to whichever task file
        # had the newest mtime, which made it re-point at a stale survivor the
        # moment a foreground command exited — in the field, a 157KB gate log
        # from hours earlier, still labelled as the session's live output — and
        # flap between two streams whenever two ran at once, resetting the
        # reader's offset on every flip. Per-command spool ids remove the
        # expression rather than patching around it, so the stream_key of a
        # spooled stream never changes and the client never has to reset.
        if parts[2] == "spool" and len(parts) == 4:
            name = parts[3]
            if not _RE_NAME.match(name):
                return None
            path = _contained(r["registry"] / uuid / "spool" / f"{name}.log",
                              r["registry"])
            if path is None or not path.is_file():
                return None
            # A tailer still holding the source fd means the command has not
            # exited — the truthful liveness signal here, where there is no
            # pid to test.
            live = _spool_live(uuid, name)
            return {"path": path, "label": name, "stream_key": name,
                    "pid": None, "rc": None if live else 0, "kind": "spool"}

        if parts[2] == "proc" and len(parts) == 4:
            name = parts[3]
            if not _RE_NAME.match(name):
                return None
            desc_file = _contained(r["registry"] / uuid / f"{name}.json", r["registry"])
            if desc_file is None or not desc_file.is_file():
                return None
            try:
                desc = json.loads(desc_file.read_text())
            except (OSError, ValueError):
                return None
            target = desc.get("path")
            if not target:
                return None
            path = Path(os.path.realpath(target))
            if not path.is_file():
                return None
            return {"path": path, "label": desc.get("label") or name,
                    "stream_key": name, "pid": desc.get("pid"),
                    "rc": desc.get("rc"), "kind": "proc"}

    return None


def console_read(path: Path, from_byte=None, cap=CONSOLE_CHUNK_CAP):
    """Byte-offset tail.

    `from_byte=None` opens on the tail rather than loading a whole build log.
    A file that SHRANK was rotated or truncated: that is reported as a reset and
    re-read from 0, because silently holding an offset past EOF makes the pane
    stick forever — indistinguishable from a finished job.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return {"text": "", "next": from_byte or 0, "size": 0,
                "reset": False, "missing": True}

    reset = False
    if from_byte is None:
        start = max(0, size - CONSOLE_FIRST_TAIL)
    elif from_byte > size:
        reset, start = True, 0
    else:
        start = max(0, int(from_byte))

    try:
        with path.open("rb") as fh:
            fh.seek(start)
            data = fh.read(cap)
    except OSError:
        return {"text": "", "next": start, "size": size,
                "reset": reset, "missing": True}

    return {"text": data.decode("utf-8", errors="replace"),
            "next": start + len(data), "size": size,
            "reset": reset, "missing": False}


def _console_alive(meta):
    pid = meta.get("pid")
    if pid:
        return Path(f"/proc/{int(pid)}").exists()
    return None


def find_free_port(preferred: int, limit: int = 100) -> int:
    """Try preferred port first, then scan upward until a free one is found."""
    for port in range(preferred, preferred + limit):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("localhost", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port found in {preferred}–{preferred + limit - 1}")


def instance_running(port, name):
    """True if the server answering on localhost:port serves <name>'s docs.

    A bare connect is necessary but not sufficient: ports are dynamic, so a
    stale server.port can name a port ANOTHER project's server now holds —
    "something answers" then reads as "mine is up", and /goto redirects to the
    wrong manifest (observed 2026-09-01: one project's stale port file named
    a port a different project's server had since taken).
    /meta's subtitle is the identity — the same check, for the same reason, as
    open-manifest.sh's is_mine().
    """
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/meta",
                                    timeout=1) as r:
            return json.load(r).get("subtitle") == f"{name}/aur-docs"
    except Exception:
        return False


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def setup(self):
        # Prevent a single hung client from blocking the whole server.
        self.request.settimeout(30)
        super().setup()

    def log_message(self, fmt, *args):
        # Suppress per-request noise; only log errors
        if args and str(args[1]) not in ("200", "304"):
            super().log_message(fmt, *args)

    # ── Reload restore ──────────────────────────────────────────────────────
    #
    # VSCode's Simple Browser hosts the page in a cross-origin iframe and can
    # never observe navigation inside it, so the URL it holds stays the one it
    # was opened with — always the manifest. Refreshing while reading a
    # document therefore landed on the index.
    #
    # It is fixable because Simple Browser stamps its OWN url, measured:
    #
    #   Alt+D    /manifest.html?id=<uuid>&vscodeBrowserReqId=<epoch-ms>
    #   refresh  /manifest.html?id=<same uuid>&vscodeBrowserReqId=<new epoch-ms>
    #   link     /manifest.html                       (no query at all)
    #
    # `id` is a STABLE per-webview session key — VSCode hands us exactly the
    # session identity needed, so no cookie and no global state; two viewers
    # cannot contend, and it stays correct if tabs ever exist. The presence of
    # `vscodeBrowserReqId` says the WEBVIEW loaded this url rather than the
    # user clicking a link.
    #
    # So the rule is decided purely by request shape, with no timer anywhere:
    #
    #   manifest WITH vscodeBrowserReqId + a recorded doc  ->  302 to the doc
    #   manifest WITHOUT it (a link click)                 ->  serve the index
    #
    # A refresh and an Alt+D are genuinely identical at this layer, so Alt+D
    # also lands on the last document. Reaching the index is one click on
    # "← Manifest", which clears the record precisely because it arrives with
    # no query string. Distinguishing them would need an out-of-band flag with
    # a time window, and a time window is the one thing this design refuses.
    #
    # The map is in memory: a restart forgets, and forgetting shows the index,
    # which is the safe direction.
    _last_doc = {}

    def do_GET(self):
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)

        if parsed.path == "/_seen":
            sid, doc = (q.get("sid") or [""])[0], (q.get("doc") or [""])[0]
            if sid and doc.endswith(".html") and "/" not in doc.lstrip("/")[:-5]:
                Handler._last_doc[sid] = "/" + doc.lstrip("/")
            self._json({"ok": True})
            return
        if parsed.path == "/_clear":
            Handler._last_doc.pop((q.get("sid") or [""])[0], None)
            self._json({"ok": True})
            return

        if parsed.path == "/manifest.html" and "vscodeBrowserReqId" in q:
            target = Handler._last_doc.get((q.get("id") or [""])[0])
            if target:
                self.send_response(302)
                self.send_header("Location", target)
                self.end_headers()
                return

        if parsed.path.startswith("/annotations/"):
            self._get_annotations(parsed.path[len("/annotations/"):])
        elif parsed.path == "/docs":
            self._get_docs()
        elif parsed.path == "/meta":
            self._get_meta()
        elif parsed.path == "/spec/":
            self._get_spec_list()
        elif parsed.path.startswith("/spec-aur/"):
            self._serve_aur_spec(parsed.path[len("/spec-aur/"):])
        elif parsed.path.startswith("/spec/"):
            self._serve_spec(parsed.path[len("/spec/"):])
        elif parsed.path == "/ai-context/":
            self._get_ai_context_list()
        elif parsed.path.startswith("/ai-context/"):
            self._serve_ai_context(parsed.path[len("/ai-context/"):])
        elif parsed.path == "/goto-aur":
            self._goto_aur()
        elif parsed.path == "/projects":
            self._get_projects()
        elif parsed.path.startswith("/goto/"):
            self._goto_project(unquote(parsed.path[len("/goto/"):]).strip("/"))
        elif parsed.path == "/oneshots":
            self._json(console_oneshots())
        elif parsed.path == "/procstats":
            self._json(console_procstats())
        elif parsed.path == "/consoles":
            self._get_consoles(parse_qs(parsed.query))
        elif parsed.path.startswith("/transcript/"):
            self._console_transcript(unquote(parsed.path[len("/transcript/"):]),
                                     parse_qs(parsed.query))
        elif parsed.path.startswith("/history/"):
            self._console_history(unquote(parsed.path[len("/history/"):]),
                                  parse_qs(parsed.query))
        elif parsed.path.startswith("/console/"):
            self._console_tail(unquote(parsed.path[len("/console/"):]),
                               parse_qs(parsed.query))
        else:
            p = parsed.path
            if p.endswith(".html") and p not in ("/manifest.html",):
                stem = p.lstrip("/")[:-5]
                if stem:
                    self._track_read(stem)
            super().do_GET()

    # ── Cross-project navigation ────────────────────────────────────────────
    #
    # Projects are discovered on the FILESYSTEM, never by probing ports. A port
    # scan can only find servers that are ALREADY RUNNING, and the single most
    # valuable thing /goto does is start one that is not — so a scan would be
    # blind to exactly the case the feature exists for. The sibling layout it
    # walks (`develop/<project>/`) is not a new assumption: every shared asset
    # in this doc system is a relative symlink that already depends on it.
    #
    # Two layouts are recognised because AUR self-hosts from `server/`, while
    # every installed project keeps its instance under `aur/`.

    @staticmethod
    def _project_server(name):
        """Path to <name>'s server.py, from the REGISTRY — never from a layout.

        A directory layout is one person's convention; the registry is a record
        of what was actually installed, written by the component that knew.
        """
        if not name or "/" in name or name.startswith("."):
            return None
        if aurshare is None:
            return None
        for row in aurshare.registry_read():
            if Path(row["path"]).name != name:
                continue
            cand = Path(row["path"]) / row.get("layout", "aur") / "server.py"
            if cand.is_file():
                return cand
        return None

    def _get_projects(self):
        """Registered doc-server instances, for the manifest's project menu.

        Reports every instance, running or not: the menu's job is to offer them,
        and /goto starts the ones that are down — which is precisely why this
        cannot be a port scan.
        """
        out = []
        me = _HERE.parent.resolve()
        if aurshare is not None:
            for row in aurshare.registry_read():
                proj = Path(row["path"])
                server = proj / row.get("layout", "aur") / "server.py"
                if not server.is_file():
                    continue
                try:
                    port = int((server.parent / "server.port").read_text().strip())
                except (OSError, ValueError):
                    port = None
                out.append({"name": proj.name,
                            "self": proj.resolve() == me,
                            "port": port,
                            "running": bool(port and
                                            instance_running(port, proj.name))})
        self._json(out)

    def _goto_aur(self):
        """Legacy route. AUR is just another sibling; keep the URL working."""
        self._goto_project("aur")

    def _goto_project(self, name):
        """Redirect to <name>'s doc manifest, STARTING its server if it is down.

        Handing the reader a command to paste is not a fallback, it is the
        failure: a doc server that has just established another doc server is
        not running is in a strictly better position to start it than the
        person who clicked a link.

        Ports are dynamic — find_free_port scans upward when the preferred one
        is busy — so the port file is re-read on every poll, not once.
        """
        import html as _html

        server = self._project_server(name)
        port_file = (server.parent / "server.port") if server else None

        def live_port():
            try:
                p = int(port_file.read_text().strip())
            except (OSError, ValueError, AttributeError):
                return None
            return p if instance_running(p, name) else None

        port = live_port()
        started = False
        if port is None and server is not None:
            try:
                # Detached, so it outlives the handler thread that spawned it.
                subprocess.Popen(
                    [sys.executable, str(server)], cwd=str(server.parent),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True)
                started = True
            except OSError:
                started = False
            deadline = time.monotonic() + 5.0
            while port is None and time.monotonic() < deadline:
                time.sleep(0.1)
                port = live_port()

        if port is not None:
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{port}/manifest.html")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return

        # Reached only when starting it FAILED. Report what was ATTEMPTED, not
        # merely what to try: a reader told "run this" after the server already
        # ran it, and was not told so, will run it and watch it fail identically.
        safe = _html.escape(name)
        if server is None:
            cause = (f"<p>No doc-server instance found for <code>{safe}</code> "
                     f"under <code>{_HERE.parent.parent}</code>. It may have been "
                     f"moved, renamed, or never installed.</p>")
        elif started:
            cause = (f"<p>It was started automatically but did not bind a port "
                     f"within 5 seconds. Its startup output is discarded, so run "
                     f"it in a terminal to see why:</p>"
                     f"<pre style='background:#f1f3f5;padding:.8rem;"
                     f"border-radius:6px'>python3 {server}</pre>")
        else:
            cause = (f"<p>It could not be launched at all. Run it in a terminal "
                     f"to see why:</p>"
                     f"<pre style='background:#f1f3f5;padding:.8rem;"
                     f"border-radius:6px'>python3 {server}</pre>")
        body = ("<!DOCTYPE html><meta charset='utf-8'>"
                f"<title>{safe} docs — could not start</title>"
                "<body style='font-family:system-ui;max-width:640px;"
                "margin:3rem auto;padding:0 1rem;line-height:1.5'>"
                f"<h2>The {safe} doc server could not be started</h2>{cause}"
                "<p><a href='/manifest.html'>← back to this project"
                "'s manifest</a></p>").encode()
        self.send_response(503)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _track_read(self, stem):
        """Record that a document was opened, for the manifest's read column.

        Restored 2026-08-08: destroyed by a line-range replacement that assumed
        everything between two anchors belonged to the method being rewritten.
        It did not. Every document fetch 500'd for hours and no test caught it,
        because the tests only ever requested /manifest.html, /meta and /goto —
        never an actual document.
        """
        with _doc_lock:
            try:
                reads = json.loads(READS_FILE.read_text()) if READS_FILE.exists() else {}
                reads[stem] = datetime.now().isoformat()
                READS_FILE.write_text(json.dumps(reads, indent=2))
            except Exception:
                pass

    def _get_docs(self):
        import html
        import re
        reads = {}
        with _doc_lock:
            if READS_FILE.exists():
                try:
                    reads = json.loads(READS_FILE.read_text())
                except Exception:
                    pass
        docs = []
        for f in ROOT.glob("*.html"):
            if f.name == "manifest.html":
                continue
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
                title_m = re.search(r"<title>([^<]+)</title>", content, re.IGNORECASE)
                title = title_m.group(1).strip() if title_m else f.stem
                # The regex above lifts the title as raw SOURCE text, so every
                # entity in it is still written out.  Decode the whole set, not
                # one entity by hand: the client renders this through
                # textContent, which re-escapes whatever survives, so an
                # undecoded `&mdash;` reaches the reader as those eight
                # literal characters.  Decoding FIRST also lets the date
                # strippers below see a real em dash.
                title = html.unescape(title)
                title = re.sub(r"\(\d{4}-\d{2}-\d{2},\s*", "(", title)
                title = re.sub(r"\s*[·•]\s*\d{4}-\d{2}-\d{2}\s*$", "", title)
                title = re.sub(r"\s*—\s*\d{4}-\d{2}-\d{2}\s*$", "", title)
                title = re.sub(r"\s+\d{4}-\d{2}-\d{2}\s*$", "", title)
                title = title.strip()

                ann_count = 0
                ann_file = ANNOTATIONS_DIR / f"{f.stem}.json"
                if ann_file.exists():
                    try:
                        d = json.loads(ann_file.read_text())
                        ann_count = sum(1 for v in d.values() if str(v).strip())
                    except Exception:
                        pass

                dt_ts = f.stat().st_mtime
                dt_str = datetime.fromtimestamp(dt_ts).strftime("%Y-%m-%d %H:%M:%S")

                docs.append({
                    "filename": f.name,
                    "datetime": dt_str,
                    "datetime_ts": dt_ts,
                    "title": title,
                    "annotation_count": ann_count,
                    "last_read": reads.get(f.stem),
                })
            except Exception:
                pass

        docs.sort(key=lambda x: x["datetime_ts"], reverse=True)
        data = json.dumps(docs).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/annotations/"):
            self._post_annotations(parsed.path[len("/annotations/"):])
        elif parsed.path == "/oneshots/delete":
            self._delete_oneshots()
        else:
            self.send_error(404)

    def _delete_oneshots(self):
        """Delete selected one-shot reports.

        POST rather than GET because it destroys data, and a GET would be
        followed by a link prefetcher. The response reports what actually went
        and what was refused, per name — a bulk delete that answers only "ok"
        cannot be checked, and this is the one operation here with no undo.
        """
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, OSError):
            self.send_error(400, "bad JSON")
            return
        names = payload.get("names")
        if not isinstance(names, list) or not names:
            self.send_error(400, "expected a non-empty `names` list")
            return
        self._json(console_delete_oneshots(names))

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _get_meta(self):
        data = json.dumps({
            "project":  PROJECT_NAME.upper(),
            "subtitle": f"{ROOT.parent.name}/{ROOT.name}",
            # Project-pinned documents, surfaced as header badges. A project
            # always has one or two docs worth reaching in one click, and
            # hard-coding MTI's into the shared page is what made this file a
            # fork in the first place.
            "links":    CONFIG["header_links"],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _get_spec_list(self):
        import re
        from datetime import datetime
        project_root = ROOT.parent

        def _describe(f):
            # First non-heading line, as a short preview.
            preview = f.read_text(encoding="utf-8", errors="ignore")[:600]
            for line in preview.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    return line[:140]
            return ""

        specs = []
        for f in sorted(project_root.glob("*.md")):
            if f.name.startswith("."):
                continue
            try:
                specs.append({
                    "filename": f.name,
                    "mtime": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "description": _describe(f),
                    "source": "project",
                    "url": f"/spec/{f.name}",
                })
            except Exception:
                pass

        # Inherited AUR specs (explicit allowlist; served via /spec-aur/). Tagged
        # source="AUR" so the manifest can group/label them apart from MTI's own.
        for name, path in AUR_SPECS.items():
            if not path.exists():
                continue
            try:
                specs.append({
                    "filename": name,
                    "mtime": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "description": _describe(path),
                    "source": "AUR",
                    "url": f"/spec-aur/{name}",
                })
            except Exception:
                pass

        data = json.dumps(specs).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _serve_spec(self, filename):
        # No path traversal: reject any separator/parent ref before joining.
        if not filename or "/" in filename or ".." in filename or not filename.endswith(".md"):
            self.send_error(404)
            return
        md_path = ROOT.parent / filename
        if not md_path.exists():
            self.send_error(404)
            return
        self._render_md_page(md_path)

    def _serve_aur_spec(self, filename):
        # Inherited AUR spec: resolve ONLY via the explicit allowlist dict — the
        # client string indexes a pre-resolved map, it is never joined to a path,
        # so the traversal guard is structurally unnecessary here (no path math).
        md_path = AUR_SPECS.get(filename)
        if md_path is None or not md_path.exists():
            self.send_error(404)
            return
        self._render_md_page(md_path)

    def _get_ai_context_list(self):
        import re
        from datetime import datetime

        def _frontmatter_description(text):
            m = re.match(r"^---\s*\n(.*?\n)---\s*\n", text, re.DOTALL)
            if not m:
                return None
            d = re.search(r'^description:\s*"?(.*?)"?\s*$', m.group(1), re.MULTILINE)
            return d.group(1) if d else ""

        def _first_line(text):
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith(("#", "---")):
                    return line[:160]
            return ""

        items = []
        for f in sorted(AI_CONTEXT_ROOT.rglob("*.md")):
            if AI_CONTEXT_EXCLUDE_DIRS & set(f.relative_to(AI_CONTEXT_ROOT).parts[:-1]):
                continue
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
                rel = f.relative_to(AI_CONTEXT_ROOT).as_posix()
                if rel == "CLAUDE.md":
                    category = "Instructions"
                elif rel in ("memory/MEMORY.md", "memory/MEMORY-archive.md"):
                    category = "Index"
                elif rel.startswith("memory/feedback_"):
                    category = "Feedback"
                elif rel.startswith("memory/session_checkpoint_"):
                    category = "Checkpoints"
                elif rel.startswith("memory/project_"):
                    category = "Project"
                else:
                    category = "Other"
                desc = _frontmatter_description(content)
                if desc is None:
                    desc = _first_line(content)
                items.append({
                    "filename": rel,
                    "mtime": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "description": desc,
                    "category": category,
                    "source": "project",
                    "url": f"/ai-context/{rel}",
                })
            except Exception:
                pass

        for name, path in AUR_SPECS.items():
            if not path.exists():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
                items.append({
                    "filename": name,
                    "mtime": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "description": _first_line(content),
                    "category": "Instructions",
                    "source": "AUR",
                    "url": f"/spec-aur/{name}",
                })
            except Exception:
                pass

        data = json.dumps(items).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _serve_ai_context(self, relpath):
        # No traversal: reject separators-as-parent-refs and resolve must stay
        # under AI_CONTEXT_ROOT.
        if not relpath or ".." in relpath or not relpath.endswith(".md"):
            self.send_error(404)
            return
        md_path = (AI_CONTEXT_ROOT / relpath).resolve()
        if AI_CONTEXT_ROOT.resolve() not in md_path.parents or not md_path.exists():
            self.send_error(404)
            return
        self._render_md_page(md_path, strip_frontmatter=True)

    def _render_md_page(self, md_path, strip_frontmatter=False):
        import html as html_mod
        content = md_path.read_text(encoding="utf-8", errors="ignore")
        import markdown as md_lib, re as _re
        from datetime import datetime as _dt
        if strip_frontmatter:
            fm = _re.match(r"^---\s*\n.*?\n---\s*\n", content, _re.DOTALL)
            if fm:
                content = content[fm.end():]
        # Strip code fences that wrap pipe tables so they render as HTML tables.
        # Other code blocks (C, bash, etc.) are preserved intact.
        def _strip_table_fences(text):
            def _check(m):
                inner = m.group(1)
                return inner if inner.strip().startswith("|") else m.group(0)
            return _re.sub(r"```[^\n]*\n(.*?)```", _check, text, flags=_re.DOTALL)
        body_html = md_lib.markdown(
            _strip_table_fences(content),
            extensions=["tables", "fenced_code", "nl2br", "sane_lists"],
        )
        # Wrap every table in a scrollable div
        body_html = body_html.replace("<table>", '<div class="tbl-wrap"><table>').replace("</table>", "</table></div>")
        # File mtime formatted as YYYY-MM-DD H:MMam/pm for the bar
        _mt = _dt.fromtimestamp(md_path.stat().st_mtime)
        _h  = _mt.hour
        ts_str = f"{_mt.year}-{_mt.month:02d}-{_mt.day:02d} {_h%12 or 12}:{_mt.minute:02d}{'pm' if _h>=12 else 'am'}"
        title = html_mod.escape(md_path.stem)
        page = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<title>{title} — {PROJECT_NAME} Spec</title>
<style>
:root{{--bg:#1e1e2e;--bd:#3a3a55;--tx:#cdd6f4;--mu:#7f849c;
  --ac:#89b4fa;--cy:#89dceb;--cb:#181825;--de:#313244;}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  font-size:14px;line-height:1.7;background:var(--bg);color:var(--tx);
  padding:32px;max-width:960px;margin:0 auto}}
.bar{{position:sticky;top:0;z-index:100;background:var(--cb);
  border-bottom:1px solid var(--bd);padding:8px 16px;display:flex;
  align-items:center;gap:12px;margin:-32px -32px 32px;font-size:12px;color:var(--mu)}}
.bar a{{color:var(--ac);text-decoration:none;font-size:12px}}
.bar a:hover{{text-decoration:underline}}
.bar strong{{color:var(--tx)}}
#content h1{{font-size:22px;font-weight:700;color:var(--ac);margin:24px 0 12px}}
#content h2{{font-size:16px;font-weight:700;color:var(--ac);margin:20px 0 8px;
  padding-bottom:4px;border-bottom:1px solid var(--bd)}}
#content h3{{font-size:14px;font-weight:600;color:var(--tx);margin:16px 0 6px}}
#content h4{{font-size:13px;font-weight:600;color:var(--mu);margin:12px 0 4px}}
#content p{{margin-bottom:10px}}
#content ul,#content ol{{padding-left:22px;margin-bottom:10px}}
#content li{{margin-bottom:4px}}
#content code{{color:var(--cy);font-family:"SF Mono","Fira Code",Consolas,monospace;font-size:12px}}
#content pre{{background:var(--cb);border:1px solid var(--bd);border-radius:4px;
  padding:12px 14px;overflow-x:auto;margin:10px 0}}
#content pre code{{color:var(--tx);font-size:12px}}
.tbl-wrap{{overflow-x:auto;margin:10px 0}}
.tbl-wrap table{{border-collapse:collapse;font-size:13px;white-space:nowrap}}
#content th{{background:var(--de);color:var(--mu);font-size:11px;font-weight:700;
  text-align:left;padding:7px 10px;border-bottom:2px solid var(--bd)}}
#content td{{padding:7px 10px;border-bottom:1px solid var(--bd);vertical-align:top}}
#content tr:hover td{{background:rgba(137,180,250,.04)}}
#content blockquote{{border-left:3px solid var(--ac);margin:10px 0;
  padding:4px 14px;color:var(--mu)}}
#content a{{color:var(--ac)}}
#content strong{{color:var(--tx);font-weight:600}}
#content hr{{border:none;border-top:1px solid var(--bd);margin:20px 0}}
</style></head>
<body>
<div class="bar">
  <a href="/manifest.html">← Manifest</a>
  <span class="altd-label" style="font-size:11px;color:var(--mu);opacity:.5">Alt+D</span>
  <strong>{title}</strong>
  <span class="doc-right" style="margin-left:auto;display:flex;align-items:center;gap:8px;flex-shrink:0">
    <span style="font-size:11px;color:var(--mu);opacity:.75">{ts_str}</span>
    <button onclick="location.reload()" style="background:none;border:1px solid var(--bd);color:var(--mu);font-size:11px;padding:2px 8px;border-radius:4px;cursor:pointer;font-family:inherit" onmouseenter="this.style.borderColor='var(--ac)';this.style.color='var(--ac)'" onmouseleave="this.style.borderColor='var(--bd)';this.style.color='var(--mu)'">↻ Refresh</button>
  </span>
</div>
<div id="content">{body_html}</div>
</body></html>"""
        data = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _get_consoles(self, query=None):
        # The archive is only built when the client is filtering: the 10-second
        # poll must stay small, but a search box that cannot reach an older log
        # would be lying about what it searches.
        want_all = bool((query or {}).get("all"))
        self._json(console_groups(include_archive=want_all))

    def _console_transcript(self, uuid, query):
        """The rendered conversation. `after` is the client's cursor, so a long
        session streams in rather than arriving as one multi-megabyte reply."""
        if not _RE_UUID.match(uuid):
            self.send_error(404, "Unknown session")
            return
        raw = (query.get("after") or ["0"])[0]
        try:
            after = max(0, int(raw))
        except (TypeError, ValueError):
            after = 0
        self._json(console_transcript(CONSOLE_ROOTS["projects"], uuid, after))

    def _console_history(self, uuid, query):
        """The merged stream. The cursor is the client's — the server holds no
        per-reader state, so two tabs on the same session cannot desynchronize
        each other."""
        if not _RE_UUID.match(uuid):
            self.send_error(404, "Unknown session")
            return
        raw = (query.get("cursor") or ["{}"])[0]
        try:
            cursor = json.loads(raw)
            if not isinstance(cursor, dict):
                raise ValueError("cursor must be an object")
            # Keys index the spool by name and values are byte offsets; both are
            # client-supplied, so both are validated before they reach a path.
            cursor = {k: int(v) for k, v in cursor.items()
                      if _RE_NAME.match(str(k)) and int(v) >= 0}
        except (ValueError, TypeError):
            cursor = {}
        self._json(console_history(CONSOLE_ROOTS["registry"], uuid, cursor))

    def _console_tail(self, sid, query):
        meta = console_resolve(sid)
        if meta is None:
            self.send_error(404, "Unknown console stream")
            return
        raw = (query.get("from") or [None])[0]
        try:
            from_byte = None if raw in (None, "") else int(raw)
        except ValueError:
            self.send_error(400, "Bad 'from' offset")
            return
        out = console_read(meta["path"], from_byte)
        out.update({
            "id": sid,
            "label": meta["label"],
            "stream_key": meta["stream_key"],
            "source": str(meta["path"]),
            "alive": _console_alive(meta),
            "rc": meta.get("rc"),
        })
        self._json(out)

    def _get_annotations(self, docname):
        path = ANNOTATIONS_DIR / f"{docname}.json"
        with _doc_lock:
            data = path.read_bytes() if path.exists() else b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _post_annotations(self, docname):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return
        path = ANNOTATIONS_DIR / f"{docname}.json"
        with _doc_lock:
            path.write_text(json.dumps(data, indent=2))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps({"saved": True}).encode())


if __name__ == "__main__":
    # If a port file exists and that port is answering AS THIS PROJECT, a
    # healthy instance is running — exit rather than start a duplicate. The
    # identity half matters: a stale port file can name a port another
    # project's server now holds, and a bare connect would read that as "mine
    # is up" and exit, leaving this project permanently unstartable until
    # someone deletes the file by hand.
    if PORT_FILE.exists():
        try:
            existing = int(PORT_FILE.read_text().strip())
            if instance_running(existing, ROOT.parent.name):
                print(f"AUR doc server already running on port {existing}.")
                sys.exit(0)
        except (ValueError, OSError):
            pass

    port = find_free_port(PREFERRED_PORT)
    PORT_FILE.write_text(str(port))

    def _release_port_file():
        """Remove the port file ONLY while it still names this instance.

        A departing server must not delete a live one's pointer. The window is
        real and was observed here: an instance signalled at T finishes its
        teardown at T+n, by which time a successor has already bound a new port
        and written the file — and the unconditional unlink erased it, leaving
        a healthy server that nothing could find. The next visitor then starts
        *another* server on another port, and they accumulate.
        """
        try:
            if PORT_FILE.read_text().strip() == str(port):
                PORT_FILE.unlink(missing_ok=True)
        except OSError:
            pass

    # Capture starts BEFORE the socket binds. A command running in the seconds
    # it takes to come up is still complete on disk, so the startup scan gets it
    # in full — but only if the scan happens before anything else can block.
    _SYSINFO_STREAM = SysinfoStream()
    if _SYSINFO_STREAM.start():
        print(f"[sysinfo] sampling via {SYSINFO_BIN} every "
              f"{SYSINFO_INTERVAL_MS}ms", flush=True)

    # Prune BEFORE capture starts, so a server that has been down for days does
    # not first re-list a week of expired sessions and then delete them under a
    # reader who has already started scrolling.
    prune_spool(CONSOLE_ROOTS["registry"])

    _SPOOL = ConsoleSpool(CONSOLE_ROOTS["tmp_root"], CONSOLE_ROOTS["registry"])
    if _SPOOL.start():
        print(f"[console-spool] capturing task output into "
              f"{CONSOLE_ROOTS['registry']}", flush=True)
    else:
        _SPOOL = None

    # ...and hourly thereafter, because this server runs for days at a stretch:
    # a startup-only prune means retention silently stops applying the moment
    # the process outlives the window it enforces.
    def _prune_loop():
        while True:
            time.sleep(3600)
            try:
                prune_spool(CONSOLE_ROOTS["registry"])
            except Exception as exc:                       # never kills the thread
                print(f"[console-spool] prune failed: {exc}",
                      file=sys.stderr, flush=True)

    threading.Thread(target=_prune_loop, name="spool-prune", daemon=True).start()

    def _shutdown(signum=None, frame=None):
        _SYSINFO_STREAM.stop()
        if _SPOOL is not None:
            _SPOOL.stop()
        _release_port_file()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)

    os.chdir(ROOT)
    class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        allow_reuse_address = True
        daemon_threads = True

    # Sanity-check and heal the shared root on every start. Cheap, and the
    # failures it prevents are silent: a dangling stable link, a VERSION that
    # disagrees with what is actually installed, a registry row for a project
    # that has been deleted. Every repair is reported rather than performed
    # quietly — a repair that leaves no trace is indistinguishable from a system
    # that was never broken.
    if aurshare is not None:
        try:
            healed = aurshare.verify_and_heal(
                log=lambda m: print(f"[shared-root]{m}", flush=True))
            if healed:
                print(f"[shared-root] healed {len(healed)} condition(s)", flush=True)
        except Exception as exc:                       # never block startup
            print(f"[shared-root] check failed: {exc}", flush=True)

    server = ThreadingServer(("localhost", port), Handler)
    print(f"AUR doc server: http://localhost:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # Every exit path, not just the signalled one: a sampler outliving the
        # server it feeds is an orphan nobody will reap (AUR P8).
        _SYSINFO_STREAM.stop()
        if _SPOOL is not None:
            _SPOOL.stop()
        _release_port_file()
