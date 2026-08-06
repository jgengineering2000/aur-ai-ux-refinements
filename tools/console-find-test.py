#!/usr/bin/env python3
"""Functional gate for the Console Manager's in-pane search.

Drives the REAL page served by the doc server in a real browser over the
Chrome DevTools Protocol, because the search is DOM behaviour — hit wrapping,
scroll positioning, keystroke routing — and none of that is observable from a
static read of the template. A headless load that merely parses proves the
script has no syntax error; it does not prove the feature works.

The fixture token is deliberately one no real log can contain, so assertions
stay exact even though the pane under test is attached to a LIVE log that
keeps appending underneath them.

Usage:
    tools/console-find-test.py [--port N] [--perf] [--keep] [--verbose]

    --port N    doc server port (default: read aur/server.port, else 7432)
    --perf      measure search on a pane filled to the spec's 5MB cap instead
                of running the assertions
    --keep      leave the browser running for inspection on failure
    --verbose   print every assertion, not just failures
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import websockets

HERE = os.path.dirname(os.path.abspath(__file__))
AUR = os.path.dirname(HERE)
TOKEN = "ZQXFIXTURE"          # cannot occur in a real log line
DEBUG_PORT_BASE = 9333
BROWSER_READY_TIMEOUT = 20.0   # seconds to wait for the devtools endpoint


# --------------------------------------------------------------------------
# Argument handling. An unrecognised argument prints help and exits non-zero:
# a test driver that silently reinterprets a typo as "no flag given" runs the
# whole suite from a keystroke, and the operator's evidence that they were
# understood is that the tool refuses what it did not understand.
# --------------------------------------------------------------------------
ACCEPTED = {"--port", "--keep", "--verbose", "--perf", "--help", "-h"}


def parse_args(argv):
    opts = {"port": None, "keep": False, "verbose": False, "perf": False}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--help", "-h"):
            print(__doc__)
            sys.exit(0)
        if a not in ACCEPTED:
            sys.stderr.write(
                f"unknown option `{a}`\n"
                f"accepted: {' '.join(sorted(ACCEPTED))}\n")
            sys.exit(2)
        if a == "--port":
            # A flag documented as taking a value and given none is refused,
            # never silently defaulted.
            if i + 1 >= len(argv):
                sys.stderr.write("option `--port` requires a value\n")
                sys.exit(2)
            try:
                opts["port"] = int(argv[i + 1])
            except ValueError:
                sys.stderr.write(f"option `--port` wants a number, got `{argv[i+1]}`\n")
                sys.exit(2)
            i += 2
            continue
        opts[a.lstrip("-")] = True
        i += 1
    return opts


def infer_port(explicit):
    """Inference ladder, ending somewhere that always answers."""
    if explicit:
        return explicit
    f = os.path.join(AUR, "server.port")
    if os.path.exists(f):
        try:
            return int(open(f).read().strip())
        except ValueError:
            pass
    return 7432


def find_browser():
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    sys.stderr.write("no chromium/chrome on PATH — cannot run the DOM gate\n")
    sys.exit(3)


# --------------------------------------------------------------------------
# The in-page test body. Returns a list of {name, ok, got, want} objects, so
# the Python side reports rather than re-derives.
# --------------------------------------------------------------------------
PAGE_TEST = r"""
(async () => {
  const R = [];
  const ck = (name, got, want) =>
    R.push({ name, ok: JSON.stringify(got) === JSON.stringify(want), got, want });
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const until = async (fn, ms = 8000) => {
    const t0 = Date.now();
    for (;;) {
      const v = fn();
      if (v) return v;
      if (Date.now() - t0 > ms) return null;
      await sleep(50);
    }
  };

  // 1. Open a pane through the real path — a click on a real chip. The list
  //    is fetched, so a started page is not yet a populated one: wait for the
  //    chip to exist rather than assuming load means ready.
  const chip = await until(() =>
    document.querySelector('#console-list .dmn-open, #console-list .chip'));
  if (!chip) return [{ name: 'a console chip exists to open', ok: false,
                       got: null, want: 'one chip' }];
  chip.click();
  const pane = await until(() => document.querySelector('.pane'));
  if (!pane) return [{ name: 'clicking a chip opens a pane', ok: false,
                       got: null, want: '.pane' }];

  const body  = pane.querySelector('.pane-body');
  const row   = pane.querySelector('.pane-find');
  const q     = pane.querySelector('.find-q');
  const mode  = pane.querySelector('.find-mode');
  const dir   = pane.querySelector('.find-dir');
  const count = pane.querySelector('.find-count');
  const btnS  = pane.querySelector('[data-act="search"]');
  const btnH  = pane.querySelector('[data-act="hold"]');
  const T = 'ZQXFIXTURE';

  ck('find row starts hidden', row.hidden, true);

  // 2. A deterministic fixture. The pane is attached to a LIVE log that keeps
  //    appending, so every assertion is anchored on a token no log can carry.
  pane.querySelector('[data-act="clear"]').click();
  const fixture = [
    'one ' + T + ' alpha',
    'two ' + T.toLowerCase() + ' beta',
    'three ' + T + ' ' + T + ' gamma'
  ];
  for (const line of fixture) {
    const s = document.createElement('span');
    s.className = 'ln';
    s.textContent = line + '\n';
    body.appendChild(s);
  }

  // 3. The Search button opens the row, focuses the field, and latches Hold —
  //    a search IS a read of history, so the pane must stop tailing.
  btnS.click();
  ck('Search opens the find row',   row.hidden, false);
  ck('Search marks its own button', btnS.classList.contains('on'), true);
  ck('Search latches Hold on',      btnH.classList.contains('on'), true);
  ck('default mode is regex',       mode.textContent, '(regex)');
  ck('default direction is up',     dir.textContent.trim(), '▲');

  const type = async (text) => {
    q.value = text;
    q.dispatchEvent(new Event('input', { bubbles: true }));
    await sleep(300);            // past FIND_DEBOUNCE_MS
  };
  const enter = (shift) => q.dispatchEvent(new KeyboardEvent('keydown',
    { key: 'Enter', shiftKey: !!shift, bubbles: true }));
  const hits = () => body.querySelectorAll('mark.find-hit').length;
  const cur  = () => body.querySelector('mark.find-hit.cur');

  // 4. Three occurrences of the token across the fixture.
  await type(T);
  ck('regex query finds every occurrence', hits(), 3);
  ck('count reads position/total',         count.textContent, '3/3');
  ck('a hit is current',                   !!cur(), true);

  // 5. Searching UP lands on the LAST match first — a console is searched for
  //    something that has already gone past.
  ck('up-search opens at the last hit',
     cur() === body.querySelectorAll('mark.find-hit')[2], true);

  // 6. n steps in the direction shown; it wraps rather than stopping.
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'n', bubbles: true }));
  ck('n steps up to the previous hit', count.textContent, '2/3');
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'N', bubbles: true }));
  ck('N steps back the other way',     count.textContent, '3/3');
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'n', bubbles: true }));
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'n', bubbles: true }));
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'n', bubbles: true }));
  ck('n wraps past the first hit',     count.textContent, '3/3');

  // 7. Enter repeats without leaving the field.
  enter(false);
  ck('Enter steps like n', count.textContent, '2/3');
  enter(true);
  ck('Shift+Enter reverses', count.textContent, '3/3');

  // 8. Direction toggle flips the glyph and the sense of a step.
  dir.click();
  ck('direction toggles to down', dir.textContent.trim(), '▼');
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'n', bubbles: true }));
  ck('n now steps forward', count.textContent, '1/3');
  dir.click();

  // 9. Mode toggle. `.` is a wildcard in regex and a literal in plain, so one
  //    query separates the two modes without depending on the fixture text.
  await type(T.slice(0, 3) + '.' + T.slice(4));
  ck('regex treats . as a wildcard', hits(), 3);
  mode.click();
  await sleep(50);
  ck('mode toggles to plain', mode.textContent, '(plain)');
  ck('plain treats . as a literal', hits(), 0);
  ck('no match says so', count.textContent, 'no match');
  mode.click();
  await sleep(50);

  // 10. A half-typed regex is the normal state while typing one. It must read
  //     as a bad pattern, never as "no match" — that is a wrong answer.
  await type('[' + T);
  ck('bad regex marks the field', q.classList.contains('bad'), true);
  ck('bad regex says bad pattern', count.textContent, 'bad pattern');

  // 11. Case sensitivity: the lowercase copy on line two is a different string.
  await type(T.toLowerCase());
  ck('search is case sensitive', hits(), 1);

  // 12. Closing removes every mark and restores the text exactly.
  const before = body.textContent;
  q.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  ck('Escape hides the find row',  row.hidden, true);
  ck('Escape clears every mark',   hits(), 0);
  ck('text survives unmarking',    body.textContent, before);
  ck('Hold is NOT released',       btnH.classList.contains('on'), true);

  // 13. Ctrl+F opens in PLAIN mode; / and ? open in REGEX with a direction.
  document.dispatchEvent(new KeyboardEvent('keydown',
    { key: 'f', ctrlKey: true, bubbles: true }));
  ck('Ctrl+F opens the find row', row.hidden, false);
  ck('Ctrl+F selects plain mode', mode.textContent, '(plain)');
  q.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

  document.dispatchEvent(new KeyboardEvent('keydown', { key: '/', bubbles: true }));
  ck('/ opens in regex mode', mode.textContent, '(regex)');
  ck('/ searches down',       dir.textContent.trim(), '▼');
  q.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

  document.dispatchEvent(new KeyboardEvent('keydown', { key: '?', bubbles: true }));
  ck('? searches up', dir.textContent.trim(), '▲');
  q.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

  return R;
})()
"""


# --------------------------------------------------------------------------
# Perf probe. The spec fixes the buffer at 5MB held / 2MB live, and a cap is a
# claim about responsiveness that has to be measured at the size it names —
# "it felt fine on a small pane" is not evidence about a full one.
# --------------------------------------------------------------------------
PAGE_PERF = r"""
(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const until = async (fn, ms = 8000) => {
    const t0 = Date.now();
    for (;;) {
      const v = fn(); if (v) return v;
      if (Date.now() - t0 > ms) return null;
      await sleep(50);
    }
  };
  const chip = await until(() =>
    document.querySelector('#console-list .dmn-open, #console-list .chip'));
  if (!chip) return { error: 'no chip to open' };
  chip.click();
  const pane = await until(() => document.querySelector('.pane'));
  if (!pane) return { error: 'no pane opened' };
  const body = pane.querySelector('.pane-body');
  const q    = pane.querySelector('.find-q');
  const cnt  = pane.querySelector('.find-count');

  pane.querySelector('[data-act="clear"]').click();
  // A realistic line: the MTI prefix is ~70 characters before any message.
  const mk = (i) =>
    `[2026-08-06 18:00:${String(i % 60).padStart(2,'0')}] [Info] [PID:4242] ` +
    `[Thread:worker-${i % 8}] [console] record ${i} served md5=` +
    `${(i * 2654435761 >>> 0).toString(16)} tier=${i % 4} size=${i % 9999}\n`;

  const TARGET = __TARGET__;
  const t0 = performance.now();
  let bytes = 0, i = 0;
  const frag = document.createDocumentFragment();
  while (bytes < TARGET) {
    const s = document.createElement('span');
    s.className = 'ln';
    s.textContent = mk(i++);
    bytes += s.textContent.length;
    frag.appendChild(s);
  }
  body.appendChild(frag);
  const tFill = performance.now() - t0;
  await sleep(120);

  pane.querySelector('[data-act="search"]').click();

  // A query that matches often enough to exercise wrapping, but is not the
  // pathological one-character case the FIND_MAX_HITS cap exists for.
  // Time the WORK, not the debounce: dispatch, then poll until the count
  // changes, and subtract the debounce the timer was always going to spend.
  // Sleeping a fixed interval and reporting it measures the sleep.
  const run = async (text) => {
    const before = cnt.textContent;
    q.value = text;
    const t = performance.now();
    q.dispatchEvent(new Event('input', { bubbles: true }));
    for (let i = 0; i < 4000 && cnt.textContent === before; i++) await sleep(2);
    return { ms: performance.now() - t - 140, count: cnt.textContent };
  };

  // Three shapes: one that hits the mark cap, one that returns thousands of
  // real hits under the cap, and one that matches nothing — the last is the
  // worst case for scanning, since it cannot stop early.
  const capped = await run('md5=');
  const common = await run('record 4[0-9]{4}');
  const rare   = await run('md5=deadbeef00');
  const t1 = performance.now();
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'n', bubbles: true }));
  const step = performance.now() - t1;

  return {
    lines: i, bytes,
    fill_ms:   Math.round(tFill),
    capped_ms: Math.round(capped.ms), capped_count: capped.count,
    common_ms: Math.round(common.ms), common_count: common.count,
    rare_ms:   Math.round(rare.ms),   rare_count:   rare.count,
    step_ms:   Math.round(step * 100) / 100
  };
})()
"""


async def drive_perf(ws_url, target):
    async with websockets.connect(ws_url, max_size=32 * 1024 * 1024) as ws:
        seq = [0]

        async def call(method, **params):
            seq[0] += 1
            mid = seq[0]
            await ws.send(json.dumps({"id": mid, "method": method,
                                      "params": params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == mid:
                    if "error" in msg:
                        raise RuntimeError(f"{method}: {msg['error']}")
                    return msg["result"]

        await call("Runtime.enable")
        res = await call("Runtime.evaluate",
                         expression=PAGE_PERF.replace("__TARGET__", str(target)),
                         awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in res:
            raise RuntimeError("page threw: "
                               + json.dumps(res["exceptionDetails"])[:900])
        return res["result"]["value"]


async def drive(ws_url, verbose):
    async with websockets.connect(ws_url, max_size=32 * 1024 * 1024) as ws:
        seq = [0]

        async def call(method, **params):
            seq[0] += 1
            mid = seq[0]
            await ws.send(json.dumps({"id": mid, "method": method,
                                      "params": params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == mid:
                    if "error" in msg:
                        raise RuntimeError(f"{method}: {msg['error']}")
                    return msg["result"]

        await call("Runtime.enable")
        res = await call("Runtime.evaluate", expression=PAGE_TEST,
                         awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in res:
            raise RuntimeError("page threw: "
                               + json.dumps(res["exceptionDetails"])[:900])
        return res["result"]["value"]


def wait_for_devtools(port, deadline):
    """A started browser is not a usable one. Wait on the endpoint it
    publishes rather than treating spawn as ready."""
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/list", timeout=1) as r:
                targets = json.load(r)
            for t in targets:
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    return t["webSocketDebuggerUrl"]
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            last = e
        time.sleep(0.2)
    raise RuntimeError(f"devtools endpoint never came up on {port}: {last}")


def main():
    opts = parse_args(sys.argv[1:])
    port = infer_port(opts["port"])
    browser = find_browser()
    url = f"http://127.0.0.1:{port}/manifest.html"

    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            r.read(64)
    except (urllib.error.URLError, OSError) as e:
        sys.stderr.write(f"doc server not answering at {url}: {e}\n")
        return 3

    dport = DEBUG_PORT_BASE
    profile = tempfile.mkdtemp(prefix="console-find-test-")
    proc = subprocess.Popen(
        [browser, "--headless=new", "--disable-gpu", "--no-sandbox",
         f"--remote-debugging-port={dport}", f"--user-data-dir={profile}",
         "--no-first-run", "--disable-extensions", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    t0 = time.time()
    try:
        ws_url = wait_for_devtools(dport, t0 + BROWSER_READY_TIMEOUT)
        if opts["perf"]:
            m = asyncio.run(drive_perf(ws_url, 5 * 1024 * 1024))
            if m.get("error"):
                sys.stderr.write(f"FAIL: {m['error']}\n")
                return 1
            print(f"buffer      {m['bytes']:,} bytes in {m['lines']:,} lines "
                  f"(built in {m['fill_ms']} ms)")
            print(f"cap hit     {m['capped_ms']:>5} ms   {m['capped_count']}")
            print(f"common hit  {m['common_ms']:>5} ms   {m['common_count']}")
            print(f"rare hit    {m['rare_ms']:>5} ms   {m['rare_count']}")
            print(f"one step    {m['step_ms']:>5} ms")
            return 0
        results = asyncio.run(drive(ws_url, opts["verbose"]))
    except Exception as e:                      # noqa: BLE001 — reported, not swallowed
        sys.stderr.write(f"FAIL: driving the page: {e}\n")
        if not opts["keep"]:
            proc.terminate()
        return 1
    finally:
        if not opts["keep"]:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            shutil.rmtree(profile, ignore_errors=True)

    bad = [r for r in results if not r["ok"]]
    for r in results:
        if not r["ok"] or opts["verbose"]:
            mark = "ok  " if r["ok"] else "FAIL"
            print(f"{mark} {r['name']}")
            if not r["ok"]:
                print(f"       got  {r['got']!r}")
                print(f"       want {r['want']!r}")
    dt = time.time() - t0
    print(f"\n{len(results) - len(bad)}/{len(results)} assertions passed "
          f"in {dt:.1f}s")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
