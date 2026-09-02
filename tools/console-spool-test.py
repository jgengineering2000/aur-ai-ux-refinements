#!/usr/bin/env python3
"""Functional gate for the console spool's capture contract.

Simulates the AI harness against a REAL ConsoleSpool instance: create a
session's tasks directory, write a task output file one line at a time the way
a running command does, unlink the name mid-write while the writer keeps its
descriptor (exactly what the harness does at command exit), keep writing,
close.

Two contracts, and both are load-bearing:

  1. The source file's link count NEVER exceeds 1 while the file exists. The
     Claude Code harness (>= 2.1.252) refuses to consume any task output whose
     link count is not 1 and can SIGKILL the running command — so a capture
     mechanism that raises nlink is a capture mechanism that kills commands.
     This is the assertion the old hardlink spool fails.

  2. The spool file carries EVERY byte the writer produced, including bytes
     written after the unlink — retention is the point of the spool.

Plus the liveness ripple: while the writer runs the stream reads live, after
the unlink is processed it reads ended, and the prune guard respects a live
tail while refusing to be fooled by a dead one.

Usage:
    tools/console-spool-test.py [--verbose]
"""

import argparse
import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER_PY = HERE.parent / "server" / "server.py"

UUID = "12345678-1234-1234-1234-123456789abc"
TASK = "cmdtest01"

_failures = []
_verbose = False


def check(cond, what):
    tag = "ok  " if cond else "FAIL"
    if not cond:
        _failures.append(what)
    if _verbose or not cond:
        print(f"  {tag}  {what}")


def _load_server():
    spec = importlib.util.spec_from_file_location("aur_doc_server", SERVER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _wait(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verbose", action="store_true",
                    help="print every assertion, not just failures")
    ap.add_argument("--poll", action="store_true",
                    help="force the polling fallback (as when inotify "
                         "instances are exhausted) and gate that path too")
    args = ap.parse_args()
    global _verbose
    _verbose = args.verbose

    srv = _load_server()
    if args.poll:
        class _NoInotify:
            def __init__(self):
                raise OSError(24, "poll mode forced by --poll")
        srv._Inotify = _NoInotify

    with tempfile.TemporaryDirectory(prefix="spool-test-") as td:
        tmp_root = Path(td) / "tasksroot"
        registry = Path(td) / "registry"
        spool = srv.ConsoleSpool(tmp_root, registry)
        check(spool.start(), "spool starts")
        print(f"  mode: {spool.mode}")
        srv._SPOOL = spool          # the liveness sites read the running watcher
        try:
            tasks = tmp_root / UUID / "tasks"
            tasks.mkdir(parents=True)
            check(spool.settle(), "settled after session+tasks creation")

            src = tasks / f"{TASK}.output"
            wrote = b""
            wfd = os.open(src, os.O_WRONLY | os.O_CREAT, 0o644)
            try:
                # -- growing file, harness-style: nlink must never leave 1 --
                for i in range(1, 6):
                    line = f"line {i}\n".encode()
                    os.write(wfd, line)
                    wrote += line
                    check(spool.settle(), f"settled after line {i}")
                    st = os.stat(src)
                    check(st.st_nlink == 1,
                          f"source nlink == 1 after line {i} "
                          f"(got {st.st_nlink}) — the harness contract")
                    time.sleep(0.06)   # let poll mode see at least one pass

                dst = registry / UUID / "spool" / f"{TASK}.log"
                check(_wait(lambda: dst.exists() and
                            dst.stat().st_size >= len(wrote)),
                      "spool file exists and carries the pre-unlink bytes")

                live = getattr(spool, "is_live", None)
                if live is None:
                    check(False, "spool exposes is_live() — the liveness signal")
                else:
                    check(live(UUID, TASK), "stream live while the writer runs")
                    spooled = srv._spooled(registry, UUID)
                    check(spooled and spooled[0]["live"] is True,
                          "_spooled reports live while the writer runs")
                    # prune must refuse a session with a live tail, however old
                    pruned = srv.prune_spool(registry,
                                             now=time.time() + 10 * 86400)
                    check(pruned["dirs"] == 0 and dst.exists(),
                          "prune refuses the session while its tail is live")

                # -- the harness's exit sequence: unlink, then final writes --
                os.unlink(src)
                for i in (6, 7):
                    line = f"line {i} AFTER UNLINK\n".encode()
                    os.write(wfd, line)
                    wrote += line
            finally:
                os.close(wfd)

            if live is not None:
                check(_wait(lambda: not live(UUID, TASK)),
                      "stream reads ended after the source name is deleted")
            check(_wait(lambda: dst.exists() and dst.read_bytes() == wrote),
                  f"spool carries every byte incl. post-unlink writes "
                  f"({len(wrote)} bytes)")
            st = dst.stat()
            check(st.st_nlink == 1, "spool file itself is a plain nlink-1 file")

            spooled = srv._spooled(registry, UUID)
            check(bool(spooled) and spooled[0]["live"] is False,
                  "_spooled reports ended after the writer is gone")

            # dead tail: prune may now collect the aged session
            pruned = srv.prune_spool(registry, now=time.time() + 10 * 86400)
            check(pruned["dirs"] == 1 and not dst.exists(),
                  "prune collects the session once nothing holds it")
        finally:
            srv._SPOOL = None
            spool.stop()

    if _failures:
        print(f"\n{len(_failures)} FAILURE(S):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("\nall assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
