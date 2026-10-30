"""Tests for scripts/shared_files.py: the blocks of the line files, the includes and units of the other shared files,
the own files, and the record of the parts in .agents/stack.json."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stdout

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import shared_files  # noqa: E402
from shared_files import Owner  # noqa: E402


def owner(key, lines=None, files=None, parts=None, units=None, starter=None, skipped=None):
    name = key.split(':')[0]
    removal = f'`just uninstall {key.split(":")[-1]}` removes them.'
    return Owner(key, f'the owner {key}', f'jnprofile-{name}', 'c299beb000', removal, lines or {}, files or {},
                 parts or {}, units or {}, starter or {}, skipped or {})


def installer():
    return Owner('jnterfaces', 'the installer', 'jnterfaces', 'abcdef1234',
                 'They stay while the project keeps .agents/stack.json.', json.loads(json.dumps(shared_files.JNTERFACES_LINES)))


PY = {'.gitignore': ['.venv/', 'dist/']}
TS = {'.gitignore': ['node_modules/', 'dist/']}
HOOKS = {'hooks': {'SessionStart': [{'matcher': 'startup', 'hooks': [{'type': 'command', 'command': 'start.sh'}]}]}}


class Base(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix='shared-files-test-')
        self.addCleanup(scratch.cleanup)
        self.target = Path(scratch.name)
        self.source = self.target / 'source'
        self.source.mkdir()
        self.parts = {}
        self.migrating = False

    def sync(self, active, known=None, command='just install', dry_run=False, stop=False, unsure=()):
        """One run. stop leaves the record as it was and keeps .agents/stack-pending.json: a run that wrote the
        files and stopped before it saved the record."""
        notes, parts, _ = shared_files.sync(self.target, active, known or active, command,
                                            {'jnprofile-python': 'c299beb'}, self.parts, unsure, dry_run,
                                            self.migrating)
        if not dry_run and not stop:
            self.parts = parts
            shared_files.finish(self.target)
        return notes

    def text(self, path='.gitignore'):
        return (self.target / path).read_text()

    def rules(self, path='.gitignore'):
        return [line for line in self.text(path).splitlines() if shared_files.is_rule(line)]

    def claims(self, path='.gitignore'):
        return shared_files.claims_of_entries(self.parts.get(path, {}))

    def history(self):
        path = self.target / shared_files.LOG
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def check(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = shared_files.check(self.target, self.parts)
        return code, out.getvalue()

    def source_file(self, name, text):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path


class LineFileTests(Base):
    def test_a_new_file_gets_a_block_with_a_precise_comment_and_the_record_its_lines(self):
        self.sync([owner('python', PY)], command='just install --profile python')
        text = self.text()
        self.assertTrue(text.startswith('# begin lines of python, the owner python, from jnprofile-python at c299beb.\n'))
        self.assertIn('# Last written to .gitignore by `just install --profile python`. The record is '
                      'parts.".gitignore" in .agents/stack.json.\n', text)
        self.assertIn('# end lines of python. `just uninstall python` removes them.\n', text)
        self.assertEqual(self.rules(), ['.venv/', 'dist/'])
        self.assertEqual(self.claims(), {'python': {'.venv': 'added', 'dist': 'added'}})
        self.assertEqual(self.parts['.gitignore']['dist'], {'state': 'added', 'needs': ['python'], 'holder': 'python'})
        self.assertEqual([entry['command'] for entry in self.history()], ['just install --profile python'])
        self.assertFalse((self.target / shared_files.PENDING).exists())

    def test_a_run_with_no_change_writes_nothing(self):
        self.sync([owner('python', PY)])
        before, entries = (self.target / '.gitignore').read_bytes(), len(self.history())
        self.assertEqual(self.sync([owner('python', PY)]), [])
        self.assertEqual((self.target / '.gitignore').read_bytes(), before)
        self.assertEqual(len(self.history()), entries)

    def test_a_shared_line_moves_to_the_next_owner_when_the_first_goes(self):
        py, ts = owner('python', PY), owner('typescript', TS)
        self.sync([py, ts])
        self.assertEqual(self.rules(), ['.venv/', 'dist/', 'node_modules/'])
        self.assertEqual(self.parts['.gitignore']['dist'],
                         {'state': 'added', 'needs': ['python', 'typescript'], 'holder': 'python'})
        self.sync([ts], [py, ts], command='just uninstall python')
        self.assertEqual(self.rules(), ['node_modules/', 'dist/'])
        self.assertEqual(self.parts['.gitignore']['dist']['holder'], 'typescript')
        self.assertNotIn('begin lines of python', self.text())
        self.sync([], [py, ts], command='just uninstall typescript')
        self.assertFalse((self.target / '.gitignore').exists())

    def test_your_lines_stay_and_come_after_the_blocks(self):
        (self.target / '.gitignore').write_text('# mine\ndist\n!keep.log\n')
        py = owner('python', PY)
        self.sync([py])
        self.assertEqual(self.rules(), ['.venv/', 'dist', '!keep.log'])
        self.assertTrue(self.text().endswith('\n\n# mine\ndist\n!keep.log\n'))
        self.assertEqual(self.claims(), {'python': {'.venv': 'added', 'dist': 'present'}})
        self.sync([], [py])
        self.assertEqual(self.text(), '# mine\ndist\n!keep.log\n')

    def test_a_line_that_you_deleted_stays_out(self):
        py = owner('python', PY)
        self.sync([py])
        (self.target / '.gitignore').write_text(self.text().replace('dist/\n', ''))
        self.sync([py])
        self.assertEqual(self.rules(), ['.venv/'])
        self.assertEqual(self.claims()['python']['dist'], 'declined')
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
        self.assertEqual(self.claims(), {'python': {'.venv': 'added', 'build': 'added'}})
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
        notes = self.sync([owner('python', {'sub/.gitignore': ['x/']})])
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue(any('sub is a symlink' in note for note in notes), notes)

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
        mine = 'a b\r\nc\x0cd\nlone\rcr\r\n'
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

    def test_the_files_of_the_installer_are_reserved(self):
        for path in ('.agents/stack-log.jsonl', '.agents/stack.json', '.agents/stack-pending.json',
                     '.agents/just/x.just', '.agents/config/x', '.agents/instructions/x.md'):
            with self.assertRaises(shared_files.SharedError):
                self.sync([owner('python', {path: ['x']})])

    def test_a_file_of_the_installer_in_another_letter_case_is_reserved(self):
        self.sync([owner('python', PY)])
        for path in ('.agents/Stack-Log.jsonl', '.agents/STACK.json', '.agents/JUST/x.just', '.agents/Config/x'):
            with self.assertRaises(shared_files.SharedError):
                self.sync([owner('python', {path: ['evil']})])
        self.assertEqual(self.check()[0], 0)

    def test_a_line_with_slashes_at_the_end_is_stable(self):
        py = owner('python', {'.gitignore': ['build//']})
        self.sync([py])
        entries = len(self.history())
        self.sync([owner('python', {'.gitignore': ['x/']})])
        self.sync([owner('python', {'.gitignore': ['x/']})])
        self.assertEqual(len(self.history()), entries + 1)

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

    def test_the_check_reads_past_a_byte_order_mark(self):
        (self.target / '.gitignore').write_bytes('﻿mine/\n'.encode())
        self.sync([owner('python', PY)])
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def test_your_copy_inside_the_block_of_another_owner_is_yours(self):
        py, ts = owner('python', PY), owner('typescript', TS)
        self.sync([py, ts])
        text = self.text().replace('.venv/\n', '.venv/\nnode_modules/\n', 1)
        (self.target / '.gitignore').write_text(text)
        self.sync([py, ts])
        self.sync([py], [py, ts])
        self.assertIn('node_modules/', self.rules())

    def test_a_dry_run_writes_nothing(self):
        notes = self.sync([owner('python', PY)], dry_run=True)
        self.assertIn('Would write .gitignore: adds .venv, dist', notes)
        self.assertFalse((self.target / '.gitignore').exists())
        self.assertFalse((self.target / '.agents').exists())
        self.assertEqual(self.parts, {})


class RecordTests(Base):
    """A run that stops, the record that merges, and the history."""

    def test_two_runs_that_stop_in_a_row_keep_the_record_of_the_first(self):
        py = owner('python', PY)
        self.sync([py], stop=True)
        self.sync([py], stop=True)
        self.sync([], [py])
        self.assertFalse((self.target / '.gitignore').exists())

    def test_a_run_that_stopped_after_it_wrote_keeps_its_lines_as_its_own(self):
        py = owner('python', PY)
        self.sync([py], stop=True)
        self.assertTrue((self.target / shared_files.PENDING).exists())
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn('A run stopped while it changed .gitignore', out)
        # The record still has nothing. The next run reads the pending file, so the lines are its own, not yours.
        self.sync([], [py])
        self.assertFalse((self.target / '.gitignore').exists())
        self.assertFalse((self.target / shared_files.PENDING).exists())

    def test_a_run_that_stopped_before_it_wrote_declines_nothing(self):
        py = owner('python', PY)
        self.sync([py])
        (self.target / '.gitignore').unlink()
        shared_files.save_pending(self.target, {'.gitignore': {'old': self.parts['.gitignore'],
                                                               'new': self.parts['.gitignore']}})
        self.assertEqual(self.check()[0], 1)
        self.sync([py])
        self.assertEqual(self.rules(), ['.venv/', 'dist/'])
        self.assertEqual(self.check()[0], 0)

    def test_an_unsure_line_never_removes_your_line(self):
        py = owner('python', PY)
        self.sync([py])
        old = self.parts['.gitignore']
        self.parts = {}
        shared_files.save_pending(self.target, {'.gitignore': {'old': {}, 'new': old}})
        (self.target / '.gitignore').write_text(self.text().replace('dist/\n', '') + 'dist/\n')
        self.sync([], [py])
        self.assertEqual(self.rules(), ['dist/'])

    def test_a_removal_that_stopped_is_finished_by_the_next_run(self):
        lint = owner('python:lint', parts={'ty.toml': self.source_file('ty.toml', '[src]\nexclude = [".agents"]\n')})
        self.sync([lint])
        before = self.parts
        self.sync([], [lint], stop=True)
        self.assertFalse((self.target / 'ty.toml').exists())
        # Put the file back as it was, as if the run stopped before it took the unit out.
        (self.target / 'ty.toml').write_text('[src]\nexclude = [".agents"]\n')
        self.parts = before
        self.sync([], [lint])
        self.assertFalse((self.target / 'ty.toml').exists())
        self.assertEqual(self.parts, {})

    def test_a_merge_that_kept_both_blocks_and_one_record_takes_the_block_into_the_record(self):
        py, lint = owner('python', PY), owner('python:lint', {'.gitignore': ['.ruff_cache/']})
        self.sync([py, lint])
        # The record of the other side has no lines of python:lint.
        del self.parts['.gitignore']['.ruff_cache']
        notes = self.sync([py, lint])
        self.assertTrue(any('takes it into the record' in note for note in notes), notes)
        self.assertEqual(self.claims()['python:lint'], {'.ruff_cache': 'added'})
        self.sync([py], [py, lint])
        self.assertEqual(self.rules(), ['.venv/', 'dist/'])

    def test_the_log_is_a_history_that_nothing_reads(self):
        py = owner('python', PY)
        self.sync([py])
        (self.target / shared_files.LOG).write_text('')
        self.sync([], [py])
        self.assertFalse((self.target / '.gitignore').exists())
        self.assertEqual(self.history()[0]['changes'], {'.gitignore': 'lines'})

    def test_the_installer_lines_name_the_history_and_the_pending_file(self):
        self.sync([installer(), owner('python', PY)])
        self.assertEqual(self.rules()[:2], ['.agents/stack-log.jsonl', '.agents/stack-pending.json'])
        self.assertFalse((self.target / '.agents/.gitattributes').exists())


class IncludeTests(Base):
    """Parts with an include: an own file, and one line or key in the shared file."""

    def ruff(self, text='line-length = 120\n'):
        return owner('python:lint', parts={'ruff.toml': self.source_file('ruff.toml', text)})

    def test_ruff_gets_an_extend_and_an_own_file(self):
        lint = self.ruff()
        self.sync([lint])
        self.assertEqual(self.text('.agents/config/python/ruff.toml'), 'line-length = 120\n')
        text = self.text('ruff.toml')
        self.assertIn('extend = ".agents/config/python/ruff.toml"\n', text)
        self.assertIn('# extend is a part of python:lint, the owner python:lint, from jnprofile-python at c299beb.',
                      text)
        self.assertEqual(self.parts['ruff.toml']['extend']['state'], 'added')
        self.assertEqual(self.parts['.agents/config/python/ruff.toml']['(own file)']['needs'], ['python:lint'])
        self.sync([], [lint])
        self.assertFalse((self.target / 'ruff.toml').exists())
        self.assertFalse((self.target / '.agents/config').exists())
        self.assertEqual(self.parts, {})

    def test_your_settings_in_ruff_toml_stay_and_the_extend_goes_with_its_owner(self):
        (self.target / 'ruff.toml').write_text('# mine\nline-length = 99\n\n[lint]\nselect = ["E"]\n')
        lint = self.ruff()
        self.sync([lint])
        self.assertIn('line-length = 99', self.text('ruff.toml'))
        self.assertIn('extend =', self.text('ruff.toml'))
        self.sync([], [lint])
        self.assertEqual(self.text('ruff.toml'), '# mine\nline-length = 99\n\n[lint]\nselect = ["E"]\n')

    def test_an_update_writes_the_own_file_again(self):
        self.sync([self.ruff()])
        notes = self.sync([self.ruff('line-length = 100\n')])
        self.assertEqual(self.text('.agents/config/python/ruff.toml'), 'line-length = 100\n')
        self.assertIn('Wrote .agents/config/python/ruff.toml for python:lint', notes)

    def test_an_extend_of_your_own_stays_and_no_own_file_is_written(self):
        (self.target / 'ruff.toml').write_text('extend = "base.toml"\n')
        lint = self.ruff()
        notes = self.sync([lint])
        self.assertEqual(self.text('ruff.toml'), 'extend = "base.toml"\n')
        self.assertFalse((self.target / '.agents/config').exists())
        self.assertTrue(any('The installer does not write the own file' in note for note in notes), notes)
        self.sync([], [lint])
        self.assertEqual(self.text('ruff.toml'), 'extend = "base.toml"\n')

    def test_a_ruff_toml_that_you_deleted_stays_deleted_and_its_own_file_goes(self):
        lint = self.ruff()
        self.sync([lint])
        (self.target / 'ruff.toml').unlink()
        notes = self.sync([lint])
        self.assertFalse((self.target / 'ruff.toml').exists())
        self.assertFalse((self.target / '.agents/config/python/ruff.toml').exists())
        self.assertEqual(self.parts['ruff.toml']['extend']['state'], 'declined')
        self.assertTrue(any('records it as declined' in note for note in notes), notes)
        self.sync([lint])
        self.assertFalse((self.target / 'ruff.toml').exists())

    def test_two_owners_of_one_ruff_include_stop_the_run(self):
        one = self.ruff()
        two = owner('other:lint', parts={'ruff.toml': self.source_file('other/ruff.toml', 'x = 1\n')})
        with self.assertRaises(shared_files.SharedError):
            self.sync([one, two])
        self.assertFalse((self.target / 'ruff.toml').exists())

    def test_two_tools_share_one_tsconfig_own_file(self):
        source = self.source_file('tsconfig.json', '{"include": ["${configDir}/src"]}\n')
        check = owner('typescript:typecheck', parts={'tsconfig.json': source})
        test = owner('typescript:test', parts={'tsconfig.json': source})
        self.sync([check, test])
        data = json.loads(self.text('tsconfig.json'))
        self.assertEqual(data, {'extends': ['./.agents/config/typescript/tsconfig.json']})
        self.sync([test], [check, test])
        self.assertTrue((self.target / '.agents/config/typescript/tsconfig.json').exists())
        self.assertEqual(self.parts['tsconfig.json']['extends["./.agents/config/typescript/tsconfig.json"]']['holder'],
                         'typescript:test')
        self.sync([], [check, test])
        self.assertFalse((self.target / 'tsconfig.json').exists())
        self.assertFalse((self.target / '.agents/config').exists())

    def test_your_tsconfig_keeps_its_keys_and_its_own_extends_comes_after(self):
        (self.target / 'tsconfig.json').write_text('{\n    "extends": "./base.json",\n    "compilerOptions": {"strict": false}\n}\n')
        check = owner('typescript:typecheck', parts={'tsconfig.json': self.source_file('tsconfig.json', '{}\n')})
        self.sync([check])
        self.sync([check])
        data = json.loads(self.text('tsconfig.json'))
        self.assertEqual(data['extends'], ['./.agents/config/typescript/tsconfig.json', './base.json'])
        self.assertIn('\n    "compilerOptions"', self.text('tsconfig.json'))
        self.sync([], [check])
        # Your extends was one string, so it is one string again.
        self.assertEqual(json.loads(self.text('tsconfig.json')),
                         {'extends': './base.json', 'compilerOptions': {'strict': False}})

    def test_a_string_extends_comes_back_when_the_parts_go_in_any_order(self):
        mine = {'extends': './base.json'}
        first = owner('typescript:typecheck', parts={'tsconfig.json': self.source_file('tsconfig.json', '{}\n')})
        second = owner('other', parts={'tsconfig.json': self.source_file('other.json', '{}\n')})
        for steps in ([[first], [first, second], [second], []], [[first], [second], []]):
            (self.target / 'tsconfig.json').write_text(json.dumps(mine) + '\n')
            self.parts = {}
            before = []
            for active in steps:
                self.sync(active, active + [o for o in before if o not in active])
                before = active
            self.assertEqual(json.loads(self.text('tsconfig.json')), mine, steps)

    def test_the_last_of_two_toml_units_takes_out_the_table_that_the_first_made(self):
        mine = '[project]\nname = "a"\n'
        (self.target / 'pyproject.toml').write_text(mine)
        first = owner('python', units={'pyproject.toml': {'tool.uv.default-groups': 'all'}})
        second = owner('other', units={'pyproject.toml': {'tool.uv.package': False}})
        self.sync([first])
        self.sync([first, second], [first, second])
        self.sync([second], [first, second])
        self.sync([], [first, second])
        self.assertEqual(self.text('pyproject.toml'), mine)

    def test_the_last_of_two_parts_takes_out_what_the_first_made(self):
        first = owner('typescript:typecheck', parts={'tsconfig.json': self.source_file('tsconfig.json', '{}\n')})
        second = owner('other', parts={'tsconfig.json': self.source_file('other.json', '{}\n')})
        (self.target / 'tsconfig.json').write_text('{"compilerOptions": {}}\n')
        self.sync([first])
        self.sync([first, second], [first, second])
        self.sync([second], [first, second])
        self.sync([], [first, second])
        self.assertEqual(json.loads(self.text('tsconfig.json')), {'compilerOptions': {}})

    def test_a_tsconfig_with_comments_is_left_alone_and_the_output_names_the_include(self):
        mine = '{\n  // mine\n  "compilerOptions": {}\n}\n'
        (self.target / 'tsconfig.json').write_text(mine)
        check = owner('typescript:typecheck', parts={'tsconfig.json': self.source_file('tsconfig.json', '{}\n')})
        notes = self.sync([check])
        self.assertEqual(self.text('tsconfig.json'), mine)
        self.assertTrue(any('Add the include yourself: extends ./.agents/config/typescript/tsconfig.json' in note
                            for note in notes), notes)
        # The include that the output names reads an own file, so the own file is there, until its owner goes.
        self.assertEqual(self.text('.agents/config/typescript/tsconfig.json'), '{}\n')
        self.sync([], [check])
        self.assertFalse((self.target / '.agents/config').exists())
        self.assertEqual(self.text('tsconfig.json'), mine)

    def test_biome_jsonc_takes_the_part_when_it_exists(self):
        mine = '{\n  // mine\n  "formatter": {}\n}\n'
        (self.target / 'biome.jsonc').write_text(mine)
        lint = owner('typescript:lint', parts={'biome.json': self.source_file('lint.json', '{}\n')})
        notes = self.sync([lint])
        self.assertFalse((self.target / 'biome.json').exists())
        self.assertEqual(self.text('biome.jsonc'), mine)
        self.assertTrue(any('biome.jsonc: the installer leaves the file alone' in note for note in notes), notes)
        self.assertEqual(self.text('.agents/config/typescript/lint.json'), '{}\n')

    def test_an_own_file_that_git_ignores_stops_the_run(self):
        subprocess.run(['git', 'init', '-q', str(self.target)], check=True)
        (self.target / '.gitignore').write_text('.agents/\n')
        with self.assertRaises(shared_files.SharedError) as caught:
            self.sync([self.ruff()])
        self.assertIn('.gitignore:1:.agents/', str(caught.exception))
        self.assertFalse((self.target / 'ruff.toml').exists())

    def test_a_skipped_part_is_not_added(self):
        (self.target / 'pyproject.toml').write_text('[tool.ruff]\n')
        lint = owner('python:lint', parts={'ruff.toml': self.source_file('ruff.toml', 'x = 1\n')},
                     skipped={'ruff.toml': ['pyproject.toml', '[tool.ruff']})
        notes = self.sync([lint])
        self.assertFalse((self.target / 'ruff.toml').exists())
        self.assertIn('Left out the part of python:lint in ruff.toml: pyproject.toml already has [tool.ruff', notes)


class UnitTests(Base):
    """Parts without an include: keys and items in TOML, items of a list in YAML, and hooks in JSON."""

    def test_ty_items_are_units_and_your_items_stay(self):
        (self.target / 'ty.toml').write_text('# mine\n[src]\nexclude = ["legacy"]\n')
        ty = owner('python:typecheck', parts={'ty.toml': self.source_file('ty.toml', '[src]\nexclude = [".agents"]\n')})
        self.sync([ty])
        self.assertEqual(shared_files.edit_toml.read(self.text('ty.toml'), ['src.exclude']),
                         {'src.exclude': ['legacy', '.agents']})
        self.sync([], [ty])
        self.assertEqual(self.text('ty.toml'), '# mine\n[src]\nexclude = ["legacy"]\n')

    def test_a_unit_with_your_value_stays_yours(self):
        (self.target / 'pyproject.toml').write_text('[tool.uv]\ndefault-groups = ["dev"]\n')
        py = owner('python', units={'pyproject.toml': {'tool.uv.default-groups': 'all'}})
        notes = self.sync([py])
        self.assertEqual(self.text('pyproject.toml'), '[tool.uv]\ndefault-groups = ["dev"]\n')
        self.assertTrue(any(note.startswith('Note: pyproject.toml: tool.uv.default-groups is already set')
                            and 'uv sync will not install the groups of the tools' in note for note in notes), notes)
        self.assertEqual(self.parts['pyproject.toml']['tool.uv.default-groups']['state'], 'present')
        self.sync([], [py])
        self.assertEqual(self.text('pyproject.toml'), '[tool.uv]\ndefault-groups = ["dev"]\n')

    def test_default_groups_goes_in_and_out_with_its_profile(self):
        mine = '[project]\nname = "app"\n'
        (self.target / 'pyproject.toml').write_text(mine)
        py = owner('python', units={'pyproject.toml': {'tool.uv.default-groups': 'all'}})
        self.sync([py])
        self.assertIn('[tool.uv]\n# tool.uv.default-groups is a part of python', self.text('pyproject.toml'))
        self.sync([], [py])
        self.assertEqual(self.text('pyproject.toml'), mine)

    def test_no_pyproject_toml_is_made_for_default_groups(self):
        py = owner('python', units={'pyproject.toml': {'tool.uv.default-groups': 'all'}})
        self.sync([py])
        self.assertFalse((self.target / 'pyproject.toml').exists())

    def test_a_unit_that_you_changed_stays_when_its_owner_goes(self):
        py = owner('python', units={'pyproject.toml': {'tool.uv.default-groups': 'all'}})
        (self.target / 'pyproject.toml').write_text('[project]\nname = "app"\n')
        self.sync([py])
        (self.target / 'pyproject.toml').write_text(self.text('pyproject.toml').replace('"all"', '["lint"]'))
        self.assertIn('tool.uv.default-groups of python is changed', self.check()[1])
        notes = self.sync([], [py])
        self.assertIn('default-groups = ["lint"]', self.text('pyproject.toml'))
        self.assertTrue(any('you changed tool.uv.default-groups' in note for note in notes), notes)

    def test_two_owners_with_two_values_of_one_unit_stop_the_run(self):
        one = owner('python', units={'pyproject.toml': {'tool.uv.default-groups': 'all'}})
        two = owner('other', units={'pyproject.toml': {'tool.uv.default-groups': ['dev']}})
        (self.target / 'pyproject.toml').write_text('')
        with self.assertRaises(shared_files.SharedError) as caught:
            self.sync([one, two])
        self.assertIn('python and other need tool.uv.default-groups with two values', str(caught.exception))

    def test_pre_commit_repos_keep_your_comments_and_indent(self):
        mine = ('# my checks\nrepos:\n    # my hook\n    -   repo: local\n        hooks:\n          - id: x\n'
                '            name: x\n            entry: x\n            language: system\n')
        (self.target / '.pre-commit-config.yaml').write_text(mine)
        source = self.source_file('pre-commit-config.yaml', '# top\nrepos:\n  # ruff\n  - repo: https://r\n    rev: v1\n'
                                  '    hooks:\n      - id: ruff\n')
        lint = owner('python:lint', parts={'.pre-commit-config.yaml': source})
        self.sync([lint])
        text = self.text('.pre-commit-config.yaml')
        self.assertTrue(text.startswith(mine), text)
        self.assertIn('\n    - repo: https://r\n      rev: v1\n', text)
        self.assertIn('    # repos[repo=https://r] is a part of python:lint,', text)
        self.sync([], [lint])
        self.assertEqual(self.text('.pre-commit-config.yaml'), mine)

    def test_a_new_pre_commit_file_goes_when_its_last_item_goes(self):
        source = self.source_file('pre-commit-config.yaml', 'repos:\n  - repo: https://r\n    rev: v1\n    hooks: []\n')
        lint = owner('python:lint', parts={'.pre-commit-config.yaml': source})
        self.sync([lint])
        self.assertTrue(self.text('.pre-commit-config.yaml').startswith('repos:\n'))
        self.sync([], [lint])
        self.assertFalse((self.target / '.pre-commit-config.yaml').exists())

    def test_hooks_are_merged_and_taken_out_by_command(self):
        settings = self.target / '.claude/settings.json'
        settings.parent.mkdir()
        settings.write_text(json.dumps({'permissions': {'allow': []}, 'hooks': {
            'SessionStart': [{'matcher': 'startup', 'hooks': [{'type': 'command', 'command': 'mine.sh'}]}]}}))
        role = owner('tasker', parts={'.claude/settings.json': self.source_file('settings.json', json.dumps(HOOKS))})
        self.sync([role])
        data = json.loads(settings.read_text())
        self.assertEqual([hook['command'] for hook in data['hooks']['SessionStart'][0]['hooks']], ['mine.sh', 'start.sh'])
        self.sync([], [role])
        self.assertEqual(json.loads(settings.read_text()), {'permissions': {'allow': []}, 'hooks': {
            'SessionStart': [{'matcher': 'startup', 'hooks': [{'type': 'command', 'command': 'mine.sh'}]}]}})

    def test_a_hook_that_you_deleted_stays_out(self):
        role = owner('tasker', parts={'.codex/hooks.json': self.source_file('hooks.json', json.dumps(HOOKS))})
        self.sync([role])
        (self.target / '.codex/hooks.json').write_text('{}\n')
        self.sync([role])
        self.assertEqual(json.loads(self.text('.codex/hooks.json')), {})
        self.assertEqual(next(iter(self.parts['.codex/hooks.json'].values()))['state'], 'declined')

    def test_a_marked_unit_with_no_record_is_taken_into_the_record(self):
        source = self.source_file('ty.toml', '[src]\nrespect-ignore-files = false\n')
        ty = owner('python:typecheck', parts={'ty.toml': source})
        self.sync([ty])
        self.parts = {}
        self.sync([ty])
        self.assertEqual(self.parts['ty.toml']['src.respect-ignore-files']['state'], 'added')
        self.sync([], [ty])
        self.assertFalse((self.target / 'ty.toml').exists())


class RoleTests(Base):
    """A role adds a starter, a pointer in AGENTS.md and the own file of its instructions."""

    def role(self, key='tasker', rules='# Rules\nWork on a branch.\n'):
        return owner(key, parts={'AGENTS.md': self.source_file(f'{key}.md', rules)},
                     starter={'AGENTS.md': self.source_file('starter.md', '# Project\nProject name: <replace-me>\n')})

    def test_a_new_agents_md_gets_the_starter_and_a_pointer(self):
        role = self.role()
        self.sync([role])
        text = self.text('AGENTS.md')
        self.assertTrue(text.startswith('<!-- begin lines of tasker, the owner tasker'), text)
        self.assertIn('\nRead and follow `.agents/instructions/tasker.md`.\n', text)
        self.assertTrue(text.endswith('\n\n# Project\nProject name: <replace-me>\n'))
        self.assertEqual(self.text('.agents/instructions/tasker.md'), '# Rules\nWork on a branch.\n')
        self.assertEqual(self.parts['AGENTS.md']['(starter)']['state'], 'seeded')
        self.sync([], [role])
        self.assertEqual(self.text('AGENTS.md'), '# Project\nProject name: <replace-me>\n')
        self.assertFalse((self.target / '.agents/instructions').exists())

    def test_your_agents_md_gets_only_the_pointer(self):
        (self.target / 'AGENTS.md').write_text('# Mine\n')
        self.sync([self.role()])
        self.assertTrue(self.text('AGENTS.md').endswith('\n\n# Mine\n'))
        self.assertNotIn('(starter)', self.parts['AGENTS.md'])

    def test_an_agents_md_that_you_delete_stays_deleted(self):
        role = self.role()
        self.sync([role])
        (self.target / 'AGENTS.md').unlink()
        self.sync([role])
        self.sync([role])
        self.assertFalse((self.target / 'AGENTS.md').exists())
        self.assertEqual(self.parts['AGENTS.md']['(starter)']['state'], 'seeded')
        self.assertTrue((self.target / '.agents/instructions/tasker.md').exists())
        # A new install of the role, after it went, writes the starter and the pointer again.
        self.sync([], [role])
        self.assertFalse((self.target / 'AGENTS.md').exists())
        self.sync([role])
        text = self.text('AGENTS.md')
        self.assertIn('Read and follow `.agents/instructions/tasker.md`.', text)
        self.assertTrue(text.endswith('# Project\nProject name: <replace-me>\n'), text)

    def test_a_kept_agents_md_that_you_delete_gets_the_starter_and_the_pointer(self):
        (self.target / 'AGENTS.md').write_text('# Mine\n')
        self.parts = {'AGENTS.md': {'(file)': {'state': 'kept', 'needs': ['tasker']}}}
        role = self.role()
        self.sync([role])
        self.assertEqual(self.text('AGENTS.md'), '# Mine\n')
        (self.target / 'AGENTS.md').unlink()
        self.sync([role])
        text = self.text('AGENTS.md')
        self.assertIn('Read and follow `.agents/instructions/tasker.md`.', text)
        self.assertTrue(text.endswith('# Project\nProject name: <replace-me>\n'), text)

    def test_a_new_install_of_a_role_writes_its_starter_while_another_role_stays(self):
        # The other role has a pointer in AGENTS.md but no starter.
        tasker = self.role()
        reporter = owner('reporter', parts={'AGENTS.md': self.source_file('reporter.md', '# Report\n')})
        self.sync([tasker, reporter])
        self.sync([reporter], [tasker, reporter])
        (self.target / 'AGENTS.md').unlink()
        self.sync([reporter, tasker], [tasker, reporter])
        self.assertTrue(self.text('AGENTS.md').endswith('# Project\nProject name: <replace-me>\n'))

    def test_a_starter_of_a_role_with_no_parts_stays_deleted_while_the_role_stays(self):
        alone = owner('starter-only', starter={'AGENTS.md': self.source_file('alone.md', '# Project\n')})
        self.sync([alone, self.role()])
        (self.target / 'AGENTS.md').unlink()
        self.sync([alone])
        self.assertFalse((self.target / 'AGENTS.md').exists())
        self.sync([], [alone])
        self.sync([alone])
        self.assertEqual(self.text('AGENTS.md'), '# Project\n')

    def test_with_two_roles_a_deleted_agents_md_stays_deleted(self):
        tasker, reporter = self.role(), self.role('reporter', '# Report\n')
        self.sync([tasker, reporter])
        for _ in range(2):
            (self.target / 'AGENTS.md').unlink(missing_ok=True)
            self.sync([tasker, reporter])
            self.assertFalse((self.target / 'AGENTS.md').exists())
        self.assertEqual(self.parts['AGENTS.md']['(starter)']['needs'], ['tasker', 'reporter'])

    def test_a_run_that_stopped_before_it_wrote_the_starter_writes_it_next_time(self):
        role = self.role()
        real = shared_files.write

        def stop_at_agents(file, data):
            if file.name == 'AGENTS.md':
                raise KeyboardInterrupt
            real(file, data)
        with mock.patch.object(shared_files, 'write', stop_at_agents):
            with self.assertRaises(KeyboardInterrupt):
                self.sync([role])
        # The old record in the pending file is the record before the run, which has no starter yet.
        pending = json.loads((self.target / shared_files.PENDING).read_text())
        self.assertNotIn('(starter)', pending['files']['AGENTS.md']['old'])
        self.sync([role])
        self.assertTrue(self.text('AGENTS.md').endswith('# Project\nProject name: <replace-me>\n'))

    def test_a_run_that_removed_the_file_while_it_stopped_does_not_bring_the_starter_back(self):
        alone = owner('starter-only', starter={'AGENTS.md': self.source_file('alone.md', '# Project\n')})
        tasker = self.role()
        self.sync([alone, tasker])
        # You keep only the block of tasker, so its removal empties the file and the installer deletes it.
        lines = self.text('AGENTS.md').splitlines(keepends=True)
        end = next(index for index, line in enumerate(lines) if 'end lines of tasker' in line)
        (self.target / 'AGENTS.md').write_text(''.join(lines[:end + 1]))
        self.sync([alone, tasker])
        self.sync([alone], [alone, tasker], stop=True)
        self.assertFalse((self.target / 'AGENTS.md').exists())
        self.sync([alone], [alone, tasker])
        self.assertFalse((self.target / 'AGENTS.md').exists())

    def test_a_second_role_adds_its_own_pointer(self):
        self.sync([self.role(), self.role('reporter', '# Report\n')])
        self.assertEqual([line for line in self.text('AGENTS.md').splitlines() if line.startswith('Read')],
                         ['Read and follow `.agents/instructions/tasker.md`.',
                          'Read and follow `.agents/instructions/reporter.md`.'])


class MoveTests(Base):
    """The first run after the old installer: the record from the old log and the records of the packages."""

    def old_log(self, *entries):
        (self.target / '.agents').mkdir(exist_ok=True)
        (self.target / shared_files.LOG).write_text(''.join(json.dumps(entry) + '\n' for entry in entries))

    def test_the_claims_of_the_log_become_the_record(self):
        self.old_log({'id': 'a', 'status': 'finished', 'command': 'baseline'},
                     {'id': 'b', 'status': 'started', 'lines': {'.gitignore': {
                         'python': {'added': ['.venv/'], 'present': ['dist/']}}}},
                     {'id': 'b', 'status': 'finished'})
        parts, unsure, last = shared_files.from_log(self.target, {})
        self.assertEqual(parts['.gitignore']['.venv'], {'state': 'added', 'needs': ['python'], 'holder': 'python'})
        self.assertEqual(parts['.gitignore']['dist'], {'state': 'present', 'needs': ['python']})
        self.assertEqual((unsure, last), (set(), 'b'))

    def test_an_unfinished_entry_makes_its_files_unsure(self):
        self.old_log({'id': 'b', 'status': 'started', 'lines': {'.gitignore': {'python': {'added': ['dist/']}}}})
        parts, unsure, _ = shared_files.from_log(self.target, {})
        self.assertEqual(unsure, {'.gitignore'})
        self.parts = parts
        self.sync([owner('python', PY)], unsure=unsure)
        self.assertEqual(self.rules(), ['.venv/', 'dist/'])

    def test_an_old_whole_file_that_nobody_changed_becomes_a_part(self):
        source = self.source_file('ruff.toml', 'line-length = 120\n')
        (self.target / 'ruff.toml').write_text('line-length = 120\n')
        self.old_log({'id': 'b', 'status': 'started', 'files': {'ruff.toml': {
            'owners': ['python:lint'], 'hash': shared_files.digest(b'line-length = 120\n')}}},
                     {'id': 'b', 'status': 'finished'})
        self.parts, _, _ = shared_files.from_log(self.target, {})
        self.migrating = True
        self.sync([owner('python:lint', parts={'ruff.toml': source})])
        self.assertIn('extend = ".agents/config/python/ruff.toml"', self.text('ruff.toml'))
        self.assertEqual(self.text('.agents/config/python/ruff.toml'), 'line-length = 120\n')

    def test_an_old_whole_file_that_you_changed_stays_yours_until_you_delete_it(self):
        source = self.source_file('ruff.toml', 'line-length = 120\n')
        (self.target / 'ruff.toml').write_text('line-length = 99\n')
        self.old_log({'id': 'b', 'status': 'started', 'files': {'ruff.toml': {
            'owners': ['python:lint'], 'hash': shared_files.digest(b'line-length = 120\n')}}},
                     {'id': 'b', 'status': 'finished'})
        self.parts, _, _ = shared_files.from_log(self.target, {})
        lint = owner('python:lint', parts={'ruff.toml': source})
        notes = self.sync([lint])
        self.assertEqual(self.text('ruff.toml'), 'line-length = 99\n')
        self.assertTrue(any('it stays yours, with no part' in note for note in notes), notes)
        self.sync([lint])
        self.assertEqual(self.text('ruff.toml'), 'line-length = 99\n')
        (self.target / 'ruff.toml').unlink()
        self.sync([lint])
        self.assertIn('extend =', self.text('ruff.toml'))

    def test_an_old_whole_file_that_you_deleted_stays_out(self):
        source = self.source_file('ruff.toml', 'line-length = 120\n')
        self.old_log({'id': 'b', 'status': 'started', 'files': {'ruff.toml': {'declined': True}}},
                     {'id': 'b', 'status': 'finished'})
        self.parts, _, _ = shared_files.from_log(self.target, {})
        self.sync([owner('python:lint', parts={'ruff.toml': source})])
        self.assertFalse((self.target / 'ruff.toml').exists())

    def test_an_old_seed_of_agents_md_becomes_the_starter_and_the_pointer(self):
        seed = '# Old template\nAll the rules.\n'
        (self.target / 'AGENTS.md').write_text(seed)
        self.parts, _, _ = shared_files.from_log(self.target, {'tasker': {'seeds': {
            'AGENTS.md': shared_files.digest(seed.encode())}}})
        role = RoleTests.role(self)
        self.sync([role])
        self.assertNotIn('All the rules', self.text('AGENTS.md'))
        self.assertIn('Project name: <replace-me>', self.text('AGENTS.md'))

    def test_an_old_agents_md_that_you_changed_stays_and_the_own_file_is_written(self):
        (self.target / 'AGENTS.md').write_text('# Mine now\n')
        self.parts, _, _ = shared_files.from_log(self.target, {'tasker': {'seeds': {'AGENTS.md': 'other'}}})
        self.sync([RoleTests.role(self)])
        self.assertEqual(self.text('AGENTS.md'), '# Mine now\n')
        self.assertTrue((self.target / '.agents/instructions/tasker.md').exists())

    def test_merged_hooks_become_units_of_the_role(self):
        settings = self.target / '.claude/settings.json'
        settings.parent.mkdir()
        settings.write_text(json.dumps(HOOKS))
        self.parts, _, _ = shared_files.from_log(self.target, {'tasker': {'merged_hooks': {'.claude/settings.json': HOOKS}}})
        role = owner('tasker', parts={'.claude/settings.json': self.source_file('settings.json', json.dumps(HOOKS))})
        self.sync([role])
        self.assertEqual(json.loads(settings.read_text()), HOOKS)
        self.sync([], [role])
        self.assertFalse(settings.exists())

    def test_the_old_default_groups_with_its_comment_becomes_a_unit(self):
        (self.target / 'pyproject.toml').write_text(
            '[tool.uv]\n# uv sync and uv run install these dependency groups, the groups of the tools of just install\n'
            'default-groups = "all"\n')
        py = owner('python', units={'pyproject.toml': {'tool.uv.default-groups': 'all'}})
        self.migrating = True
        self.sync([py])
        self.assertEqual(self.parts['pyproject.toml']['tool.uv.default-groups']['state'], 'added')

    def test_an_entry_of_the_wrong_shape_fails_cleanly(self):
        self.old_log({'id': 'x', 'lines': {'a': ['b']}})
        with self.assertRaises(shared_files.SharedError):
            shared_files.from_log(self.target, {})

    def test_a_log_that_is_not_json_fails_cleanly(self):
        (self.target / '.agents').mkdir()
        (self.target / shared_files.LOG).write_text('{"id": "x"}\nnot json\n')
        with self.assertRaises(shared_files.SharedError):
            shared_files.from_log(self.target, {})

    def test_a_path_outside_the_project_fails_cleanly(self):
        for path in ('../victim', '/tmp/victim', '.git/config', 'a\u0000b'):
            self.old_log({'id': 'x', 'status': 'finished', 'files': {path: {'owners': ['python'], 'hash': 'h'}}})
            with self.assertRaises(shared_files.SharedError):
                shared_files.from_log(self.target, {})

    def test_the_history_of_the_new_installer_is_not_read_as_the_old_log(self):
        self.sync([owner('python', PY)])
        self.assertEqual(shared_files.read_log(self.target), [])


class WholeFileTests(Base):
    """A package from before the parts ships whole files, for one release."""

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

    def test_a_whole_file_with_two_owners_goes_with_the_last(self):
        (self.source / 'tsconfig.json').write_text('{}\n')
        files = {'tsconfig.json': (self.source / 'tsconfig.json', None)}
        check, test = owner('typescript:typecheck', files=files), owner('typescript:test', files=files)
        self.sync([check])
        self.sync([check, test])
        self.assertEqual(self.parts['tsconfig.json']['(whole file)']['needs'],
                         ['typescript:typecheck', 'typescript:test'])
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

    def test_a_skip_if_file_through_a_link_is_not_read(self):
        outside = self.target / 'outside'
        outside.mkdir()
        (outside / 'pyproject.toml').write_text('[tool.ruff]\n')
        (self.target / 'cfg').symlink_to(outside)
        (self.source / 'ruff.toml').write_text('x\n')
        lint = owner('python:lint', files={'ruff.toml': (self.source / 'ruff.toml', ['cfg/pyproject.toml', '[tool.ruff'])})
        self.sync([lint])
        self.assertTrue((self.target / 'ruff.toml').is_file())


if __name__ == '__main__':
    unittest.main()
