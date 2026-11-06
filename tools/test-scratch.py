#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""test-scratch -- executable decision table for scratch.py and unforced.py. HERMETIC: each case builds a project root
(a real git repository with a main scratch, labs/*/scratch, a ledger) under a temp directory; the receipt log is
redirected there. The clock is moved past the 48 h floor with `aged()`, never the files: a ctime cannot be set back.

    python3 tools/test-scratch.py
"""
import contextlib, fcntl, io, os, stat, subprocess, sys, tempfile, time, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scratch as S  # noqa: E402
import threads as T  # noqa: E402
import unforced as U  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, 'proj')
        self.ledger = os.path.join(self.root, 'threads', 'ledger.jsonl')
        os.environ['AUR_RECEIPT_LOG'] = os.path.join(self.tmp.name, 'receipts.log')
        self.addCleanup(os.environ.pop, 'AUR_RECEIPT_LOG', None)
        for d in ('scratch', 'labs/a/scratch', 'labs/b/scratch'):
            os.makedirs(os.path.join(self.root, d))
            self.manage(d)
        self.git('init', '-q')
        self.git('config', 'user.name', 'test')
        self.git('config', 'user.email', 'test')

    def manage(self, scratch_dir):
        """Bring a level under management the way a reviewer can see: an empty MANIFEST, independent of `adopt`."""
        open(os.path.join(self.root, scratch_dir, 'MANIFEST'), 'w').close()

    def git(self, *argv, cwd=None):
        return subprocess.run(['git', '-C', cwd or self.root] + list(argv), check=True, capture_output=True, text=True)

    def row(self, level_dir, item, holder):
        """Write a manifest row by hand: what a tool refuses to write can still be on disk."""
        with open(os.path.join(self.root, level_dir, 'MANIFEST'), 'a') as f:
            f.write('%s\t%s\tw\n' % (item, holder))

    def aged(self, hours=49):
        """Run the clock HOURS ahead, so everything on disk reads as that old."""
        return mock.patch.object(S, 'now', lambda: time.time() + hours * 3600, create=True)

    def put(self, rel, data='x'):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w') as f:
            f.write(data)
        return rel

    def thread(self, ident, close=False):
        T.append(self.ledger, {'ev': 'open', 'id': ident, 'ts': '2026-01-01T00:00:00Z'})
        if close:
            T.append(self.ledger, {'ev': 'close', 'id': ident, 'ts': '2026-01-02T00:00:00Z', 'outcome': {}})

    def sc(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = S.main(['--root', self.root, '--ledger', self.ledger] + list(argv))
        return rc, out.getvalue(), err.getvalue()

    def exists(self, rel):
        return os.path.lexists(os.path.join(self.root, rel))


class Naming(Base):
    def test_add_infers_the_level_and_writes_the_row(self):
        item = self.put('labs/a/scratch/run1')
        self.assertEqual(self.sc('add', item, '--what', 'a run', '--holder', 'permanent')[0], 0)
        self.assertEqual(S.read_manifest(os.path.join(self.root, 'labs/a/scratch')), [[item, 'permanent', 'a run']])

    def test_free_text_holder_is_refused_before_any_side_effect(self):
        item = self.put('labs/c/scratch/x')
        rc, _, err = self.sc('add', item, '--what', 'w', '--holder', 'the census')
        self.assertEqual(rc, 2)
        self.assertIn('free text is refused', err)
        self.assertFalse(os.path.exists(os.path.join(self.root, 'labs/c/scratch', 'MANIFEST')))

    def test_unknown_argument_is_refused(self):
        rc, _, err = self.sc('check', '--bogus')
        self.assertEqual(rc, 2)
        self.assertIn('usage', err)

    def test_link_names_another_levels_item_in_the_dependents_manifest(self):
        item = self.put('labs/a/scratch/data')
        self.assertEqual(self.sc('link', item, '--level', 'b', '--holder', 'lab:b')[0], 0)
        self.assertEqual(S.read_manifest(os.path.join(self.root, 'labs/b/scratch'))[0][:2], [item, 'lab:b'])
        self.assertEqual(S.read_manifest(os.path.join(self.root, 'labs/a/scratch')), [])

    def test_unlink_drops_the_name_and_refuses_an_absent_one(self):
        item = self.put('scratch/x')
        self.sc('add', item, '--what', 'w', '--holder', 'permanent')
        self.assertEqual(self.sc('unlink', item, '--level', 'main')[0], 0)
        rc, _, err = self.sc('unlink', item, '--level', 'main')
        self.assertEqual(rc, 1)
        self.assertIn('no name for', err)


class Check(Base):
    def test_clean_project_reports_nothing(self):
        item = self.put('scratch/x')
        self.sc('add', item, '--what', 'w', '--holder', 'permanent')
        self.assertEqual(self.sc('check')[0], 0)

    def test_orphan_dangling_and_bad_holder_are_each_reported(self):
        self.put('scratch/orphan')
        gone = self.put('scratch/gone')
        self.sc('add', gone, '--what', 'w', '--holder', 'permanent')
        os.remove(os.path.join(self.root, gone))
        held = self.put('scratch/held')
        self.thread('other')  # a ledger exists, and t1 is not in it
        self.row('scratch', held, 'thread:t1')  # `add` refuses a holder that is not live: write what it cannot
        rc, out, _ = self.sc('check')
        self.assertEqual(rc, 1)
        self.assertIn('ORPHAN (on disk, unnamed): scratch/orphan', out)
        self.assertIn('DANGLING (named, not on disk): main names scratch/gone', out)
        self.assertIn('BAD HOLDER: main names scratch/held held by thread:t1: thread t1 is not in the ledger', out)

    def test_open_and_paused_threads_hold_closed_ones_do_not(self):
        self.thread('open1')
        self.thread('done1', close=True)
        self.assertIsNone(S.holder_problem(self.root, self.ledger, 'thread:open1'))
        self.assertIn('closed', S.holder_problem(self.root, self.ledger, 'thread:done1'))
        self.assertIsNone(S.holder_problem(self.root, self.ledger, 'lab:a'))
        self.assertIn('does not exist', S.holder_problem(self.root, self.ledger, 'lab:zzz'))

    def test_a_name_in_one_level_covers_an_item_in_another(self):
        item = self.put('labs/a/scratch/data')
        self.sc('link', item, '--level', 'b', '--holder', 'lab:b')
        self.assertEqual(self.sc('check')[0], 0)


class Clean(Base):
    def test_unnamed_and_dead_held_items_go_named_live_ones_stay(self):
        keep = self.put('scratch/keep')
        self.sc('add', keep, '--what', 'w', '--holder', 'permanent')
        self.thread('t1')
        run = self.put('labs/a/scratch/run')
        self.sc('add', run, '--what', 'w', '--holder', 'thread:t1')
        self.put('scratch/orphan', 'y' * 100)
        dead = self.put('scratch/dead')
        self.thread('t2', close=True)
        self.sc('add', dead, '--what', 'w', '--holder', 'thread:t2')
        with self.aged():
            rc, out, _ = self.sc('clean')
        self.assertEqual(rc, 0)
        self.assertTrue(self.exists(keep) and self.exists(run))
        self.assertFalse(self.exists('scratch/orphan') or self.exists(dead))
        self.assertIn('freed 101 bytes', out)
        self.assertIn('kept scratch/keep: named by main/permanent', out)
        self.assertEqual([r[0] for r in S.read_manifest(os.path.join(self.root, 'scratch'))], [keep])

    def test_closing_the_thread_frees_its_item_on_the_next_clean(self):
        self.thread('t1')
        run = self.put('scratch/run')
        self.sc('add', run, '--what', 'w', '--holder', 'thread:t1')
        self.sc('clean')
        self.assertTrue(self.exists(run))
        T.append(self.ledger, {'ev': 'close', 'id': 't1', 'ts': '2026-01-03T00:00:00Z', 'outcome': {}})
        self.sc('clean')
        self.assertFalse(self.exists(run))

    def test_dry_run_removes_nothing(self):
        self.put('scratch/orphan')
        with self.aged():
            rc, out, _ = self.sc('clean', '--dry-run')
        self.assertIn('would free 1 bytes; removed 1: scratch/orphan', out)
        self.assertTrue(self.exists('scratch/orphan'))

    def test_an_item_a_process_holds_open_is_kept(self):
        p = self.put('scratch/open')
        with open(os.path.join(self.root, p)), self.aged():
            rc, out, _ = self.sc('clean')
        self.assertTrue(self.exists(p))
        self.assertIn('kept scratch/open: open in pid', out)

    def test_write_protected_is_preserved_and_reported(self):
        d = os.path.join(self.root, 'scratch', 'prot')
        os.makedirs(d)
        open(os.path.join(d, 'f'), 'w').close()
        os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(os.chmod, d, stat.S_IRWXU)
        with self.aged():
            rc, out, err = self.sc('clean')
        self.assertTrue(self.exists('scratch/prot/f'))
        self.assertIn('kept scratch/prot: not removable', out)

    def test_receipts_are_written_by_the_one_routine(self):
        self.put('scratch/orphan')
        with self.aged():
            self.sc('clean')
        log = open(os.environ['AUR_RECEIPT_LOG']).read()
        self.assertIn('BEFORE', log)
        self.assertIn('AFTER', log)


class OldFormat(Base):
    def test_a_manifest_of_another_format_is_refused_and_clean_removes_nothing(self):
        self.put('scratch/precious')
        with open(os.path.join(self.root, 'scratch', 'MANIFEST'), 'w') as f:
            f.write('precious\twhat\t5\tproducer\tdependency\n')
        for verb in ('check', 'clean'):
            rc, _, err = self.sc(verb)
            self.assertEqual(rc, 1, verb)
            self.assertIn('not the 3 of ITEM, HOLDER, WHAT', err)
        self.assertTrue(self.exists('scratch/precious'))


class UnmanagedLevel(Base):
    """A level with no MANIFEST: clean never touches it, check names it in one line, and adopt alone brings it in."""

    def unmanaged(self):
        item = self.put('labs/c/scratch/corpus/data.bin', 'corpus')
        os.remove(os.path.join(self.root, 'scratch', 'MANIFEST'))
        return item, self.put('scratch/corpus/old.bin', 'corpus')

    def test_clean_never_touches_it_at_any_age(self):
        lab_item, main_item = self.unmanaged()
        with self.aged():
            rc, out, _ = self.sc('clean')
        self.assertEqual(rc, 0)
        self.assertTrue(self.exists(lab_item) and self.exists(main_item))
        self.assertIn('not touched (unmanaged, no MANIFEST): scratch', out)
        self.assertIn('not touched (unmanaged, no MANIFEST): labs/c/scratch', out)

    def test_check_names_it_in_one_line_and_does_not_fail(self):
        self.unmanaged()
        rc, out, _ = self.sc('check')
        self.assertEqual(rc, 0, out)
        self.assertEqual(len([l for l in out.splitlines() if 'labs/c/scratch' in l]), 1, out)
        self.assertEqual(len([l for l in out.splitlines() if l.startswith('UNMANAGED') and l.endswith(': scratch')]), 1, out)
        self.assertNotIn('ORPHAN', out)
        self.assertIn('scratch check: clean', out)

    def test_add_and_link_refuse_it_and_create_no_manifest(self):
        lab_item, main_item = self.unmanaged()
        rc, _, err = self.sc('add', main_item, '--what', 'w', '--holder', 'permanent')
        self.assertEqual(rc, 1)
        self.assertIn('unmanaged', err)
        rc, _, err = self.sc('link', self.put('labs/a/scratch/d'), '--level', 'c', '--holder', 'permanent')
        self.assertEqual(rc, 1)
        self.assertIn('unmanaged', err)
        self.assertFalse(os.path.lexists(os.path.join(self.root, 'scratch', 'MANIFEST')))
        self.assertFalse(os.path.lexists(os.path.join(self.root, 'labs/c/scratch', 'MANIFEST')))

    def test_a_dead_name_in_another_level_does_not_free_what_is_in_it(self):
        lab_item, _ = self.unmanaged()
        self.thread('t1', close=True)
        self.row('labs/a/scratch', lab_item, 'thread:t1')
        with self.aged():
            self.sc('clean')
        self.assertTrue(self.exists(lab_item))

    def test_a_scratch_that_is_a_symlink_is_unmanaged(self):
        shared = self.put('scratch/shared')
        os.makedirs(os.path.join(self.root, 'labs/c'))
        os.symlink('../../scratch', os.path.join(self.root, 'labs/c/scratch'))
        self.sc('add', shared, '--what', 'w', '--holder', 'permanent')
        with self.aged():
            rc, out, _ = self.sc('clean')
        self.assertTrue(self.exists(shared))
        self.assertIn('not touched (unmanaged, no MANIFEST): labs/c/scratch', out)

    def test_release_of_an_unmanaged_lab_still_moves_what_another_level_names(self):
        item = self.put('labs/c/scratch/data')
        self.row('labs/b/scratch', item, 'lab:b')
        rc, out, _ = self.sc('release', 'c')
        self.assertEqual(rc, 0)
        self.assertIn('moved labs/c/scratch/data -> labs/b/scratch/data', out)
        self.assertTrue(self.exists('labs/b/scratch/data'))


class OnlyInsideAScratch(Base):
    """Nothing outside a managed level's scratch, the scratch itself, or its manifest, is ever a candidate."""

    def test_a_name_outside_every_scratch_is_never_removed(self):
        self.thread('t1', close=True)
        data = self.put('research/data/x')
        self.row('labs/a/scratch', data, 'thread:t1')
        with self.aged():
            rc, out, _ = self.sc('clean')
        self.assertTrue(self.exists(data))
        self.assertIn('kept research/data/x: ', out)

    def test_naming_the_scratch_or_its_manifest_is_refused(self):
        for item in ('scratch', 'scratch/MANIFEST'):
            rc, _, err = self.sc('link', item, '--level', 'a', '--holder', 'lab:a')
            self.assertEqual(rc, 1, item)
            self.assertIn('not an item', err)

    def test_a_dead_name_on_the_scratch_or_its_manifest_removes_neither(self):
        self.thread('t1', close=True)
        keep = self.put('scratch/keep')
        self.row('labs/a/scratch', 'scratch', 'thread:t1')
        self.row('labs/a/scratch', 'scratch/MANIFEST', 'thread:t1')
        self.row('labs/a/scratch', keep, 'permanent')
        with self.aged():
            self.sc('clean')
        self.assertTrue(self.exists('scratch/MANIFEST') and self.exists(keep))

    def test_a_row_climbing_out_of_the_root_refuses_everything(self):
        self.thread('t1', close=True)
        outside = os.path.join(self.tmp.name, 'outside')
        open(outside, 'w').close()
        self.row('scratch', 'scratch/../../outside', 'thread:t1')
        orphan = self.put('labs/a/scratch/orphan')
        with self.aged():
            rc, _, err = self.sc('clean')
        self.assertEqual(rc, 1)
        self.assertIn('not a clean path', err)
        self.assertTrue(os.path.exists(outside) and self.exists(orphan))

    def test_a_symlink_on_the_way_is_not_followed_out(self):
        self.thread('t1', close=True)
        target = self.put('elsewhere/f')
        os.symlink('../elsewhere', os.path.join(self.root, 'scratch', 'l'))
        self.row('scratch', 'scratch/l/f', 'thread:t1')
        # Young, the unnamed link itself stays, so the row through it is judged: only the physical test protects the target.
        rc, out, _ = self.sc('clean')
        self.assertTrue(self.exists(target), out)
        self.assertIn('kept scratch/l/f: not inside', out)
        # Old, the link goes as a link and is not followed.
        with self.aged():
            self.sc('clean')
        self.assertTrue(self.exists(target))
        self.assertFalse(self.exists('scratch/l'))

    def test_a_dead_inner_name_frees_that_path_and_no_more(self):
        self.thread('t1', close=True)
        dead = self.put('scratch/d/dead')
        other = self.put('scratch/d/other')
        self.row('scratch', dead, 'thread:t1')
        rc, out, _ = self.sc('clean')
        self.assertFalse(self.exists(dead))
        self.assertTrue(self.exists(other), out)
        self.assertIn('kept scratch/d/other: unnamed', out)  # d is a container: its entries are judged one by one

    def test_removing_an_inner_path_keeps_a_name_above_it(self):
        self.thread('t1', close=True)
        dead = self.put('scratch/d/dead')
        self.git('add', '-f', self.put('scratch/d/tracked'))
        self.row('scratch', 'scratch/d', 'thread:t1')
        self.row('scratch', dead, 'thread:t1')
        self.sc('clean')
        self.assertFalse(self.exists(dead))
        self.assertIn('scratch/d', [r[0] for r in S.read_manifest(os.path.join(self.root, 'scratch'))])


class TrackedPaths(Base):
    """A tracked path, or an item holding one (git ls-files at that level's tree), is never a candidate."""

    def test_a_tracked_file_stays_unnamed_and_is_no_orphan(self):
        p = self.put('scratch/tool.py')
        self.git('add', '-f', p)
        with self.aged():
            rc, out, _ = self.sc('clean')
        self.assertTrue(self.exists(p))
        self.assertIn('kept scratch/tool.py: tracked', out)
        self.assertNotIn('ORPHAN', self.sc('check')[1])

    def test_a_tracked_file_named_by_a_dead_holder_stays(self):
        self.thread('t1', close=True)
        p = self.put('scratch/tool.py')
        self.git('add', '-f', p)
        self.row('scratch', p, 'thread:t1')
        with self.aged():
            self.sc('clean')
        self.assertTrue(self.exists(p))

    def test_a_directory_holding_a_tracked_file_is_a_container_whose_tracked_path_stays(self):
        p = self.put('scratch/d/sub/tool.py')
        junk = self.put('scratch/d/junk')
        self.git('add', '-f', p)
        with self.aged():
            rc, out, _ = self.sc('clean')
        self.assertTrue(self.exists(p), out)
        self.assertFalse(self.exists(junk), out)  # unnamed and old: judged beside the tracked path, not kept by it
        self.assertIn('kept scratch/d/sub/tool.py: tracked', out)

    def worktree(self):
        tool = self.put('scratch/tool.py')
        self.git('add', '-f', tool)
        self.git('commit', '-qm', 'tool')
        self.git('worktree', 'add', '-q', os.path.join(self.root, 'labs/w'), '-b', 'w')
        self.manage('labs/w/scratch')
        return 'labs/w/scratch/tool.py', self.put('labs/w/scratch/run.out')

    def test_inside_a_worktree_its_own_index_decides(self):
        tool, run = self.worktree()
        with self.aged():
            rc, out, _ = self.sc('clean')
        self.assertTrue(self.exists(tool), out)
        self.assertFalse(self.exists(run), out)

    def test_git_location_variables_do_not_change_the_answer(self):
        # A hook runs with these set; read through them, ls-files answers for another index, and it answers empty.
        tool, _ = self.worktree()
        empty = os.path.join(self.tmp.name, 'empty-index')
        with mock.patch.dict(os.environ, {'GIT_INDEX_FILE': empty, 'GIT_DIR': os.path.join(self.root, '.git')}), self.aged():
            self.sc('clean')
        self.assertTrue(self.exists(tool))

    def test_when_git_cannot_answer_the_level_is_kept_and_reported(self):
        tool, run = self.worktree()
        with open(os.path.join(self.root, 'labs/w/.git'), 'w') as f:
            f.write('gitdir: /nonexistent\n')
        with self.aged():
            rc, out, _ = self.sc('clean')
        self.assertTrue(self.exists(tool) and self.exists(run), out)
        self.assertIn('kept labs/w/scratch/run.out: git cannot say', out)
        rc, out, _ = self.sc('check')
        self.assertEqual(rc, 1)
        self.assertIn('GIT CANNOT ANSWER: level w', out)
        before = open(os.path.join(self.root, 'labs/w/scratch/MANIFEST')).read()
        self.assertEqual(self.sc('adopt', '--level', 'w', '--holder', 'permanent')[0], 1)
        self.assertEqual(open(os.path.join(self.root, 'labs/w/scratch/MANIFEST')).read(), before)


class OnlyProvablyDeadFrees(Base):
    """Dead: a thread closed in a ledger read whole, or a lab whose directory is gone. All else keeps, and check says so."""

    def held(self, holder):
        item = self.put('scratch/held')
        self.row('scratch', item, holder)
        return item

    def assert_kept_and_reported(self, item, why):
        with self.aged():
            rc, out, _ = self.sc('clean')
        self.assertTrue(self.exists(item), 'freed: ' + out)
        self.assertIn('kept %s: ' % item, out)
        rc, out, _ = self.sc('check')
        self.assertEqual(rc, 1)
        self.assertIn('BAD HOLDER', out)
        self.assertIn(why, out)

    def test_a_thread_the_ledger_does_not_know(self):
        self.thread('t1')
        self.assert_kept_and_reported(self.held('thread:t1x'), 'thread t1x is not in the ledger')

    def test_a_malformed_holder(self):
        for holder in ('the census', 'thread:', 'lab:', 'lab:../x', 'Thread:t1', 'permanent:x'):
            item = self.held(holder)
            self.assert_kept_and_reported(item, 'not permanent, thread:ID or lab:NAME')
            os.remove(os.path.join(self.root, 'scratch', 'MANIFEST'))
            self.manage('scratch')

    def test_a_missing_ledger(self):
        self.assertFalse(os.path.exists(self.ledger))
        self.assert_kept_and_reported(self.held('thread:t1'), 'ledger')

    def test_a_ledger_that_cannot_be_read(self):
        os.makedirs(self.ledger)  # a directory where the file belongs: open() fails
        self.assert_kept_and_reported(self.held('thread:t1'), 'ledger')

    def test_a_ledger_with_an_unreadable_line_proves_nothing(self):
        # The line lost might be the one that matters: nothing read past it is proof.
        self.thread('t1', close=True)
        with open(self.ledger, 'a') as f:
            f.write('{"ev": "open", "id": "t1", torn\n')
        self.assert_kept_and_reported(self.held('thread:t1'), 'ledger')

    def test_a_lab_link_that_dangles_is_not_a_lab_that_is_gone(self):
        # An unmounted drive looks exactly like this.
        os.symlink('/nonexistent/mount/z', os.path.join(self.root, 'labs', 'z'))
        self.assert_kept_and_reported(self.held('lab:z'), 'lab z')

    def test_a_lab_that_is_gone_frees(self):
        item = self.held('lab:gone')
        rc, out, _ = self.sc('clean')
        self.assertFalse(self.exists(item), out)

    def test_a_thread_closed_in_a_good_ledger_frees_at_any_age(self):
        self.thread('t1', close=True)
        item = self.held('thread:t1')
        rc, out, _ = self.sc('clean')
        self.assertFalse(self.exists(item), out)

    def test_release_does_not_treat_an_unknown_holder_as_gone(self):
        item = self.put('labs/a/scratch/d')
        self.row('labs/a/scratch', item, 'thread:unknown')
        rc, _, err = self.sc('release', 'a')
        self.assertEqual(rc, 1)
        self.assertIn('labs/a/scratch/d', err)


class AgeFloor(Base):
    """An unnamed item younger than 48 h is kept and reported; older, it goes, its path in the receipt first."""

    def test_a_young_unnamed_item_is_kept_and_reported_and_check_still_fails(self):
        p = self.put('scratch/just-written')
        rc, out, _ = self.sc('clean')
        self.assertEqual(rc, 0)
        self.assertTrue(self.exists(p), out)
        self.assertIn('kept scratch/just-written: unnamed', out)
        rc, out, _ = self.sc('check')
        self.assertEqual(rc, 1)
        self.assertIn('ORPHAN (on disk, unnamed): scratch/just-written', out)

    def test_the_floor_is_48_hours_and_an_older_item_goes(self):
        p = self.put('scratch/old')
        with self.aged(47.9):
            self.sc('clean')
        self.assertTrue(self.exists(p))
        with self.aged(48.1):
            self.sc('clean')
        self.assertFalse(self.exists(p))

    def test_a_touch_deep_inside_makes_the_whole_item_young(self):
        deep = self.put('scratch/d/e/f/deep')
        soon = time.time() + 48.5 * 3600
        os.utime(os.path.join(self.root, deep), (soon, soon))
        with self.aged():
            rc, out, _ = self.sc('clean')
        self.assertTrue(self.exists(deep), out)

    def test_the_receipt_names_the_path_before_it_goes(self):
        p = self.put('scratch/old')
        with self.aged():
            self.sc('clean')
        lines = open(os.environ['AUR_RECEIPT_LOG']).read().splitlines()
        self.assertIn('BEFORE', lines[0])
        self.assertIn(os.path.join(self.root, p), lines[0])
        self.assertIn('AFTER', lines[1])

    def test_when_the_receipt_cannot_be_written_nothing_goes_and_it_is_logged(self):
        p = self.put('scratch/old')
        os.environ['AUR_RECEIPT_LOG'] = '/proc/nonexistent/receipts.log'
        with self.aged():
            rc, out, err = self.sc('clean')
        self.assertEqual(rc, 1)
        self.assertTrue(self.exists(p))
        self.assertIn('kept scratch/old: not removed', out)
        self.assertIn('receipt log', err)

    def test_a_process_running_inside_an_item_holds_it(self):
        d = os.path.dirname(os.path.join(self.root, self.put('scratch/d/f')))
        p = subprocess.Popen(['sleep', '60'], cwd=d)
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        with self.aged():
            rc, out, _ = self.sc('clean')
        self.assertTrue(os.path.isdir(d), out)
        self.assertIn('kept scratch/d: open in pid %d' % p.pid, out)


class AdoptWrites(Base):
    """`adopt --level L --holder H` creates L's MANIFEST, names what is unnamed, and deletes nothing."""

    def unmanaged_lab(self):
        a, b = self.put('labs/c/scratch/one'), self.put('labs/c/scratch/sub/two')
        return a, b

    def test_it_names_each_item_and_deletes_nothing(self):
        a, b = self.unmanaged_lab()
        rc, out, _ = self.sc('adopt', '--level', 'c', '--holder', 'permanent', '--what', 'found at the move')
        self.assertEqual(rc, 0, out)
        rows = S.read_manifest(os.path.join(self.root, 'labs/c/scratch'))
        self.assertEqual(rows, [[a, 'permanent', 'found at the move'], ['labs/c/scratch/sub', 'permanent', 'found at the move']])
        self.assertTrue(self.exists(a) and self.exists(b))
        with self.aged():
            self.sc('clean')
        self.assertTrue(self.exists(a) and self.exists(b))
        self.assertNotIn('ORPHAN', self.sc('check')[1])

    def test_it_is_what_brings_an_empty_level_under_management(self):
        os.makedirs(os.path.join(self.root, 'labs/c/scratch'))
        self.assertIn('labs/c/scratch', self.sc('check')[1])
        self.assertEqual(self.sc('adopt', '--level', 'c', '--holder', 'lab:c')[0], 0)
        self.assertNotIn('labs/c/scratch', self.sc('check')[1])

    def test_it_keeps_existing_names_and_skips_tracked_paths(self):
        kept, new = self.put('scratch/kept'), self.put('scratch/new')
        tool = self.put('scratch/tool.py')
        self.git('add', '-f', tool)
        self.sc('add', kept, '--what', 'mine', '--holder', 'lab:a')
        self.assertEqual(self.sc('adopt', '--level', 'main', '--holder', 'permanent')[0], 0)
        rows = S.read_manifest(os.path.join(self.root, 'scratch'))
        self.assertEqual([r[:2] for r in rows], [[kept, 'lab:a'], [new, 'permanent']])

    def test_bad_forms_are_refused_before_any_side_effect(self):
        self.put('labs/c/scratch/one')
        for argv in (['--level', 'c'], ['--holder', 'permanent'], ['--what', 'w'],
                     ['--level', 'c', '--holder', 'free text']):
            rc, _, _ = self.sc('adopt', *argv)
            self.assertEqual(rc, 2, argv)
            self.assertFalse(os.path.lexists(os.path.join(self.root, 'labs/c/scratch/MANIFEST')), argv)

    def test_it_refuses_a_manifest_of_another_format_and_an_unknown_level(self):
        with open(os.path.join(self.root, 'scratch', 'MANIFEST'), 'w') as f:
            f.write('x\twhat\t5\tproducer\tdependency\n')
        rc, _, err = self.sc('adopt', '--level', 'main', '--holder', 'permanent')
        self.assertEqual(rc, 1)
        rc, _, err = self.sc('adopt', '--level', 'nosuch', '--holder', 'permanent')
        self.assertEqual(rc, 1)
        self.assertIn('nosuch', err)


class Writers(Base):
    def test_a_writer_waits_for_the_levels_lock(self):
        # Two writers read-modify-write one manifest; without the lock one row is lost, and a lost name is a later deletion.
        item = self.put('scratch/x')
        fd = os.open(os.path.join(self.root, 'scratch'), os.O_RDONLY)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        p = subprocess.Popen([sys.executable, os.path.join(HERE, 'scratch.py'), '--root', self.root, '--ledger', self.ledger,
                              'add', item, '--what', 'w', '--holder', 'permanent'])
        self.addCleanup(p.wait)
        time.sleep(1)
        waiting = p.poll() is None
        written = S.read_manifest(os.path.join(self.root, 'scratch'))
        fcntl.flock(fd, fcntl.LOCK_UN)
        self.assertEqual(p.wait(timeout=30), 0)
        self.assertTrue(waiting, 'the writer did not wait for the lock')
        self.assertEqual(written, [])
        self.assertEqual(S.read_manifest(os.path.join(self.root, 'scratch'))[0][0], item)


class OldFormatInCheck(Base):
    def test_check_still_reports_the_other_levels_and_clean_still_refuses_whole(self):
        with open(os.path.join(self.root, 'labs/a/scratch/MANIFEST'), 'w') as f:
            f.write('x\twhat\t5\tproducer\tdependency\n')
        orphan = self.put('scratch/orphan')
        rc, out, _ = self.sc('check')
        self.assertEqual(rc, 1)
        self.assertIn('UNREADABLE MANIFEST: level a', out)
        self.assertIn('ORPHAN (on disk, unnamed): ' + orphan, out)
        with self.aged():
            self.assertEqual(self.sc('clean')[0], 1)
        self.assertTrue(self.exists(orphan))


class Adopt(Base):
    def test_adopt_lists_unnamed_content_and_deletes_nothing(self):
        self.put('scratch/old', 'zz')
        rc, out, _ = self.sc('adopt')
        self.assertIn('scratch/old\t2 bytes\tunnamed', out)
        self.assertTrue(self.exists('scratch/old'))


class Release(Base):
    def test_item_named_elsewhere_moves_to_that_level_and_its_names_follow(self):
        item = self.put('labs/a/scratch/data')
        self.sc('link', item, '--level', 'b', '--holder', 'lab:b')
        rc, out, _ = self.sc('release', 'a')
        self.assertEqual(rc, 0)
        self.assertIn('moved labs/a/scratch/data -> labs/b/scratch/data', out)
        self.assertTrue(self.exists('labs/b/scratch/data'))
        self.assertFalse(self.exists(item))
        self.assertEqual(S.read_manifest(os.path.join(self.root, 'labs/b/scratch'))[0][0], 'labs/b/scratch/data')

    def test_a_permanent_item_only_the_lab_names_blocks_the_close(self):
        item = self.put('labs/a/scratch/keepme')
        self.sc('add', item, '--what', 'w', '--holder', 'permanent')
        rc, _, err = self.sc('release', 'a')
        self.assertEqual(rc, 1)
        self.assertIn('keepme', err)
        self.assertTrue(self.exists(item))

    def test_items_held_only_by_the_lab_itself_do_not_block(self):
        item = self.put('labs/a/scratch/tmp')
        self.sc('add', item, '--what', 'w', '--holder', 'lab:a')
        self.assertEqual(self.sc('release', 'a')[0], 0)


class SharedRoot(Base):
    """The installer's route makes the links, and the tool runs from them, a checkout's link or a deployed copy alike."""

    NAMES = ('threads.py', 'unforced.py', 'scratch.py')

    def installer(self):
        sys.path.insert(0, os.path.dirname(HERE))
        self.addCleanup(sys.path.remove, os.path.dirname(HERE))
        import aurshare
        return aurshare

    def runs(self, tool):
        self.put('scratch/orphan')
        r = subprocess.run([sys.executable, tool, '--root', self.root, '--ledger', self.ledger, 'check'],
                           capture_output=True, text=True, cwd=self.tmp.name)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn('ORPHAN (on disk, unnamed): scratch/orphan', r.stdout)

    def test_the_installer_carries_all_three_and_their_sources_exist(self):
        assets = self.installer().SHARED_ASSETS
        for name in self.NAMES:
            self.assertEqual(assets.get(name), 'tools/' + name)
            self.assertTrue(os.path.exists(os.path.join(os.path.dirname(HERE), assets[name])))

    def test_devlink_makes_the_links_and_the_tool_runs_through_them(self):
        share = os.path.join(self.tmp.name, 'share')
        made = self.installer().devlink(os.path.dirname(HERE), list(self.NAMES), log=lambda m: None, share=share)
        self.assertEqual(made, 3)
        for name in self.NAMES:
            self.assertTrue(os.path.islink(os.path.join(share, name)))
            self.assertTrue(os.path.exists(os.path.join(share, name)))
        self.runs(os.path.join(share, 'scratch.py'))

    def test_a_deployed_copy_finds_its_neighbours_beside_it(self):
        share = os.path.join(self.tmp.name, 'deployed')
        os.makedirs(share)
        for name in self.NAMES:
            with open(os.path.join(HERE, name)) as src, open(os.path.join(share, name), 'w') as dst:
                dst.write(src.read())
        self.runs(os.path.join(share, 'scratch.py'))


class Hoist(Base):
    def test_unforced_has_a_generic_default_log_and_no_project_name(self):
        del os.environ['AUR_RECEIPT_LOG']
        os.environ['XDG_DATA_HOME'] = self.tmp.name
        self.addCleanup(os.environ.pop, 'XDG_DATA_HOME', None)
        self.assertEqual(U.receipt_log_path(), os.path.join(self.tmp.name, 'aur', 'cleanup-receipts.log'))
        os.environ['AUR_RECEIPT_LOG'] = os.path.join(self.tmp.name, 'r.log')

    def test_the_newest_touch_walk_came_with_it(self):
        deep = os.path.join(self.root, self.put('scratch/d/e/deep'))
        os.utime(deep, (1, 4000000000))
        self.assertEqual(U.newest_touch(os.path.join(self.root, 'scratch/d')), 4000000000)

    def test_processes_under_sees_a_cwd_and_an_open_fd_and_nothing_beside_them(self):
        d = os.path.join(self.root, 'scratch', 'd')
        beside = os.path.join(self.root, 'scratch', 'dd')
        os.makedirs(d)
        os.makedirs(beside)
        p = subprocess.Popen(['sleep', '60'], cwd=d)
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        self.assertIn(p.pid, U.processes_under(d))
        self.assertNotIn(p.pid, U.processes_under(beside))
        with open(os.path.join(beside, 'f'), 'w'):
            self.assertIn(os.getpid(), U.processes_under(beside))

    def test_explicit_log_is_honoured(self):
        p = self.put('scratch/f')
        log = os.path.join(self.tmp.name, 'mine.log')
        U.remove_unforced(os.path.join(self.root, p), log=log)
        self.assertIn('BEFORE', open(log).read())


class HolderMustBeLive(Base):
    """A holder is checked when it is written: a name that is dead at birth is a deletion order with no floor."""

    def bad_holders(self):
        self.thread('t-closed', close=True)
        return ['lab:typo', 'thread:unknown', 'thread:t-closed']

    def attempt(self, verb, holder):
        src = self.put('labs/a/scratch/src')
        if verb == 'add':
            return self.sc('add', src, '--what', 'w', '--holder', holder)
        if verb == 'link':
            return self.sc('link', src, '--level', 'b', '--holder', holder)
        return self.sc('adopt', '--level', 'b', '--holder', holder)

    def manifests(self):
        return [open(os.path.join(self.root, d, 'MANIFEST')).read() for d in ('labs/a/scratch', 'labs/b/scratch')]

    def test_a_holder_that_is_not_live_is_refused_by_all_three_verbs(self):
        for verb in ('add', 'link', 'adopt'):
            for holder in self.bad_holders():
                before = self.manifests()
                rc, _, err = self.attempt(verb, holder)
                self.assertNotEqual(rc, 0, (verb, holder))
                self.assertIn(holder, err)
                self.assertEqual(self.manifests(), before, (verb, holder))

    def test_a_thread_is_refused_while_the_ledger_cannot_be_read(self):
        os.makedirs(self.ledger)
        for verb in ('add', 'link', 'adopt'):
            before = self.manifests()
            rc, _, err = self.attempt(verb, 'thread:t1')
            self.assertNotEqual(rc, 0, verb)
            self.assertIn('ledger', err)
            self.assertEqual(self.manifests(), before)

    def test_permanent_an_existing_lab_and_an_open_or_paused_thread_are_accepted(self):
        self.thread('t-open')
        self.thread('t-paused')
        T.append(self.ledger, {'ev': 'pause', 'id': 't-paused', 'ts': '2026-01-03T00:00:00Z'})
        for verb in ('add', 'link', 'adopt'):
            for holder in ('permanent', 'lab:a', 'thread:t-open', 'thread:t-paused'):
                rc, _, err = self.attempt(verb, holder)
                self.assertEqual(rc, 0, (verb, holder, err))


class OnlyTopLevelEntriesWereJudged(Base):
    """A level is judged by its frontier: a directory that something below is named or tracked is only a container,
    and everything beside the named thing is judged by itself."""

    def young(self, rel):
        soon = time.time() + 48.5 * 3600  # with aged(), 0.5 h old
        os.utime(os.path.join(self.root, rel), (soon, soon))

    def keep_old_young(self, base):
        keep, old, young = self.put(base + '/keep'), self.put(base + '/old'), self.put(base + '/young')
        self.sc('add', keep, '--what', 'w', '--holder', 'permanent')
        self.young(young)
        return keep, old, young

    def check_and_clean(self, keep, old, young):
        with self.aged():
            rc, out, _ = self.sc('check')
        self.assertEqual(rc, 1, out)
        self.assertIn('ORPHAN (on disk, unnamed): ' + old, out)
        self.assertIn('ORPHAN (on disk, unnamed): ' + young, out)
        self.assertNotIn('ORPHAN (on disk, unnamed): ' + keep, out)
        with self.aged():
            rc, out, _ = self.sc('clean')
        self.assertEqual(rc, 0, out)
        self.assertFalse(self.exists(old), out)
        self.assertTrue(self.exists(keep) and self.exists(young), out)
        self.assertIn('kept ' + young + ': unnamed', out)

    def test_one_live_name_does_not_keep_its_unnamed_siblings(self):
        self.check_and_clean(*self.keep_old_young('scratch/c'))

    def test_the_same_three_levels_deep(self):
        self.check_and_clean(*self.keep_old_young('scratch/c/d/e'))

    def test_a_tracked_file_makes_a_container_and_stays(self):
        tracked = self.put('scratch/t/tool.py')
        self.git('add', '-f', tracked)
        old = self.put('scratch/t/old')
        with self.aged():
            rc, out, _ = self.sc('check')
            self.assertIn('ORPHAN (on disk, unnamed): ' + old, out)
            self.assertNotIn('ORPHAN (on disk, unnamed): ' + tracked, out)
            self.sc('clean')
        self.assertFalse(self.exists(old))
        self.assertTrue(self.exists(tracked))

    def test_a_dead_name_above_a_live_one_frees_the_siblings_without_the_floor(self):
        self.thread('t1', close=True)
        self.row('scratch', 'scratch/a', 'thread:t1')
        b, sib = self.put('scratch/a/b'), self.put('scratch/a/sib')
        self.row('scratch', b, 'permanent')
        rc, out, _ = self.sc('clean')
        self.assertFalse(self.exists(sib), out)
        self.assertTrue(self.exists(b), out)

    def test_a_dead_name_below_a_directory_frees_it_and_the_young_sibling_stays(self):
        self.thread('t1', close=True)
        dead, sib = self.put('scratch/d/dead'), self.put('scratch/d/sib')
        self.row('scratch', dead, 'thread:t1')
        self.young(sib)
        with self.aged():
            rc, out, _ = self.sc('clean')
        self.assertFalse(self.exists(dead), out)
        self.assertTrue(self.exists(sib), out)

    def test_a_symlink_in_a_container_is_removed_as_a_link_and_a_linked_directory_is_not_descended(self):
        outside = os.path.join(self.tmp.name, 'outside')
        os.makedirs(os.path.join(outside, 'sub'))
        open(os.path.join(outside, 'sub', 'f'), 'w').close()
        keep = self.put('scratch/c/keep')
        self.sc('add', keep, '--what', 'w', '--holder', 'permanent')
        link = os.path.join(self.root, 'scratch/c/lnk')
        os.symlink(outside, link)
        # a name below the link would make a real directory a container; a link is never one
        self.row('scratch', 'scratch/c/lnk/sub', 'permanent')
        with self.aged():
            rc, out, _ = self.sc('clean')
        self.assertTrue(os.path.exists(os.path.join(outside, 'sub', 'f')), out)
        self.assertNotIn('kept scratch/c/lnk/sub/f', out)
        link2 = os.path.join(self.root, 'scratch/c/lnk2')
        os.symlink(outside, link2)
        with self.aged():
            self.sc('clean')
        self.assertTrue(os.path.exists(os.path.join(outside, 'sub', 'f')))
        self.assertFalse(os.path.lexists(link2))

    def test_a_write_protected_old_directory_in_a_container_is_kept_and_the_run_carries_on(self):
        keep = self.put('scratch/c/keep')
        self.sc('add', keep, '--what', 'w', '--holder', 'permanent')
        d = os.path.join(self.root, 'scratch/c/prot')
        os.makedirs(d)
        open(os.path.join(d, 'f'), 'w').close()
        os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(os.chmod, d, stat.S_IRWXU)
        old = self.put('scratch/c/zold')
        with self.aged():
            rc, out, _ = self.sc('clean')
        self.assertTrue(self.exists('scratch/c/prot/f'))
        self.assertIn('kept scratch/c/prot: not removable', out)
        self.assertFalse(self.exists(old), out)

    def test_adopt_names_the_deep_siblings_never_a_tracked_path_and_deletes_nothing(self):
        keep = self.put('scratch/c/keep')
        self.sc('add', keep, '--what', 'w', '--holder', 'permanent')
        sib = self.put('scratch/c/sib')
        tracked = self.put('scratch/c/tool.py')
        self.git('add', '-f', tracked)
        rc, out, _ = self.sc('adopt')
        self.assertIn(sib + '\t', out)
        self.assertNotIn(tracked + '\t', out)
        self.assertNotIn(keep + '\t', out)
        self.assertEqual(self.sc('adopt', '--level', 'main', '--holder', 'permanent')[0], 0)
        names = [r[0] for r in S.read_manifest(os.path.join(self.root, 'scratch'))]
        self.assertIn(sib, names)
        self.assertNotIn(tracked, names)
        self.assertTrue(all(self.exists(p) for p in (keep, sib, tracked)))


if __name__ == '__main__':
    unittest.main()
