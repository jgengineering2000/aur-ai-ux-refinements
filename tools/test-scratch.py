#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""test-scratch -- executable decision table for scratch.py and unforced.py. HERMETIC: each case builds a synthetic
project root (a main scratch, labs/*/scratch, a ledger) under a temp directory; the receipt log is redirected there.

    python3 tools/test-scratch.py
"""
import contextlib, io, os, stat, sys, tempfile, unittest

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
        item = self.put('scratch/x')
        rc, _, err = self.sc('add', item, '--what', 'w', '--holder', 'the census')
        self.assertEqual(rc, 2)
        self.assertIn('free text is refused', err)
        self.assertFalse(os.path.exists(os.path.join(self.root, 'scratch', 'MANIFEST')))

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
        self.sc('add', held, '--what', 'w', '--holder', 'thread:t1')  # t1 is not in the ledger
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
        self.sc('add', dead, '--what', 'w', '--holder', 'thread:t2')
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
        rc, out, _ = self.sc('clean', '--dry-run')
        self.assertIn('would free', out)
        self.assertTrue(self.exists('scratch/orphan'))

    def test_an_item_a_process_holds_open_is_kept(self):
        p = self.put('scratch/open')
        with open(os.path.join(self.root, p)):
            rc, out, _ = self.sc('clean')
        self.assertTrue(self.exists(p))
        self.assertIn('kept scratch/open: open in pid', out)

    def test_write_protected_is_preserved_and_reported(self):
        d = os.path.join(self.root, 'scratch', 'prot')
        os.makedirs(d)
        open(os.path.join(d, 'f'), 'w').close()
        os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(os.chmod, d, stat.S_IRWXU)
        rc, out, err = self.sc('clean')
        self.assertTrue(self.exists('scratch/prot/f'))
        self.assertIn('kept scratch/prot: not removable', out)

    def test_receipts_are_written_by_the_one_routine(self):
        self.put('scratch/orphan')
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


class Hoist(Base):
    def test_unforced_has_a_generic_default_log_and_no_project_name(self):
        del os.environ['AUR_RECEIPT_LOG']
        os.environ['XDG_DATA_HOME'] = self.tmp.name
        self.addCleanup(os.environ.pop, 'XDG_DATA_HOME', None)
        self.assertEqual(U.receipt_log_path(), os.path.join(self.tmp.name, 'aur', 'cleanup-receipts.log'))
        os.environ['AUR_RECEIPT_LOG'] = os.path.join(self.tmp.name, 'r.log')

    def test_explicit_log_is_honoured(self):
        p = self.put('scratch/f')
        log = os.path.join(self.tmp.name, 'mine.log')
        U.remove_unforced(os.path.join(self.root, p), log=log)
        self.assertIn('BEFORE', open(log).read())


if __name__ == '__main__':
    unittest.main()
