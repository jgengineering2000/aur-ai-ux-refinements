#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
test-commit-guard - decision table for the personal-tree rule of commit-guard.py.

Drives scan(), the function the guard itself runs, on one added line at a time.

    python3 tools/test-commit-guard.py [-v|--verbose] [--module PATH]

--module PATH tests another copy of the guard (default: tools/commit-guard.py).
Exit 0 all pass, 1 any failure, 64 bad argument.
"""

import importlib.util
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
USAGE = ("usage: test-commit-guard.py [-v|--verbose] [--module PATH]\n"
         "  -v, --verbose   list every case, not only failures\n"
         "  --module PATH   guard file to test (default tools/commit-guard.py)\n"
         "  -h, --help      this text\n")

# (line, expected number of findings, why). A flagged case writes its folder
# as {Documents}, filled in at run time from the guard's PERSONAL_DIRS, so this
# file's own text names no personal folder and passes the guard it tests.
CASES = [
    ("| AIFF | https://www.mmsp.ece.mcgill.ca/Documents/AudioFormats/AIFF/Docs/AIFF-1.3.pdf |",
     0, "remote URL path holding Documents/ names nothing local"),
    ("https://example.org/Pictures/a.png", 0, "remote URL, Pictures/"),
    ("see (https://example.org/Downloads/x.zip) now", 0, "remote URL closed by )"),
    ("`https://example.org/Music/y.mp3`", 0, "remote URL closed by backtick"),
    ("file:///{Documents}/x", 1, "file:// URL stays flagged"),
    ("http://localhost/{Documents}/x", 1, "localhost stays flagged"),
    ("http://localhost:8080/{Documents}/x", 1, "localhost with port stays flagged"),
    ("http://127.0.0.1/{Pictures}/x", 1, "127.x.x.x stays flagged"),
    ("http://127.1.2.3:80/{Pictures}/x", 1, "127.x.x.x with port stays flagged"),
    ("http://[::1]/{Desktop}/x", 1, "[::1] stays flagged"),
    ("~/{Documents}/x", 1, "bare ~ path stays flagged"),
    ("/{Documents}/x", 1, "bare absolute path stays flagged"),
    ("see ~/{Documents}/x and https://example.org/Documents/y", 1,
     "personal path elsewhere on the line still one finding"),
    ("https://example.org/Documents/y and /{Pictures}/z", 1,
     "personal path after a remote URL still flagged"),
]


def load(path):
    spec = importlib.util.spec_from_file_location("commit_guard_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    verbose, module = False, HERE / "commit-guard.py"
    args = sys.argv[1:]
    while args:
        a = args.pop(0)
        if a in ("-h", "--help"):
            sys.stdout.write(USAGE)
            return 0
        if a in ("-v", "--verbose"):
            verbose = True
        elif a == "--module" and args:
            module = Path(args.pop(0))
        else:
            sys.stderr.write(f"unknown option `{a}`\n{USAGE}")
            return 64
    t0 = time.perf_counter()
    guard = load(module)
    repo = str(HERE.parent)
    fails = 0
    folders = {d: d for d in guard.PERSONAL_DIRS}
    for template, want, why in CASES:
        line = template.format_map(folders)
        found = guard.scan(repo, {"f.md": [(1, line)]}, [], print)
        ok = len(found) == want
        if not ok:
            fails += 1
        if not ok or verbose:
            print(f"{'ok  ' if ok else 'FAIL'} {why}: want {want}, got {len(found)}: {line}")
    print(f"\ntest-commit-guard: {len(CASES) - fails}/{len(CASES)} passed in "
          f"{time.perf_counter() - t0:.2f}s")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
