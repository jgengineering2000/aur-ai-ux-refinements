#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""threads -- a per-project ledger of delegated or paused work, so nothing interrupted is lost.

One append-only JSON-lines ledger per project (tracked in git, no PII: plain titles, repo-relative
paths, SHAs, log file BASENAMES). A thread is one unit of delegated or paused work; a thread with no
close is open, whatever happened to its worker. `status` also sweeps the artifacts work leaves behind
and flags each one no thread accounts for as an orphan: workers crash and forget, so self-report alone
is not enough.

usage: threads.py [--ledger FILE] [--root DIR] VERB ...
  open ID --title "3-8 plain words" --goal TEXT --done TEXT [--brief PATH] [--lab BRANCH] [--session S]
  checkpoint ID --state TEXT [--commits SHA,...] [--map LOGFILE] [--covers KEY,...]
  pause ID --reason TEXT --by WHO --resume-when TEXT [--map LOGFILE]   prints the resume-map location
  resume ID [--session S]
  close ID (--landed SHA | --superseded-by THREAD-OR-SHA | --dropped REASON) [--if-open]
  status [--table] [--fast]     one line: threads: N open (P paused, oldest Xh), K orphans
  show ID                       the folded state of one thread
  --if-open (close, resume, checkpoint): an unknown or already-closed ID is a logged no-op, exit 0
  (for the tool hooks, whose labs may predate the ledger).

Ledger: --ledger, else $THREADS_LEDGER, else <main tree of the git repo at --root or cwd>/threads/ledger.jsonl
(the MAIN tree even from a lab, so the branches never fork the file).
Sweep sources: threads/sources.json (tracked, repo-relative) merged with threads/sources.local.json
(untracked: the log directories, which live outside the repo) and $THREADS_LOG_DIRS (colon list).
An artifact is accounted for when a thread (open or closed) names its key in `lab`, `brief`, `map` or
`covers` (fnmatch patterns allowed). Keys: lab branch name; repo-relative path; log file basename.
Any other argument is refused with this text, exit status 2. Every refusal is logged to stderr with
verb, identifier, values, phase and route."""
import calendar, fcntl, fnmatch, glob, json, os, subprocess, sys, time

TRUNK_DEFAULT = 'master'
RESUME_WORDS = ('RESUME MAP', 'HANDOFF')
OUTCOMES = ('--landed', '--superseded-by', '--dropped')
VERBS = {
    'open': ({'--title', '--goal', '--done', '--brief', '--lab', '--session'}, set(), 1),
    'checkpoint': ({'--state', '--commits', '--map', '--covers'}, {'--if-open'}, 1),
    'pause': ({'--reason', '--by', '--resume-when', '--map'}, set(), 1),
    'resume': ({'--session'}, {'--if-open'}, 1),
    'close': ({'--landed', '--superseded-by', '--dropped'}, {'--if-open'}, 1),
    'status': (set(), {'--table', '--fast'}, 0),
    'show': (set(), set(), 1),
}


def usage(msg=None, code=2):
    if msg:
        sys.stderr.write('threads.py: %s\n' % msg)
    sys.stderr.write(__doc__ + '\n')
    return code


def refuse(verb, ident, phase, why, **values):
    extra = ' '.join('%s=%r' % kv for kv in sorted(values.items()))
    sys.stderr.write('threads.py: REFUSED verb=%s id=%r phase=%s route=%s %s\n' % (verb, ident, phase, why, extra))
    return 2


def git(root, *argv):
    r = subprocess.run(['git', '-C', root] + list(argv), capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def main_root(start):
    rc, out, err = git(start, 'rev-parse', '--path-format=absolute', '--git-common-dir')
    if rc:
        raise SystemExit('threads.py: not inside a git repo: %s (%s)' % (start, err))
    return os.path.dirname(out) if os.path.basename(out) == '.git' else out


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def base(path):
    """Ledger rule: a path outside the repo is recorded by its basename only."""
    return os.path.basename(path.rstrip('/')) if path and os.path.isabs(path) else path


# ---- ledger -------------------------------------------------------------------------------------

def read(ledger):
    if not os.path.exists(ledger):
        return []
    out = []
    with open(ledger) as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError as e:
                sys.stderr.write('threads.py: ledger %s line %d unreadable (%s); skipped\n' % (ledger, n, e))
    return out


def read_strict(ledger):
    """The ledger's events, for a caller that acts on them: OSError when it is missing or cannot be opened,
    ValueError naming the line when one cannot be read. `read` skips such a line and calls a missing ledger empty,
    which is right for a report; a line it skipped might be the one event that matters."""
    out = []
    with open(ledger) as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        for n, line in enumerate(f, 1):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError as e:
                    raise ValueError('%s line %d: %s' % (ledger, n, e))
    return out


def append(ledger, event):
    os.makedirs(os.path.dirname(ledger), exist_ok=True)
    with open(ledger, 'a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(json.dumps(event, sort_keys=True) + '\n')
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def fold(events):
    """id -> state dict. status: open | paused | closed."""
    th = {}
    for e in events:
        i, ev = e.get('id'), e.get('ev')
        if ev == 'open':
            th[i] = {'id': i, 'status': 'open', 'opened': e['ts'], 'title': e.get('title', ''), 'goal': e.get('goal', ''),
                     'done': e.get('done', ''), 'brief': e.get('brief', ''), 'lab': e.get('lab', ''),
                     'session': e.get('session', ''), 'commits': [], 'maps': [], 'covers': [], 'state': '',
                     'last': e['ts'], 'paused': None, 'outcome': None}
            continue
        t = th.get(i)
        if t is None:
            continue
        t['last'] = e['ts']
        if ev == 'checkpoint':
            t['state'] = e.get('state', t['state'])
            t['commits'] += e.get('commits', [])
            t['maps'] += e.get('maps', [])
            t['covers'] += e.get('covers', [])
        elif ev == 'pause':
            t['status'] = 'paused'
            t['paused'] = {'ts': e['ts'], 'reason': e.get('reason', ''), 'by': e.get('by', ''),
                           'resume_when': e.get('resume_when', '')}
            t['maps'] += e.get('maps', [])
        elif ev == 'resume':
            t['status'] = 'open'
            t['paused'] = None
        elif ev == 'close':
            t['status'] = 'closed'
            t['outcome'] = e.get('outcome')
    return th


def hours(ts):
    return max(0, (time.time() - calendar.timegm(time.strptime(ts, '%Y-%m-%dT%H:%M:%SZ'))) / 3600.0)


# ---- sweep --------------------------------------------------------------------------------------
# The sources: one table, extend by adding a row. Each takes (root, cfg) and yields (kind, key, note).

def load_cfg(root):
    cfg = {'trunk': TRUNK_DEFAULT, 'log_dirs': [], 'patch_dirs': [], 'brief_dirs': [], 'brief_days': 21}
    for name in ('sources.json', 'sources.local.json'):
        p = os.path.join(root, 'threads', name)
        if os.path.exists(p):
            with open(p) as f:
                for k, v in json.load(f).items():
                    cfg[k] = (cfg.get(k, []) + v) if isinstance(v, list) else v
    cfg['log_dirs'] += [d for d in os.environ.get('THREADS_LOG_DIRS', '').split(':') if d]
    return cfg


def src_lab_branches(root, cfg, fast):
    rc, out, _ = git(root, 'branch', '--no-merged', cfg['trunk'], '--format=%(refname:short)')
    for b in out.splitlines():
        if b.strip():
            yield 'lab-branch', b.strip(), 'branch not merged to %s' % cfg['trunk']


def worktrees(root):
    rc, out, _ = git(root, 'worktree', 'list', '--porcelain')
    cur, res = None, []
    for line in out.splitlines():
        if line.startswith('worktree '):
            cur = {'path': line[9:], 'branch': ''}
            res.append(cur)
        elif line.startswith('branch ') and cur:
            cur['branch'] = line[7:].replace('refs/heads/', '')
    return res


def src_dirty_trees(root, cfg, fast):
    if fast:
        return
    for w in worktrees(root)[1:]:                       # [0] is the main tree
        if not os.path.isdir(w['path']):
            continue
        rc, out, _ = git(w['path'], 'status', '--porcelain', '--untracked-files=no')
        if out:
            yield 'dirty-tree', w['branch'] or os.path.basename(w['path']), '%d uncommitted path(s) in %s' % (len(out.splitlines()), os.path.basename(w['path']))


def src_resume_lines(root, cfg, fast):
    for d in cfg['log_dirs']:
        for p in sorted(glob.glob(os.path.join(d, '*'))):
            if not os.path.isfile(p) or os.path.getsize(p) > 20_000_000:
                continue
            try:
                with open(p, errors='replace') as f:
                    for line in f:
                        if any(w in line for w in RESUME_WORDS):
                            yield 'resume-map', os.path.basename(p), line.strip()[:80]
                            break
            except OSError as e:
                sys.stderr.write('threads.py: sweep cannot read %s (%s)\n' % (p, e))


def src_patches(root, cfg, fast):
    for d in cfg['patch_dirs']:
        for p in sorted(glob.glob(os.path.join(root, d, '*.patch'))):
            yield 'patch', os.path.relpath(p, root), 'saved patch'


def src_briefs(root, cfg, fast):
    rc, log, _ = git(root, 'log', cfg['trunk'], '--format=%B')
    cutoff = time.time() - cfg['brief_days'] * 86400
    for d in cfg['brief_dirs']:
        for p in sorted(glob.glob(os.path.join(root, d, '*'))):
            name = os.path.basename(p)
            if os.path.getmtime(p) < cutoff or name in log:
                continue
            yield 'brief', os.path.relpath(p, root), 'no commit on %s names it' % cfg['trunk']


SOURCES = (src_lab_branches, src_dirty_trees, src_resume_lines, src_patches, src_briefs)


def keys_of(t):
    ks = [t['lab'], t['brief']] + t['maps'] + t['covers']
    return [k for k in ks if k]


def accounted(art, threads):
    kind, key, _ = art
    for t in threads.values():
        for k in keys_of(t):
            if k == key or fnmatch.fnmatch(key, k) or key.rstrip('/') == k.rstrip('/'):
                return t['id']
        if kind in ('lab-branch', 'dirty-tree') and (t['id'] == key):
            return t['id']
    return None


def sweep(root, cfg, threads, fast=False):
    orphans = []
    for src in SOURCES:
        for art in src(root, cfg, fast):
            if accounted(art, threads) is None:
                orphans.append(art)
    return orphans


# ---- verbs --------------------------------------------------------------------------------------

def parse(argv):
    """-> (verb, positional, valued, flags) or an exit status."""
    ledger = os.environ.get('THREADS_LEDGER')
    root = os.getcwd()
    a = list(argv)
    while a and a[0] in ('--ledger', '--root'):
        if len(a) < 2:
            return usage('%s needs a value' % a[0])
        if a[0] == '--ledger':
            ledger = a[1]
        else:
            root = a[1]
        a = a[2:]
    if not a or a[0] in ('-h', '--help'):
        return usage(code=0 if a else 2)
    verb = a.pop(0)
    if verb not in VERBS:
        return usage('unknown verb %r' % verb)
    valued_ok, flags_ok, npos = VERBS[verb]
    pos, val, flg = [], {}, set()
    while a:
        x = a.pop(0)
        if x in ('-h', '--help'):
            return usage(code=0)
        if x in flags_ok:
            flg.add(x)
        elif x in valued_ok:
            if not a:
                return usage('%s needs a value' % x)
            val[x] = a.pop(0)
        elif x.startswith('-'):
            return usage('unknown argument %r for %s' % (x, verb))
        else:
            pos.append(x)
    if len(pos) != npos:
        return usage('%s takes %d positional argument(s), got %r' % (verb, npos, pos))
    return verb, pos, val, flg, ledger, root


def split(s):
    return [x for x in (s or '').split(',') if x]


def report_scratch(mroot, ledger, verb, ident):
    """After pause and close: the project's scratch check (scratch.py), reported in this command's output.
    A failed check is printed, never fatal: the ledger event is already written."""
    try:
        import scratch
        lines, summary = scratch.check_report(mroot, ledger)
    except Exception as e:  # noqa: BLE001 -- reporting must not undo a recorded event
        sys.stderr.write('threads.py: scratch check unavailable (verb=%s id=%s phase=report: %s); route=skipped\n'
                         % (verb, ident, e))
        return
    for l in lines:
        print('  ' + l)
    print(summary)


def run(argv):
    p = parse(argv)
    if isinstance(p, int):
        return p
    verb, pos, val, flg, ledger, root = p
    mroot = main_root(root)
    ledger = ledger or os.path.join(mroot, 'threads', 'ledger.jsonl')
    ident = pos[0] if pos else ''
    th = fold(read(ledger))
    t = th.get(ident)
    if verb == 'open':
        for need in ('--title', '--goal', '--done'):
            if not val.get(need):
                return refuse(verb, ident, 'validate', 'missing-' + need[2:])
        words = len(val['--title'].split())
        if not 3 <= words <= 8:
            return refuse(verb, ident, 'validate', 'title-must-be-3-to-8-words', title=val['--title'], words=words)
        if t is not None:
            return refuse(verb, ident, 'validate', 'id-exists', status=t['status'])
        append(ledger, {'ts': now(), 'ev': 'open', 'id': ident, 'title': val['--title'], 'goal': val['--goal'],
                        'done': val['--done'], 'brief': base(val.get('--brief', '')), 'lab': base(val.get('--lab', '')),
                        'session': val.get('--session', '')})
        print('thread opened: %s  %s' % (ident, val['--title']))
        return 0
    if verb in ('checkpoint', 'pause', 'resume', 'close', 'show'):
        if t is None:
            if '--if-open' in flg:
                sys.stderr.write('threads.py: no thread %r; nothing recorded (verb=%s, --if-open)\n' % (ident, verb))
                return 0
            return refuse(verb, ident, 'lookup', 'unknown-thread', ledger=ledger)
        if verb != 'show' and t['status'] == 'closed':
            if '--if-open' in flg:
                sys.stderr.write('threads.py: thread %r already closed; nothing recorded (verb=%s)\n' % (ident, verb))
                return 0
            return refuse(verb, ident, 'lookup', 'thread-closed', outcome=t['outcome'])
    if verb == 'show':
        print(json.dumps(t, indent=1, sort_keys=True))
        return 0
    if verb == 'checkpoint':
        if not val.get('--state'):
            return refuse(verb, ident, 'validate', 'missing-state')
        append(ledger, {'ts': now(), 'ev': 'checkpoint', 'id': ident, 'state': val['--state'],
                        'commits': split(val.get('--commits')), 'maps': [base(m) for m in split(val.get('--map'))],
                        'covers': [base(c) for c in split(val.get('--covers'))]})
        print('checkpoint recorded: %s' % ident)
        return 0
    if verb == 'pause':
        for need in ('--reason', '--by', '--resume-when'):
            if not val.get(need):
                return refuse(verb, ident, 'validate', 'missing-' + need[2:])
        m = val.get('--map')
        maps = [base(m)] if m else []
        append(ledger, {'ts': now(), 'ev': 'pause', 'id': ident, 'reason': val['--reason'], 'by': val['--by'],
                        'resume_when': val['--resume-when'], 'maps': maps})
        print('thread paused: %s' % ident)
        print('resume map: %s' % (m if m else 'NONE RECORDED -- add one with `checkpoint %s --map LOGFILE`' % ident))
        print('resume when: %s' % val['--resume-when'])
        report_scratch(mroot, ledger, verb, ident)
        return 0
    if verb == 'resume':
        if t['status'] != 'paused':
            if '--if-open' in flg:
                return 0
            return refuse(verb, ident, 'state', 'not-paused', status=t['status'])
        append(ledger, {'ts': now(), 'ev': 'resume', 'id': ident, 'session': val.get('--session', '')})
        print('thread resumed: %s' % ident)
        return 0
    if verb == 'close':
        got = [k for k in OUTCOMES if val.get(k)]
        if len(got) != 1:
            return refuse(verb, ident, 'validate', 'exactly-one-outcome-required', given=got)
        k = got[0]
        outcome = {'kind': k[2:], 'value': val[k]}
        append(ledger, {'ts': now(), 'ev': 'close', 'id': ident, 'outcome': outcome})
        print('thread closed: %s  %s %s' % (ident, outcome['kind'], outcome['value']))
        report_scratch(mroot, ledger, verb, ident)
        return 0
    return status(mroot, th, ledger, '--table' in flg, '--fast' in flg)


def status(mroot, th, ledger, table, fast):
    live = [t for t in th.values() if t['status'] != 'closed']
    paused = [t for t in live if t['status'] == 'paused']
    cfg = load_cfg(mroot)
    orphans = sweep(mroot, cfg, th, fast)
    oldest = max((hours(t['opened']) for t in live), default=0)
    print('threads: %d open (%d paused, oldest %dh), %d orphans' % (len(live), len(paused), oldest, len(orphans)))
    if table:
        for t in sorted(live, key=lambda x: x['opened']):
            why = ('  [%s: %s; resume when %s]' % (t['paused']['by'], t['paused']['reason'], t['paused']['resume_when'])) if t['paused'] else ''
            print('  %-8s %-34s %5dh  %s%s' % (t['status'].upper(), t['id'][:34], hours(t['opened']), t['title'], why))
        for kind, key, note in orphans:
            print('  ORPHAN   %-12s %s  (%s)' % (kind, key, note))
    return 0


def main():
    return run(sys.argv[1:])


if __name__ == '__main__':
    sys.exit(main())
