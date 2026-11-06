#!/usr/bin/env python3
"""unforced.py -- the one deletion routine: delete the way `rm -r` without `-f` does, never more.

A path that is write-protected is PRESERVED: a file without the owner write bit, a directory without it
(whole and undescended), any target whose parent directory lacks it, and every directory that therefore
still holds something, are kept and reported (`kept: write-protected (preserved)`). No `-f`, no chmod, no
ignore-errors. Every other OSError is reported with its path and cause, never swallowed or retried with force.

Every call that is not a dry run appends to one persistent receipt log: a BEFORE line (when, pid, argv, the
target, the planned files and bytes from a dry walk of the same code) written before anything is removed, and an
AFTER line (deleted files and bytes, kept and vetoed counts with first paths, errors, whether the target is
gone). A deletion whose BEFORE line cannot be written does not proceed: OSError naming the log and the cause.
The log is `log=` if given, else `$AUR_RECEIPT_LOG`, else `$XDG_DATA_HOME/aur/cleanup-receipts.log`.

Also here: `held_open_by` / `open_holders`, which processes hold a path open (read from /proc/*/fd);
`processes_under`, which processes hold, run in or run from anything at or under a directory; and `newest_touch`,
the age floor's clock. Import it; it has no command line. Generic: a project wraps it to choose its own log.
"""

from __future__ import annotations

import json
import os
import stat as stat_mod
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

# The one reason text a protected entry carries; callers match on its prefix.
PROTECTED = "kept: write-protected (preserved)"


def allocated_bytes(st: os.stat_result) -> int:
    """Bytes the file occupies on disk (blocks), else its apparent size."""
    blocks = getattr(st, "st_blocks", None)
    return blocks * 512 if blocks is not None else st.st_size

def open_holders(paths: Iterable[os.PathLike]) -> Dict[str, List[int]]:
    """realpath -> sorted pids for each of PATHS some live process has open
    (/proc/*/fd). A process whose fds cannot be read (another user's, or one
    that exited mid-scan) is skipped: it cannot be this user's build. An
    unreadable /proc itself raises OSError — "nobody holds it" is a claim that
    needs the scan to have happened, so a caller must not read a failed scan
    as an empty one."""
    want = {os.path.realpath(p) for p in paths}
    found: Dict[str, Set[int]] = {}
    if not want:
        return {}
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            for fd in os.scandir(f"/proc/{entry.name}/fd"):
                try:
                    tgt = os.readlink(fd.path)
                except OSError:
                    continue
                if tgt in want:
                    found.setdefault(tgt, set()).add(int(entry.name))
        except OSError:
            continue
    return {k: sorted(v) for k, v in found.items()}


def held_open_by(path: os.PathLike) -> List[int]:
    """Pids holding PATH open (sorted); empty when no process does. Raises
    OSError when /proc cannot be scanned (see open_holders)."""
    return open_holders([path]).get(os.path.realpath(path), [])


def processes_under(path: os.PathLike) -> List[int]:
    """Pids with an open fd, a working directory or an executable at or under PATH (sorted), from /proc alone:
    no walk of PATH, so its size costs nothing. A process that cannot be read (another user's, or one that
    exited mid-scan) is skipped, as in open_holders; an unreadable /proc raises OSError, because "nobody" is a
    claim that needs the scan to have happened."""
    target = os.path.realpath(path)
    under = target.rstrip("/") + "/"
    found: Set[int] = set()
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        links = [f"/proc/{entry.name}/cwd", f"/proc/{entry.name}/exe"]
        try:
            links += [fd.path for fd in os.scandir(f"/proc/{entry.name}/fd")]
        except OSError:
            pass
        for link in links:
            try:
                tgt = os.readlink(link)
            except OSError:
                continue
            if tgt == target or tgt.startswith(under):
                found.add(int(entry.name))
                break
    return sorted(found)


def newest_touch(path: os.PathLike) -> float:
    """Newest of mtime and ctime over PATH and everything under it (lstat,
    symlinks never followed). An entry that vanishes mid-walk is skipped — it
    was being touched, which only makes the answer newer, never older."""
    newest = 0.0
    stack = [os.fspath(path)]
    while stack:
        cur = stack.pop()
        try:
            st = os.lstat(cur)
        except OSError:
            continue
        newest = max(newest, st.st_mtime, st.st_ctime)
        if stat_mod.S_ISDIR(st.st_mode):
            try:
                with os.scandir(cur) as it:
                    stack.extend(e.path for e in it)
            except OSError:
                continue
    return newest

@dataclass
class Receipt:
    """What one remove_unforced did (or, in a dry run, would do). Paths are
    relative to the target; the target itself is "."."""
    deleted_files: int = 0
    deleted_bytes: int = 0
    kept: List[Tuple[str, str]] = field(default_factory=list)          # (path, "kept: ...")
    vetoed: List[Tuple[str, str, int]] = field(default_factory=list)   # (path, reason, bytes)
    errors: List[str] = field(default_factory=list)
    gone: bool = False  # the target itself is gone (or, in a dry run, would be)

def receipt_log_path() -> str:
    """The default receipt log, resolved at call time: $AUR_RECEIPT_LOG, else the XDG data directory."""
    explicit = os.environ.get("AUR_RECEIPT_LOG")
    if explicit:
        return explicit
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "aur", "cleanup-receipts.log")


def _receipt_append(log: str, line: str) -> None:
    """Append one record line to LOG (created with its directory on first use).
    OSError propagates: the caller decides whether a missing record is fatal."""
    os.makedirs(os.path.dirname(log), exist_ok=True)
    fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (line + "\n").encode("utf-8", "surrogateescape"))
    finally:
        os.close(fd)


def _q(text: str) -> str:
    """One greppable, single-line, quoted token."""
    return json.dumps(text, ensure_ascii=False)


def _sample(items: List[Tuple[str, str]], limit: int = 3) -> str:
    shown = ", ".join(f"{_q(p)} ({why})" for p, why in items[:limit])
    return shown + (f", ... and {len(items) - limit} more" if len(items) > limit else "")


def remove_unforced(path: os.PathLike, dry_run: bool = False,
                    veto: Optional[Callable[[str, os.stat_result], Optional[str]]] = None,
                    skip: Optional[Set[str]] = None, log: Optional[str] = None) -> Receipt:
    """Delete PATH the way `rm -r` without `-f` does, never more (module doc),
    and record it: a BEFORE and an AFTER line in the receipt log (a dry run
    writes none). Raises OSError, removing nothing, if the BEFORE line cannot
    be written. See `_walk` for the deletion rules."""
    if dry_run:
        return _walk(path, True, veto, skip)
    target = os.fspath(path)
    log = log or receipt_log_path()
    plan = _walk(path, True, veto, skip)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    try:
        _receipt_append(
            log,
            f"{stamp} BEFORE pid={os.getpid()} argv={json.dumps(sys.argv, ensure_ascii=False)} "
            f"target={_q(os.path.abspath(target))} planned_files={plan.deleted_files} "
            f"planned_bytes={plan.deleted_bytes} planned_kept={len(plan.kept)} "
            f"planned_vetoed={len(plan.vetoed)}")
    except OSError as e:
        raise OSError(e.errno, f"refusing to delete {target}: the receipt log {log} cannot be "
                               f"written: {e.strerror or e}") from e
    r = _walk(path, False, veto, skip)
    try:
        _receipt_append(
            log,
            f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} AFTER pid={os.getpid()} "
            f"target={_q(os.path.abspath(target))} deleted_files={r.deleted_files} "
            f"deleted_bytes={r.deleted_bytes} kept={len(r.kept)} [{_sample(r.kept)}] "
            f"vetoed={len(r.vetoed)} [{_sample([(p, why) for p, why, _b in r.vetoed])}] "
            f"errors={len(r.errors)} [{', '.join(_q(x) for x in r.errors[:3])}"
            f"{', ... and %d more' % (len(r.errors) - 3) if len(r.errors) > 3 else ''}] "
            f"gone={'yes' if r.gone else 'no'}")
    except OSError as e:
        msg = f"{target}: deleted, but the AFTER receipt could not be written to {log}: {e}"
        r.errors.append(msg)
        print(f"unforced: {msg}", file=sys.stderr)
    return r


def _walk(path: os.PathLike, dry_run: bool,
          veto: Optional[Callable[[str, os.stat_result], Optional[str]]],
          skip: Optional[Set[str]]) -> Receipt:
    """Delete PATH the way `rm -r` without `-f` does, never more (module doc).

    Symlinks are removed as links, never followed. A dry run walks identically
    and removes nothing, so its numbers are the real pass's. VETO(path, lstat)
    is a caller's content rule for non-symlink files: a reason string keeps the
    file (reported in `vetoed`), None allows it. SKIP names subtrees something
    else removes; they are left untouched and do not hold their parents."""
    skip = skip or set()
    target = os.fspath(path)
    r = Receipt()
    seen_inodes: Set[Tuple[int, int]] = set()

    def rel(p: str) -> str:
        return os.path.relpath(p, target)

    def unwritable(st: os.stat_result) -> bool:
        return not (st.st_mode & stat_mod.S_IWUSR)

    def remove_file(p: str, st: os.stat_result) -> bool:
        if stat_mod.S_ISLNK(st.st_mode):
            size = 0
        else:
            if veto is not None:
                why = veto(p, st)
                if why:
                    r.vetoed.append((rel(p), why, allocated_bytes(st)))
                    return False
            if unwritable(st):
                r.kept.append((rel(p), PROTECTED))
                return False
            key = (st.st_dev, st.st_ino)
            size = 0 if key in seen_inodes else allocated_bytes(st)
            seen_inodes.add(key)
        if not dry_run:
            try:
                os.unlink(p)
            except OSError as e:
                r.errors.append(f"{p}: unlink failed: {e}")
                return False
        r.deleted_bytes += size
        r.deleted_files += 1
        return True

    def remove_dir(d: str, st: os.stat_result) -> Tuple[bool, int]:
        """(D is or would be gone, protected entries still under D)."""
        if unwritable(st):
            r.kept.append((rel(d), PROTECTED))
            return False, 1
        try:
            entries = list(os.scandir(d))
        except OSError as e:
            r.errors.append(f"{d}: scandir failed: {e}")
            return False, 0
        remaining = False
        protected_below = 0
        for e in entries:
            if e.path in skip:
                continue
            try:
                est = e.stat(follow_symlinks=False)
            except OSError as err:
                r.errors.append(f"{e.path}: lstat failed: {err}")
                remaining = True
                continue
            if stat_mod.S_ISDIR(est.st_mode):
                gone, n = remove_dir(e.path, est)
                protected_below += n
                remaining = remaining or not gone
            else:
                before = len(r.kept)
                if not remove_file(e.path, est):
                    remaining = True
                protected_below += len(r.kept) - before
        if remaining:
            if protected_below:
                r.kept.append((rel(d), f"{PROTECTED} — holds {protected_below} protected "
                                       f"entr{'y' if protected_below == 1 else 'ies'}"))
            return False, protected_below
        if not dry_run:
            try:
                os.rmdir(d)
            except OSError as err:
                r.errors.append(f"{d}: rmdir failed: {err}")
                return False, 0
        return True, 0

    try:
        st = os.lstat(target)
    except OSError as e:
        r.errors.append(f"{target}: lstat failed: {e}")
        return r
    parent = os.path.dirname(os.path.abspath(target))
    try:
        pst = os.lstat(parent)
    except OSError as e:
        r.errors.append(f"{parent}: lstat of the parent of {target} failed: {e}")
        return r
    if unwritable(pst):
        r.kept.append((rel(target), PROTECTED))
        return r
    if stat_mod.S_ISDIR(st.st_mode):
        r.gone, _ = remove_dir(target, st)
    else:
        r.gone = remove_file(target, st)
    return r
