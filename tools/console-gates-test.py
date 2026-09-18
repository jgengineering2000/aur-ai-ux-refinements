#!/usr/bin/env python3
"""Unit coverage for `_read_gate_entries` (server/server.py) — the Python half
of the rule the writer's own Rust reader (`read_gate_entries`) states, so
/procstats' `gates` array and `cargo test -p launcher` can never disagree
about which blocked-gate entries are live.

Three cases: a live entry (its pid is THIS test process, which is always
alive) is kept and its fields carried through; a dead-pid entry is pruned —
reported empty AND the file deleted; malformed JSON is skipped with a logged
line and the file left in place for a human to inspect, never silently
dropped.

Usage:
    tools/console-gates-test.py [--verbose]
"""

import argparse
import importlib.util
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER_PY = HERE.parent / "server" / "server.py"

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


def main():
    global _verbose
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    _verbose = args.verbose

    srv = _load_server()

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)

        # -- a live entry: this process's own pid is always alive --
        live_pid = os.getpid()
        live = {
            "gate": "rustc-gate", "pid": live_pid, "verb": "link",
            "target": "k7_conditioning", "since": 1700000000, "waited_s": 34,
            "blocked_by": "disk 3.6 GB < reserve 36.9 GB",
            "demand": "4C 3500M-ram 0M-disk", "criteria": "headroom",
        }
        (d / f"{live_pid}.json").write_text(json.dumps(live))

        # -- a dead-pid entry: guaranteed not to be alive on this machine --
        dead_pid = 4_000_000
        dead = {
            "gate": "res-gate", "pid": dead_pid, "verb": "test",
            "target": "census_parity", "since": 1700000010, "waited_s": 5,
            "blocked_by": "mem 300 MB < reserve 512 MB",
            "demand": "2C 1000M-ram 50M-disk", "criteria": "headroom",
        }
        dead_path = d / f"{dead_pid}.json"
        dead_path.write_text(json.dumps(dead))

        # -- malformed JSON --
        malformed_path = d / "999999.json"
        malformed_path.write_text("{not valid json")

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            entries = srv._read_gate_entries(str(d))
        logged = stderr.getvalue()

        by_pid = {e["pid"]: e for e in entries}
        check(live_pid in by_pid, f"live entry (pid {live_pid}) is kept")
        if live_pid in by_pid:
            e = by_pid[live_pid]
            check(e["gate"] == "rustc-gate" and e["verb"] == "link"
                  and e["target"] == "k7_conditioning" and e["waited_s"] == 34
                  and e["blocked_by"] == "disk 3.6 GB < reserve 36.9 GB",
                  f"live entry fields carried through verbatim: {e}")

        check(dead_pid not in by_pid, "dead-pid entry is not reported live")
        check(not dead_path.exists(),
              "dead-pid entry's file is pruned (deleted) by the reader")

        check(malformed_path.exists(),
              "malformed entry's file is left in place, never deleted")
        check("999999.json" in logged and "malformed" in logged.lower(),
              f"malformed entry is skipped with a logged line (got: {logged!r})")

        # -- a missing directory entirely is [], never an error --
        missing = d / "does-not-exist"
        check(srv._read_gate_entries(str(missing)) == [],
              "a missing gate-state directory reads as []")

    if _failures:
        print(f"\n{len(_failures)} FAILURE(S):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("\nall assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
