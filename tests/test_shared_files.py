"""Tests for scripts/shared_files.py: the blocks of the line files, the whole files and the log of each change."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import shared_files  # noqa: E402
from shared_files import Owner  # noqa: E402


def owner(key, lines=None, files=None):
    name = key.split(':')[0]
    removal = f'`just uninstall {key.split(":")[-1]}` removes them.'
    return Owner(key, f'the owner {key}', f'jnprofile-{name}', 'c299beb000', removal, lines or {}, files or {})


INSTALLER = Owner('jnterfaces', 'the installer', 'jnterfaces', 'abcdef1234',
                  'They stay while the project keeps .agents/stack-log.jsonl.', shared_files.JNTERFACES_LINES)
PY = {'.gitignore': ['.venv/', 'dist/']}
TS = {'.gitignore': ['node_modules/', 'dist/']}


class SharedFilesTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix='shared-files-test-')
        self.addCleanup(scratch.cleanup)
        self.target = Path(scratch.name)
        self.source = self.target / 'source'
        self.source.mkdir()

    def sync(self, active, known=None, command='just install', dry_run=False):
        return shared_files.sync(self.target, active, known or active, command, {'jnprofile-python': 'c299beb'},
                                 dry_run)

    def text(self, path='.gitignore'):
        return (self.target / path).read_text()

    def rules(self, path='.gitignore'):
        return [line for line in self.text(path).splitlines() if shared_files.is_rule(line)]

    def log(self):
        return [json.loads(line) for line in (self.target / shared_files.LOG).read_text().splitlines()]

    def check(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = shared_files.check(self.target)
        return code, out.getvalue()

    def test_a_new_file_gets_a_block_with_a_precise_comment_and_the_log_two_entries(self):
        self.sync([owner('python', PY)], command='just install --profile python')
        text = self.text()
        self.assertTrue(text.startswith('# begin lines of python, the owner python, from jnprofile-python at c299beb.\n'))
        entry = self.log()[1]
        self.assertIn(f'# Last written to .gitignore by `just install --profile python`, entry {entry["id"]} of '
                      f'.agents/stack-log.jsonl.\n', text)
        self.assertIn('# end lines of python. `just uninstall python` removes them.\n', text)
        self.assertEqual(self.rules(), ['.venv/', 'dist/'])
        log = self.log()
        self.assertEqual([e['command'] for e in log[:2]], ['baseline', 'just install --profile python'])
        self.assertEqual([e['status'] for e in log], ['finished', 'started', 'finished'])
        self.assertEqual(log[1]['lines']['.gitignore']['python']['added'], ['.venv/', 'dist/'])
        self.assertEqual(log[1]['id'], log[2]['id'])

    def test_a_run_with_no_change_writes_nothing(self):
        self.sync([owner('python', PY)])
        before, entries = (self.target / '.gitignore').read_bytes(), len(self.log())
        self.assertEqual(self.sync([owner('python', PY)]), [])
        self.assertEqual((self.target / '.gitignore').read_bytes(), before)
        self.assertEqual(len(self.log()), entries)

    def test_a_shared_line_moves_to_the_next_owner_when_the_first_goes(self):
        py, ts = owner('python', PY), owner('typescript', TS)
        self.sync([py, ts])
        self.assertEqual(self.rules(), ['.venv/', 'dist/', 'node_modules/'])
        self.assertEqual(self.log()[1]['lines']['.gitignore']['typescript'],
                         {'added': ['node_modules/'], 'present': ['dist/']})
        self.sync([ts], [py, ts], command='just uninstall python')
        self.assertEqual(self.rules(), ['node_modules/', 'dist/'])
        self.assertNotIn('begin lines of python', self.text())
        self.sync([], [py, ts], command='just uninstall typescript')
        self.assertFalse((self.target / '.gitignore').exists())

    def test_your_lines_stay_and_come_after_the_blocks(self):
        (self.target / '.gitignore').write_text('# mine\ndist\n!keep.log\n')
        py = owner('python', PY)
        self.sync([py])
        self.assertEqual(self.rules(), ['.venv/', 'dist', '!keep.log'])
        self.assertTrue(self.text().endswith('\n\n# mine\ndist\n!keep.log\n'))
        self.assertEqual(self.log()[1]['lines']['.gitignore']['python'], {'added': ['.venv/'], 'present': ['dist/']})
        self.sync([], [py])
        self.assertEqual(self.text(), '# mine\ndist\n!keep.log\n')

    def test_a_line_that_you_deleted_stays_out(self):
        py = owner('python', PY)
        self.sync([py])
        (self.target / '.gitignore').write_text(self.text().replace('dist/\n', ''))
        self.sync([py])
        self.assertEqual(self.rules(), ['.venv/'])
        self.assertEqual(self.log()[-2]['lines']['.gitignore']['python'], {'declined': ['dist/']})
        self.sync([py])
        self.assertEqual(self.rules(), ['.venv/'])
        self.assertEqual(self.check()[0], 0)

    def test_a_line_file_that_you_deleted_stays_deleted(self):
        py = owner('python', PY)
        self.sync([py])
        (self.target / '.gitignore').unlink()
        self.sync([py])
        self.assertFalse((self.target / '.gitignore').exists())
        self.sync([py, owner('python:lint', {'.gitignore': ['.ruff_cache/']})])
        self.assertEqual(self.rules(), ['.ruff_cache/'])

    def test_a_line_that_you_moved_out_stays_and_goes_with_its_owner(self):
        py = owner('python', PY)
        self.sync([py])
        (self.target / '.gitignore').write_text(self.text().replace('dist/\n', '') + 'logs/\ndist/\n')
        self.sync([py])
        self.assertEqual(self.rules(), ['.venv/', 'logs/', 'dist/'])
        self.sync([], [py])
        self.assertEqual(self.rules(), ['logs/'])

    def test_a_line_that_is_twice_in_your_part_stays_with_a_note(self):
        py = owner('python', PY)
        self.sync([py])
        (self.target / '.gitignore').write_text(self.text().replace('dist/\n', '') + 'dist/\ndist\n')
        self.sync([py])
        notes = self.sync([], [py])
        self.assertEqual(self.rules(), ['dist/', 'dist'])
        self.assertTrue(any('is in the file 2 times' in note for note in notes), notes)

    def test_a_possible_match_is_named_and_stays(self):
        py = owner('python', PY)
        self.sync([py])
        # The owner goes in the same run as the edit; a run in between would record dist/ as declined.
        (self.target / '.gitignore').write_text(self.text().replace('dist/\n', '') + '/dist\n')
        notes = self.sync([], [py])
        self.assertEqual(self.rules(), ['/dist'])
        self.assertTrue(any('may be the same line' in note for note in notes), notes)

    def test_a_line_that_you_added_inside_a_block_moves_to_your_part(self):
        py = owner('python', PY)
        self.sync([py])
        (self.target / '.gitignore').write_text(self.text().replace('dist/\n', 'dist/\nmine/\n# my note\n'))
        code, out = self.check()
        self.assertIn('mine/ is inside the block of python', out)
        self.assertEqual(code, 0)
        self.sync([py])
        self.assertTrue(self.text().endswith('# end lines of python. `just uninstall python` removes them.\n\n'
                                             'mine/\n# my note\n'))
        self.sync([], [py])
        self.assertEqual(self.text(), 'mine/\n# my note\n')

    def test_a_block_without_its_end_comment_still_goes_with_its_owner(self):
        py = owner('python', PY)
        self.sync([py])
        (self.target / '.gitignore').write_text(
            '\n'.join(line for line in self.text().splitlines() if not line.startswith('# end')) + '\nmine/\n')
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn('the block of python lacks its begin or end comment', out)
        self.sync([], [py])
        self.assertEqual(self.text(), 'mine/\n')

    def test_a_newer_owner_adds_a_line_and_drops_a_line(self):
        self.sync([owner('python', PY)])
        newer = owner('python', {'.gitignore': ['.venv/', 'build/']})
        self.sync([newer], command='just install --update')
        self.assertEqual(self.rules(), ['.venv/', 'build/'])
        self.assertEqual(self.log()[-2]['lines']['.gitignore']['python'],
                         {'added': ['build/'], 'released': ['dist']})
        self.assertIn('by `just install --update`', self.text())

    def test_comments_of_an_owner_go_in_its_block_and_go_with_it(self):
        py = owner('python', {'.gitignore': ['# made by uv', '.venv/']})
        self.sync([py])
        self.assertIn('\n# made by uv\n.venv/\n', self.text())
        self.sync([], [py])
        self.assertFalse((self.target / '.gitignore').exists())

    def test_line_endings_and_a_missing_newline_at_the_end(self):
        (self.target / '.gitignore').write_bytes(b'mine/\r\nlogs/')
        before = (self.target / '.gitignore').read_bytes()
        self.sync([owner('python', {'.gitignore': ['logs/']})])
        self.assertEqual((self.target / '.gitignore').read_bytes(), before)
        self.sync([owner('python', PY)])
        data = (self.target / '.gitignore').read_bytes()
        self.assertNotIn(b'\n', data.replace(b'\r\n', b''))
        self.assertTrue(data.endswith(b'mine/\r\nlogs/\r\n'))

    def test_a_line_file_that_is_a_link_is_left_alone(self):
        (self.target / 'real').write_text('mine/\n')
        (self.target / '.gitignore').symlink_to('real')
        notes = self.sync([owner('python', PY)])
        self.assertIn('.gitignore: .gitignore is a symlink, so the installer leaves .gitignore alone', notes)
        self.assertEqual((self.target / 'real').read_text(), 'mine/\n')

    def test_no_write_goes_through_a_symlinked_folder(self):
        outside = self.target / 'outside'
        outside.mkdir()
        (self.target / 'sub').symlink_to(outside)
        (self.source / 'tsconfig.json').write_text('{}\n')
        whole = owner('typescript:test', files={'sub/tsconfig.json': (self.source / 'tsconfig.json', None)})
        notes = self.sync([whole])
        self.assertIn('sub/tsconfig.json: sub is a symlink, so the installer leaves sub/tsconfig.json alone', notes)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((self.target / shared_files.LOG).exists())
        notes = self.sync([owner('python', {'sub/.gitignore': ['x/']})])
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue(any('sub is a symlink' in note for note in notes), notes)

    # The findings of the adversarial review, one test each.

    def test_your_copy_of_an_owner_line_stays_when_the_owner_goes(self):
        py = owner('python', PY)
        self.sync([py])
        (self.target / '.gitignore').write_text(self.text() + '.venv/\n')
        self.sync([py])
        self.sync([], [py])
        self.assertEqual(self.text(), '.venv/\n')

    def test_your_comments_that_look_like_block_comments_stay(self):
        mine = ('# Last written to by hand\n# begin lines of docs, my own notes\nnotes/\n# end lines of docs. Mine.\n'
                '# end lines of python. a stray comment\n')
        (self.target / '.gitignore').write_text(mine)
        py = owner('python', PY)
        self.sync([py])
        self.assertTrue(self.text().endswith('\n\n' + mine), self.text())
        self.sync([], [py])
        self.assertEqual(self.text(), mine)

    def test_only_a_newline_ends_a_row_and_each_row_keeps_its_ending(self):
        mine = 'a b\r\nc\x0cd\nlone\rcr\r\n'
        (self.target / '.gitignore').write_bytes(mine.encode())
        py = owner('python', PY)
        self.sync([py])
        self.assertTrue((self.target / '.gitignore').read_bytes().endswith(mine.encode()))
        self.sync([], [py])
        self.assertEqual((self.target / '.gitignore').read_bytes(), mine.encode())

    def test_a_row_with_a_form_feed_is_one_row(self):
        # git reads x\x0c.venv/ as one pattern, so .venv/ is not in the file and the block needs it.
        (self.target / '.gitignore').write_bytes(b'x\x0c.venv/\n')
        self.sync([owner('python', PY)])
        self.assertIn(b'\n.venv/\n', (self.target / '.gitignore').read_bytes())

    def test_a_run_that_stopped_before_it_wrote_declines_nothing(self):
        py = owner('python', PY)
        self.sync([py])
        (self.target / '.gitignore').unlink()
        log = self.target / shared_files.LOG
        rows = log.read_text().splitlines()
        # Keep the baseline and the started entry only: the run stopped before it wrote .gitignore.
        log.write_text('\n'.join(rows[:2]) + '\n')
        self.assertEqual(self.check()[0], 1)
        self.sync([py])
        self.assertEqual(self.rules(), ['.venv/', 'dist/'])
        # The run recorded the claims again and settled the entry that stopped.
        self.assertEqual(self.check()[0], 0)

    def test_a_path_that_is_a_line_file_and_a_whole_file_stops_the_run(self):
        (self.source / 'gitignore').write_text('x\n')
        clash = owner('python:lint', files={'.gitignore': (self.source / 'gitignore', None)})
        with self.assertRaises(shared_files.SharedError):
            self.sync([owner('python', PY), clash])
        self.assertFalse((self.target / '.gitignore').exists())

    def test_the_files_of_the_installer_are_reserved(self):
        for path in ('.agents/stack-log.jsonl', '.agents/stack.json', '.agents/just/x.just'):
            with self.assertRaises(shared_files.SharedError):
                self.sync([owner('python', {path: ['x']})])

    def test_a_line_with_slashes_at_the_end_is_stable(self):
        py = owner('python', {'.gitignore': ['build//']})
        self.sync([py])
        entries = len(self.log())
        self.sync([owner('python', {'.gitignore': ['x/']})])
        self.sync([owner('python', {'.gitignore': ['x/']})])
        self.assertEqual(len(self.log()), entries + 2)

    def test_a_whole_file_that_you_deleted_stays_deleted(self):
        (self.source / 'ruff.toml').write_text('x\n')
        lint = owner('python:lint', files={'ruff.toml': (self.source / 'ruff.toml', None)})
        self.sync([lint])
        (self.target / 'ruff.toml').unlink()
        self.sync([lint])
        self.sync([lint])
        self.assertFalse((self.target / 'ruff.toml').exists())
        (self.target / 'ruff.toml').write_text('mine\n')
        self.sync([lint])
        self.sync([], [lint])
        self.assertEqual((self.target / 'ruff.toml').read_text(), 'mine\n')

    def test_a_shared_line_that_you_deleted_stays_out_for_every_owner(self):
        py, ts = owner('python', PY), owner('typescript', TS)
        self.sync([py, ts])
        (self.target / '.gitignore').write_text(self.text().replace('dist/\n', ''))
        self.sync([py, ts])
        self.assertNotIn('dist/', self.rules())
        self.sync([ts], [py, ts])
        self.assertEqual(self.rules(), ['node_modules/'])

    def test_your_own_line_that_you_deleted_stays_out(self):
        (self.target / '.gitignore').write_text('dist/\nmine/\n')
        py = owner('python', PY)
        self.sync([py])
        (self.target / '.gitignore').write_text(self.text().replace('dist/\n', ''))
        self.sync([py])
        self.assertEqual(self.rules(), ['.venv/', 'mine/'])
        self.assertFalse(self.text().endswith('\n\n'))

    def test_a_log_entry_of_the_wrong_shape_fails_cleanly(self):
        (self.target / '.agents').mkdir()
        (self.target / shared_files.LOG).write_text('{"id": "x", "lines": {"a": ["b"]}}\n')
        self.assertEqual(self.check()[0], 1)
        with self.assertRaises(shared_files.SharedError):
            self.sync([owner('python', PY)])

    def test_the_last_line_going_removes_a_file_of_blank_rows(self):
        py = owner('python', PY)
        self.sync([py])
        (self.target / '.gitignore').write_text(self.text().replace('dist/\n', '') + '\ndist/\n')
        self.sync([py])
        self.sync([], [py])
        self.assertFalse((self.target / '.gitignore').exists())

    def test_a_command_with_a_newline_stays_on_one_row(self):
        self.sync([owner('python', PY)], command='just install\nevil/')
        self.assertIn('by `just install evil/`', self.text())
        self.assertEqual(self.rules(), ['.venv/', 'dist/'])

    # The findings of the second adversarial review, one test each.

    def test_a_path_in_the_log_outside_the_project_stops_the_run(self):
        victim = self.target.parent / f'{self.target.name}-victim.txt'
        victim.write_text('keep\n')
        self.addCleanup(victim.unlink, missing_ok=True)
        (self.target / '.agents').mkdir()
        for path in (f'../{victim.name}', str(victim), '.git/config'):
            entry = {'id': 'x', 'status': 'finished', 'files': {path: {'owners': ['python'], 'hash': 'h'}}}
            (self.target / shared_files.LOG).write_text(json.dumps(entry) + '\n')
            with self.assertRaises(shared_files.SharedError):
                self.sync([])
            self.assertEqual(self.check()[0], 1)
        self.assertEqual(victim.read_text(), 'keep\n')

    def test_an_owner_path_outside_the_project_stops_the_run(self):
        for path in ('../x', '/tmp/x', '.git/info/exclude'):
            with self.assertRaises(shared_files.SharedError):
                self.sync([owner('python', {path: ['x']})])

    def test_your_line_inside_a_block_is_yours_when_an_owner_needs_it(self):
        py, ts = owner('python', PY), owner('typescript', TS)
        self.sync([py])
        (self.target / '.gitignore').write_text(self.text().replace('dist/\n', 'dist/\nnode_modules/\n'))
        self.sync([py, ts])
        self.sync([py], [py, ts])
        self.assertIn('node_modules/', self.rules())

    def test_a_whole_file_from_a_stopped_run_is_recorded_again(self):
        (self.source / 'ruff.toml').write_text('x\n')
        lint = owner('python:lint', files={'ruff.toml': (self.source / 'ruff.toml', None)})
        self.sync([lint])
        log = self.target / shared_files.LOG
        log.write_text('\n'.join(log.read_text().splitlines()[:-1]) + '\n')
        self.sync([lint])
        self.assertEqual(self.log()[-3]['files']['ruff.toml']['owners'], ['python:lint'])
        (self.target / 'ruff.toml').unlink()
        self.sync([lint])
        self.assertFalse((self.target / 'ruff.toml').exists())

    def test_a_new_decline_is_named_in_the_output(self):
        py = owner('python', PY)
        self.sync([py])
        (self.target / '.gitignore').write_text(self.text().replace('dist/\n', ''))
        notes = self.sync([py])
        self.assertTrue(any('dist/ is not in the file, so python records it as declined' in n for n in notes), notes)
        self.assertEqual(self.sync([py]), [])

    def test_a_byte_order_mark_stays_at_the_start(self):
        (self.target / '.gitignore').write_bytes('﻿.venv/\nmine/\n'.encode())
        py = owner('python', PY)
        self.sync([py])
        data = (self.target / '.gitignore').read_bytes().decode()
        self.assertTrue(data.startswith('﻿# begin lines of python'))
        self.assertEqual(data.count('.venv/'), 1)
        self.sync([], [py])
        self.assertEqual((self.target / '.gitignore').read_bytes(), '﻿.venv/\nmine/\n'.encode())

    def test_a_skip_if_rule_that_starts_to_apply_keeps_the_file(self):
        (self.source / 'ruff.toml').write_text('x\n')
        guard = ['pyproject.toml', '[tool.ruff']
        lint = owner('python:lint', files={'ruff.toml': (self.source / 'ruff.toml', guard)})
        self.sync([lint])
        (self.target / 'pyproject.toml').write_text('[tool.ruff]\n')
        self.sync([lint])
        self.assertTrue((self.target / 'ruff.toml').is_file())
        self.sync([], [lint])
        self.assertFalse((self.target / 'ruff.toml').exists())

    # The findings of the third adversarial review, one test each.

    def test_a_file_of_the_installer_in_another_letter_case_is_reserved(self):
        self.sync([owner('python', PY)])
        for path in ('.agents/Stack-Log.jsonl', '.agents/STACK.json', '.agents/JUST/x.just'):
            with self.assertRaises(shared_files.SharedError):
                self.sync([owner('python', {path: ['evil']})])
        self.assertEqual(self.check()[0], 0)

    def test_an_unsure_claim_never_removes_your_line(self):
        py = owner('python', PY)
        self.sync([py])
        log = self.target / shared_files.LOG
        log.write_text('\n'.join(log.read_text().splitlines()[:-1]) + '\n')
        (self.target / '.gitignore').write_text(self.text().replace('dist/\n', '') + 'dist/\n')
        self.sync([], [py])
        self.assertEqual(self.rules(), ['dist/'])

    def test_the_check_reads_past_a_byte_order_mark(self):
        (self.target / '.gitignore').write_bytes('﻿mine/\n'.encode())
        self.sync([owner('python', PY)])
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def test_a_stopped_entry_of_a_skipped_path_stays_unsettled(self):
        py = owner('python', {'sub/.gitignore': ['dist/']})
        self.sync([py])
        log = self.target / shared_files.LOG
        log.write_text('\n'.join(log.read_text().splitlines()[:-1]) + '\n')
        real = self.target / 'sub'
        moved = self.target / 'real-sub'
        real.rename(moved)
        real.symlink_to(moved)
        self.sync([py, owner('python:lint', {'.gitignore': ['x/']})])
        real.unlink()
        moved.rename(real)
        (real / '.gitignore').unlink()
        self.sync([py])
        self.assertEqual(self.rules('sub/.gitignore'), ['dist/'])

    def test_your_copy_inside_the_block_of_another_owner_is_yours(self):
        py, ts = owner('python', PY), owner('typescript', TS)
        self.sync([py, ts])
        text = self.text().replace('.venv/\n', '.venv/\nnode_modules/\n', 1)
        (self.target / '.gitignore').write_text(text)
        self.sync([py, ts])
        self.sync([py], [py, ts])
        self.assertIn('node_modules/', self.rules())

    def test_a_skip_if_file_through_a_link_is_not_read(self):
        outside = self.target / 'outside'
        outside.mkdir()
        (outside / 'pyproject.toml').write_text('[tool.ruff]\n')
        (self.target / 'cfg').symlink_to(outside)
        (self.source / 'ruff.toml').write_text('x\n')
        lint = owner('python:lint', files={'ruff.toml': (self.source / 'ruff.toml', ['cfg/pyproject.toml', '[tool.ruff'])})
        self.sync([lint])
        self.assertTrue((self.target / 'ruff.toml').is_file())

    def test_a_path_with_a_nul_in_the_log_fails_cleanly(self):
        (self.target / '.agents').mkdir()
        entry = {'id': 'x', 'status': 'finished', 'files': {'a\u0000b': {'owners': ['python'], 'hash': 'h'}}}
        (self.target / shared_files.LOG).write_text(json.dumps(entry) + '\n')
        with self.assertRaises(shared_files.SharedError):
            self.sync([])

    def test_a_whole_file_with_two_owners_goes_with_the_last(self):
        (self.source / 'tsconfig.json').write_text('{}\n')
        files = {'tsconfig.json': (self.source / 'tsconfig.json', None)}
        check, test = owner('typescript:typecheck', files=files), owner('typescript:test', files=files)
        self.sync([check])
        self.sync([check, test])
        self.assertEqual(self.log()[-2]['files']['tsconfig.json']['owners'], ['typescript:typecheck', 'typescript:test'])
        self.sync([test], [check, test], command='just uninstall typecheck')
        self.assertTrue((self.target / 'tsconfig.json').is_file())
        self.sync([], [check, test], command='just uninstall test')
        self.assertFalse((self.target / 'tsconfig.json').exists())

    def test_an_edited_whole_file_stays_when_its_owner_goes(self):
        (self.source / 'ruff.toml').write_text('line-length = 120\n')
        lint = owner('python:lint', files={'ruff.toml': (self.source / 'ruff.toml', None)})
        self.sync([lint])
        (self.target / 'ruff.toml').write_text('line-length = 99\n')
        self.assertIn('ruff.toml of python:lint is edited', self.check()[1])
        notes = self.sync([], [lint])
        self.assertTrue((self.target / 'ruff.toml').is_file())
        self.assertIn('ruff.toml stays: you edited it, and its owners python:lint are gone', notes)

    def test_your_whole_file_is_never_written_or_removed(self):
        (self.source / 'ruff.toml').write_text('line-length = 120\n')
        (self.target / 'ruff.toml').write_text('mine\n')
        lint = owner('python:lint', files={'ruff.toml': (self.source / 'ruff.toml', None)})
        self.sync([lint])
        self.sync([], [lint])
        self.assertEqual((self.target / 'ruff.toml').read_text(), 'mine\n')

    def test_a_skip_if_rule_leaves_a_whole_file_out(self):
        (self.source / 'ruff.toml').write_text('x\n')
        (self.target / 'pyproject.toml').write_text('[tool.ruff]\n')
        guard = ['pyproject.toml', '[tool.ruff']
        lint = owner('python:lint', files={'ruff.toml': (self.source / 'ruff.toml', guard)})
        notes = self.sync([lint])
        self.assertIn('Left out ruff.toml: pyproject.toml already has [tool.ruff', notes)
        self.assertFalse((self.target / 'ruff.toml').exists())

    def test_a_dry_run_writes_nothing(self):
        notes = self.sync([owner('python', PY)], dry_run=True)
        self.assertIn('Would write .gitignore: adds .venv, dist', notes)
        self.assertFalse((self.target / '.gitignore').exists())
        self.assertFalse((self.target / '.agents').exists())

    def test_a_run_that_stopped_is_named_by_the_check(self):
        py = owner('python', PY)
        self.sync([py])
        log = self.target / shared_files.LOG
        log.write_text('\n'.join(log.read_text().splitlines()[:-1]) + '\n')
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn('started and did not finish', out)
        self.sync([], [py])
        self.assertFalse((self.target / '.gitignore').exists())

    def test_a_log_that_is_not_json_fails_the_check_and_the_run(self):
        (self.target / '.agents').mkdir()
        (self.target / shared_files.LOG).write_text('{"id": "x"}\nnot json\n')
        self.assertEqual(self.check()[0], 1)
        with self.assertRaises(shared_files.SharedError):
            self.sync([owner('python', PY)])

    def test_two_branches_merge_the_log_with_no_conflict(self):
        def git(*args):
            return subprocess.run(['git', '-c', 'user.name=T', '-c', 'user.email=t@example.invalid', *args],
                                  cwd=self.target, check=True, capture_output=True, text=True)
        git('init', '-qb', 'main')
        self.sync([INSTALLER, owner('python', PY)])
        self.assertEqual(self.rules('.agents/.gitattributes'), ['stack-log.jsonl merge=union'])
        git('add', '-A')
        git('commit', '-qm', 'one')
        git('checkout', '-qb', 'side')
        self.sync([INSTALLER, owner('python', PY), owner('python:lint', {'.worktreedisposable': ['.ruff_cache/']})])
        git('commit', '-qam', 'side')
        git('add', '-A')
        git('commit', '-qm', 'side files')
        git('checkout', '-q', 'main')
        self.sync([INSTALLER, owner('python', PY), owner('typescript', {'.dockerignore': ['node_modules/']})])
        git('add', '-A')
        git('commit', '-qm', 'main')
        git('merge', '-q', '--no-edit', 'side')
        ids = [entry['id'] for entry in self.log()]
        self.assertEqual(len(ids), 7)
        self.assertEqual(self.check()[0], 0)


if __name__ == '__main__':
    unittest.main()
