"""Tests for scripts/install_stack.py and .agents/install.just: roles, tools and profiles in one target."""
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

RECORDS = {'jnterfaces': '.agents/jnterfaces-install.json', 'agent_gest_git_skills': '.agents/gest-git-install.json',
           'nossaifd': '.agents/nossaifd-install.json', 'sklanes': '.agents/sklanes-install.json'}
SKILLS = {'agent_gest_git_skills': 'gtw', 'nossaifd': 'nossaifd', 'sklanes': 'lanes'}
# A uv and a uvx that log each call, so that the tests of the tools run offline. Like the real ones, they fail to add a
# package to a project that has no pyproject.toml or package.json, and their init makes that file.
FAKE_UV = """#!/bin/sh
printf "%s %s\\n" "$(basename "$0")" "$*" >> "$TOOL_LOG"
[ -n "$FAIL_TOOL" ] && [ "$FAIL_TOOL" = "$3" ] && exit 1
case "$1" in
  init) printf '[project]\\nname = "app"\\n' > pyproject.toml ;;
  add|remove) [ -f pyproject.toml ] || { echo "No pyproject.toml found" >&2; exit 2; } ;;
esac
exit 0
"""
FAKE_UVX = """#!/bin/sh
printf "%s %s\\n" "$(basename "$0")" "$*" >> "$TOOL_LOG"
case "$4" in
  init) echo '{}' > package.json ;;
  install|uninstall) [ -f package.json ] || { echo "no package.json" >&2; exit 2; } ;;
esac
exit 0
"""


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), '-c', 'user.name=T', '-c', 'user.email=t@example.invalid', *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def commit(path):
    git(path, 'add', '-A')
    git(path, 'commit', '-q', '-m', 'change')


class StackTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.packages = self.base / 'packages'
        jn = self.packages / 'jnterfaces'
        shutil.copytree(REPO, jn, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        git(jn, 'init', '-q')
        commit(jn)
        for name, skill in SKILLS.items():
            self.package(name, skill)
        self.target = self.base / 'target'
        self.target.mkdir()
        git(self.target, 'init', '-q')
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        for tool, text in (('uv', FAKE_UV), ('uvx', FAKE_UVX)):
            (self.bin / tool).write_text(text)
            (self.bin / tool).chmod(0o755)
        self.log = self.base / 'tools.log'

    def package(self, name, skill, extras=None):
        root = self.packages / name
        (root / '.agents/skills' / skill).mkdir(parents=True)
        (root / '.agents/skills' / skill / 'SKILL.md').write_text(f'---\nname: {skill}\ndescription: d\n---\n')
        manifest = {'repository': {'name': name}, 'skills': [{'name': skill, 'path': f'.agents/skills/{skill}'}]}
        if name == 'agent_gest_git_skills':
            (root / '.claude/hooks').mkdir(parents=True)
            (root / '.claude/hooks/start.sh').write_text('echo start\n')
            (root / '.claude/settings.json').write_text(json.dumps(
                {'hooks': {'SessionStart': [{'hooks': [{'type': 'command', 'command': 'start.sh'}]}]}}))
            manifest['install_extras'] = {'files': ['.claude/hooks'], 'merge_hooks': ['.claude/settings.json']}
        (root / 'skill-package.json').write_text(json.dumps(manifest))
        git(root, 'init', '-q')
        commit(root)

    def stack(self, *args, env=None, target=None):
        return subprocess.run([sys.executable, self.packages / 'jnterfaces/scripts/install_stack.py',
                               '--target', target or self.target, *args],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              env=dict(os.environ, PATH=f'{self.bin}:{os.environ["PATH"]}', TOOL_LOG=str(self.log),
                                       **(env or {})))

    def saved(self):
        return json.loads((self.target / '.agents/stack.json').read_text())

    def tools_run(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def has(self, skill):
        return (self.target / f'.agents/skills/{skill}/SKILL.md').is_file()

    def test_without_a_terminal_or_saved_choices_it_asks_for_names(self):
        run = self.stack()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('no terminal to ask on', run.stderr)
        self.assertFalse((self.target / '.agents').exists())

    def test_defaults_install_just_tasker_and_prose_and_the_recipe_file(self):
        run = self.stack('--defaults')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(all(self.has(s) for s in ('just', 'gtw', 'nossaifd')))
        self.assertFalse(self.has('cx') or self.has('lanes'))
        self.assertTrue((self.target / '.agents/install.just').is_file())
        self.assertTrue((self.target / '.claude/hooks/start.sh').is_file())
        self.assertEqual(self.saved(), {'version': 2, 'profiles': [], 'tools': [], 'choices': {
            'cx': False, 'jagt-protocol': False, 'prose': True, 'reporter': False, 'tasker': True}})
        self.assertIn('No Justfile and no profile', run.stderr)
        for record in ('jnterfaces', 'agent_gest_git_skills', 'nossaifd'):
            self.assertRegex(json.loads((self.target / RECORDS[record]).read_text())['source_revision'], '^[0-9a-f]{40}$')

    def test_a_named_role_turns_on_and_a_later_run_reuses_the_record(self):
        self.assertEqual(self.stack('--defaults', 'reporter', 'cx').returncode, 0)
        self.assertTrue(self.has('lanes') and self.has('cx'))
        shutil.rmtree(self.target / '.agents/skills/lanes')
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(self.has('lanes'))

    def test_without_removes_a_package_and_an_optional_skill(self):
        self.assertEqual(self.stack('--all').returncode, 0)
        settings = self.target / '.claude/settings.json'
        self.assertIn('start.sh', settings.read_text())
        run = self.stack('--without', 'cx,reporter,tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        for skill in ('cx', 'lanes', 'gtw'):
            self.assertFalse(self.has(skill), skill)
        self.assertFalse((self.target / RECORDS['sklanes']).exists())
        self.assertFalse((self.target / '.claude').exists())
        self.assertFalse(self.saved()['choices']['reporter'])
        # A later run has nothing left to remove.
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('Removed', run.stdout)

    def test_a_removal_keeps_an_edited_file_and_does_not_count_as_installed(self):
        self.assertEqual(self.stack('--defaults', 'reporter').returncode, 0)
        edited = self.target / '.agents/skills/lanes/SKILL.md'
        edited.write_text('mine\n')
        run = self.stack('--without', 'reporter')
        self.assertIn('Kept .agents/skills/lanes/SKILL.md', run.stderr)
        self.assertEqual(edited.read_text(), 'mine\n')
        self.assertEqual(self.stack().returncode, 0)
        self.assertEqual(install_stack.installed(self.target, install_stack.load_registry(REPO / 'stack.json')[0])
                         .get('reporter'), None)

    def test_old_names_and_an_old_record_still_work(self):
        (self.target / '.agents').mkdir()
        old = {'cx': False, 'gest': True, 'jagt-protocol': False, 'nossaifd': False, 'sklanes': True}
        (self.target / '.agents/stack.json').write_text(json.dumps({'version': 1, 'choices': old}))
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.saved()['choices'], {'cx': False, 'jagt-protocol': False, 'prose': False,
                                                   'reporter': True, 'tasker': True})
        self.assertEqual(self.stack('--without', 'sklanes').returncode, 0)
        self.assertFalse(self.saved()['choices']['reporter'])

    def test_a_target_without_saved_choices_starts_from_what_is_installed(self):
        (self.target / '.agents').mkdir()
        (self.target / '.agents/jnterfaces-install.json').write_text(json.dumps({'skills': ['cx', 'just']}))
        (self.target / '.agents/sklanes-install.json').write_text('{}')
        run = self.stack('--defaults', '--dry-run')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('agent_gest_git_skills, nossaifd, sklanes would be installed', run.stdout)

    def test_a_role_newer_than_the_saved_record_is_named(self):
        (self.target / '.agents').mkdir()
        old = {'cx': False, 'tasker': True, 'jagt-protocol': False, 'reporter': False}
        (self.target / '.agents/stack.json').write_text(json.dumps({'version': 2, 'choices': old}))
        run = self.stack('--dry-run')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("New role(s) that the saved record does not have: prose=yes", run.stderr)

    def test_a_python_profile_sets_the_defaults_adds_its_tools_and_writes_the_justfile(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "app"\n')
        run = self.stack('--defaults')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('Profile(s) found from the files of the project: python', run.stderr)
        self.assertEqual(self.tools_run(), ['uv add --group lint ruff pre-commit', 'uv add --group test pytest pytest-cov'])
        self.assertIn('default-groups = "all"', (self.target / 'pyproject.toml').read_text())
        self.assertTrue((self.target / 'ruff.toml').is_file() and (self.target / '.pre-commit-config.yaml').is_file())
        justfile = (self.target / 'Justfile').read_text()
        self.assertIn("import? '.agents/install.just'", justfile)
        self.assertEqual(self.saved()['profiles'], ['python'])
        self.assertEqual(self.saved()['tools'], ['lint', 'test'])
        self.assertTrue(self.saved()['choices']['tasker'])
        # A later run adds no tool again, and leaves the Justfile and the config files alone.
        (self.target / 'ruff.toml').write_text('line-length = 80\n')
        self.log.unlink()
        self.assertEqual(self.stack().returncode, 0)
        self.assertEqual(self.tools_run(), [])
        self.assertEqual((self.target / 'ruff.toml').read_text(), 'line-length = 80\n')
        self.assertEqual((self.target / 'Justfile').read_text(), justfile)

    def test_a_named_tool_runs_its_command_and_without_removes_unshared_packages(self):
        self.assertEqual(self.stack('--profile', 'python', 'typecheck').returncode, 0)
        self.assertEqual(self.tools_run()[0], 'uv init --bare --no-workspace')
        self.assertIn('uv add --group lint ty', self.tools_run())
        self.log.unlink()
        run = self.stack('--without', 'typecheck,lint')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.tools_run(), ['uv remove --group lint ty ruff pre-commit'])
        self.assertEqual(self.saved()['tools'], ['test'])
        self.log.unlink()
        self.assertEqual(self.stack('lint').returncode, 0)
        self.assertEqual(self.tools_run(), ['uv add --group lint ruff pre-commit'])

    def test_ruff_toml_is_left_out_when_pyproject_has_ruff_settings(self):
        (self.target / 'pyproject.toml').write_text('[tool.ruff]\nline-length = 99\n')
        run = self.stack('--defaults')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse((self.target / 'ruff.toml').exists())
        self.assertIn('Left out ruff.toml', run.stderr)

    def test_typescript_runs_npm_through_uvx(self):
        run = self.stack('--profile', 'typescript')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.tools_run()[:2], ['uvx --from nodejs-wheel==24.* npm init --yes',
                                                'uvx --from nodejs-wheel==24.* npm install --save-dev @biomejs/biome@2'])
        self.assertNotIn('default-groups', (self.target / 'package.json').read_text())
        self.log.unlink()
        self.assertEqual(self.stack('--without', 'typecheck,test').returncode, 0)
        self.assertEqual(self.tools_run(), ['uvx --from nodejs-wheel==24.* npm uninstall typescript @types/node'])
        self.assertTrue((self.target / 'tsconfig.json').is_file())
        self.assertIn('nodejs-wheel', (self.target / 'Justfile').read_text())

    def test_a_failed_tool_keeps_the_roles_and_a_later_run_tries_it_again(self):
        run = self.stack('--profile', 'python', env={'FAIL_TOOL': 'lint'})
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('run just install again, which tries the tool again', run.stderr)
        self.assertTrue(self.has('gtw'))
        self.assertTrue((self.target / 'Justfile').is_file())
        self.assertEqual(self.saved()['tools'], [])
        self.assertEqual(self.saved()['pending_tools'], ['lint', 'test'])
        self.log.unlink()
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.tools_run(), ['uv add --group lint ruff pre-commit', 'uv add --group test pytest pytest-cov'])
        self.assertEqual(self.saved()['tools'], ['lint', 'test'])
        self.assertNotIn('pending_tools', self.saved())

    def test_a_new_project_gets_its_project_file_justfile_and_tools(self):
        run = self.stack('--profile', 'python')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.tools_run()[0], 'uv init --bare --no-workspace')
        self.assertTrue((self.target / 'Justfile').is_file())
        self.assertEqual(self.saved()['tools'], ['lint', 'test'])

    def test_a_dangling_justfile_symlink_is_never_written_through(self):
        outside = self.base / 'outside'
        (self.target / 'Justfile').symlink_to(outside)
        run = self.stack('--profile', 'python')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('is a symlink, so the installer leaves it alone', run.stderr)
        self.assertFalse(outside.exists())

    def test_the_default_groups_join_an_existing_tool_uv_table(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n\n[tool.uv]\npackage = false\n')
        self.assertEqual(self.stack('--profile', 'python').returncode, 0)
        text = (self.target / 'pyproject.toml').read_text()
        self.assertEqual(text.count('[tool.uv]'), 1)
        self.assertIn('default-groups = "all"\npackage = false', text)
        (self.target / 'pyproject.toml').write_text('[tool.uv]\ndefault-groups = ["dev"]\n')
        self.assertEqual(self.stack('lint').returncode, 0)
        self.assertEqual((self.target / 'pyproject.toml').read_text(), '[tool.uv]\ndefault-groups = ["dev"]\n')

    def test_default_groups_are_set_only_where_the_toml_stays_the_same(self):
        for text in ('[tool.uv]  # mine\npackage = false\n', '[ tool.uv ]\npackage = false\n',
                     '[tool]\nuv.package = false\n', '["tool".uv]\npackage = false\n', 'not = [toml\n',
                     'x = """\n[tool.uv]\n"""\n'):
            path = self.base / 'pyproject.toml'
            path.write_text(text)
            with mock.patch('sys.stderr', io.StringIO()) as err:
                install_stack.set_default_groups(path, 'all')
            after = path.read_text()
            if after != text:
                data = install_stack.tomllib.loads(after)
                self.assertEqual(data['tool']['uv']['default-groups'], 'all', text)
            else:
                self.assertIn('add this line under [tool.uv] yourself', err.getvalue(), text)
        path.write_text('[project]\nname = "a"\n')
        install_stack.set_default_groups(path, 'all')
        self.assertEqual(install_stack.tomllib.loads(path.read_text())['tool']['uv']['default-groups'], 'all')

    def test_a_symlinked_project_file_stops_the_init(self):
        outside = self.base / 'outside.toml'
        (self.target / 'pyproject.toml').symlink_to(outside)
        run = self.stack('--profile', 'python', '--without', 'tasker,prose')
        self.assertIn('pyproject.toml is a symlink', run.stderr)
        self.assertFalse(outside.exists())

    def test_a_failed_removal_keeps_the_tool_in_the_record(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        run = self.stack('--without', 'lint', env={'FAIL_TOOL': 'lint'})
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('lint', self.saved()['tools'])
        self.log.unlink()
        self.assertEqual(self.stack('--without', 'lint').returncode, 0)
        self.assertEqual(self.tools_run(), ['uv remove --group lint ruff pre-commit'])

    def registry_with(self, change):
        registry = json.loads((REPO / 'stack.json').read_text())
        change(registry)
        path = self.base / 'registry.json'
        path.write_text(json.dumps(registry))
        return path

    def test_a_failed_removal_in_one_profile_keeps_the_tool_when_another_profile_removed_it(self):
        def two(r):
            r['profiles']['pb'] = json.loads(json.dumps(r['profiles']['python']))
            r['profiles']['pb'].update(detect=[], remove=['false'], init={}, choices={})
            r['profiles']['pb'].pop('init')
        registry = self.registry_with(two)
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        self.assertEqual(self.stack('--profile', 'python,pb', '--defaults', '--registry', registry).returncode, 0)
        run = self.stack('--without', 'lint', '--registry', registry)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('lint', self.saved()['tools'])

    def test_a_turned_off_tool_with_no_remove_step_leaves_the_record_even_when_another_step_fails(self):
        self.assertEqual(self.stack('--profile', 'typescript', '--defaults').returncode, 0)
        self.assertEqual(self.saved()['tools'], ['lint', 'typecheck'])
        self.assertEqual(self.stack('test').returncode, 0)
        def failing_add(r):
            r['profiles']['typescript']['add'] = ['false']
        run = self.stack('--without', 'test', 'lint', '--registry', self.registry_with(failing_add))
        self.assertNotEqual(run.returncode, 0)
        self.assertNotIn('test', self.saved()['tools'])

    def test_a_file_error_after_a_removal_still_saves_the_record(self):
        # The python profile removes test, then a file of the typescript profile fails on a symlinked folder.
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        self.assertEqual(self.stack('--profile', 'python,typescript', '--defaults', 'test').returncode, 0)
        self.assertIn('test', self.saved()['tools'])
        def nested(r):
            r['profiles']['typescript']['tools']['typecheck']['files'] = {'sub/tsconfig.json': 'profiles/typescript/tsconfig.json'}
        (self.target / 'sub').symlink_to(self.base)
        self.log.unlink()
        run = self.stack('typecheck', '--without', 'test', '--registry', self.registry_with(nested))
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('uv remove --group test pytest pytest-cov', self.tools_run())
        self.assertIn('sub is a symlink', run.stderr)
        self.assertNotIn('Traceback', run.stderr)
        self.assertNotIn('test', self.saved()['tools'])
        self.assertFalse((self.base / 'tsconfig.json').exists())

    def test_default_groups_never_crash_on_dates_or_odd_tables(self):
        for text in ('[tool.x]\nreleased = 2024-01-01\n', 'tool = 1\n', '[tool]\nuv = 3\n'):
            path = self.base / 'pyproject.toml'
            path.write_text(text)
            with mock.patch('sys.stderr', io.StringIO()):
                install_stack.set_default_groups(path, 'all')
            after = path.read_text()
            if after != text:
                self.assertEqual(install_stack.tomllib.loads(after)['tool']['uv']['default-groups'], 'all')
        path.write_text('[tool.x]\nreleased = 2024-01-01\n')
        install_stack.set_default_groups(path, 'all')
        self.assertIn('default-groups = "all"', path.read_text())

    def test_a_dry_run_also_stops_on_a_symlinked_project_file(self):
        (self.target / 'pyproject.toml').symlink_to(self.base / 'outside.toml')
        run = self.stack('--profile', 'python', '--defaults', '--dry-run')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('is a symlink', run.stderr)

    def test_registry_paths_refuse_dot_git_and_outside_guards(self):
        for change in (lambda r: r['packages'][1].update(record='.'),
                       lambda r: r['packages'][1].update(record='a/./b.json'),
                       lambda r: r['profiles']['python'].update(init={'file': '.git/x', 'command': ['true']}),
                       lambda r: r['profiles']['python']['tools']['lint']['files'].update({'vendor/.git/hooks/x': 'profiles/python/ruff.toml'}),
                       lambda r: r['profiles']['python']['tools']['lint'].update(skip_if={'ruff.toml': ['../x', 'y']})):
            with self.assertRaises(install_stack.StackError):
                install_stack.load_registry(self.registry_with(change))

    def test_named_roles_alone_turn_on_only_those_roles_without_a_terminal(self):
        run = self.stack('tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('Only the named roles are turned on', run.stderr)
        self.assertTrue(self.has('gtw'))
        self.assertFalse(self.has('nossaifd'))

    def test_package_names_lose_their_versions(self):
        for spec, name in (('@biomejs/biome@2', '@biomejs/biome'), ('typescript@7', 'typescript'), ('ruff', 'ruff'),
                           ('ruff>=0.17', 'ruff'), ('pytest[testing]~=8', 'pytest')):
            self.assertEqual(install_stack.package_name(spec), name)

    def test_an_existing_justfile_is_kept_and_the_import_line_is_named(self):
        (self.target / 'justfile').write_text('test:\n    echo hi\n')
        run = self.stack('--profile', 'python')
        self.assertIn("Add this line to justfile, so that just install works: import? '.agents/install.just'", run.stderr)
        self.assertEqual((self.target / 'justfile').read_text(), 'test:\n    echo hi\n')
        self.assertFalse((self.target / 'Justfile').exists() and (self.target / 'Justfile').read_text() != 'test:\n    echo hi\n')

    def test_a_tool_needs_a_profile_that_has_it(self):
        run = self.stack('lint')
        self.assertIn('No active profile has the tool(s) lint', run.stderr)
        self.assertFalse((self.target / '.agents').exists())

    def test_a_missing_clone_is_cloned_and_a_named_clone_must_exist(self):
        origin = self.base / 'origin-nossaifd'
        shutil.move(self.packages / 'nossaifd', origin)
        registry = json.loads((REPO / 'stack.json').read_text())
        registry['packages'][2]['repository'] = str(origin)
        (self.packages / 'jnterfaces/stack.json').write_text(json.dumps(registry))
        run = self.stack('--defaults')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn(f'Cloning {origin}', run.stderr)
        self.assertTrue((self.packages / 'nossaifd/skill-package.json').is_file())
        run = self.stack('--clone', f'nossaifd={self.base / "nowhere"}')
        self.assertIn('No nossaifd clone', run.stderr)
        run = self.stack('--clone', f'nossaifd={self.packages / "sklanes"}')
        self.assertIn('is not the nossaifd package', run.stderr)

    def test_update_pulls_each_clone(self):
        origin = self.packages / 'sklanes'
        work = self.base / 'sklanes-clone'
        git(self.base, 'clone', '-q', str(origin), str(work))
        (origin / '.agents/skills/lanes/NEW.md').write_text('new\n')
        commit(origin)
        run = self.stack('--defaults', 'reporter', '--update', '--clone', f'sklanes={work}')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue((self.target / '.agents/skills/lanes/NEW.md').is_file())

    def test_dry_run_writes_nothing(self):
        (self.target / 'pyproject.toml').write_text('')
        run = self.stack('--all', '--dry-run')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse((self.target / '.agents').exists())
        self.assertFalse((self.target / 'Justfile').exists())
        self.assertIn('Would run: uv add --group lint ruff pre-commit', run.stdout)
        self.assertEqual(self.tools_run(), [])

    def test_a_failed_check_writes_nothing(self):
        (self.packages / 'sklanes/skill-package.json').write_text('{"repository": {"name": "sklanes"}, "skills": []}')
        commit(self.packages / 'sklanes')
        run = self.stack('--all')
        self.assertIn('The dry run of sklanes failed', run.stderr)
        self.assertFalse((self.target / '.agents').exists())

    def test_bad_names_and_options(self):
        run = self.stack('nope')
        self.assertIn('Unknown name: nope', run.stderr)
        self.assertIn('The roles are: cx, jagt-protocol, tasker, prose, reporter', run.stderr)
        self.assertIn('cannot be both turned on and turned off', self.stack('cx', '--without', 'cx').stderr)
        self.assertIn('--all or --defaults', self.stack('--all', '--defaults').stderr)
        self.assertIn('--clone needs NAME=DIR', self.stack('--defaults', '--clone', 'nossaifd').stderr)
        self.assertIn('--clone names no package: lanes', self.stack('--defaults', '--clone', 'lanes=/x').stderr)
        self.assertIn('does not go with --all', self.stack('--ask', '--all').stderr)
        self.assertIn('Unknown profile(s): rust', self.stack('--profile', 'rust').stderr)
        self.assertIn('must not be this jnterfaces clone', self.stack('--defaults', target=self.packages / 'jnterfaces').stderr)
        self.assertFalse((self.target / '.agents').exists())

    def test_a_symlinked_target_is_refused(self):
        link = self.base / 'link'
        link.symlink_to(self.target)
        for path in (link, f'{link}/', f'{link}//'):
            self.assertIn('must not be a symlink', self.stack('--defaults', target=path).stderr)
        self.assertFalse((self.target / '.agents').exists())

    def test_a_broken_saved_record_fails_with_a_way_out(self):
        (self.target / '.agents').mkdir()
        (self.target / '.agents/stack.json').write_text('{"choices": {"cx": "yes"}}')
        self.assertIn('not true or false', self.stack().stderr)
        (self.target / '.agents/stack.json').write_text('not json')
        self.assertIn('remove it and pass the choices again', self.stack().stderr)

    def test_clean_installs_the_committed_head(self):
        (self.packages / 'nossaifd/.agents/skills/nossaifd/EXTRA.md').write_text('not committed\n')
        self.assertEqual(self.stack('--defaults', '--clean').returncode, 0)
        self.assertFalse((self.target / '.agents/skills/nossaifd/EXTRA.md').exists())

    def test_a_package_whose_need_is_off_is_refused(self):
        registry = json.loads((REPO / 'stack.json').read_text())
        registry['packages'][3]['needs'] = ['nossaifd']
        path = self.base / 'registry.json'
        path.write_text(json.dumps(registry))
        run = self.stack('--defaults', 'reporter', '--without', 'prose', '--registry', path)
        self.assertIn('sklanes needs nossaifd', run.stderr)

    def test_questions_use_the_saved_or_default_answer_on_enter(self):
        _, choices, _, _ = install_stack.load_registry(REPO / 'stack.json')
        args = mock.Mock(with_=[], without=[], all=False, defaults=False, ask=False)
        answers = io.StringIO('\ny\nmaybe\nn\n\nyes\n')
        with mock.patch('sys.stdin', answers), mock.patch('sys.stderr', io.StringIO()) as err:
            chosen = install_stack.resolve(args, choices, None, True)
        self.assertEqual(chosen, {'cx': False, 'jagt-protocol': True, 'tasker': False, 'prose': True, 'reporter': True})
        self.assertIn('Answer y or n.', err.getvalue())
        self.assertIn('Install tasker (track work with Gest: the g* workflow skills)? [Y/n]', err.getvalue())

    def test_on_a_terminal_only_new_roles_are_asked(self):
        _, choices, _, _ = install_stack.load_registry(REPO / 'stack.json')
        args = mock.Mock(with_=[], without=[], all=False, defaults=False, ask=False)
        saved = {'cx': True, 'tasker': True, 'jagt-protocol': False, 'reporter': False}
        with mock.patch('sys.stdin', io.StringIO('n\n')), mock.patch('sys.stderr', io.StringIO()) as err:
            chosen = install_stack.resolve(args, choices, saved, True)
        self.assertEqual(err.getvalue().count('Install '), 1)
        self.assertEqual(chosen, {**saved, 'prose': False})

    def test_questions_stop_when_the_terminal_closes(self):
        _, choices, _, _ = install_stack.load_registry(REPO / 'stack.json')
        args = mock.Mock(with_=[], without=[], all=False, defaults=False, ask=True)
        with mock.patch('sys.stdin', io.StringIO('y\n')), mock.patch('sys.stderr', io.StringIO()):
            with self.assertRaises(install_stack.StackError):
                install_stack.resolve(args, choices, {}, True)

    def test_a_malformed_registry_is_refused(self):
        registry = json.loads((REPO / 'stack.json').read_text())
        for change in (lambda r: r['aliases'].update(x='nope'), lambda r: r['profiles']['python'].update(add='uv add'),
                       lambda r: r['profiles']['python']['tools'].update(cx={'packages': ['x']}),
                       lambda r: r['profiles']['python'].update(default_tools=['nope']),
                       lambda r: r.update(packages={'a': 1}), lambda r: r['profiles'].update(python=[]),
                       lambda r: r['packages'][0]['optional_skills'].append({'about': 'no choice'}),
                       lambda r: r['packages'][0].pop('required_skills'),
                       lambda r: r['profiles']['python'].update(justfile='../x.just'),
                       lambda r: r['profiles']['python']['tools']['lint']['files'].update({'../x': 'profiles/python/ruff.toml'}),
                       lambda r: r['packages'][1].update(record='/etc/x.json'),
                       lambda r: r['packages'][1].update(record='.GIT/config'),
                       lambda r: r['packages'][1].update(needs='jnterfaces'),
                       lambda r: r['packages'][2].pop('about'),
                       lambda r: r['packages'][0]['optional_skills'].append({'choice': 'tasker', 'about': 'twice'}),
                       lambda r: r['profiles']['python']['init'].update(command=[]),
                       lambda r: r['profiles']['python']['tools']['lint'].update(group=3),
                       lambda r: r['profiles']['python']['tools']['lint'].update(skip_if={'ruff.toml': 'x'}),
                       lambda r: r['profiles']['python'].update(uv_default_groups={'a': 1})):
            broken = json.loads(json.dumps(registry))
            change(broken)
            path = self.base / 'broken.json'
            path.write_text(json.dumps(broken))
            with self.assertRaises(install_stack.StackError):
                install_stack.load_registry(path)

    def test_the_readme_table_matches_stack_json(self):
        _, choices, _, _ = install_stack.load_registry(REPO / 'stack.json')
        readme = (REPO / 'README.md').read_text()
        rows = dict(re.findall(r'^\| `([a-z-]+)` \| [^|]+ \| [^|]+ \| (yes|no) \|$', readme, re.M))
        self.assertEqual(rows, {name: 'yes' if entry.get('default') else 'no' for name, entry in choices.items()})

    def test_the_profile_files_and_templates_exist(self):
        _, _, _, profiles = install_stack.load_registry(REPO / 'stack.json')
        for profile in profiles.values():
            self.assertIn("import? '.agents/install.just'", (REPO / profile['justfile']).read_text())
            for tool in profile['tools'].values():
                for origin in tool.get('files', {}).values():
                    self.assertTrue((REPO / origin).is_file(), origin)


class RealPackagesTest(unittest.TestCase):
    """Install the real clones next to this one, when they are there and have no installer of their own."""

    def test_real_packages_when_present(self):
        clones = {name: REPO.parent / name for name in SKILLS}
        if not all((path / 'skill-package.json').is_file() and not (path / 'scripts/install.sh').exists()
                   for path in clones.values()):
            self.skipTest('no clones of the other packages, in their new layout, next to this repository')
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            git(target, 'init', '-q')
            options = [arg for name, path in clones.items() for arg in ('--clone', f'{name}={path}')]

            def stack(*args):
                return subprocess.run([sys.executable, REPO / 'scripts/install_stack.py', '--target', target, *options,
                                       *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
            run = stack('--all')
            self.assertEqual(run.returncode, 0, run.stderr)
            for record in RECORDS.values():
                self.assertRegex(json.loads((target / record).read_text())['source_revision'], '^[0-9a-f]{40}$')
            for skill in ('just', 'cx', 'jagt-protocol', 'gtw', 'nossaifd', 'lanes'):
                self.assertTrue((target / f'.agents/skills/{skill}/SKILL.md').is_file(), skill)
            self.assertIn('session-start', (target / '.codex/hooks.json').read_text())
            run = stack('--without', 'cx,tasker')
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertFalse((target / '.agents/skills/cx').exists() or (target / '.agents/skills/gtw').exists())
            self.assertFalse((target / '.codex').exists())


@unittest.skipUnless(shutil.which('just') and shutil.which('uv'), 'needs just and uv')
class RecipeTests(unittest.TestCase):
    """Run just install in a target, through .agents/install.just, as a user does."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.jn = self.base / 'packages/jnterfaces'
        shutil.copytree(REPO, self.jn, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        registry = json.loads((REPO / 'stack.json').read_text())
        registry['packages'] = registry['packages'][:1]
        registry['aliases'] = {}
        for profile in registry['profiles'].values():
            profile['choices'] = {}
        (self.jn / 'stack.json').write_text(json.dumps(registry))
        git(self.jn, 'init', '-q')
        commit(self.jn)
        self.target = self.base / 'target'
        (self.target / '.agents').mkdir(parents=True)
        git(self.target, 'init', '-q')
        shutil.copy(REPO / '.agents/install.just', self.target / '.agents/install.just')
        (self.target / 'Justfile').write_text("import? '.agents/install.just'\n")

    def just(self, *args, **env):
        return subprocess.run(['just', *args], cwd=self.target, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              env=dict(os.environ, **env))

    def test_just_install_and_uninstall_run_the_installer(self):
        run = self.just('install', '--defaults', 'cx', JNTERFACES_DIR=str(self.jn))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue((self.target / '.agents/skills/cx/SKILL.md').is_file())
        run = self.just('uninstall', 'cx', JNTERFACES_DIR=str(self.jn))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse((self.target / '.agents/skills/cx').exists())

    def test_the_folder_of_packages_comes_from_skill_packages_dir(self):
        env = {key: value for key, value in os.environ.items() if key != 'JNTERFACES_DIR'}
        run = subprocess.run(['just', 'install', '--defaults', 'cx'], cwd=self.target, capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, env=dict(env, SKILL_PACKAGES_DIR=str(self.jn.parent),
                                                                HOME=str(self.base / 'home')))
        self.assertEqual(run.returncode, 0, run.stderr)
        record = json.loads((self.target / '.agents/jnterfaces-install.json').read_text())
        self.assertEqual(record['source_revision'], git(self.jn, 'rev-parse', 'HEAD'))
        self.assertFalse((self.base / 'home/.local/share/skill-packages').exists())


if __name__ == '__main__':
    unittest.main()
