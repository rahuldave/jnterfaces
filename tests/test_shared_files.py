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


def stopping_at(name):
    """Stop the run when it writes the file name."""
    real = shared_files.write

    def write(file, data):
        if file.name == name:
            raise KeyboardInterrupt
        real(file, data)
    return mock.patch.object(shared_files, 'write', write)


class RecordTests(Base):
    """A run that stops, the record that merges, and the history."""

    def test_two_runs_that_stop_in_a_row_keep_the_record_of_the_first(self):
        py = owner('python', PY)
        self.sync([py], stop=True)
        self.sync([py], stop=True)
        self.sync([], [py])
        self.assertFalse((self.target / '.gitignore').exists())

    def test_a_second_run_that_stops_keeps_the_new_record_of_the_first(self):
        (self.target / '.gitignore').write_text('mine/\n')
        py = owner('python', PY)
        # The first run writes the block and stops. The second removes python and stops before it writes.
        self.sync([py], stop=True)
        with stopping_at('.gitignore'), self.assertRaises(KeyboardInterrupt):
            self.sync([], [py])
        self.sync([], [py])
        self.assertEqual(self.text(), 'mine/\n')

    def test_a_run_that_stops_on_biome_jsonc_files_the_pending_file_under_biome_json(self):
        (self.target / 'biome.jsonc').write_text('{\n  "formatter": {"enabled": true}\n}\n')
        ts = owner('ts:lint', parts={'biome.json': self.source_file('biome.json', '{"linter": {"enabled": true}}\n')})
        self.sync([ts], stop=True)
        pending = json.loads((self.target / shared_files.PENDING).read_text())
        self.assertIn('biome.json', pending['files'])
        self.assertNotIn('biome.jsonc', pending['files'])
        self.sync([], [ts])
        self.assertNotIn('extends', self.text('biome.jsonc'))

    def test_a_unit_that_a_stopped_run_took_out_is_yours_when_you_add_it_again(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "x"\n')
        py = owner('python', units={'pyproject.toml': {'tool.uv.package': False}})
        lint = owner('lint', files={'tool.cfg': (self.source_file('tool.cfg', 'a\n'), None)})
        self.sync([py], stop=True)
        # This run takes the key out of pyproject.toml, then stops at a later file.
        with stopping_at('tool.cfg'), self.assertRaises(KeyboardInterrupt):
            self.sync([lint], [py, lint])
        self.assertNotIn('package', self.text('pyproject.toml'))
        (self.target / 'pyproject.toml').write_text('[project]\nname = "x"\n\n[tool.uv]\npackage = false\ndev = 1\n')
        self.sync([lint], [py, lint])
        self.assertIn('package = false', self.text('pyproject.toml'))

    def test_a_pending_file_of_an_older_installer_under_biome_jsonc_still_counts(self):
        ts = owner('ts:lint', parts={'biome.json': self.source_file('biome.json', '{"linter": {"enabled": true}}\n')})
        (self.target / 'biome.jsonc').write_text('{\n  "formatter": {"enabled": true}\n}\n')
        self.sync([ts], stop=True)
        file = self.target / shared_files.PENDING
        data = json.loads(file.read_text())
        data['files']['biome.jsonc'] = data['files'].pop('biome.json')
        file.write_text(json.dumps(data))
        self.sync([], [ts])
        self.assertNotIn('extends', self.text('biome.jsonc'))

    def value_owners(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "x"\n')
        return (owner('python', PY, units={'pyproject.toml': {'tool.uv.package': False}}),
                owner('python', TS, units={'pyproject.toml': {'tool.uv.package': True}}))

    def test_a_value_that_changes_across_two_stops_stays_the_installers(self):
        first, second = self.value_owners()
        self.sync([first], stop=True)
        with stopping_at('.gitignore'), self.assertRaises(KeyboardInterrupt):
            self.sync([second])
        notes = self.sync([second])
        self.assertIn('package = true', self.text('pyproject.toml'))
        self.assertFalse(any('you changed' in note for note in notes))
        self.assertEqual(self.parts['pyproject.toml']['tool.uv.package']['state'], 'added')
        self.assertNotIn('earlier', self.parts['pyproject.toml']['tool.uv.package'])

    def test_a_value_that_changes_in_a_run_that_stops_before_it_writes_stays_the_installers(self):
        first, second = self.value_owners()
        self.sync([first])
        with stopping_at('.gitignore'), self.assertRaises(KeyboardInterrupt):
            self.sync([second])
        self.assertIn('package = false', self.text('pyproject.toml'))
        self.sync([second])
        self.assertIn('package = true', self.text('pyproject.toml'))

    def test_a_removal_after_a_stopped_change_of_value_takes_the_unit_out(self):
        first, second = self.value_owners()
        self.sync([first])
        with stopping_at('.gitignore'), self.assertRaises(KeyboardInterrupt):
            self.sync([second])
        self.sync([], [second])
        self.assertNotIn('package', self.text('pyproject.toml'))

    def test_a_unit_that_a_stopped_run_never_wrote_is_yours_when_you_add_it(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "x"\n')
        py = owner('python', units={'pyproject.toml': {'tool.uv.package': False}})
        lint = owner('lint', files={'tool.cfg': (self.source_file('tool.cfg', 'a\n'), None)})
        # The first run stops before it writes pyproject.toml. The second has nothing to write there.
        with stopping_at('pyproject.toml'), self.assertRaises(KeyboardInterrupt):
            self.sync([py])
        with stopping_at('tool.cfg'), self.assertRaises(KeyboardInterrupt):
            self.sync([lint], [py, lint])
        (self.target / 'pyproject.toml').write_text('[project]\nname = "x"\n\n[tool.uv]\npackage = false\ndev = 1\n')
        self.sync([lint], [py, lint])
        self.assertIn('package = false', self.text('pyproject.toml'))

    def test_a_file_that_a_run_cannot_read_keeps_the_values_of_a_stopped_run(self):
        first, second = self.value_owners()
        self.sync([first], stop=True)
        with stopping_at('.gitignore'), self.assertRaises(KeyboardInterrupt):
            self.sync([second])
        good = self.text('pyproject.toml')
        (self.target / 'pyproject.toml').write_text(good + '[[[broken\n')
        self.sync([second])
        (self.target / 'pyproject.toml').write_text(good)
        notes = self.sync([second])
        self.assertFalse(any('you changed' in note for note in notes))
        self.assertIn('package = true', self.text('pyproject.toml'))
        self.assertEqual(self.parts['pyproject.toml']['tool.uv.package']['state'], 'added')
        self.assertNotIn('earlier', self.parts['pyproject.toml']['tool.uv.package'])

    def unsure_then_unreadable(self, spoil):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "x"\n')
        py = owner('python', units={'pyproject.toml': {'tool.uv.package': False}})
        with stopping_at('pyproject.toml'), self.assertRaises(KeyboardInterrupt):
            self.sync([py])
        mend = spoil(self.target / 'pyproject.toml')
        self.sync([py])
        self.assertTrue(self.parts['pyproject.toml']['tool.uv.package'].get('unsure'))
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn('a later run could not read the file', out)
        mend()
        notes = self.sync([py])
        self.assertFalse(any('declined' in note for note in notes))
        self.assertIn('package = false', self.text('pyproject.toml'))
        self.assertNotIn('unsure', self.parts['pyproject.toml']['tool.uv.package'])

    def test_a_file_that_a_run_cannot_read_stays_unsure(self):
        def spoil(file):
            good = file.read_text()
            file.write_text('[project\nname = "x"\n')
            return lambda: file.write_text(good)
        self.unsure_then_unreadable(spoil)

    def test_a_file_that_is_a_symlink_for_one_run_stays_unsure(self):
        def spoil(file):
            real = file.with_name('real.toml')
            real.write_text(file.read_text())
            file.unlink()
            file.symlink_to(real)

            def mend():
                file.unlink()
                file.write_text(real.read_text())
            return mend
        self.unsure_then_unreadable(spoil)

    def test_a_file_that_is_yours_after_a_stopped_run_is_not_left_unsure(self):
        (self.target / '.gitignore').write_text('mine/\n')
        self.parts = {'.gitignore': {'(file)': {'state': 'kept', 'needs': ['python']}}}
        py = owner('python', PY)
        self.sync([py], unsure=['.gitignore'])
        self.assertNotIn('unsure', self.parts['.gitignore']['(file)'])
        code, out = self.check()
        self.assertNotIn('could not read the file', out)

    def test_a_file_that_is_yours_and_a_symlink_is_not_left_unsure(self):
        (self.target / 'real-gitignore').write_text('mine/\n')
        (self.target / '.gitignore').symlink_to(self.target / 'real-gitignore')
        self.parts = {'.gitignore': {'(file)': {'state': 'kept', 'needs': ['python']}}}
        self.sync([owner('python', PY)], unsure=['.gitignore'])
        self.assertNotIn('unsure', self.parts['.gitignore']['(file)'])
        self.assertEqual(self.check()[0], 0)

    def test_a_unit_file_that_is_yours_and_a_symlink_is_not_left_unsure(self):
        (self.target / 'real.toml').write_text('line-length = 100\n')
        (self.target / 'ruff.toml').symlink_to(self.target / 'real.toml')
        self.parts = {'ruff.toml': {'(file)': {'state': 'kept', 'needs': ['python:lint']}}}
        lint = owner('python:lint', parts={'ruff.toml': self.source_file('ruff.toml', 'line-length = 88\n')})
        self.sync([lint], unsure=['ruff.toml'])
        self.assertNotIn('unsure', self.parts['ruff.toml']['(file)'])

    def test_a_pyproject_that_you_delete_after_a_stop_is_not_unsure(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "x"\n')
        py = owner('python', units={'pyproject.toml': {'tool.uv.package': False}})
        with stopping_at('pyproject.toml'), self.assertRaises(KeyboardInterrupt):
            self.sync([py])
        (self.target / 'pyproject.toml').unlink()
        self.sync([py])
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn('pyproject.toml: the file is not there', out)
        self.assertNotIn('could not read the file', out)

    def test_a_value_that_you_set_after_a_stop_is_still_yours(self):
        first, second = self.value_owners()
        self.sync([first])
        with stopping_at('.gitignore'), self.assertRaises(KeyboardInterrupt):
            self.sync([second])
        text = self.text('pyproject.toml').replace('package = false', 'package = "mine"')
        (self.target / 'pyproject.toml').write_text(text)
        self.sync([second])
        self.assertIn('package = "mine"', self.text('pyproject.toml'))

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

    def test_an_include_is_written_as_biome_formats_it(self):
        (self.target / 'tsconfig.json').write_text('{\n  "compilerOptions": {"strict": true}\n}\n')
        tsc = owner('typescript:typecheck', parts={'tsconfig.json': self.source_file('tsconfig.json', '{}\n')})
        self.sync([tsc])
        self.assertEqual(self.text('tsconfig.json'), '{\n  "compilerOptions": {\n    "strict": true\n  },\n'
                         '  "extends": ["./.agents/config/typescript/tsconfig.json"]\n}\n')

    def test_a_list_that_does_not_fit_the_line_has_one_item_on_each_line(self):
        text = shared_files.json_dump({'include': ['a' * 30, 'b' * 30], 'n': [1, 2]}, None, {'width': 60})
        self.assertEqual(text, '{\n  "include": [\n    "' + 'a' * 30 + '",\n    "' + 'b' * 30 + '"\n  ],\n'
                               '  "n": [1, 2]\n}\n')
        # The line '  "include": ["a...", "b..."]' is 81 characters.
        self.assertEqual(shared_files.json_dump({'include': ['a' * 30, 'b' * 30]}, None, {'width': 81}),
                         '{\n  "include": ["' + 'a' * 30 + '", "' + 'b' * 30 + '"]\n}\n')

    def test_the_comma_after_a_list_counts_in_the_line(self):
        # The line '  "a": ["xxxxxx"],' is 18 characters, with its comma.
        self.assertEqual(shared_files.json_dump({'a': ['xxxxxx'], 'b': 1}, None, {'width': 18}),
                         '{\n  "a": ["xxxxxx"],\n  "b": 1\n}\n')
        self.assertEqual(shared_files.json_dump({'a': ['xxxxxx'], 'b': 1}, None, {'width': 17}),
                         '{\n  "a": [\n    "xxxxxx"\n  ],\n  "b": 1\n}\n')

    def test_lists_of_lists_and_of_numbers_take_the_layout_of_biome(self):
        # The texts that the Biome formatter 2 prints for these values.
        self.assertEqual(shared_files.json_dump({'k': [[1, 2], [3]]}, None, {'width': 80}), '{\n  "k": [[1, 2], [3]]\n}\n')
        self.assertEqual(shared_files.json_dump({'n': [1, 2.5, -3]}, None, {'width': 15}),
                         '{\n  "n": [\n    1, 2.5, -3\n  ]\n}\n')
        self.assertEqual(shared_files.json_dump({'n': [100, 101, 102]}, None, {'width': 13}),
                         '{\n  "n": [\n    100, 101,\n    102\n  ]\n}\n')

    def test_the_line_width_comes_from_the_biome_config_of_the_project(self):
        width = lambda: shared_files.json_style(self.target)('tsconfig.json')['width']
        self.assertEqual(width(), 80)
        (self.target / '.agents/config/typescript').mkdir(parents=True)
        (self.target / '.agents/config/typescript/lint.json').write_text('{"formatter": {"lineWidth": 120}}')
        (self.target / 'biome.json').write_text('{"extends": ["./.agents/config/typescript/lint.json"]}')
        self.assertEqual(width(), 120)
        (self.target / 'biome.json').write_text('{"extends": ["./.agents/config/typescript/lint.json"], '
                                                '"json": {"formatter": {"lineWidth": 100}}}')
        self.assertEqual(width(), 100)

    def test_the_indent_comes_from_the_biome_config_and_a_tab_is_the_default(self):
        style = lambda: shared_files.json_style(self.target)('tsconfig.json')
        self.assertEqual(style()['indent'], None)
        (self.target / 'biome.json').write_text('{}')
        self.assertEqual(style()['indent'], '\t')
        self.assertEqual(shared_files.json_dump({'a': {'b': 1}}, None, style()), '{\n\t"a": {\n\t\t"b": 1\n\t}\n}\n')
        (self.target / 'biome.json').write_text('{"formatter": {"indentStyle": "space", "indentWidth": 4}}')
        self.assertEqual(shared_files.json_dump({'a': 1}, '{\n  "a": 0\n}\n', style()), '{\n    "a": 1\n}\n')

    def test_a_tab_counts_as_the_indent_width_in_the_line(self):
        # Biome counts the tab before "k" as indentWidth columns: 1 + 5 + 10 + 2 + 1 = 19 columns at indentWidth 2.
        value = {'k': ['abcdefgh']}
        self.assertEqual(shared_files.json_dump(value, None, {'width': 19, 'indent': '\t', 'tab': 2}),
                         '{\n\t"k": ["abcdefgh"]\n}\n')
        self.assertEqual(shared_files.json_dump(value, None, {'width': 19, 'indent': '\t', 'tab': 4}),
                         '{\n\t"k": [\n\t\t"abcdefgh"\n\t]\n}\n')

    def test_a_file_with_no_biome_config_keeps_its_tab_indent(self):
        self.assertEqual(shared_files.json_dump({'a': 1}, '{\n\t"a": 0\n}\n'), '{\n\t"a": 1\n}\n')

    def test_the_style_reads_a_biome_jsonc_with_comments_and_trailing_commas(self):
        (self.target / 'biome.jsonc').write_text('{\n  // mine, with a "quote"\n  /* a block */ "formatter": '
                                                 '{"lineWidth": 120, "indentStyle": "space",},\n  "x": "a//b",\n}\n')
        style = shared_files.json_style(self.target)('tsconfig.json')
        self.assertEqual((style['width'], style['indent']), (120, 2))

    def test_an_extends_is_read_from_the_folder_of_the_file_that_names_it(self):
        (self.target / 'cfg').mkdir()
        (self.target / 'cfg/a.json').write_text('{"extends": ["./b.json"]}')
        (self.target / 'cfg/b.json').write_text('{"formatter": {"lineWidth": 120}}')
        (self.target / 'biome.json').write_text('{"extends": ["./cfg/a.json"]}')
        self.assertEqual(shared_files.json_style(self.target)('tsconfig.json')['width'], 120)

    def test_the_settings_of_all_the_files_merge_and_json_wins(self):
        (self.target / 'more.json').write_text('{"json": {"formatter": {"lineWidth": 120}}}')
        (self.target / 'biome.json').write_text('{"extends": ["./more.json"], "formatter": {"lineWidth": 100}}')
        self.assertEqual(shared_files.json_style(self.target)('tsconfig.json')['width'], 120)

    def test_an_override_that_matches_the_file_sets_its_layout(self):
        (self.target / 'biome.json').write_text(json.dumps({'formatter': {'lineWidth': 100}, 'overrides': [
            {'includes': ['**/tsconfig*.json', '!other/**'], 'formatter': {'lineWidth': 40}}]}))
        style = shared_files.json_style(self.target)
        self.assertEqual(style('tsconfig.json')['width'], 40)
        self.assertEqual(style('biome.json')['width'], 100)
        self.assertEqual(style('other/tsconfig.json')['width'], 100)

    def test_an_extends_cycle_or_a_missing_file_gives_the_default(self):
        (self.target / 'a.json').write_text('{"extends": ["./biome.json"], "formatter": {"lineWidth": "wide"}}')
        (self.target / 'biome.json').write_text('{"extends": ["./a.json", "./gone.json", "some-package"]}')
        self.assertEqual(shared_files.json_style(self.target)('tsconfig.json')['width'], 80)

    def test_empty_objects_exponents_and_wide_characters_take_the_layout_of_biome(self):
        self.assertEqual(shared_files.json_dump({'d': [{}], 'm': ['a', {}, 2.5]}, None),
                         '{\n  "d": [{}],\n  "m": ["a", {}, 2.5]\n}\n')
        self.assertEqual(shared_files.json_dump({'n': [1e100, 1e-07]}, None), '{\n  "n": [1e100, 1e-7]\n}\n')
        # Each emoji takes two columns: 2 + 5 + 2 + 30 * 2 + 2 = 71 columns fit in 71, not in 70.
        wide = {'k': ['\U0001F600' * 30]}
        self.assertEqual(shared_files.json_dump(wide, None, {'width': 71}).count('\n'), 3)
        self.assertEqual(shared_files.json_dump(wide, None, {'width': 70}).count('\n'), 5)

    def test_biome_json_takes_the_layout_of_the_config_that_it_holds_after_the_change(self):
        (self.target / 'biome.json').write_text('{\n\t"formatter": {\n\t\t"enabled": true\n\t}\n}\n')
        lint = owner('typescript:lint', parts={'biome.json': self.source_file(
            'lint.json', '{"formatter": {"indentStyle": "space"}}\n')})
        self.sync([lint])
        self.assertEqual(self.text('biome.json'), '{\n  "formatter": {\n    "enabled": true\n  },\n'
                                                  '  "extends": ["./.agents/config/typescript/lint.json"]\n}\n')
        self.sync([], [lint])
        self.assertEqual(self.text('biome.json'), '{\n\t"formatter": {\n\t\t"enabled": true\n\t}\n}\n')

    def test_a_later_layer_wins_over_json_formatter_of_an_earlier_one(self):
        (self.target / 'biome.json').write_text(json.dumps({'json': {'formatter': {'indentStyle': 'space'}}, 'overrides': [
            {'includes': ['**'], 'formatter': {'indentStyle': 'tab'}},
            {'includes': ['**'], 'json': {'formatter': {'indentWidth': 8}}},
            {'includes': ['**'], 'formatter': {'indentWidth': 4}}]}))
        style = shared_files.json_style(self.target)('tsconfig.json')
        self.assertEqual((style['indent'], style['tab']), ('\t', 4))

    def test_an_extends_with_a_bare_file_name_is_read_next_to_the_config(self):
        (self.target / 'base.json').write_text('{"formatter": {"indentStyle": "space", "lineWidth": 60}}')
        (self.target / 'biome.json').write_text('{"extends": ["base.json", "a-package"]}')
        style = shared_files.json_style(self.target)('tsconfig.json')
        self.assertEqual((style['indent'], style['width']), (2, 60))

    def test_an_include_with_a_dot_slash_or_braces_matches(self):
        (self.target / 'biome.json').write_text(json.dumps({'overrides': [
            {'includes': ['./tsconfig.json'], 'formatter': {'lineWidth': 40}},
            {'includes': ['**/*.{json,jsonc}'], 'formatter': {'indentWidth': 4}}]}))
        style = shared_files.json_style(self.target)
        self.assertEqual((style('tsconfig.json')['width'], style('tsconfig.json')['tab']), (40, 4))
        self.assertEqual((style('other.json')['width'], style('a/b.jsonc')['tab']), (80, 4))
        self.assertEqual(style('a.js')['tab'], 2)

    def test_expand_trailing_commas_and_the_line_ending_take_the_layout_of_biome(self):
        value = {'a': {'b': 1}, 'n': [1, 2, 3], 's': ['x', 'y'], 'e': [], 'l': [[1], {'k': 1}], 'q': [{}]}
        self.assertEqual(shared_files.json_dump(value, None, {'indent': 2, 'expand': 'never'}),
                         '{\n  "a": { "b": 1 },\n  "n": [1, 2, 3],\n  "s": ["x", "y"],\n  "e": [],\n'
                         '  "l": [[1], { "k": 1 }],\n  "q": [{}]\n}\n')
        self.assertEqual(shared_files.json_dump({'n': [1, 2], 'q': [{}]}, None, {'indent': 2, 'expand': 'always'}),
                         '{\n  "n": [\n    1,\n    2\n  ],\n  "q": [\n    {}\n  ]\n}\n')
        self.assertEqual(shared_files.json_dump(value, None, {'indent': 2, 'width': 12, 'trailing': True}),
                         '{\n  "a": {\n    "b": 1,\n  },\n  "n": [\n    1, 2, 3\n  ],\n  "s": [\n    "x",\n'
                         '    "y",\n  ],\n  "e": [],\n  "l": [\n    [1],\n    {\n      "k": 1,\n    },\n  ],\n'
                         '  "q": [{}],\n}\n')
        self.assertEqual(shared_files.json_dump({'a': 1}, None, {'indent': 2, 'ending': 'crlf'}),
                         '{\r\n  "a": 1\r\n}\r\n')
        (self.target / 'biome.json').write_text('{"formatter": {"expand": "never", "lineEnding": "crlf"}, '
                                                '"json": {"formatter": {"trailingCommas": "all"}}}')
        style = shared_files.json_style(self.target)('tsconfig.json')
        # Biome's parser rejects a comma after the last item of a .json file unless the config allows it. It takes
        # one in a .jsonc file and in tsconfig*.json, and never in biome.json.
        self.assertEqual((style['expand'], style['trailing'], style['ending']), ('never', True, 'crlf'))
        style = shared_files.json_style(self.target)
        self.assertEqual([style(name)['trailing'] for name in (
            'a.json', 'tsconfig.app.json', 'sub/jsconfig.json', 'mytsconfig.json', 'biome.jsonc', 'biome.json')],
            [False, True, True, False, True, False])
        (self.target / 'biome.json').write_text('{"json": {"formatter": {"trailingCommas": "all"}, '
                                                '"parser": {"allowTrailingCommas": true}}}')
        style = shared_files.json_style(self.target)
        self.assertEqual([style(name)['trailing'] for name in ('a.json', 'biome.json')], [True, False])

    def test_a_file_with_trailing_commas_can_be_changed_again(self):
        (self.target / 'tsconfig.json').write_text('{\n  "a": ["x,]",],\n}\n')
        types = owner('typescript:typecheck', parts={'tsconfig.json': self.source_file('ts.json', '{}\n')})
        self.sync([types])
        self.assertEqual(json.loads(self.text('tsconfig.json'))['a'], ['x,]'])
        self.assertIn('extends', self.text('tsconfig.json'))

    def test_the_config_reader_keeps_commas_in_strings_and_skips_a_byte_order_mark(self):
        self.assertEqual(shared_files.jsonc_load('\ufeff{"includes": ["a,]", "b",], /* c, */ "x": "//y",}'),
                         {'includes': ['a,]', 'b'], 'x': '//y'})
        (self.target / 'biome.json').write_text('\ufeff{"formatter": {"indentStyle": "space"}}')
        self.assertEqual(shared_files.json_style(self.target)('tsconfig.json')['indent'], 2)

    def test_the_agent_hooks_files_never_get_a_trailing_comma(self):
        (self.target / 'biome.json').write_text('{"json": {"formatter": {"trailingCommas": "all"}, '
                                                '"parser": {"allowTrailingCommas": true}}}')
        style = shared_files.json_style(self.target)
        self.assertEqual([style(name)['trailing'] for name in ('.claude/settings.json', '.codex/hooks.json', 'a.json')],
                         [False, False, True])

    def test_a_symlinked_biome_config_keeps_the_indent_of_each_file(self):
        (self.source / 'shared.json').write_text('{"formatter": {"indentStyle": "space", "indentWidth": 2}}')
        (self.target / 'biome.json').symlink_to(self.source / 'shared.json')
        self.assertEqual(shared_files.json_style(self.target)('tsconfig.json')['indent'], None)
        (self.target / 'biome.json').unlink()
        (self.target / 'biome.json').write_text('{"extends": ["./linked.json"]}')
        (self.target / 'linked.json').symlink_to(self.source / 'shared.json')
        self.assertEqual(shared_files.json_style(self.target)('tsconfig.json')['indent'], None)

    def test_a_number_line_that_is_too_long_gets_the_trailing_comma(self):
        value = {'a' * 20: [1, 12345678901234567]}
        self.assertEqual(shared_files.json_dump(value, None, {'indent': 2, 'width': 20, 'trailing': True}),
                         '{\n  "' + 'a' * 20 + '": [\n    1,\n    12345678901234567,\n  ],\n}\n')
        self.assertEqual(shared_files.json_dump({'n': [1, 2, 3]}, None, {'indent': 2, 'width': 12, 'trailing': True}),
                         '{\n  "n": [\n    1, 2, 3\n  ],\n}\n')

    def test_a_tsconfig_written_with_a_new_biome_json_takes_its_layout(self):
        (self.target / 'tsconfig.json').write_text('{\n  "compilerOptions": {"lib": [' + ', '.join(
            f'"lib{n:02}"' for n in range(10)) + ']}\n}\n')
        lint = owner('typescript:lint', parts={'biome.json': self.source_file(
            'lint.json', '{"formatter": {"indentStyle": "space", "lineWidth": 120}}\n')})
        types = owner('typescript:typecheck', parts={'tsconfig.json': self.source_file('ts.json', '{}\n')})
        self.sync([types, lint])
        self.assertIn('"lib": ["lib00", ', self.text('tsconfig.json'))

    def test_biome_jsonc_takes_the_part_when_it_exists(self):
        mine = '{\n  // mine\n  "formatter": {}\n}\n'
        (self.target / 'biome.jsonc').write_text(mine)
        lint = owner('typescript:lint', parts={'biome.json': self.source_file('lint.json', '{}\n')})
        notes = self.sync([lint])
        self.assertFalse((self.target / 'biome.json').exists())
        self.assertEqual(self.text('biome.jsonc'), mine)
        self.assertTrue(any('biome.jsonc: the installer leaves the file alone' in note for note in notes), notes)
        self.assertEqual(self.text('.agents/config/typescript/lint.json'), '{}\n')

    def biome_in_jsonc(self):
        """The part of lint in a biome.jsonc, then a biome.json of yours next to it."""
        (self.target / 'biome.jsonc').write_text('{\n  "formatter": {"enabled": true}\n}\n')
        lint = owner('typescript:lint', parts={'biome.json': self.source_file('lint.json', '{}\n')})
        self.sync([lint])
        self.assertIn('extends', self.text('biome.jsonc'))
        (self.target / 'biome.json').write_text('{\n  "linter": {"enabled": true}\n}\n')
        return lint

    def test_the_record_names_biome_jsonc_when_it_holds_the_part(self):
        self.biome_in_jsonc()
        units = self.parts['biome.json']
        self.assertEqual({entry.get('file') for unit, entry in units.items() if not unit.startswith('(')},
                         {'biome.jsonc'})

    def test_the_part_moves_to_a_biome_json_that_you_add(self):
        lint = self.biome_in_jsonc()
        notes = self.sync([lint])
        self.assertNotIn('extends', self.text('biome.jsonc'))
        self.assertIn('"formatter"', self.text('biome.jsonc'))
        self.assertIn('extends', self.text('biome.json'))
        self.assertIn('"linter"', self.text('biome.json'))
        self.assertTrue(any('move from biome.jsonc to biome.json' in note for note in notes), notes)
        self.assertEqual({entry.get('file') for entry in self.parts['biome.json'].values()}, {'biome.json'})
        self.assertEqual(self.check()[0], 0)
        self.sync([], [lint])
        self.assertNotIn('extends', self.text('biome.json'))
        self.assertNotIn('extends', self.text('biome.jsonc'))

    def test_the_part_of_an_older_record_moves_too(self):
        lint = self.biome_in_jsonc()
        for entry in self.parts['biome.json'].values():
            entry.pop('file', None)
        self.sync([lint])
        self.assertNotIn('extends', self.text('biome.jsonc'))
        self.assertIn('extends', self.text('biome.json'))

    def test_a_part_that_you_deleted_stays_out_when_it_moves(self):
        lint = self.biome_in_jsonc()
        (self.target / 'biome.json').unlink()
        (self.target / 'biome.jsonc').write_text('{\n  "formatter": {"enabled": true}\n}\n')
        self.sync([lint])
        (self.target / 'biome.json').write_text('{\n  "linter": {"enabled": true}\n}\n')
        self.sync([lint])
        self.assertNotIn('extends', self.text('biome.json'))

    def test_a_dry_run_does_not_move_the_part(self):
        lint = self.biome_in_jsonc()
        before = self.parts
        self.sync([lint], dry_run=True)
        self.assertIn('extends', self.text('biome.jsonc'))
        self.assertNotIn('extends', self.text('biome.json'))
        self.assertEqual(self.parts, before)

    def test_check_names_a_part_that_will_move(self):
        self.biome_in_jsonc()
        code, out = self.check()
        self.assertIn('the tool reads biome.json before biome.jsonc', out)
        self.assertNotIn('is gone', out)
        self.assertIn('the files agree, apart from the notes above', out)

    def test_a_check_with_no_notes_names_no_notes(self):
        lint = owner('typescript:lint', parts={'biome.json': self.source_file('lint.json', '{}\n')})
        self.sync([lint])
        code, out = self.check()
        self.assertEqual(code, 0)
        self.assertEqual(out, 'just install --check: the record in .agents/stack.json and the files agree\n')

    def test_a_move_that_stops_before_its_first_write_still_takes_the_part_out(self):
        lint = self.biome_in_jsonc()
        with stopping_at('biome.jsonc'), self.assertRaises(KeyboardInterrupt):
            self.sync([lint])
        self.sync([lint])
        self.assertNotIn('extends', self.text('biome.jsonc'))
        self.assertIn('extends', self.text('biome.json'))
        self.sync([], [lint])
        self.assertNotIn('extends', self.text('biome.jsonc'))
        self.assertNotIn('extends', self.text('biome.json'))

    def test_a_move_that_stops_after_both_writes_keeps_the_part_ours(self):
        lint = self.biome_in_jsonc()
        # The run writes both files, then stops before it saves the record.
        self.sync([lint], stop=True)
        self.sync([lint])
        unit = next(unit for unit in self.parts['biome.json'] if unit.startswith('extends['))
        self.assertEqual(self.parts['biome.json'][unit]['state'], 'added')
        self.sync([], [lint])
        self.assertNotIn('extends', self.text('biome.json'))

    def test_a_pending_file_with_a_file_that_is_not_a_name_stops_the_run(self):
        lint = self.biome_in_jsonc()
        with stopping_at('biome.jsonc'), self.assertRaises(KeyboardInterrupt):
            self.sync([lint])
        pending = self.target / shared_files.PENDING
        data = json.loads(pending.read_text())
        for entry in data['files']['biome.json']['old'].values():
            entry['file'] = ['biome.jsonc']
        pending.write_text(json.dumps(data))
        with self.assertRaises(shared_files.SharedError):
            self.sync([lint])

    def test_a_symlinked_biome_jsonc_after_a_stopped_move_is_left_alone(self):
        lint = self.biome_in_jsonc()
        with stopping_at('biome.jsonc'), self.assertRaises(KeyboardInterrupt):
            self.sync([lint])
        (self.target / 'real.jsonc').write_text(self.text('biome.jsonc'))
        (self.target / 'biome.jsonc').unlink()
        (self.target / 'biome.jsonc').symlink_to('real.jsonc')
        notes = self.sync([lint])
        self.assertIn('extends', self.text('real.jsonc'))
        self.assertNotIn('extends', self.text('biome.json'))
        self.assertTrue(any('is a symlink' in note for note in notes), notes)

    def symlinked_jsonc_after_a_stop(self):
        """A move that stops before its first write, then a biome.jsonc that you make a symlink."""
        lint = self.biome_in_jsonc()
        with stopping_at('biome.jsonc'), self.assertRaises(KeyboardInterrupt):
            self.sync([lint])
        (self.target / 'real.jsonc').write_text(self.text('biome.jsonc'))
        (self.target / 'biome.jsonc').unlink()
        (self.target / 'biome.jsonc').symlink_to('real.jsonc')
        return lint

    def test_a_part_left_in_a_symlink_keeps_its_own_file(self):
        lint = self.symlinked_jsonc_after_a_stop()
        self.sync([lint])
        self.assertIn('lint.json', self.text('real.jsonc'))
        self.assertTrue((self.target / '.agents/config/typescript/lint.json').exists())

    def test_a_part_left_in_a_symlink_is_named_by_each_run(self):
        lint = self.symlinked_jsonc_after_a_stop()
        self.sync([lint])
        notes = self.sync([lint])
        self.assertTrue(any('biome.jsonc is a symlink' in note for note in notes), notes)
        self.assertTrue((self.target / '.agents/config/typescript/lint.json').exists())

    def test_a_symlinked_biome_json_keeps_its_own_file(self):
        (self.target / 'biome.json').write_text('{\n  "linter": {"enabled": true}\n}\n')
        lint = owner('typescript:lint', parts={'biome.json': self.source_file('lint.json', '{}\n')})
        self.sync([lint])
        (self.target / 'real.json').write_text(self.text('biome.json'))
        (self.target / 'biome.json').unlink()
        (self.target / 'biome.json').symlink_to('real.json')
        self.sync([lint])
        self.assertTrue((self.target / '.agents/config/typescript/lint.json').exists())

    def symlinked_biome_json(self):
        (self.target / 'biome.json').write_text('{\n  "linter": {"enabled": true}\n}\n')
        lint = owner('typescript:lint', parts={'biome.json': self.source_file('lint.json', '{}\n')})
        self.sync([lint])
        (self.target / 'real.json').write_text(self.text('biome.json'))
        (self.target / 'biome.json').unlink()
        (self.target / 'biome.json').symlink_to('real.json')
        return lint

    def test_an_uninstall_keeps_the_own_file_of_a_part_in_a_symlinked_biome_json(self):
        lint = self.symlinked_biome_json()
        self.sync([], [lint])
        self.assertIn('lint.json', self.text('real.json'))
        self.assertTrue((self.target / '.agents/config/typescript/lint.json').exists())

    def test_a_tool_that_leaves_the_catalog_keeps_the_own_file_of_a_part_in_a_symlink(self):
        self.symlinked_biome_json()
        self.sync([], [])
        self.assertIn('lint.json', self.text('real.json'))
        self.assertTrue((self.target / '.agents/config/typescript/lint.json').exists())

    def two_own_files_with_one_name_a_prefix_of_the_other(self):
        (self.target / 'biome.json').write_text('{\n  "linter": {"enabled": true}\n}\n')
        (self.target / 'tsconfig.json').write_text('{\n  "compilerOptions": {}\n}\n')
        lint = owner('typescript:lint', parts={'biome.json': self.source_file('a/base.jsonc', '{}\n')})
        tsc = owner('typescript:tsc', parts={'tsconfig.json': self.source_file('b/base.json', '{}\n')})
        self.sync([lint, tsc])
        (self.target / 'real.json').write_text(self.text('biome.json'))
        (self.target / 'biome.json').unlink()
        (self.target / 'biome.json').symlink_to('real.json')
        return lint, tsc

    def test_an_own_file_that_a_symlink_does_not_include_goes(self):
        lint, tsc = self.two_own_files_with_one_name_a_prefix_of_the_other()
        self.sync([lint], [lint, tsc])
        self.assertTrue((self.target / '.agents/config/typescript/base.jsonc').exists())
        self.assertFalse((self.target / '.agents/config/typescript/base.json').exists())

    def test_an_own_file_of_a_tool_that_leaves_the_catalog_goes_when_no_symlink_includes_it(self):
        lint, _ = self.two_own_files_with_one_name_a_prefix_of_the_other()
        self.sync([lint], [lint])
        self.assertFalse((self.target / '.agents/config/typescript/base.json').exists())
        self.assertNotIn('.agents/config/typescript/base.json', self.parts)

    def test_an_uninstall_keeps_the_own_file_of_a_part_left_in_a_symlinked_biome_jsonc(self):
        lint = self.symlinked_jsonc_after_a_stop()
        self.sync([lint])
        self.sync([], [lint])
        self.assertIn('lint.json', self.text('real.jsonc'))
        self.assertTrue((self.target / '.agents/config/typescript/lint.json').exists())

    def test_check_names_a_symlinked_biome_jsonc_and_promises_no_move(self):
        lint = self.biome_in_jsonc()
        (self.target / 'real.jsonc').write_text(self.text('biome.jsonc'))
        (self.target / 'biome.jsonc').unlink()
        (self.target / 'biome.jsonc').symlink_to('real.jsonc')
        notes = self.sync([lint])
        self.assertIn('biome.json: biome.jsonc is a symlink, so the installer leaves biome.json alone', notes)
        code, out = self.check()
        self.assertNotIn('The next just install moves them', out)
        self.assertIn('biome.jsonc is a symlink', out)

    def test_check_names_a_pending_file_whose_change_is_not_a_map(self):
        pending = self.target / shared_files.PENDING
        pending.parent.mkdir(exist_ok=True)
        for change in (['x'], {'old': ['x']}):
            pending.write_text(json.dumps({'files': {'biome.json': change}}))
            code, out = self.check()
            self.assertEqual(code, 1)
            self.assertIn('is not a file that the installer wrote', out)

    def test_a_copy_of_biome_json_as_biome_jsonc_keeps_the_part(self):
        (self.target / 'biome.json').write_text('{\n  "linter": {"enabled": true}\n}\n')
        lint = owner('typescript:lint', parts={'biome.json': self.source_file('lint.json', '{}\n')})
        self.sync([lint])
        (self.target / 'biome.jsonc').write_text(self.text('biome.json'))
        self.sync([lint])
        unit = next(unit for unit in self.parts['biome.json'] if unit.startswith('extends['))
        self.assertEqual(self.parts['biome.json'][unit]['state'], 'added')
        self.sync([], [lint])
        self.assertNotIn('extends', self.text('biome.json'))

    def test_an_older_record_with_the_part_in_both_files_keeps_biome_json(self):
        (self.target / 'biome.json').write_text('{\n  "linter": {"enabled": true}\n}\n')
        lint = owner('typescript:lint', parts={'biome.json': self.source_file('lint.json', '{}\n')})
        self.sync([lint])
        for entry in self.parts['biome.json'].values():
            entry.pop('file', None)
        (self.target / 'biome.jsonc').write_text(self.text('biome.json'))
        self.sync([lint])
        unit = next(unit for unit in self.parts['biome.json'] if unit.startswith('extends['))
        self.assertEqual(self.parts['biome.json'][unit]['state'], 'added')

    def test_a_file_in_the_record_outside_the_project_is_not_touched(self):
        self.biome_in_jsonc()
        outside = self.target.parent / (self.target.name + '-outside.json')
        unit = next(unit for unit in self.parts['biome.json'] if unit.startswith('extends['))
        outside.write_text(json.dumps({'extends': [self.parts['biome.json'][unit]['value']]}))
        self.addCleanup(outside.unlink, missing_ok=True)
        for entry in self.parts['biome.json'].values():
            entry['file'] = '../' + outside.name
        self.sync([owner('typescript:lint', parts={'biome.json': self.source_file('lint.json', '{}\n')})])
        self.assertTrue(outside.exists())

    def test_check_after_you_rename_biome_jsonc_to_biome_json(self):
        self.biome_in_jsonc()
        (self.target / 'biome.json').unlink()
        (self.target / 'biome.jsonc').rename(self.target / 'biome.json')
        code, out = self.check()
        self.assertNotIn('is gone', out)

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

    def test_a_role_that_goes_while_agents_md_is_a_symlink_keeps_its_instructions(self):
        role = self.role()
        self.sync([role])
        (self.target / 'real.md').write_text(self.text('AGENTS.md'))
        (self.target / 'AGENTS.md').unlink()
        (self.target / 'AGENTS.md').symlink_to('real.md')
        self.sync([], [role])
        self.assertIn('.agents/instructions/tasker.md', self.text('real.md'))
        self.assertTrue((self.target / '.agents/instructions/tasker.md').exists())

    def test_a_role_that_leaves_the_catalog_while_agents_md_is_a_symlink_keeps_its_instructions(self):
        role = self.role()
        self.sync([role])
        (self.target / 'real.md').write_text(self.text('AGENTS.md'))
        (self.target / 'AGENTS.md').unlink()
        (self.target / 'AGENTS.md').symlink_to('real.md')
        self.sync([], [])
        self.assertTrue((self.target / '.agents/instructions/tasker.md').exists())

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

    def test_a_starter_that_a_stopped_run_wrote_is_not_written_again_after_the_installer_deleted_it(self):
        alone = owner('starter-only', starter={'AGENTS.md': self.source_file('alone.md', '# Project\n')})
        tasker = self.role()
        self.sync([alone, tasker], stop=True)
        lines = self.text('AGENTS.md').splitlines(keepends=True)
        end = next(index for index, line in enumerate(lines) if 'end lines of tasker' in line)
        (self.target / 'AGENTS.md').write_text(''.join(lines[:end + 1]))
        # This run empties the file, so it deletes the file, and it stops.
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

    def test_a_skip_if_file_through_a_link_holds_and_is_not_read(self):
        # The file through the link has no [tool.ruff, so a read would let ruff.toml in. The guard holds unread.
        outside = self.target / 'outside'
        outside.mkdir()
        (outside / 'pyproject.toml').write_text('[project]\n')
        (self.target / 'cfg').symlink_to(outside)
        (self.source / 'ruff.toml').write_text('x\n')
        lint = owner('python:lint', files={'ruff.toml': (self.source / 'ruff.toml', ['cfg/pyproject.toml', '[tool.ruff'])})
        notes = self.sync([lint])
        self.assertFalse((self.target / 'ruff.toml').exists())
        self.assertIn('Left out ruff.toml: cfg/pyproject.toml is a symbolic link, which the installer does not read',
                      notes)


if __name__ == '__main__':
    unittest.main()
