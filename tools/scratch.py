#!/usr/bin/env python3
"""scratch.py -- the link table of a project's run data: what exists, who holds it, and nothing else.

Layout: <root>/scratch is the main tree's level; <root>/labs/<lab>/scratch is a lab's. Each scratch holds a MANIFEST,
one tab-separated row per name: ITEM (a path relative to <root>), HOLDER, WHAT. A name is a link, like a hard link on a
file system: an item exists while some manifest names it with a live holder (or a process holds it open); a link lives
in the manifest of the level that depends on the item. A holder is checkable, never free text:
  permanent        the item is kept until unlinked
  thread:ID        live while the thread is open or paused in the project's ledger (threads.py)
  lab:NAME         live while <root>/labs/NAME exists

usage: scratch.py [--root DIR] [--ledger FILE] VERB ...
  add PATH --what TEXT --holder HOLDER      name PATH (under some level's scratch) in that level's manifest
  link ITEM --level LEVEL --holder HOLDER [--what TEXT]
                                            name ITEM (any level's) in LEVEL's manifest; LEVEL is `main` or a lab name
  unlink ITEM --level LEVEL [--holder HOLDER]   drop that level's name(s) for ITEM
  check [--quiet]                           orphans (on disk, unnamed), dangling names, bad holders; exit 1 if any
  clean [--dry-run]                         remove every item with no live name and no process holding it open
                                            (orphans too), through unforced.remove_unforced; report kept and freed
  list                                      every name, then the total on disk
  adopt                                     list content on disk that no manifest names; deletes nothing
  release LAB                               a lab is closing: items it holds that another level names move to
                                            that level; its own manifest is dropped; refuses (exit 1) while a
                                            permanent or thread-held item would be lost
--root defaults to the main tree of the git repository containing the cwd; --ledger to <root>/threads/ledger.jsonl.
Any other argument: this text on stderr, exit 2, before any side effect. Every failure is logged with the verb,
identifier, values, phase and the route taken."""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import unforced  # noqa: E402  the one deletion routine
import threads  # noqa: E402  the ledger

MANIFEST = 'MANIFEST'
MAIN = 'main'


class Refuse(Exception):
    """Bad argument: help and exit 2."""


class Fail(Exception):
    """Failure with a logged route: exit 1."""


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
    return rel


def read_manifest(d):
    p = os.path.join(d, MANIFEST)
    if not os.path.exists(p):
        return []
    rows = []
    with open(p) as fh:
        for line in fh:
            if line.strip():
                f = line.rstrip('\n').split('\t')
                if len(f) != 3:
                    raise Fail('%s has a row of %d fields, not the 3 of ITEM, HOLDER, WHAT (an older manifest '
                               'format?); nothing was changed. Migrate it by hand first' % (p, len(f)))
                rows.append(f)
    return rows


def write_manifest(d, rows):
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, MANIFEST + '.tmp')
    with open(tmp, 'w') as fh:
        fh.write(''.join('\t'.join(r) + '\n' for r in rows))
    os.replace(tmp, os.path.join(d, MANIFEST))


def all_names(root):
    """[(level, item, holder, what)] across every level."""
    return [(lv, r[0], r[1], r[2]) for lv, d in levels(root).items() for r in read_manifest(d)]


# ---- holders -------------------------------------------------------------------------------------

def holder_problem(root, ledger, holder):
    """None when HOLDER is a live, checkable holder, else why not."""
    kind, _, ident = holder.partition(':')
    if holder == 'permanent':
        return None
    if kind == 'thread' and ident:
        th = threads.fold(threads.read(ledger)).get(ident)
        if th is None:
            return 'thread %s is not in the ledger' % ident
        return None if th['status'] in ('open', 'paused') else 'thread %s is %s' % (ident, th['status'])
    if kind == 'lab' and ident:
        return None if os.path.isdir(os.path.join(root, 'labs', ident)) else 'lab %s does not exist' % ident
    return 'holder %r is not permanent, thread:ID or lab:NAME' % holder


def check_holder_arg(holder):
    kind, _, ident = holder.partition(':')
    if holder != 'permanent' and not (kind in ('thread', 'lab') and ident and ':' not in ident and '\t' not in ident):
        raise Refuse('holder %r: free text is refused; use permanent, thread:ID or lab:NAME' % holder)


# ---- the relation ----------------------------------------------------------------------------------

def related(named, on_disk):
    """A name keeps an item when it is that item, lies under it, or the item lies under it."""
    return named == on_disk or named.startswith(on_disk + '/') or on_disk.startswith(named + '/')


def disk_items(root):
    out = []
    for lv, d in levels(root).items():
        if os.path.isdir(d):
            rel = os.path.relpath(d, root)
            out += [rel + '/' + n for n in sorted(os.listdir(d)) if n not in (MANIFEST, MANIFEST + '.tmp')]
    return out


def check(root, ledger):
    names = all_names(root)
    orphans = [i for i in disk_items(root) if not any(related(n[1], i) for n in names)]
    dangling = [(lv, it, h) for lv, it, h, _ in names if not os.path.lexists(os.path.join(root, it))]
    bad = []
    for lv, it, h, _ in names:
        why = holder_problem(root, ledger, h)
        if why:
            bad.append((lv, it, h, why))
    return {'orphans': orphans, 'dangling': dangling, 'bad_holders': bad}


def check_lines(rep):
    out = ['ORPHAN (on disk, unnamed): %s' % i for i in rep['orphans']]
    out += ['DANGLING (named, not on disk): %s names %s (%s)' % (lv, it, h) for lv, it, h in rep['dangling']]
    out += ['BAD HOLDER: %s names %s held by %s: %s' % b for b in rep['bad_holders']]
    return out


def check_report(root=None, ledger=None):
    """For the tools that call check: ([problem lines], summary). Never raises on a missing project."""
    try:
        root = root or project_root()
        rep = check(root, ledger or os.path.join(root, 'threads', 'ledger.jsonl'))
    except (SystemExit, Exception) as e:  # noqa: BLE001 -- a hook must report, not die
        return ['scratch check could not run: %s' % e], 'scratch check: could not run'
    lines = check_lines(rep)
    return lines, ('scratch check: clean' if not lines else 'scratch check: %d problem(s)' % len(lines))


# ---- verbs ---------------------------------------------------------------------------------------

def size_of(path):
    if os.path.islink(path) or os.path.isfile(path):
        return os.lstat(path).st_size
    return sum(os.lstat(os.path.join(r, f)).st_size for r, _, fs in os.walk(path) for f in fs)


def set_name(root, level, item, holder, what):
    d = levels(root).get(level)
    if d is None:
        raise Fail('level %r is not a level of this project (main or a directory under labs/)' % level)
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
    rows = read_manifest(d)
    keep = [r for r in rows if not (r[0] == item and (holder is None or r[1] == holder))]
    if len(keep) == len(rows):
        raise Fail('level %s has no name for %s%s' % (level, item, '' if holder is None else ' held by ' + holder))
    write_manifest(d, keep)


def drop_names(root, item):
    for lv, d in levels(root).items():
        rows = read_manifest(d)
        keep = [r for r in rows if not related(r[0], item)]
        if len(keep) != len(rows):
            write_manifest(d, keep)


def files_under(path):
    if os.path.isdir(path) and not os.path.islink(path):
        return [os.path.join(r, f) for r, _, fs in os.walk(path) for f in fs]
    return [path]


def clean(root, ledger, dry_run=False):
    """{'freed', 'kept': [(item, why)], 'removed': [item], 'errors': [...]}"""
    names = all_names(root)
    cands = sorted({n[1] for n in names if os.path.lexists(os.path.join(root, n[1]))} | set(disk_items(root)))
    live = {}
    for c in cands:
        live[c] = [n for n in names if related(n[1], c) and holder_problem(root, ledger, n[2]) is None]
    open_now = unforced.open_holders([f for c in cands if not live[c] for f in files_under(os.path.join(root, c))])
    rep = {'freed': 0, 'kept': [], 'removed': [], 'errors': []}
    for c in cands:
        full = os.path.join(root, c)
        if not os.path.lexists(full):
            continue
        if live[c]:
            rep['kept'].append((c, 'named by ' + ', '.join(sorted({'%s/%s' % (n[0], n[2]) for n in live[c]}))))
            continue
        held = sorted({p for f in files_under(full) for p in open_now.get(os.path.realpath(f), [])})
        if held:
            rep['kept'].append((c, 'open in pid ' + ','.join(map(str, held))))
            continue
        before = size_of(full) if os.path.lexists(full) else 0
        r = unforced.remove_unforced(full, dry_run=dry_run)
        if r.errors or r.kept or r.vetoed or not r.gone:
            log('clean', c, 'remove', 'not removed', 'kept, names stay', errors=r.errors, kept=r.kept)
            rep['kept'].append((c, 'not removable (write-protected or error)'))
            rep['errors'] += r.errors
            continue
        rep['freed'] += before
        rep['removed'].append(c)
        if not dry_run:
            drop_names(root, c)
            lvl_dir = os.path.join(root, os.path.relpath(levels(root).get(level_of(root, c) or MAIN, ''), root))
            up = os.path.dirname(full)
            while os.path.abspath(up) != os.path.abspath(lvl_dir) and os.path.isdir(up) and not os.listdir(up):
                unforced.remove_unforced(up)
                up = os.path.dirname(up)
    return rep


def release(root, ledger, lab):
    """A lab is closing. Returns (moved [(old, new)], blockers [str]); moves only when no blocker."""
    lv = levels(root)
    if lab not in lv or lab == MAIN:
        raise Fail('%r is not a lab of this project' % lab)
    lab_rel = os.path.relpath(lv[lab], root)
    names = all_names(root)
    plan, blockers = [], []
    for item in disk_items(root):
        if not (item == lab_rel or item.startswith(lab_rel + '/')):
            continue
        live = [n for n in names if related(n[1], item) and holder_problem(root, ledger, n[2]) is None
                and not (n[2] == 'lab:' + lab)]
        elsewhere = [n for n in live if n[0] != lab]
        if elsewhere:
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
        os.makedirs(lv[dest_level], exist_ok=True)
        os.rename(os.path.join(root, item), dest)
        new = os.path.relpath(dest, root)
        for lvn, d in lv.items():
            rows = read_manifest(d)
            if any(r[0] == item or r[0].startswith(item + '/') for r in rows):
                write_manifest(d, [[new + r[0][len(item):] if (r[0] == item or r[0].startswith(item + '/')) else r[0], r[1], r[2]]
                                   for r in rows])
        moved.append((item, new))
    own = lv[lab]
    if os.path.exists(os.path.join(own, MANIFEST)):
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
             'clean': ({'--dry-run': 0}, 0), 'list': ({}, 0), 'adopt': ({}, 0), 'release': ({}, 1)}
    if verb not in specs:
        raise Refuse('unknown verb %r' % verb)
    pos, o = parse_opts(rest, specs[verb][0], specs[verb][1])
    if verb == 'add' and ('--what' not in o or '--holder' not in o):
        raise Refuse('add needs --what and --holder')
    if verb == 'link' and ('--level' not in o or '--holder' not in o):
        raise Refuse('link needs --level and --holder')
    if verb == 'unlink' and '--level' not in o:
        raise Refuse('unlink needs --level')
    if '--holder' in o and verb != 'unlink':
        check_holder_arg(o['--holder'])
    root = root or project_root()
    ledger = ledger or os.path.join(root, 'threads', 'ledger.jsonl')
    try:
        if verb == 'add':
            add(root, pos[0], o['--what'], o['--holder'])
        elif verb == 'link':
            link(root, pos[0], o['--level'], o['--holder'], o.get('--what', ''))
        elif verb == 'unlink':
            unlink(root, pos[0], o['--level'], o.get('--holder'))
        elif verb == 'check':
            lines = check_lines(check(root, ledger))
            for l in lines:
                print(l)
            if not o.get('--quiet') or lines:
                print('scratch check: %s' % ('clean' if not lines else '%d problem(s)' % len(lines)))
            return 1 if lines else 0
        elif verb == 'clean':
            rep = clean(root, ledger, bool(o.get('--dry-run')))
            print('%s %d bytes; removed %d: %s' % ('would free' if o.get('--dry-run') else 'freed', rep['freed'],
                                                  len(rep['removed']), ', '.join(rep['removed']) or '-'))
            for item, why in rep['kept']:
                print('kept %s: %s' % (item, why))
            return 1 if rep['errors'] else 0
        elif verb == 'list':
            for lv, it, h, w in all_names(root):
                print('\t'.join((lv, it, h, w)))
            print('total on disk: %d bytes' % sum(size_of(os.path.join(root, i)) for i in disk_items(root)))
        elif verb == 'adopt':
            names = all_names(root)
            for i in disk_items(root):
                if not any(related(n[1], i) for n in names):
                    print('%s\t%d bytes\tunnamed' % (i, size_of(os.path.join(root, i))))
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
