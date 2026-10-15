#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
test-rustfmt-on-edit -- executable cases for the PostToolUse hook that formats a
`.rs` file the moment an agent writes it (so no unformatted code reaches a build).

HERMETIC. Every fixture lives in a temp dir this test removes.

    python3 -I hooks/test-rustfmt-on-edit.py [-v|--verbose]

Exit 0 all pass, 1 any failure, 64 bad argument.
"""

import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "rustfmt-on-edit.py"
LATENCY_BUDGET_MS = 300.0
UGLY = "fn   main( ){let x=1;\n println!(\"{}\",x);}\n"
USAGE = ("usage: test-rustfmt-on-edit.py [-v|--verbose]\n"
         "  -v, --verbose   list every case, not only failures\n"
         "  -h, --help      this text\n")


class Tally:
    def __init__(self, verbose):
        self.verbose, self.total, self.fails = verbose, 0, 0

    def check(self, name, ok, detail=""):
        self.total += 1
        if not ok:
            self.fails += 1
        if not ok or self.verbose:
            print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else ""))


def run(path, tool="Edit", raw=None):
    payload = raw if raw is not None else json.dumps({"tool_name": tool, "tool_input": {"file_path": str(path)}})
    t0 = time.perf_counter()
    p = subprocess.run([sys.executable, "-I", str(HOOK)], input=payload, capture_output=True, text=True)
    return p, (time.perf_counter() - t0) * 1000


def note(p):
    """The additionalContext string of a hook reply, or None when silent."""
    if not p.stdout.strip():
        return None
    return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]


def mk(root, name, text, manifest=None):
    f = root / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    if manifest is not None:
        (f.parent / "Cargo.toml").write_text(manifest)
    return f


PKG = '[package]\nname = "x"\nversion = "0.1.0"\nedition = "2021"\n'


def main():
    verbose = False
    for a in sys.argv[1:]:
        if a in ("-v", "--verbose"):
            verbose = True
        elif a in ("-h", "--help"):
            print(USAGE, end="")
            return 0
        else:
            sys.stderr.write(f"unknown option `{a}`\n{USAGE}")
            return 64
    r = Tally(verbose)
    with tempfile.TemporaryDirectory(prefix="rsfmt-") as tmp:
        root = Path(tmp)

        f = mk(root, "a/src/lib.rs", UGLY, PKG)
        p, _ = run(f)
        n = note(p)
        r.check("unformatted: exit 0", p.returncode == 0, p.stderr)
        r.check("unformatted: file formatted", f.read_text() != UGLY and "fn main() {" in f.read_text())
        r.check("unformatted: note names path, edition, re-read",
                bool(n) and n.startswith("rustfmt-on-edit: reformatted ") and str(f) in n
                and "edition 2021" in n and "ms)" in n and "re-read before the next edit" in n, str(n))

        before = f.stat().st_mtime_ns
        time.sleep(0.02)
        p, _ = run(f, tool="Write")
        r.check("formatted: silent", p.returncode == 0 and p.stdout == "", p.stdout)
        r.check("formatted: mtime unchanged", f.stat().st_mtime_ns == before)

        t = mk(root, "a/notes.txt", UGLY)
        p, _ = run(t)
        r.check("non-.rs: silent, untouched", p.returncode == 0 and p.stdout == "" and t.read_text() == UGLY)
        p, _ = run(root / "a/src/missing.rs")
        r.check("missing .rs: silent", p.returncode == 0 and p.stdout == "", p.stdout)

        bad = "fn main( {\n  let x = ;\n"
        b = mk(root, "b/src/lib.rs", bad, PKG)
        bm = b.stat().st_mtime_ns
        p, _ = run(b, tool="MultiEdit")
        n = note(p)
        r.check("syntax error: exit 0, bytes identical, mtime same",
                p.returncode == 0 and b.read_text() == bad and b.stat().st_mtime_ns == bm, p.stderr)
        r.check("syntax error: note says did not parse", bool(n) and "did not parse: " in n, str(n))

        ws = root / "ws"
        ws.mkdir()
        (ws / "Cargo.toml").write_text('[workspace]\nmembers=["m"]\n[workspace.package]\nedition = "2018"\n')
        w = mk(ws, "m/src/lib.rs", UGLY, '[package]\nname="m"\nversion="0.1.0"\nedition.workspace = true\n')
        p, _ = run(w)
        n = note(p)
        r.check("workspace edition: resolved to 2018", bool(n) and "edition 2018" in n, str(n))
        r.check("workspace edition: file formatted", "fn main() {" in w.read_text())

        par = mk(root, "c/src/lib.rs", "mod child;\nfn   p( ){}\n", PKG)
        ch = mk(root, "c/src/child.rs", UGLY)
        cm = ch.stat().st_mtime_ns
        time.sleep(0.02)
        p, _ = run(par)
        r.check("parent edit formats parent", "fn p() {}" in par.read_text(), p.stderr)
        r.check("child module bytes and mtime untouched", ch.read_text() == UGLY and ch.stat().st_mtime_ns == cm)

        os.chmod(par, 0o640)
        par.write_text("fn   q( ){}\n")
        run(par)
        r.check("file mode preserved", (par.stat().st_mode & 0o777) == 0o640)

        ne = mk(root, "ne/src/lib.rs", UGLY, '[package]\nname = "x"\nversion = "0.1.0"\n')
        p, _ = run(ne)
        n = note(p)
        r.check("package without edition: 2015", bool(n) and "edition 2015" in n, str(n))

        bm_ = mk(root, "bm/src/lib.rs", UGLY, "[package\nname =")
        p, _ = run(bm_)
        n = note(p)
        r.check("unparsable manifest: note + skip", bm_.read_text() == UGLY and bool(n) and "unparsable" in n, str(n))

        wsf = root / "ws2"
        mk(wsf, "Cargo.toml", '[workspace]\n[workspace.package]\nedition = "2024"\n')
        mid = mk(wsf, "crates/m/src/lib.rs", UGLY,
                 '[package]\nname="m"\nedition = { workspace = true }\n')
        p, _ = run(mid)
        n = note(p)
        r.check("edition = { workspace = true } resolves", bool(n) and "edition 2024" in n, str(n))

        nm = mk(root, "nom/x.rs", UGLY)
        p, _ = run(nm)
        n = note(p)
        r.check("no manifest: no write", nm.read_text() == UGLY and p.returncode == 0)
        r.check("no manifest: note says skipped", bool(n) and "no Cargo.toml" in n, str(n))

        for label, raw in (("garbage stdin", "not json"), ("empty stdin", ""),
                           ("no file_path", '{"tool_input":{}}')):
            p, _ = run(None, raw=raw)
            r.check(f"{label}: exit 0, silent", p.returncode == 0 and p.stdout == "", p.stderr)

        p = subprocess.run([sys.executable, "-I", str(HOOK), "--bogus"], capture_output=True, text=True)
        r.check("unknown argument: non-zero + usage", p.returncode == 64 and "usage" in p.stderr, p.stderr)

        body = "".join(f"fn f{i}(a:i32,b:i32)->i32{{let c=a+b;c*{i}}}\n" for i in range(1000))
        times = []
        for i in range(5):
            big = mk(root, f"big{i}/src/lib.rs", body, PKG)
            p, ms = run(big)
            times.append(ms)
        big_ok = "fn f0(a: i32, b: i32) -> i32 {" in big.read_text()
        med = statistics.median(times)
        r.check(f"1000-line file formatted", big_ok)
        r.check(f"latency median {med:.0f} ms < {LATENCY_BUDGET_MS:.0f} ms", med < LATENCY_BUDGET_MS,
                f"{times}")
        print(f"  timings ms (1000-line, process start included): {[round(x) for x in times]}")

    print(f"\ntest-rustfmt-on-edit: {r.total - r.fails}/{r.total} passed")
    return 1 if r.fails else 0


if __name__ == "__main__":
    sys.exit(main())
