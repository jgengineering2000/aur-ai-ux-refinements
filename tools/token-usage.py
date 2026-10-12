#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
token-usage — where a project's Claude Code tokens go.

Every call the model makes re-reads its whole context, so what a session costs
is the sum of its context sizes over its calls, not the length of what anyone
typed. That sum is the quantity this reports: per model, per main session versus
delegate, per day, per transcript, with the distribution of how big a context
starts and how big it peaks. A change meant to make sessions cheaper is judged
by running this over a window before and after.

Definitions, fixed so two runs are comparable:

  context of a call   input + cache_creation + cache_read tokens
  tokens re-read      the sum of the context over a transcript's calls
  starting size       the context of a transcript's first call; counted only
                      for transcripts whose first call falls inside the window,
                      because a session resumed from before it did not start
                      there
  peak                the largest context in the window
  call                one API request; streamed chunks repeat a request's usage
                      record and are collapsed on (message id, request id)
  owner message       a genuine user prompt in a main transcript, as read by
                      transcript.py (tool results, harness injections and IDE
                      notices are not requests)

Window bounds are UTC, as the transcripts record them. A date-only --until
covers that whole day. A transcript file last written before --since cannot
hold a record inside the window and is not opened.

Usage:
    token-usage.py [--project DIR] [--since DATE] [--until DATE]
                   [--compare A..B C..D] [--top N] [--json]

    --project DIR        project whose transcripts to read (default: cwd); the
                         transcript directory is derived from it the way
                         Claude Code names it under ~/.claude/projects
    --since DATE         first moment of the window, ISO date or date-time (UTC)
    --until DATE         last moment of the window (inclusive)
    --compare A..B C..D  two windows side by side with the change; each is
                         SINCE..UNTIL; not combined with --since/--until
    --top N              how many costliest transcripts to list (default 10)
    --json               the full result as JSON instead of the tables
    -h, --help           this text
"""

import argparse
import collections
import datetime
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from transcript import is_confirmation, owner_text  # noqa: E402

PROGRESS_EVERY_S = 10.0
PROMPT_CHARS = 100
SYNTHETIC_MODEL = "<synthetic>"
PCTS = (10, 50, 90)


# ---------------------------------------------------------------- windows

def parse_bound(text, end):
    """An ISO date or date-time as a UTC string comparable with a record's
    timestamp. A date-only bound opens at its first moment, or closes at its
    last when `end` is set. Raises ValueError naming the offending text."""
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"`{text}` is not an ISO date or date-time") from None
    date_only = len(text) <= 10
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    if date_only and end:
        return dt.strftime("%Y-%m-%dT23:59:59.999Z")
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class Window:
    def __init__(self, since, until):
        self.since_text, self.until_text = since, until
        self.lo = parse_bound(since, False) if since else None
        self.hi = parse_bound(until, True) if until else None
        if self.lo and self.hi and self.lo > self.hi:
            raise ValueError(f"window `{since}..{until}` ends before it begins")

    def contains(self, ts):
        if not ts:
            return False
        return (self.lo is None or ts >= self.lo) and \
               (self.hi is None or ts <= self.hi)

    def label(self):
        return f"{self.since_text or 'start'}..{self.until_text or 'now'}"


def epoch_of(bound):
    """Seconds since the epoch of a bound string from parse_bound (0 for an
    open start)."""
    if bound is None:
        return 0.0
    return datetime.datetime.strptime(
        bound[:19], "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=datetime.timezone.utc).timestamp()


def union_bounds(windows):
    """The widest span covering every window: a scan reads each file once."""
    los = [w.lo for w in windows]
    his = [w.hi for w in windows]
    lo = None if any(x is None for x in los) else min(los)
    hi = None if any(x is None for x in his) else max(his)
    return lo, hi


# ---------------------------------------------------------------- locating

def candidate_names(project):
    """The names Claude Code might have given the project's transcript
    directory, most likely first: every non-alphanumeric becomes `-`; failing
    that, only `/` and `.` do. Both are tried for the path as given and for
    its symlink-resolved form."""
    paths = []
    for p in (os.path.abspath(project), os.path.realpath(project)):
        if p not in paths:
            paths.append(p)
    names = []
    for p in paths:
        for n in (re.sub(r"[^A-Za-z0-9]", "-", p), re.sub(r"[/.]", "-", p)):
            if n not in names:
                names.append(n)
    return names


def transcript_root(project):
    base = Path.home() / ".claude" / "projects"
    names = candidate_names(project)
    for n in names:
        if (base / n).is_dir():
            return base / n
    raise FileNotFoundError(
        f"no transcript directory for project `{os.path.basename(os.path.abspath(project))}` "
        f"under ~/.claude/projects; tried {len(names)} name(s): " + ", ".join(names))


def discover(root):
    """Main transcripts `<root>/*.jsonl` and delegates
    `<root>/<session>/subagents/*.jsonl`, as (path, kind, session), in a
    stable order so the first of a duplicated record is always the same."""
    found = []
    for p in sorted(root.glob("*.jsonl")):
        found.append((p, "main", p.stem))
    for p in sorted(root.glob("*/subagents/*.jsonl")):
        found.append((p, "delegate", p.parent.parent.name))
    return found


# ---------------------------------------------------------------- scanning

class Scan:
    """Everything the transcripts say, deduped, before any window is applied."""

    def __init__(self):
        self.files = []          # per transcript: dict(kind, session, ...)
        self.usage = []          # [fid, ts, model, input, cache_write, cache_read, output]
        self.images = []         # (fid, ts, count)
        self.asks = []           # (fid, ts, is_confirmation)
        self.skipped_mtime = 0
        self.unreadable = []     # (relative path, reason)
        self.bad_lines = 0
        self.duplicates = 0
        self.bytes = 0


def count_images(content):
    """Image blocks inside tool_result blocks of one user message. The
    record's top-level `toolUseResult` repeats the same image, so only the
    message content is counted."""
    n = 0
    for b in content:
        if isinstance(b, dict) and b.get("type") == "tool_result":
            inner = b.get("content")
            if isinstance(inner, list):
                n += sum(1 for x in inner
                         if isinstance(x, dict) and x.get("type") == "image")
    return n


def scan_file(path, fid, info, lo, hi, seen, scan):
    in_window = lambda ts: bool(ts) and (lo is None or ts >= lo) and (hi is None or ts <= hi)
    is_main = info["kind"] == "main"
    with open(path, "rb") as fh:
        for line in fh:
            # Cheap byte tests decide whether a line is worth parsing: tool
            # results (most of the bytes) are skipped unless they carry an
            # image to count. The tests are on bare quoted values, never on
            # `"key":value`, so they do not depend on the writer's spacing.
            if b'"usage"' in line:
                pass
            elif b'"user"' in line:
                if b'"tool_result"' in line and b'"image"' not in line:
                    continue
            else:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                scan.bad_lines += 1
                continue
            kind = rec.get("type")
            ts = rec.get("timestamp") or ""
            msg = rec.get("message") or {}
            if kind == "assistant":
                u = msg.get("usage")
                if not isinstance(u, dict) or msg.get("model") == SYNTHETIC_MODEL:
                    continue
                inp = u.get("input_tokens") or 0
                cw = u.get("cache_creation_input_tokens") or 0
                cr = u.get("cache_read_input_tokens") or 0
                out = u.get("output_tokens") or 0
                mid = msg.get("id")
                key = (mid, rec.get("requestId")) if mid else None
                if key is not None and key in seen:
                    # A streamed chunk repeats the request's usage; only its
                    # output count grows as the reply completes.
                    scan.duplicates += 1
                    ev = seen[key]
                    if ev is not None and out > ev[6]:
                        ev[6] = out
                    continue
                if info["first_ts"] is None:
                    info["first_ts"] = ts
                    info["first_ctx"] = inp + cw + cr
                ev = None
                if in_window(ts):
                    ev = [fid, ts, msg.get("model") or "?", inp, cw, cr, out]
                    scan.usage.append(ev)
                if key is not None:
                    seen[key] = ev
            elif kind == "user":
                content = msg.get("content")
                if isinstance(content, list) and any(
                        isinstance(b, dict) and b.get("type") == "tool_result"
                        for b in content):
                    n = count_images(content)
                    if n and in_window(ts):
                        scan.images.append((fid, ts, n))
                    continue
                text = owner_text(rec)
                if text is None:
                    continue
                if info["first_prompt"] is None:
                    info["first_prompt"] = " ".join(text.split())[:PROMPT_CHARS]
                if is_main and in_window(ts):
                    scan.asks.append((fid, ts, is_confirmation(text)))


def scan_project(root, windows, progress=None):
    lo, hi = union_bounds(windows)
    lo_epoch = epoch_of(lo)
    scan = Scan()
    seen = {}
    todo = []
    for path, kind, session in discover(root):
        try:
            st = path.stat()
        except OSError as e:
            scan.unreadable.append((str(path.relative_to(root)), str(e)))
            continue
        if st.st_mtime < lo_epoch:
            scan.skipped_mtime += 1
            continue
        todo.append((path, kind, session, st.st_size))
    total = sum(t[3] for t in todo)
    started = last = time.monotonic()
    if progress:
        progress(f"reading {len(todo)} transcript files "
                 f"({total / 1e9:.2f} GB; {scan.skipped_mtime} older than the window skipped)")
    for i, (path, kind, session, size) in enumerate(todo):
        info = {"kind": kind, "session": session, "transcript": path.stem,
                "first_ts": None,
                "first_ctx": 0, "first_prompt": None,
                "path": str(path.relative_to(root))}
        fid = len(scan.files)
        scan.files.append(info)
        try:
            scan_file(path, fid, info, lo, hi, seen, scan)
        except OSError as e:
            scan.unreadable.append((info["path"], str(e)))
            if progress:
                progress(f"cannot read {info['path']}: {e}")
        scan.bytes += size
        now = time.monotonic()
        if progress and now - last >= PROGRESS_EVERY_S:
            last = now
            progress(f"{i + 1}/{len(todo)} files, {scan.bytes / 1e9:.2f}/{total / 1e9:.2f} GB, "
                     f"{now - started:.0f}s")
    return scan


# ---------------------------------------------------------------- aggregation

def percentiles(values):
    v = sorted(values)
    n = len(v)
    if not n:
        return {f"p{p}": None for p in PCTS} | {"max": None}
    return {f"p{p}": v[min(n - 1, n * p // 100)] for p in PCTS} | {"max": v[-1]}


def count_commits(project, window):
    """Commits reachable from HEAD inside the window, or None with the reason
    on stderr when git cannot answer."""
    bounds = []
    if window.lo:
        bounds.append(f"--since={window.lo[:19]}Z")
    if window.hi:
        bounds.append(f"--until={window.hi[:19]}Z")
    cmd = ["git", "-C", str(project), "rev-list", "--count", *bounds, "HEAD"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"token-usage: commit count unavailable for window {window.label()}: {e}",
              file=sys.stderr)
        return None
    if r.returncode:
        print(f"token-usage: commit count unavailable for window {window.label()}: "
              f"git exited {r.returncode}: {r.stderr.strip()[:200]}", file=sys.stderr)
        return None
    return int(r.stdout.strip())


def blank_usage():
    return {"calls": 0, "input": 0, "cache_write": 0, "cache_read": 0,
            "output": 0, "reread": 0, "images": 0}


def add_call(acc, ev):
    ctx = ev[3] + ev[4] + ev[5]
    acc["calls"] += 1
    acc["input"] += ev[3]
    acc["cache_write"] += ev[4]
    acc["cache_read"] += ev[5]
    acc["output"] += ev[6]
    acc["reread"] += ctx
    return ctx


def aggregate(scan, window, project=None):
    by_model = collections.defaultdict(blank_usage)
    by_kind = {"main": blank_usage(), "delegate": blank_usage()}
    by_day = collections.defaultdict(lambda: {"main": blank_usage(), "delegate": blank_usage()})
    per = {}

    def transcript(fid):
        t = per.get(fid)
        if t is None:
            info = scan.files[fid]
            t = per[fid] = dict(blank_usage(), kind=info["kind"],
                                session=info["session"],
                                transcript=info["transcript"], peak=0,
                                models=collections.Counter(),
                                first_prompt=info["first_prompt"],
                                began_in_window=window.contains(info["first_ts"]),
                                start_ctx=info["first_ctx"])
        return t

    for ev in scan.usage:
        if not window.contains(ev[1]):
            continue
        kind = scan.files[ev[0]]["kind"]
        t = transcript(ev[0])
        t["peak"] = max(t["peak"], add_call(t, ev))
        t["models"][ev[2]] += 1
        add_call(by_model[ev[2]], ev)
        add_call(by_kind[kind], ev)
        add_call(by_day[ev[1][:10]][kind], ev)
    for fid, ts, n in scan.images:
        if window.contains(ts):
            by_kind[scan.files[fid]["kind"]]["images"] += n
            transcript(fid)["images"] += n

    owner = sum(1 for fid, ts, c in scan.asks if window.contains(ts))
    confirms = sum(1 for fid, ts, c in scan.asks if window.contains(ts) and c)

    transcripts = []
    for fid, t in per.items():
        if not t["calls"]:
            continue
        model = t["models"].most_common(1)[0][0]
        row = {k: t[k] for k in ("kind", "session", "transcript", "calls", "input", "cache_write",
                                 "cache_read", "output", "reread", "images", "peak",
                                 "first_prompt", "began_in_window")}
        row["start"] = t["start_ctx"] if t["began_in_window"] else None
        row["model"] = model
        transcripts.append(row)
    transcripts.sort(key=lambda r: (-r["reread"], r["transcript"]))

    totals = blank_usage()
    for k in by_kind.values():
        for f in totals:
            totals[f] += k[f]
    commits = count_commits(project, window) if project is not None else None
    dist = {}
    for kind in ("main", "delegate"):
        rows = [r for r in transcripts if r["kind"] == kind]
        started = [r for r in rows if r["start"] is not None]
        reread = sum(r["reread"] for r in rows)
        start_total = sum(r["start"] * r["calls"] for r in started)
        started_reread = sum(r["reread"] for r in started)
        dist[kind] = {
            "transcripts": len(rows),
            "started_in_window": len(started),
            "start": percentiles([r["start"] for r in started]),
            "peak": percentiles([r["peak"] for r in rows]),
            "calls": percentiles([r["calls"] for r in rows]),
            "start_share": (start_total / started_reread) if started_reread else None,
        }
        by_kind[kind]["transcripts"] = len(rows)
        by_kind[kind]["share"] = (by_kind[kind]["reread"] / totals["reread"]
                                  if totals["reread"] else None)
    return {
        "window": {"since": window.since_text, "until": window.until_text,
                   "label": window.label()},
        "totals": dict(totals, transcripts=len(transcripts),
                       owner_messages=owner, owner_confirmations=confirms,
                       commits=commits,
                       reread_per_owner_message=(totals["reread"] / owner) if owner else None,
                       reread_per_commit=(totals["reread"] / commits) if commits else None),
        "by_model": dict(sorted(by_model.items(), key=lambda kv: -kv[1]["reread"])),
        "by_kind": by_kind,
        "by_day": {d: by_day[d] for d in sorted(by_day)},
        "distribution": dist,
        "transcripts": transcripts,
    }


# ---------------------------------------------------------------- rendering

def tok(n):
    """A token count as 17.15B / 12.3M / 79k / 512."""
    if n is None:
        return "-"
    if abs(n) >= 1e9:
        return f"{n / 1e9:.2f}B"
    if abs(n) >= 1e6:
        return f"{n / 1e6:.1f}M"
    if abs(n) >= 1e3:
        return f"{n / 1e3:.0f}k"
    return str(int(n))


def pct(x):
    return "-" if x is None else f"{x * 100:.0f}%"


def table(header, rows, left=(0,)):
    """Columns sized to their content; the first column (and any in `left`)
    left-aligned, the rest right-aligned, two spaces between."""
    cells = [header] + [[str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(header))]
    out = []
    for r in cells:
        parts = []
        for i, c in enumerate(r):
            last_left = i in left and i == len(r) - 1
            parts.append(c if last_left else (c.ljust(widths[i]) if i in left else c.rjust(widths[i])))
        out.append("  ".join(parts).rstrip())
    return "\n".join(out)


def short_model(m):
    return m[len("claude-"):] if m.startswith("claude-") else m


def render_window(res, top, project_name):
    t = res["totals"]
    out = [f"== window {res['window']['label']} (UTC)  project {project_name}",
           f"   {t['transcripts']} transcripts, {t['calls']} calls, "
           f"{t['owner_messages']} owner messages "
           f"({t['owner_confirmations']} bare confirmations), "
           f"{'?' if t['commits'] is None else t['commits']} commits"]
    out.append("\n== totals")
    out.append(table(["", "tokens"], [
        ["re-read (sum of context)", tok(t["reread"])],
        ["  fresh input", tok(t["input"])],
        ["  cache written", tok(t["cache_write"])],
        ["  cache read", tok(t["cache_read"])],
        ["output", tok(t["output"])],
        ["images viewed", str(t["images"])],
        ["re-read per owner message", tok(t["reread_per_owner_message"])],
        ["re-read per commit", tok(t["reread_per_commit"])]]))
    out.append("\n== by model")
    out.append(table(["model", "calls", "re-read", "share", "cache written", "output"],
        [[short_model(m), v["calls"], tok(v["reread"]),
          pct(v["reread"] / t["reread"] if t["reread"] else None),
          tok(v["cache_write"]), tok(v["output"])]
         for m, v in res["by_model"].items()]))
    out.append("\n== main sessions vs delegates")
    out.append(table(["", "transcripts", "calls", "re-read", "share", "cache written", "output", "images"],
        [[k, v["transcripts"], v["calls"], tok(v["reread"]), pct(v["share"]),
          tok(v["cache_write"]), tok(v["output"]), v["images"]]
         for k, v in res["by_kind"].items()]))
    out.append("\n== by day (UTC)")
    out.append(table(["day", "calls", "re-read", "main", "delegate", "cache written", "output"],
        [[d, v["main"]["calls"] + v["delegate"]["calls"],
          tok(v["main"]["reread"] + v["delegate"]["reread"]),
          tok(v["main"]["reread"]), tok(v["delegate"]["reread"]),
          tok(v["main"]["cache_write"] + v["delegate"]["cache_write"]),
          tok(v["main"]["output"] + v["delegate"]["output"])]
         for d, v in res["by_day"].items()]))
    out.append("\n== context size distribution (starting size counts only transcripts that began in the window)")
    rows = []
    for kind, d in res["distribution"].items():
        for what, key, fmt, n in (
                ("starting size", "start", tok, d["started_in_window"]),
                ("peak", "peak", tok, d["transcripts"]),
                ("calls per transcript", "calls", lambda v: "-" if v is None else str(v),
                 d["transcripts"])):
            p = d[key]
            rows.append([kind, what, fmt(p["p10"]), fmt(p["p50"]), fmt(p["p90"]),
                         fmt(p["max"]), n])
    out.append(table(["", "", "p10", "p50", "p90", "max", "n"], rows, left=(0, 1)))
    out.append("   share of re-reads that is the starting size repeated: " +
               ", ".join(f"{k} {pct(d['start_share'])}" for k, d in res["distribution"].items()))
    out.append(f"\n== {min(top, len(res['transcripts']))} costliest transcripts")
    rows = []
    for r in res["transcripts"][:top]:
        rows.append([r["kind"], r["transcript"][:14], r["session"][:8],
                     short_model(r["model"]), r["calls"],
                     tok(r["start"]) if r["start"] is not None else "-",
                     tok(r["peak"]), tok(r["reread"]), tok(r["cache_write"]),
                     r["images"], r["first_prompt"] or ""])
    out.append(table(["kind", "transcript", "session", "model", "calls", "start", "peak",
                      "re-read", "cache written", "images", "first prompt"], rows,
                     left=(0, 1, 2, 3, 10)))
    return "\n".join(out)


def metric_rows(res):
    t, d, k = res["totals"], res["distribution"], res["by_kind"]
    return [
        ("calls", t["calls"], str),
        ("tokens re-read", t["reread"], tok),
        ("  main sessions", k["main"]["reread"], tok),
        ("  delegates", k["delegate"]["reread"], tok),
        ("delegate share of re-read", k["delegate"]["share"], pct),
        ("cache written", t["cache_write"], tok),
        ("output", t["output"], tok),
        ("images viewed", t["images"], str),
        ("owner messages", t["owner_messages"], str),
        ("commits", t["commits"], str),
        ("re-read per owner message", t["reread_per_owner_message"], tok),
        ("re-read per commit", t["reread_per_commit"], tok),
        ("main starting size p50", d["main"]["start"]["p50"], tok),
        ("main peak p50", d["main"]["peak"]["p50"], tok),
        ("main peak p90", d["main"]["peak"]["p90"], tok),
        ("main peak max", d["main"]["peak"]["max"], tok),
        ("delegate starting size p50", d["delegate"]["start"]["p50"], tok),
        ("delegate peak p50", d["delegate"]["peak"]["p50"], tok),
        ("delegate peak p90", d["delegate"]["peak"]["p90"], tok),
        ("delegate peak max", d["delegate"]["peak"]["max"], tok),
    ]


def change(a, b):
    if a is None or b is None or a == 0:
        return "-"
    v = round((b - a) / a * 100)
    return f"{v:+d}%" if v else "0%"


def render_compare(ra, rb, project_name):
    rows = []
    for (label, a, fmt), (_, b, _f) in zip(metric_rows(ra), metric_rows(rb)):
        rows.append([label, "-" if a is None else fmt(a), "-" if b is None else fmt(b), change(a, b)])
    out = [f"== compare  project {project_name} (UTC)",
           table(["", f"A {ra['window']['label']}", f"B {rb['window']['label']}", "B vs A"], rows)]
    models = sorted(set(ra["by_model"]) | set(rb["by_model"]),
                    key=lambda m: -(rb["by_model"].get(m, {}).get("reread", 0)
                                    + ra["by_model"].get(m, {}).get("reread", 0)))
    mrows = []
    for m in models:
        a = ra["by_model"].get(m, {}).get("reread", 0)
        b = rb["by_model"].get(m, {}).get("reread", 0)
        mrows.append([short_model(m), tok(a), tok(b), change(a, b)])
    out.append("\n== re-read by model")
    out.append(table(["model", "A", "B", "B vs A"], mrows))
    return "\n".join(out)


# ---------------------------------------------------------------- command line

class Parser(argparse.ArgumentParser):
    """A refusal names the offending token and prints the full accepted set."""

    def error(self, message):
        self.print_help(sys.stderr)
        self.exit(2, f"\n{self.prog}: error: {message}\n")


def build_parser():
    ap = Parser(prog="token-usage.py", allow_abbrev=False,
                description="Where a project's Claude Code tokens go.",
                formatter_class=argparse.RawDescriptionHelpFormatter,
                epilog="Window bounds are UTC ISO dates or date-times; --until is inclusive.")
    ap.add_argument("--project", metavar="DIR", default=None,
                    help="project whose transcripts to read (default: cwd)")
    ap.add_argument("--since", metavar="DATE", help="first moment of the window")
    ap.add_argument("--until", metavar="DATE", help="last moment of the window (inclusive)")
    ap.add_argument("--compare", nargs=2, metavar=("A..B", "C..D"),
                    help="two SINCE..UNTIL windows, side by side with the change")
    ap.add_argument("--top", metavar="N", type=int, default=10,
                    help="costliest transcripts to list (default 10)")
    ap.add_argument("--json", action="store_true", help="the full result as JSON")
    return ap


def parse_windows(ap, args):
    try:
        if args.compare:
            if args.since or args.until:
                ap.error("--compare takes its own windows and cannot be combined with --since/--until")
            wins = []
            for spec in args.compare:
                if ".." not in spec:
                    ap.error(f"--compare window `{spec}` is not SINCE..UNTIL")
                s, u = spec.split("..", 1)
                if not s or not u:
                    ap.error(f"--compare window `{spec}` needs both a SINCE and an UNTIL")
                wins.append(Window(s, u))
            return wins
        return [Window(args.since, args.until)]
    except ValueError as e:
        ap.error(str(e))


def run(argv, out=sys.stdout, progress=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.top < 1:
        ap.error(f"--top must be at least 1, not {args.top}")
    windows = parse_windows(ap, args)
    project = Path(args.project or os.getcwd()).expanduser()
    if not project.is_dir():
        ap.error(f"--project `{args.project}` is not a directory")
    try:
        root = transcript_root(project)
    except FileNotFoundError as e:
        ap.error(str(e))
    started = time.monotonic()
    scan = scan_project(root, windows, progress)
    results = [aggregate(scan, w, project) for w in windows]
    duration = time.monotonic() - started
    name = project.resolve().name
    meta = {"project": name, "transcript_files": len(scan.files),
            "files_skipped_by_mtime": scan.skipped_mtime,
            "unreadable_files": len(scan.unreadable), "bad_lines": scan.bad_lines,
            "duplicate_records": scan.duplicates, "bytes_read": scan.bytes,
            "duration_s": round(duration, 2)}
    if args.json:
        json.dump({"meta": meta, "windows": results}, out, indent=1)
        out.write("\n")
    else:
        if len(results) == 2:
            out.write(render_compare(results[0], results[1], name) + "\n")
        else:
            out.write(render_window(results[0], args.top, name) + "\n")
        notes = []
        if scan.unreadable:
            notes.append(f"{len(scan.unreadable)} unreadable file(s) skipped")
        if scan.bad_lines:
            notes.append(f"{scan.bad_lines} malformed line(s) skipped")
        out.write(f"\n{meta['transcript_files']} files read ({scan.bytes / 1e9:.2f} GB), "
                  f"{scan.skipped_mtime} skipped as older than the window, "
                  f"{scan.duplicates} duplicate stream records collapsed"
                  + ("; " + "; ".join(notes) if notes else "")
                  + f"; took {duration:.1f}s\n")
    return 0


def main():
    def progress(msg):
        print(f"token-usage: {msg}", file=sys.stderr, flush=True)
    return run(sys.argv[1:], progress=progress)


if __name__ == "__main__":
    sys.exit(main())
