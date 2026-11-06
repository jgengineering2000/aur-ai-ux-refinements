#!/usr/bin/env python3
"""scratch.py -- the link table of a project's run data: what exists, who holds it, and nothing else.

Layout: <root>/scratch is the main tree's level; <root>/labs/<lab>/scratch is a lab's. A level whose scratch holds a
MANIFEST is under management: one tab-separated row per name, ITEM (a path relative to <root>), HOLDER, WHAT. A name is a
link, like a hard link on a file system: an item exists while some manifest names it with a holder not proven dead (or
a process holds it open); a link lives in the manifest of the level that depends on the item. A level with no MANIFEST
is unmanaged and nothing in it is judged; `adopt --level` creates the MANIFEST, and nothing else does. A holder is
checkable, never free text:
  permanent        the item is kept until unlinked
  thread:ID        live while the thread is open or paused in the project's ledger (threads.py); dead once closed there
  lab:NAME         live while <root>/labs/NAME exists; dead once that directory is gone

usage: scratch.py [--root DIR] [--ledger FILE] VERB ...
  add PATH --what TEXT --holder HOLDER      name PATH (under a managed level's scratch) in that level's manifest
  link ITEM --level LEVEL --holder HOLDER [--what TEXT]
                                            name ITEM (any level's) in LEVEL's manifest; LEVEL is `main` or a lab name,
                                            and must be managed
  unlink ITEM --level LEVEL [--holder HOLDER]   drop that level's name(s) for ITEM
  check [--quiet]                           one note line per unmanaged level; then unnamed items of any age, dangling
                                            names, holders not live, unreadable manifests; exit 1 on any but the notes.
                                            A handoff names or removes what it reports
  clean [--dry-run]                         remove what is provably free, through unforced.remove_unforced (the receipt
                                            names each path first); report kept and freed. See below
  list                                      every name, then the total on disk in managed levels
  adopt                                     list unnamed content in every level, managed or not; writes and deletes nothing
  adopt --level LEVEL --holder HOLDER [--what TEXT]
                                            create LEVEL's MANIFEST if absent, which brings it under management, and name
                                            each unnamed, untracked item with HOLDER; deletes nothing
  release LAB                               a lab is closing: items it holds that another level names move to
                                            that level; its own manifest is dropped; refuses (exit 1) while a
                                            permanent, thread-held or unproven item would be lost, or a tracked one moved

A holder is checked when it is written: add, link and adopt refuse one that is not live (a lab with no directory, a
thread that is unknown or closed, any thread while the ledger cannot be read), name the token and why, and write nothing.

clean judges a level by its frontier, not its top level. Let N be every name in every manifest, H those whose holder is
live or not proven dead, D the rest. keeps(p): p is a tracked path, or a name in H is at or above p. A container is a
real directory (never a link) that keeps() does not hold and that has a name of N strictly below it or a tracked path in
it: it is descended and is never itself removed. Everything else is an item and is judged whole: a keeper stays, a name
in D at or above it frees it now, an unnamed one waits out the floor. So one live name deep in a tree keeps only itself,
and `check` reports every unnamed sibling beside it. An emptied container is an unnamed item the next time.

clean removes an item only if every name for it has a provably dead holder, or it has no name and was last touched 48 h
ago or more. It never touches:
  - a level with no MANIFEST, a scratch that is a symlink, or anything outside a managed level's scratch
  - a tracked path or an item holding one (git ls-files at that level's tree), named or not; nor anything in a level
    whose tree git cannot answer for
  - an item with a live name, or a holder not proven dead: an unknown thread, a malformed holder, a ledger missing or
    unreadable, a lab path that is not plainly absent. Dead means closed in a ledger read whole, or a directory gone
  - an unnamed item touched in the last 48 h (kept and reported; check still fails on it)
  - anything a process has open, runs in or runs from; anything write-protected (unforced.py)
Any manifest it cannot read stops it before anything is removed: nothing is provably unnamed without every name.

--root defaults to the main tree of the git repository containing the cwd; --ledger to <root>/threads/ledger.jsonl.
Any other argument: this text on stderr, exit 2, before any side effect. Every failure is logged with the verb,
identifier, values, phase and the route taken."""
import contextlib
import fcntl
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import unforced  # noqa: E402  the one deletion routine
import threads  # noqa: E402  the ledger

MANIFEST = 'MANIFEST'
MANIFEST_FILES = (MANIFEST, MANIFEST + '.tmp')
MAIN = 'main'
FLOOR_S = 48 * 3600  # an unnamed item touched more recently stays: a running job may have it closed for an instant
LIVE, DEAD, UNKNOWN = 'live', 'dead', 'unknown'


class Refuse(Exception):
    """Bad argument: help and exit 2."""


class Fail(Exception):
    """Failure with a logged route: exit 1."""


def now():
    return time.time()


def log(verb, ident, phase, why, route, **values):
    extra = ''.join(' %s=%s' % kv for kv in values.items())
    sys.stderr.write('scratch: %s %r [%s] %s%s; route=%s\n' % (verb, ident, phase, why, extra, route))


# ---- the project and its levels ------------------------------------------------------------------

def project_root(start=None):
    return threads.main_root(start or os.getcwd())


def levels(root):
    """level name -> scratch dir. `main` always; a lab for each labs/* directory."""
    out = {MAIN: os.path.join(root, 'scratch')}
    labs = os.path.join(root, 'labs')
    if os.path.isdir(labs):
        for n in sorted(os.listdir(labs)):
            if os.path.isdir(os.path.join(labs, n)):
                out[n] = os.path.join(labs, n, 'scratch')
    return out


def managed(d):
    """A level is under management when its scratch holds a MANIFEST (adopt creates it) and is not a link elsewhere."""
    return os.path.lexists(os.path.join(d, MANIFEST)) and not os.path.islink(d)


def unmanaged_levels(root):
    """The root-relative scratch of each level that has one on disk and is not under management."""
    return [os.path.relpath(d, root) for d in levels(root).values() if os.path.lexists(d) and not managed(d)]


def level_of(root, item):
    """The level whose scratch contains ITEM (a root-relative path), else None."""
    for name, d in levels(root).items():
        rel = os.path.relpath(d, root)
        if item == rel or item.startswith(rel + '/'):
            return name
    return None


def norm_item(root, item):
    p = os.path.normpath(os.path.join(root, item)) if not os.path.isabs(item) else os.path.normpath(item)
    rel = os.path.relpath(p, root)
    if rel.startswith('..'):
        raise Refuse('item %r is outside the project root' % item)
    for d in levels(root).values():
        lrel = os.path.relpath(d, root)
        if rel == lrel or rel in [lrel + '/' + f for f in MANIFEST_FILES]:
            raise Fail('%s is a level\'s scratch or its manifest, not an item; nothing was written' % rel)
    return rel


def clean_item(item):
    """A manifest row's ITEM must be what norm_item writes, a plain path under the root: a row that climbs out passes
    every prefix test by its spelling."""
    return bool(item) and not os.path.isabs(item) and os.path.normpath(item) == item and item.split('/')[0] != '..'


def scratch_home(root, item):
    """The managed level whose scratch ITEM lies strictly inside, physically (no symlink on the way out), and is not
    the manifest; else None. The only ground clean ever removes from."""
    for lv, d in levels(root).items():
        rel = os.path.relpath(d, root)
        if item.startswith(rel + '/') and managed(d):
            if item[len(rel) + 1:] in MANIFEST_FILES:
                return None
            real = os.path.realpath(d)
            parent = os.path.realpath(os.path.dirname(os.path.join(root, item)))
            return lv if parent == real or parent.startswith(real + '/') else None
    return None


def read_manifest(d):
    p = os.path.join(d, MANIFEST)
    if not os.path.lexists(p):
        return []
    rows = []
    try:
        with open(p) as fh:
            for line in fh:
                if line.strip():
                    f = line.rstrip('\n').split('\t')
                    if len(f) != 3:
                        raise Fail('%s has a row of %d fields, not the 3 of ITEM, HOLDER, WHAT (an older manifest '
                                   'format?); nothing was changed. Migrate it by hand first' % (p, len(f)))
                    if not clean_item(f[0]):
                        raise Fail('%s names %r, not a clean path relative to the project root; nothing was changed'
                                   % (p, f[0]))
                    rows.append(f)
    except (OSError, UnicodeDecodeError) as e:
        raise Fail('%s cannot be read (%s); nothing was changed' % (p, e))
    return rows


def write_manifest(d, rows):
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, MANIFEST + '.tmp')
    with open(tmp, 'w') as fh:
        fh.write(''.join('\t'.join(r) + '\n' for r in rows))
    os.replace(tmp, os.path.join(d, MANIFEST))


@contextlib.contextmanager
def level_lock(d):
    """Writers of one manifest take turns: two read-modify-writes at once lose a row, and a lost name is a later
    deletion. A flock on the directory itself, so it leaves nothing behind."""
    fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def all_names(root):
    """[(level, item, holder, what)] across every level."""
    return [(lv, r[0], r[1], r[2]) for lv, d in levels(root).items() for r in read_manifest(d)]


def readable_names(root):
    """([(level, item, holder, what)], [(level, why)]): for check, which reports what it can. Everything that would
    act on the names reads them with all_names, and an unreadable manifest stops it."""
    names, unreadable = [], []
    for lv, d in levels(root).items():
        try:
            names += [(lv, r[0], r[1], r[2]) for r in read_manifest(d)]
        except Fail as e:
            unreadable.append((lv, str(e)))
    return names, unreadable


# ---- holders -------------------------------------------------------------------------------------

def parse_holder(holder):
    """(kind, ident) of a well-formed holder, else None."""
    if holder == 'permanent':
        return 'permanent', ''
    kind, _, ident = holder.partition(':')
    if kind not in ('thread', 'lab') or not ident or ':' in ident or '\t' in ident:
        return None
    if kind == 'lab' and ('/' in ident or ident in ('.', '..')):
        return None
    return kind, ident


class Holders:
    """Judges holders against one strict read of the ledger: LIVE, DEAD or UNKNOWN, with why. Only DEAD frees: a thread
    closed in a ledger read whole, or a lab whose directory is plainly absent. A typo, a thread the ledger does not
    know, a ledger missing or unreadable: none of them proves anything, so they keep the item and check reports them."""

    def __init__(self, root, ledger):
        self.root, self.ledger = root, ledger
        self.threads, self.ledger_problem = None, None

    def read_ledger(self):
        if self.threads is None and self.ledger_problem is None:
            try:
                self.threads = threads.fold(threads.read_strict(self.ledger))
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
                self.ledger_problem = 'ledger %s cannot be read whole (%s)' % (self.ledger, e)
                log('holders', self.ledger, 'read ledger', self.ledger_problem, 'every thread holder is kept')

    def thread_state(self, ident):
        self.read_ledger()
        if self.ledger_problem:
            return UNKNOWN, 'thread %s: %s; kept' % (ident, self.ledger_problem)
        th = self.threads.get(ident)
        if th is None:
            return UNKNOWN, 'thread %s is not in the ledger; kept' % ident
        if th['status'] == 'closed':
            return DEAD, 'thread %s is closed' % ident
        if th['status'] in ('open', 'paused'):
            return LIVE, None
        return UNKNOWN, 'thread %s has status %r; kept' % (ident, th['status'])

    def lab_state(self, name):
        p = os.path.join(self.root, 'labs', name)
        try:
            os.lstat(p)
        except (FileNotFoundError, NotADirectoryError):
            return DEAD, 'lab %s does not exist' % name
        except OSError as e:
            return UNKNOWN, 'lab %s cannot be examined (%s); kept' % (name, e)
        if os.path.isdir(p):
            return LIVE, None
        return UNKNOWN, 'lab %s is on disk but no directory (a link to a drive not mounted?); kept' % name

    def state(self, holder):
        parsed = parse_holder(holder)
        if parsed is None:
            return UNKNOWN, 'holder %r is not permanent, thread:ID or lab:NAME; kept' % holder
        kind, ident = parsed
        if kind == 'permanent':
            return LIVE, None
        return self.thread_state(ident) if kind == 'thread' else self.lab_state(ident)


def holder_problem(root, ledger, holder):
    """None when HOLDER is live, else why not."""
    return Holders(root, ledger).state(holder)[1]


def require_live(root, ledger, holder):
    """A holder is checked when it is written: one not LIVE (a lab with no directory, a thread unknown, closed or
    unreadable) would be a deletion order at the next clean, with no floor. Refused before any side effect."""
    st, why = Holders(root, ledger).state(holder)
    if st != LIVE:
        raise Fail('holder %r refused: %s; nothing was written' % (holder, why))


def check_holder_arg(holder):
    if parse_holder(holder) is None:
        raise Refuse('holder %r: free text is refused; use permanent, thread:ID or lab:NAME' % holder)


# ---- what git holds ----------------------------------------------------------------------------------

class Tracked:
    """What git holds under each level's scratch, read once per level with `git ls-files` at that level's tree (its own
    index, for a worktree): such a path, or an item holding one, is git's custody and never a candidate. The GIT_*
    location variables are dropped, because inside a hook they would answer for another index, and answer empty. When git
    cannot answer for a tree nothing at that level is judged: `problem` says why."""

    def __init__(self, root):
        self.root = root
        self.held, self.files, self.problems = {}, {}, {}

    def tree(self, level):
        return self.root if level == MAIN else os.path.join(self.root, 'labs', level)

    def load(self, level):
        if level in self.held or level in self.problems:
            return
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        try:
            r = subprocess.run(['git', '-C', self.tree(level), 'ls-files', '-z', '--', 'scratch'],
                               capture_output=True, env=env)
        except OSError as e:
            self.problems[level] = str(e)
            log('tracked', level, 'git ls-files', str(e), 'the level is kept whole')
            return
        if r.returncode:
            self.problems[level] = 'git ls-files exit %d: %s' % (r.returncode, os.fsdecode(r.stderr).strip()[:200])
            log('tracked', level, 'git ls-files', self.problems[level], 'the level is kept whole')
            return
        held, files = set(), set()
        for raw in r.stdout.split(b'\0'):
            path = os.fsdecode(raw)
            if path:
                files.add(path)
            while path and path not in held:  # the path and every directory holding it
                held.add(path)
                path = os.path.dirname(path)
        self.held[level], self.files[level] = held, files

    def problem(self, level):
        self.load(level)
        return self.problems.get(level)

    def holds(self, level, item):
        """ITEM (root-relative) is tracked at LEVEL's tree, or holds a tracked path."""
        self.load(level)
        return os.path.relpath(os.path.join(self.root, item), self.tree(level)) in self.held.get(level, ())

    def is_path(self, level, item):
        """ITEM (root-relative) is itself a tracked path."""
        self.load(level)
        return os.path.relpath(os.path.join(self.root, item), self.tree(level)) in self.files.get(level, ())

    def below(self, level, item):
        """ITEM is not itself tracked but holds a tracked path."""
        return self.holds(level, item) and not self.is_path(level, item)


# ---- the relation ----------------------------------------------------------------------------------

def related(named, on_disk):
    """A name keeps an item when it is that item, lies under it, or the item lies under it."""
    return named == on_disk or named.startswith(on_disk + '/') or on_disk.startswith(named + '/')


def covers(named, item):
    """A name at or above ITEM speaks for all of it; a name below speaks only for itself."""
    return named == item or item.startswith(named + '/')


def listing(d):
    try:
        return sorted(os.listdir(d))
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError as e:
        raise Fail('%s cannot be listed (%s); nothing was changed' % (d, e))


def entries(d, rel):
    """Root-relative paths of the top level of a level's scratch D (REL), its manifest aside: what `release` moves."""
    return [rel + '/' + n for n in listing(d) if n not in MANIFEST_FILES]


def default_ledger(root):
    return os.path.join(root, 'threads', 'ledger.jsonl')


def frontier(root, level, names, holders, tracked):
    """(items, containers): root-relative paths. Walks down from LEVEL's scratch, its manifest files aside. With N every
    name in NAMES and H those whose holder is live or unknown: keeps(p) is a tracked path, or a name in H at or above p;
    a container is a real directory (never a link) that keeps(p) does not hold, with a name strictly below it or a
    tracked path in it. A container is descended and is never an item; everything else is an item and is not descended.
    The walk follows the names and tracked paths, so its size is theirs, never the tree's."""
    d = levels(root)[level]
    rel = os.path.relpath(d, root)
    state = {}
    upper = set()  # every directory with a name strictly below it
    kept_by = []
    for _, item, holder, _ in names:
        if holder not in state:
            state[holder] = holders.state(holder)[0]
        if state[holder] != DEAD:
            kept_by.append(item)
        p = os.path.dirname(item)
        while p and p not in upper:
            upper.add(p)
            p = os.path.dirname(p)
    kept_by = set(kept_by)

    def keeps(p):
        if tracked.is_path(level, p):
            return True
        q = p
        while q:
            if q in kept_by:
                return True
            q = os.path.dirname(q)
        return False

    items, containers = [], []

    def walk(dirpath, drel, top):
        for n in listing(dirpath):
            if top and n in MANIFEST_FILES:
                continue
            p = drel + '/' + n
            full = os.path.join(dirpath, n)
            if (not os.path.islink(full) and os.path.isdir(full) and not keeps(p)
                    and (p in upper or tracked.below(level, p))):
                containers.append(p)
                walk(full, p, False)
            else:
                items.append(p)

    walk(d, rel, True)
    return items, containers


def disk_items(root, names, holders, tracked):
    """What the managed levels hold on disk, by frontier: (items, containers)."""
    items, containers = [], []
    for lv, d in levels(root).items():
        if managed(d):
            i, c = frontier(root, lv, names, holders, tracked)
            items += i
            containers += c
    return items, containers


def check(root, ledger):
    names, unreadable = readable_names(root)
    holders, tracked = Holders(root, ledger), Tracked(root)
    orphans, git_unknown = [], []
    skip = {lv for lv, _ in unreadable}
    for lv, d in levels(root).items():
        if lv in skip or not managed(d):
            continue
        if tracked.problem(lv):
            git_unknown.append((lv, tracked.problem(lv)))
        for i in frontier(root, lv, names, holders, tracked)[0]:
            if not tracked.holds(lv, i) and not any(related(n[1], i) for n in names):
                orphans.append(i)
    dangling = [(lv, it, h) for lv, it, h, _ in names if not os.path.lexists(os.path.join(root, it))]
    bad = []
    for lv, it, h, _ in names:
        why = holders.state(h)[1]
        if why:
            bad.append((lv, it, h, why))
    return {'unmanaged': unmanaged_levels(root), 'unreadable': unreadable, 'git_unknown': git_unknown,
            'orphans': orphans, 'dangling': dangling, 'bad_holders': bad}


def note_lines(rep):
    """What check says of a level it does not judge: one line each, not a failure."""
    return ['UNMANAGED (no MANIFEST; clean never touches it, `adopt --level` brings it in): %s' % d
            for d in rep.get('unmanaged', ())]


def check_lines(rep):
    """One line for each thing a handoff has to settle."""
    out = ['UNREADABLE MANIFEST: level %s: %s' % u for u in rep.get('unreadable', ())]
    out += ['GIT CANNOT ANSWER: level %s: %s; nothing there is removed' % g for g in rep.get('git_unknown', ())]
    out += ['ORPHAN (on disk, unnamed): %s' % i for i in rep.get('orphans', ())]
    out += ['DANGLING (named, not on disk): %s names %s (%s)' % d for d in rep.get('dangling', ())]
    out += ['BAD HOLDER: %s names %s held by %s: %s' % b for b in rep.get('bad_holders', ())]
    return out


def summary_line(problems):
    if not problems:
        return 'scratch check: clean'
    return 'scratch check: %d problem(s) (name them, or run scratch.py clean to free what is provably free)' % len(problems)


def check_report(root=None, ledger=None):
    """For the tools that call check: ([note and problem lines], summary). Never raises on a missing project."""
    try:
        root = root or project_root()
        rep = check(root, ledger or os.path.join(root, 'threads', 'ledger.jsonl'))
    except (SystemExit, Exception) as e:  # noqa: BLE001 -- a hook must report, not die
        return ['scratch check could not run: %s' % e], 'scratch check: could not run'
    problems = check_lines(rep)
    return note_lines(rep) + problems, summary_line(problems)


# ---- verbs ---------------------------------------------------------------------------------------

def size_of(path):
    def one(p):
        try:
            return os.lstat(p).st_size
        except OSError:  # vanished mid-walk: it is not there to count
            return 0
    if os.path.islink(path) or not os.path.isdir(path):
        return one(path)
    return sum(one(os.path.join(r, f)) for r, _, fs in os.walk(path) for f in fs)


def set_name(root, level, item, holder, what):
    d = levels(root).get(level)
    if d is None:
        raise Fail('level %r is not a level of this project (main or a directory under labs/)' % level)
    if not managed(d):
        raise Fail('level %s is unmanaged (no MANIFEST in %s, or a symlink): `adopt --level %s --holder HOLDER` brings '
                   'it under management, naming what it holds. Nothing was written' % (level, d, level))
    with level_lock(d):
        rows = [r for r in read_manifest(d) if not (r[0] == item and r[1] == holder)]
        rows.append([item, holder, (what or '').replace('\t', ' ').replace('\n', ' ')])
        write_manifest(d, rows)


def add(root, path, what, holder):
    item = norm_item(root, path)
    level = level_of(root, item)
    if level is None:
        raise Fail('%s is under no level\'s scratch' % item)
    if not os.path.lexists(os.path.join(root, item)):
        raise Fail('%s does not exist' % item)
    set_name(root, level, item, holder, what)


def link(root, item, level, holder, what):
    item = norm_item(root, item)
    if not os.path.lexists(os.path.join(root, item)):
        raise Fail('%s does not exist' % item)
    set_name(root, level, item, holder, what)


def unlink(root, item, level, holder):
    item = norm_item(root, item)
    d = levels(root).get(level)
    if d is None:
        raise Fail('level %r is not a level of this project' % level)
    if not os.path.isdir(d):
        raise Fail('level %s has no name for %s: it has no scratch' % (level, item))
    with level_lock(d):
        rows = read_manifest(d)
        keep = [r for r in rows if not (r[0] == item and (holder is None or r[1] == holder))]
        if len(keep) == len(rows):
            raise Fail('level %s has no name for %s%s' % (level, item, '' if holder is None else ' held by ' + holder))
        write_manifest(d, keep)


def drop_names(root, item):
    """Item is gone: drop the names at or under it. A name above it speaks for what is still there."""
    for d in levels(root).values():
        if not os.path.isdir(d):
            continue
        with level_lock(d):
            rows = read_manifest(d)
            keep = [r for r in rows if not (r[0] == item or r[0].startswith(item + '/'))]
            if len(keep) != len(rows):
                write_manifest(d, keep)


def verdict(root, c, names, holders, tracked):
    """Why C stays, or None when it is free to go: the one judgement clean makes, cheapest and most absolute first."""
    level = scratch_home(root, c)
    if level is None:
        return 'not inside a managed level\'s scratch; clean never touches it'
    if tracked.problem(level):
        return 'git cannot say what is tracked at level %s (%s)' % (level, tracked.problem(level))
    if tracked.holds(level, c):
        return 'tracked, or holds a tracked path'
    named = [n for n in names if related(n[1], c)]
    judged = [(n, holders.state(n[2])) for n in named]
    live = [n for n, (st, _) in judged if st == LIVE]
    if live:
        return 'named by ' + ', '.join(sorted({'%s/%s' % (n[0], n[2]) for n in live}))
    unproven = [why for _, (st, why) in judged if st == UNKNOWN]
    if unproven:
        return 'holder not proven dead: ' + unproven[0]
    full = os.path.join(root, c)
    if not any(covers(n[1], c) for n in named):
        age = now() - unforced.newest_touch(full)
        if age < FLOOR_S:
            return 'unnamed, touched %.1f h ago, under the %d h floor; name or remove it' % (age / 3600, FLOOR_S // 3600)
    pids = unforced.processes_under(full)
    if pids:
        return 'open in pid ' + ','.join(map(str, pids))
    return None


def clean(root, ledger, dry_run=False):
    """{'freed', 'kept': [(item, why)], 'removed': [item], 'errors': [...], 'untouched': [unmanaged scratch]}.
    Shallowest first, so a removed directory takes what is under it, and the names are read again before each judgement:
    a name written a moment ago holds. Fail, before anything is removed, on a manifest it cannot read."""
    names = all_names(root)
    holders, tracked = Holders(root, ledger), Tracked(root)
    rep = {'freed': 0, 'kept': [], 'removed': [], 'errors': [], 'untouched': unmanaged_levels(root)}
    items, containers = disk_items(root, names, holders, tracked)
    cands = set(items) | {n[1] for n in names if os.path.lexists(os.path.join(root, n[1])) and n[1] not in containers}
    for c in sorted(cands, key=lambda i: (i.count('/'), i)):
        full = os.path.join(root, c)
        if not os.path.lexists(full) or any(c.startswith(r + '/') for r in rep['removed']):
            continue
        try:
            why = verdict(root, c, all_names(root), holders, tracked)
        except OSError as e:  # the scan of /proc: "nobody holds it" needs the scan to have happened
            log('clean', c, 'judge', 'no answer on who holds it: %s' % e, 'kept, names stay')
            rep['kept'].append((c, 'not removed: no answer on who holds it (%s)' % e))
            rep['errors'].append(str(e))
            continue
        if why:
            rep['kept'].append((c, why))
            continue
        before = size_of(full)
        try:
            r = unforced.remove_unforced(full, dry_run=dry_run)
        except OSError as e:  # the receipt could not be written, so nothing was removed
            log('clean', c, 'remove', str(e), 'kept, names stay')
            rep['kept'].append((c, 'not removed: %s' % e))
            rep['errors'].append(str(e))
            continue
        if r.errors or r.kept or r.vetoed or not r.gone:
            log('clean', c, 'remove', 'not removed', 'kept, names stay', errors=r.errors, kept=r.kept)
            rep['kept'].append((c, 'not removable (write-protected or error)'))
            rep['errors'] += r.errors
            continue
        rep['freed'] += before
        rep['removed'].append(c)
        if not dry_run:
            drop_names(root, c)
    return rep


def adopt(root, level, holder, what, ledger=None):
    """Bring LEVEL under management: create its MANIFEST if absent, and name each unnamed, untracked item with HOLDER.
    Deletes nothing. Returns (named [item], tracked [item])."""
    d = levels(root).get(level)
    if d is None:
        raise Fail('level %r is not a level of this project (main or a directory under labs/)' % level)
    if os.path.islink(d):
        raise Fail('%s is a symlink: what it holds belongs elsewhere, so it cannot be a level\'s' % d)
    tracked = Tracked(root)
    if tracked.problem(level):
        raise Fail('git cannot say what is tracked at level %s (%s); nothing was written' % (level, tracked.problem(level)))
    os.makedirs(d, exist_ok=True)
    rel = os.path.relpath(d, root)
    with level_lock(d):
        names = all_names(root)
        items = frontier(root, level, names, Holders(root, ledger or default_ledger(root)), tracked)[0]
        held = [i for i in items if tracked.holds(level, i)]
        new = [i for i in items if i not in held and not any(related(n[1], i) for n in names)]
        write_manifest(d, read_manifest(d) + [[i, holder, what.replace('\t', ' ').replace('\n', ' ')] for i in new])
    return new, held


def adopt_listing(root, ledger=None):
    """[(item, bytes)] unnamed in any level, managed or not, tracked paths aside."""
    names, tracked, out = all_names(root), Tracked(root), []
    holders = Holders(root, ledger or default_ledger(root))
    for lv, d in levels(root).items():
        for i in frontier(root, lv, names, holders, tracked)[0]:
            if not tracked.holds(lv, i) and not any(related(n[1], i) for n in names):
                out.append((i, size_of(os.path.join(root, i))))
    return out


def release(root, ledger, lab):
    """A lab is closing. Returns (moved [(old, new)], blockers [str]); moves only when no blocker. Reads every entry of
    the lab's scratch, managed or not: another level's name can point there either way."""
    lv = levels(root)
    if lab not in lv or lab == MAIN:
        raise Fail('%r is not a lab of this project' % lab)
    lab_rel = os.path.relpath(lv[lab], root)
    names = all_names(root)
    holders, tracked = Holders(root, ledger), Tracked(root)
    plan, blockers = [], []
    for item in entries(lv[lab], lab_rel):
        live = [n for n in names if related(n[1], item) and holders.state(n[2])[0] != DEAD
                and not (n[2] == 'lab:' + lab)]
        elsewhere = [n for n in live if n[0] != lab]
        if tracked.holds(lab, item):
            if elsewhere:
                blockers.append('%s is tracked in the lab and named by level %s; moving it would dirty the tree: point '
                                'that name elsewhere first' % (item, elsewhere[0][0]))
        elif elsewhere:
            plan.append((item, elsewhere[0][0]))
        elif live:
            blockers.append('%s is held (%s) only by the lab\'s own manifest; link it from another level first' %
                            (item, live[0][2]))
    if blockers:
        return [], blockers
    moved = []
    for item, dest_level in plan:
        dest = os.path.join(lv[dest_level], os.path.basename(item))
        if os.path.lexists(dest):
            return moved, ['%s: %s already exists in level %s' % (item, os.path.relpath(dest, root), dest_level)]
        try:
            os.rename(os.path.join(root, item), dest)
        except OSError as e:
            return moved, ['%s: could not move to level %s (%s)' % (item, dest_level, e)]
        new = os.path.relpath(dest, root)
        for d in lv.values():
            if not os.path.isdir(d):
                continue
            with level_lock(d):
                rows = read_manifest(d)
                if any(r[0] == item or r[0].startswith(item + '/') for r in rows):
                    write_manifest(d, [[new + r[0][len(item):] if (r[0] == item or r[0].startswith(item + '/')) else r[0], r[1], r[2]]
                                       for r in rows])
        moved.append((item, new))
    own = lv[lab]
    if os.path.lexists(os.path.join(own, MANIFEST)):
        with level_lock(own):
            write_manifest(own, [])
    return moved, []


# ---- command line --------------------------------------------------------------------------------

def parse_opts(args, spec, positional):
    """spec: {flag: takes_value}; returns (positionals, opts); Refuse on anything else."""
    pos, opts, i = [], {}, 0
    while i < len(args):
        a = args[i]
        if a in spec:
            if spec[a]:
                if i + 1 >= len(args):
                    raise Refuse('%s needs a value' % a)
                opts[a] = args[i + 1]
                i += 2
            else:
                opts[a] = True
                i += 1
        elif a.startswith('-'):
            raise Refuse('unknown argument %r' % a)
        else:
            pos.append(a)
            i += 1
    if len(pos) != positional:
        raise Refuse('expected %d positional argument(s), got %d' % (positional, len(pos)))
    return pos, opts


def run(argv):
    args, root, ledger = list(argv), None, None
    while args[:1] in (['--root'], ['--ledger']):
        if len(args) < 2:
            raise Refuse('%s needs a value' % args[0])
        if args[0] == '--root':
            root = os.path.abspath(args[1])
        else:
            ledger = os.path.abspath(args[1])
        args = args[2:]
    if not args:
        raise Refuse('no verb')
    verb, rest = args[0], args[1:]
    specs = {'add': ({'--what': 1, '--holder': 1}, 1), 'link': ({'--level': 1, '--holder': 1, '--what': 1}, 1),
             'unlink': ({'--level': 1, '--holder': 1}, 1), 'check': ({'--quiet': 0}, 0),
             'clean': ({'--dry-run': 0}, 0), 'list': ({}, 0), 'adopt': ({'--level': 1, '--holder': 1, '--what': 1}, 0),
             'release': ({}, 1)}
    if verb not in specs:
        raise Refuse('unknown verb %r' % verb)
    pos, o = parse_opts(rest, specs[verb][0], specs[verb][1])
    if verb == 'add' and ('--what' not in o or '--holder' not in o):
        raise Refuse('add needs --what and --holder')
    if verb == 'link' and ('--level' not in o or '--holder' not in o):
        raise Refuse('link needs --level and --holder')
    if verb == 'unlink' and '--level' not in o:
        raise Refuse('unlink needs --level')
    if verb == 'adopt' and (('--level' in o) != ('--holder' in o) or ('--what' in o and '--level' not in o)):
        raise Refuse('adopt alone lists; to write names it needs --level and --holder (and --what only with them)')
    if '--holder' in o and verb != 'unlink':
        check_holder_arg(o['--holder'])
    root = root or project_root()
    ledger = ledger or default_ledger(root)
    try:
        if verb in ('add', 'link') or (verb == 'adopt' and '--level' in o):
            require_live(root, ledger, o['--holder'])
        if verb == 'add':
            add(root, pos[0], o['--what'], o['--holder'])
        elif verb == 'link':
            link(root, pos[0], o['--level'], o['--holder'], o.get('--what', ''))
        elif verb == 'unlink':
            unlink(root, pos[0], o['--level'], o.get('--holder'))
        elif verb == 'check':
            rep = check(root, ledger)
            for lv, why in rep['unreadable']:
                log('check', lv, 'read manifest', why, 'the level is not judged; exit 1')
            problems = check_lines(rep)
            if not o.get('--quiet'):
                for l in note_lines(rep):
                    print(l)
            for l in problems:
                print(l)
            if not o.get('--quiet') or problems:
                print(summary_line(problems))
            return 1 if problems else 0
        elif verb == 'clean':
            rep = clean(root, ledger, bool(o.get('--dry-run')))
            print('%s %d bytes; removed %d: %s' % ('would free' if o.get('--dry-run') else 'freed', rep['freed'],
                                                  len(rep['removed']), ', '.join(rep['removed']) or '-'))
            for item, why in rep['kept']:
                print('kept %s: %s' % (item, why))
            for d in rep['untouched']:
                print('not touched (unmanaged, no MANIFEST): %s' % d)
            return 1 if rep['errors'] else 0
        elif verb == 'list':
            for lv, it, h, w in all_names(root):
                print('\t'.join((lv, it, h, w)))
            listed = disk_items(root, all_names(root), Holders(root, ledger), Tracked(root))[0]
            print('total on disk in managed levels: %d bytes' % sum(size_of(os.path.join(root, i)) for i in listed))
            for d in unmanaged_levels(root):
                print('not counted (unmanaged, no MANIFEST): %s' % d)
        elif verb == 'adopt':
            if '--level' in o:
                new, held = adopt(root, o['--level'], o['--holder'], o.get('--what', 'adopted'), ledger)
                print('level %s is under management: named %d item(s) with %s, skipped %d tracked; deleted nothing'
                      % (o['--level'], len(new), o['--holder'], len(held)))
            else:
                for item, size in adopt_listing(root, ledger):
                    print('%s\t%d bytes\tunnamed' % (item, size))
                for d in unmanaged_levels(root):
                    print('%s\tunmanaged\t`adopt --level LEVEL --holder HOLDER` brings it in' % d)
        elif verb == 'release':
            moved, blockers = release(root, ledger, pos[0])
            for old, new in moved:
                print('moved %s -> %s' % (old, new))
            for b in blockers:
                log('release', pos[0], 'plan', b, 'refused, nothing lost')
            return 1 if blockers else 0
    except Fail as e:
        log(verb, pos[0] if pos else '', 'run', str(e), 'exit 1')
        return 1
    return 0


def main(argv):
    try:
        return run(argv)
    except Refuse as e:
        sys.stderr.write(__doc__ + '\nscratch: %s\n' % e)
        return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
