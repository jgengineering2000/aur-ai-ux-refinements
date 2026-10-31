#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""test-threads -- executable decision table for threads.py. HERMETIC: every case builds a synthetic
git repo (master, merged and unmerged branches, a dirty lab worktree, a log directory, patches and
briefs) under a temp directory; no real lab, ledger or log is read or touched.

    python3 tools/test-threads.py
"""
import contextlib, io, json, os, subprocess, sys, tempfile, threading, time, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util
spec = importlib.util.spec_from_file_location('threads', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'threads.py'))
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)


def sh(cwd, *argv):
    subprocess.run(argv, cwd=cwd, check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t'))


def call(*argv, root=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        a = list(argv)
        if root:
            a = ['--root', root] + a
        try:
            rc = T.run(a)
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, 'proj')
        self.logs = os.path.join(self.tmp.name, 'logs')
        os.makedirs(self.repo)
        os.makedirs(self.logs)
        os.environ.pop('THREADS_LEDGER', None)
        os.environ['THREADS_LOG_DIRS'] = self.logs
        sh(self.repo, 'git', 'init', '-q', '-b', 'master')
        with open(os.path.join(self.repo, 'a.txt'), 'w') as f:
            f.write('a\n')
        sh(self.repo, 'git', 'add', 'a.txt')
        sh(self.repo, 'git', 'commit', '-q', '-m', 'first')
        self.ledger = os.path.join(self.repo, 'threads', 'ledger.jsonl')

    def op(self, ident='t1', title='fix the date order', **kw):
        a = ['open', ident, '--title', title, '--goal', 'g', '--done', 'd']
        for k, v in kw.items():
            a += ['--' + k, v]
        return call(*a, root=self.repo)

    def events(self):
        with open(self.ledger) as f:
            return [json.loads(l) for l in f]


class Verbs(Base):
    def test_open_close_cycle(self):
        self.assertEqual(self.op()[0], 0)
        rc, out, _ = call('status', root=self.repo)
        self.assertIn('threads: 1 open (0 paused,', out)
        self.assertEqual(call('close', 't1', '--landed', 'abc123', root=self.repo)[0], 0)
        self.assertIn('threads: 0 open', call('status', root=self.repo)[1])

    def test_age_is_hours_since_the_utc_stamp_whatever_the_zone(self):
        for tz in ('UTC', 'America/Chicago', 'Asia/Kolkata'):
            os.environ['TZ'] = tz
            time.tzset()
            ts = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - 5 * 3600))
            self.assertAlmostEqual(T.hours(ts), 5.0, delta=0.01, msg=tz)
        os.environ.pop('TZ')
        time.tzset()

    def test_close_needs_exactly_one_outcome(self):
        self.op()
        for argv in (['close', 't1'], ['close', 't1', '--landed', 'x', '--dropped', 'y']):
            rc, _, err = call(*argv, root=self.repo)
            self.assertEqual(rc, 2)
            self.assertIn('exactly-one-outcome-required', err)
            self.assertIn("id='t1'", err)
        self.assertEqual(len(self.events()), 1, 'a refused close wrote nothing')

    def test_title_three_to_eight_words(self):
        for title in ('fix', 'one two three four five six seven eight nine'):
            rc, _, err = self.op('t9', title=title)
            self.assertEqual(rc, 2)
            self.assertIn('title-must-be-3-to-8-words', err)
        self.assertFalse(os.path.exists(self.ledger))

    def test_duplicate_open_and_unknown_thread_refused(self):
        self.op()
        self.assertIn('id-exists', self.op()[2])
        self.assertIn('unknown-thread', call('close', 'nope', '--dropped', 'x', root=self.repo)[2])

    def test_if_open_makes_unknown_and_closed_a_noop(self):
        self.assertEqual(call('close', 'nope', '--landed', 's', '--if-open', root=self.repo)[0], 0)
        self.op()
        call('close', 't1', '--landed', 's', root=self.repo)
        n = len(self.events())
        self.assertEqual(call('close', 't1', '--landed', 's2', '--if-open', root=self.repo)[0], 0)
        self.assertEqual(len(self.events()), n)
        self.assertEqual(call('close', 't1', '--landed', 's2', root=self.repo)[0], 2)

    def test_pause_prints_map_and_counts(self):
        self.op()
        rc, out, _ = call('pause', 't1', '--reason', 'token limit', '--by', 'worker', '--resume-when', 'after b3 lands',
                          '--map', '/tmp/x/t1-RESUME-MAP.txt', root=self.repo)
        self.assertEqual(rc, 0)
        self.assertIn('resume map: /tmp/x/t1-RESUME-MAP.txt', out)
        self.assertIn('1 paused', call('status', root=self.repo)[1])
        self.assertIn('PAUSED', call('status', '--table', root=self.repo)[1])
        self.assertEqual(call('resume', 't1', root=self.repo)[0], 0)
        self.assertIn('0 paused', call('status', root=self.repo)[1])
        self.assertEqual(call('resume', 't1', root=self.repo)[0], 2)

    def test_ledger_holds_no_absolute_path(self):
        self.op(brief='/abs/outside/BRIEF.md', lab='/home/u/proj-lab')
        call('pause', 't1', '--reason', 'r', '--by', 'w', '--resume-when', 'x', '--map', '/tmp/q/ad1.log', root=self.repo)
        with open(self.ledger) as f:
            text = f.read()
        self.assertNotIn('/abs/', text)
        self.assertNotIn('/tmp/', text)
        self.assertIn('"ad1.log"', text)

    def test_unknown_argument_prints_help_nonzero_no_side_effect(self):
        for argv in (['bogus'], ['open', 't', '--wat', 'x'], ['status', '--wat'], ['--wat']):
            rc, _, err = call(*argv, root=self.repo)
            self.assertEqual(rc, 2, argv)
            self.assertIn('usage: threads.py', err)
        self.assertFalse(os.path.exists(self.ledger))

    def test_help_exits_zero(self):
        self.assertEqual(call('--help', root=self.repo)[0], 0)
        self.assertEqual(call('close', '--help', root=self.repo)[0], 0)

    def test_concurrent_appends_never_interleave(self):
        def w(i):
            for j in range(25):
                T.append(self.ledger, {'ts': T.now(), 'ev': 'checkpoint', 'id': 'x', 'state': 's' * 4000, 'n': (i, j)})
        ts = [threading.Thread(target=w, args=(i,)) for i in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(self.events()), 150)

    def test_ledger_resolves_to_main_tree_from_a_lab(self):
        lab = os.path.join(self.tmp.name, 'proj-b1')
        sh(self.repo, 'git', 'worktree', 'add', '-q', '-b', 'b1', lab)
        self.assertEqual(call('open', 't1', '--title', 'one two three', '--goal', 'g', '--done', 'd', root=lab)[0], 0)
        self.assertTrue(os.path.exists(self.ledger))
        self.assertFalse(os.path.exists(os.path.join(lab, 'threads')))


class Sweep(Base):
    def build(self):
        r = self.repo
        sh(r, 'git', 'branch', 'merged-one')
        sh(r, 'git', 'branch', 'unmerged-one')
        sh(r, 'git', 'checkout', '-q', 'unmerged-one')
        open(os.path.join(r, 'u.txt'), 'w').write('u')
        sh(r, 'git', 'add', 'u.txt')
        sh(r, 'git', 'commit', '-q', '-m', 'u')
        sh(r, 'git', 'checkout', '-q', 'master')
        self.lab = os.path.join(self.tmp.name, 'proj-dirty')
        sh(r, 'git', 'worktree', 'add', '-q', '-b', 'dirty', self.lab)
        open(os.path.join(self.lab, 'a.txt'), 'a').write('more\n')
        open(os.path.join(self.logs, 'w7.log'), 'w').write('x\nRESUME MAP (w7): stopped\n')
        open(os.path.join(self.logs, 'clean.log'), 'w').write('nothing here\n')
        os.makedirs(os.path.join(r, 'patches', 'unlanded'))
        open(os.path.join(r, 'patches', 'unlanded', 'p1.patch'), 'w').write('p')
        os.makedirs(os.path.join(r, 'briefs', 'old-landed'))
        os.makedirs(os.path.join(r, 'briefs', 'new-orphan'))
        os.makedirs(os.path.join(r, 'briefs', 'ancient'))
        os.utime(os.path.join(r, 'briefs', 'ancient'), (1, 1))
        sh(r, 'git', 'commit', '-q', '--allow-empty', '-m', 'landed old-landed brief')
        os.makedirs(os.path.join(r, 'threads'), exist_ok=True)
        json.dump({'patch_dirs': ['patches/unlanded'], 'brief_dirs': ['briefs']}, open(os.path.join(r, 'threads', 'sources.json'), 'w'))

    def orphans(self, *extra):
        out = call('status', '--table', *extra, root=self.repo)[1]
        return [l.split()[1:3] for l in out.splitlines() if l.strip().startswith('ORPHAN')], out

    def test_sweep_finds_each_kind_on_synthetic_tree(self):
        self.build()
        got, out = self.orphans()
        self.assertIn(['lab-branch', 'unmerged-one'], got)
        self.assertIn(['dirty-tree', 'dirty'], got)
        self.assertIn(['resume-map', 'w7.log'], got)
        kinds = {tuple(g) for g in got}
        self.assertIn(('patch', 'patches/unlanded/p1.patch'), kinds)
        self.assertIn(('brief', 'briefs/new-orphan'), kinds)
        self.assertNotIn(['lab-branch', 'merged-one'], got)
        self.assertNotIn(['resume-map', 'clean.log'], got)
        self.assertNotIn(('brief', 'briefs/old-landed'), kinds)
        self.assertNotIn(('brief', 'briefs/ancient'), kinds)

    def test_a_thread_accounts_for_its_artifacts(self):
        self.build()
        self.op('t1', lab='unmerged-one')
        self.op('t2', lab='dirty', brief='briefs/new-orphan')
        call('checkpoint', 't2', '--state', 's', '--map', '/x/w7.log', '--covers', 'patches/unlanded/*.patch', root=self.repo)
        got, out = self.orphans()
        self.assertEqual(got, [], out)
        self.assertIn('0 orphans', out)

    def test_closed_thread_still_accounts_but_open_count_drops(self):
        self.build()
        self.op('t1', lab='unmerged-one')
        call('close', 't1', '--dropped', 'abandoned', root=self.repo)
        got, _ = self.orphans()
        self.assertNotIn(['lab-branch', 'unmerged-one'], got)

    def test_fast_skips_dirty_tree_scan(self):
        self.build()
        got, _ = self.orphans('--fast')
        self.assertNotIn(['dirty-tree', 'dirty'], got)

    def test_sweep_never_mutates_the_tree(self):
        self.build()
        before = subprocess.run(['git', 'status', '--porcelain'], cwd=self.lab, capture_output=True, text=True).stdout
        self.orphans()
        after = subprocess.run(['git', 'status', '--porcelain'], cwd=self.lab, capture_output=True, text=True).stdout
        self.assertEqual(before, after)

    def test_unreadable_ledger_line_is_logged_and_skipped(self):
        self.op()
        with open(self.ledger, 'a') as f:
            f.write('{not json\n')
        rc, out, err = call('status', root=self.repo)
        self.assertEqual(rc, 0)
        self.assertIn('line 2 unreadable', err)
        self.assertIn('1 open', out)


if __name__ == '__main__':
    unittest.main(verbosity=1)
