"""Tests for scripts/install_stack.py, which installs jnterfaces and the chosen packages into one target."""
import io
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import install_stack  # noqa: E402

FAKE_INSTALLER = '''#!/usr/bin/env bash
# Log the call; on a real run, write a record as a package installer does.
printf '%s %s\\n' "{name}" "$*" >> "$STACK_LOG"
[ "${{FAIL_DRY_RUN:-}}" = "{name}" ] && exit 1
for arg in "$@"; do [ "$arg" = --dry-run ] && exit 0; done
target="${{@: -1}}"
mkdir -p "$target/.agents"
echo '{{"source_revision": "rev-{name}"}}' > "$target/{record}"
'''
NAMES = {'jnterfaces': '.agents/jnterfaces-install.json', 'agent_gest_git_skills': '.agents/gest-git-install.json',
         'nossaifd': '.agents/nossaifd-install.json', 'sklanes': '.agents/sklanes-install.json'}


def git(path, *args):
    subprocess.run(['git', '-C', str(path), '-c', 'user.name=T', '-c', 'user.email=t@example.invalid', *args],
                   check=True, capture_output=True)


class StackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.target = self.base / 'target'
        self.target.mkdir()
        self.log = self.base / 'log'
        for name, record in NAMES.items():
            self.fake(name, record)

    def fake(self, name, record):
        root = self.base / name
        (root / 'scripts').mkdir(parents=True)
        (root / 'skill-package.json').write_text(json.dumps({'repository': {'name': name}}))
        installer = root / 'scripts/install.sh'
        installer.write_text(FAKE_INSTALLER.format(name=name, record=record))
        installer.chmod(0o755)
        if name == 'jnterfaces':
            shutil.copy(REPO / 'scripts/install_stack.py', root / 'scripts/install_stack.py')
            shutil.copy(REPO / 'scripts/install_stack.sh', root / 'scripts/install_stack.sh')
            shutil.copy(REPO / 'stack.json', root / 'stack.json')
        git(root, 'init', '-q')
        git(root, 'add', '-A')
        git(root, 'commit', '-q', '-m', 'fake')

    def stack(self, *args, env=None):
        return subprocess.run(['bash', self.base / 'jnterfaces/scripts/install_stack.sh', self.target, *args],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              env=dict(os.environ, STACK_LOG=str(self.log), **(env or {})))

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def real_calls(self):
        return [line for line in self.calls() if '--dry-run' not in line]

    def saved(self):
        return json.loads((self.target / '.agents/stack.json').read_text())['choices']

    def test_without_a_terminal_or_saved_choices_it_asks_for_options(self):
        run = self.stack()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('no terminal to ask on', run.stderr)
        self.assertEqual(self.calls(), [])

    def test_defaults_install_just_gest_and_nossaifd_after_a_dry_run_of_each(self):
        run = self.stack('--defaults')
        self.assertEqual(run.returncode, 0, run.stderr)
        names = [line.split()[0] for line in self.calls()]
        self.assertEqual(names, ['jnterfaces', 'agent_gest_git_skills', 'nossaifd'] * 2)
        self.assertTrue(all('--dry-run' in line for line in self.calls()[:3]))
        self.assertIn('jnterfaces --skill just --exact ', self.real_calls()[0] + ' ')
        self.assertEqual(self.saved(), {'cx': False, 'gest': True, 'jagt-protocol': False, 'nossaifd': True, 'sklanes': False})
        self.assertIn('agent_gest_git_skills at rev-agent_gest_git_skills', run.stdout)

    def test_a_later_run_reuses_the_saved_choices_without_questions(self):
        self.assertEqual(self.stack('--defaults', '--with', 'cx,sklanes').returncode, 0)
        self.log.unlink()
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual([line.split()[0] for line in self.real_calls()],
                         ['jnterfaces', 'agent_gest_git_skills', 'nossaifd', 'sklanes'])
        self.assertIn('--skill just --skill cx --exact', self.real_calls()[0])

    def test_without_turns_a_skill_off_and_notes_a_package_that_stays(self):
        self.assertEqual(self.stack('--all').returncode, 0)
        self.log.unlink()
        run = self.stack('--without', 'cx,sklanes')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('--skill just --skill jagt-protocol --exact', self.real_calls()[0])
        self.assertNotIn('sklanes', ' '.join(self.calls()))
        self.assertIn('sklanes is turned off but stays installed', run.stderr)
        self.assertFalse(self.saved()['sklanes'])
        # The note comes again on each run while the package is still installed.
        self.assertIn('sklanes is turned off but stays installed', self.stack().stderr)

    def test_a_target_without_saved_choices_starts_from_what_is_installed(self):
        (self.target / '.agents').mkdir()
        (self.target / '.agents/jnterfaces-install.json').write_text(json.dumps({'skills': ['cx', 'just']}))
        (self.target / '.agents/sklanes-install.json').write_text('{}')
        run = self.stack('--defaults')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.saved(), {'cx': True, 'gest': True, 'jagt-protocol': False, 'nossaifd': True, 'sklanes': True})
        self.assertIn('--skill just --skill cx --exact', self.real_calls()[0])

    def test_a_choice_newer_than_the_saved_record_is_named(self):
        (self.target / '.agents').mkdir()
        old = {'cx': False, 'gest': True, 'jagt-protocol': False, 'sklanes': False}
        (self.target / '.agents/stack.json').write_text(json.dumps({'version': 1, 'choices': old}))
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('New choice(s) that the saved record does not have: nossaifd=yes', run.stderr)
        self.assertTrue(self.saved()['nossaifd'])

    def test_a_new_choice_is_named_when_other_choices_are_given(self):
        (self.target / '.agents').mkdir()
        old = {'cx': False, 'gest': True, 'jagt-protocol': False, 'sklanes': False}
        (self.target / '.agents/stack.json').write_text(json.dumps({'version': 1, 'choices': old}))
        run = self.stack('--with', 'cx', '--dry-run')
        self.assertIn('nossaifd=yes', run.stderr)
        run = self.stack('--with', 'cx', '--without', 'nossaifd', '--dry-run')
        self.assertNotIn('New choice', run.stderr)

    def test_ask_without_a_terminal_says_so(self):
        self.assertEqual(self.stack('--defaults').returncode, 0)
        self.assertIn('--ask needs a terminal', self.stack('--ask').stderr)

    def test_the_check_before_the_install_is_quiet_unless_it_fails(self):
        run = self.stack('--defaults')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(len(self.calls()), 6)
        failed = self.stack('--defaults', '--with', 'sklanes', env={'FAIL_DRY_RUN': 'sklanes'})
        self.assertIn('The dry run of sklanes failed', failed.stderr)

    def test_a_symlinked_target_is_refused(self):
        link = self.base / 'link'
        link.symlink_to(self.target)
        run = subprocess.run(['bash', self.base / 'jnterfaces/scripts/install_stack.sh', link, '--defaults'],
                             capture_output=True, text=True, env=dict(os.environ, STACK_LOG=str(self.log)))
        self.assertIn('must not be a symlink', run.stderr)
        self.assertEqual(self.calls(), [])

    def test_more_bad_options(self):
        self.assertIn('--clone names no package: lanes', self.stack('--defaults', '--clone', 'lanes=/x').stderr)
        self.assertIn('does not go with --all', self.stack('--ask', '--all').stderr)
        plain = self.base / 'plain'
        shutil.copytree(self.base / 'sklanes', plain, ignore=shutil.ignore_patterns('.git'))
        run = self.stack('--all', '--clean', '--clone', f'sklanes={plain}')
        self.assertIn('--clean needs a Git clone with a commit', run.stderr)
        self.assertNotIn('Traceback', run.stderr)
        self.assertEqual(self.calls(), [])

    def test_a_failed_dry_run_writes_nothing(self):
        run = self.stack('--all', env={'FAIL_DRY_RUN': 'sklanes'})
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(self.real_calls(), [])
        self.assertFalse((self.target / '.agents').exists())

    def test_dry_run_writes_nothing(self):
        run = self.stack('--all', '--dry-run')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse((self.target / '.agents').exists())
        self.assertEqual(len(self.calls()), 4)

    def test_missing_or_wrong_clone_stops_before_any_install(self):
        run = self.stack('--defaults', '--clone', f'nossaifd={self.base / "nowhere"}')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('No nossaifd clone', run.stderr)
        self.assertIn('git clone https://github.com/rahuldave/nossaifd', run.stderr)
        run = self.stack('--defaults', '--clone', f'nossaifd={self.base / "sklanes"}')
        self.assertIn('is not the nossaifd package', run.stderr)
        self.assertEqual(self.calls(), [])

    def test_bad_options_fail_with_the_list_of_choices(self):
        run = self.stack('--with', 'nope')
        self.assertIn('Unknown choice(s): nope', run.stderr)
        self.assertIn('cx, jagt-protocol, gest, nossaifd, sklanes', run.stderr)
        run = self.stack('--with', 'cx', '--without', 'cx')
        self.assertIn('cannot be in --with and in --without', run.stderr)
        run = self.stack('--all', '--defaults')
        self.assertIn('--all or --defaults', run.stderr)
        run = self.stack('--defaults', '--clone', 'nossaifd')
        self.assertIn('--clone needs NAME=DIR', run.stderr)
        self.assertEqual(self.calls(), [])

    def test_a_broken_saved_record_fails_with_a_way_out(self):
        (self.target / '.agents').mkdir()
        (self.target / '.agents/stack.json').write_text('{"choices": {"cx": "yes"}}')
        run = self.stack()
        self.assertIn('not true or false', run.stderr)
        (self.target / '.agents/stack.json').write_text('not json')
        run = self.stack()
        self.assertIn('--with and --without', run.stderr)

    def test_clean_pins_each_package_to_its_head(self):
        self.assertEqual(self.stack('--all', '--clean', '--dry-run').returncode, 0)
        for line in self.calls():
            name = line.split()[0]
            head = subprocess.check_output(['git', '-C', str(self.base / name), 'rev-parse', 'HEAD'], text=True).strip()
            self.assertIn(f'--source-commit {head}', line)

    def test_a_package_whose_need_is_off_is_refused(self):
        registry = json.loads((REPO / 'stack.json').read_text())
        registry['packages'][3]['needs'] = ['nossaifd']
        path = self.base / 'registry.json'
        path.write_text(json.dumps(registry))
        run = self.stack('--defaults', '--with', 'sklanes', '--without', 'nossaifd', '--registry', path)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('sklanes needs nossaifd', run.stderr)
        self.assertEqual(self.calls(), [])

    def test_questions_use_the_saved_or_default_answer_on_enter(self):
        _, choices = install_stack.load_registry(REPO / 'stack.json')
        args = mock.Mock(with_=[], without=[], all=False, defaults=False, ask=False)
        answers = io.StringIO('\ny\nmaybe\nn\n\nyes\n')
        with mock.patch('sys.stdin', answers), mock.patch('sys.stderr', io.StringIO()) as err:
            chosen = install_stack.resolve(args, choices, None, True)
        self.assertEqual(chosen, {'cx': False, 'jagt-protocol': True, 'gest': False, 'nossaifd': True, 'sklanes': True})
        self.assertIn('Answer y or n.', err.getvalue())
        self.assertIn('Install gest (the Gest g* workflow skills)? [Y/n]', err.getvalue())

    def test_on_a_terminal_only_new_choices_are_asked(self):
        _, choices = install_stack.load_registry(REPO / 'stack.json')
        args = mock.Mock(with_=[], without=[], all=False, defaults=False, ask=False)
        saved = {'cx': True, 'gest': True, 'jagt-protocol': False, 'sklanes': False}
        with mock.patch('sys.stdin', io.StringIO('n\n')), mock.patch('sys.stderr', io.StringIO()) as err:
            chosen = install_stack.resolve(args, choices, saved, True)
        self.assertEqual(err.getvalue().count('Install '), 1)
        self.assertIn('Install nossaifd', err.getvalue())
        self.assertEqual(chosen, {**saved, 'nossaifd': False})

    def test_questions_stop_when_the_terminal_closes(self):
        _, choices = install_stack.load_registry(REPO / 'stack.json')
        args = mock.Mock(with_=[], without=[], all=False, defaults=False, ask=True)
        with mock.patch('sys.stdin', io.StringIO('y\n')), mock.patch('sys.stderr', io.StringIO()):
            with self.assertRaises(install_stack.StackError):
                install_stack.resolve(args, choices, {}, True)

    def test_the_readme_table_matches_stack_json(self):
        _, choices = install_stack.load_registry(REPO / 'stack.json')
        readme = (REPO / 'README.md').read_text()
        rows = dict(re.findall(r'^\| `([a-z-]+)` \| [^|]+ \| (yes|no) \|$', readme, re.M))
        self.assertEqual(rows, {name: 'yes' if entry.get('default') else 'no' for name, entry in choices.items()})

    def test_real_packages_when_present(self):
        clones = {name: REPO.parent / name for name in NAMES}
        if not all((path / 'scripts/install.sh').is_file() for path in clones.values()):
            self.skipTest('no clones of all four packages next to this repository')
        git(self.target, 'init', '-q')
        run = subprocess.run(['bash', REPO / 'scripts/install_stack.sh', self.target, '--all'],
                             capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(run.returncode, 0, run.stderr)
        for record in NAMES.values():
            self.assertRegex(json.loads((self.target / record).read_text())['source_revision'], '^[0-9a-f]{40}$')
        for skill in ('just', 'cx', 'jagt-protocol', 'gtw', 'nossaifd', 'lanes'):
            self.assertTrue((self.target / f'.agents/skills/{skill}/SKILL.md').is_file(), skill)
        run = subprocess.run(['bash', REPO / 'scripts/install_stack.sh', self.target, '--without', 'cx'],
                             capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse((self.target / '.agents/skills/cx').exists())


if __name__ == '__main__':
    unittest.main()
