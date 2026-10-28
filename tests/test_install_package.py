"""Install the package into disposable target repositories and check every rule of the installer."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
RECORD = Path('.agents/jnterfaces-install.json')


def run(*args, cwd=None, check=True):
    return subprocess.run([str(arg) for arg in args], cwd=cwd, check=check, capture_output=True, text=True)


def git(path, *args):
    return run('git', '-C', path, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', *args).stdout.strip()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.source = root / 'source'
        shutil.copytree(REPO, self.source, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        git(self.source, 'init', '-q')
        git(self.source, 'add', '-A')
        git(self.source, 'commit', '-q', '-m', 'source')
        self.target = root / 'target'
        self.target.mkdir()
        git(self.target, 'init', '-q')

    def tearDown(self):
        self.temp.cleanup()

    def install(self, *args, check=True):
        return run(sys.executable, self.source / 'scripts/install_package.py', '--source', self.source, self.target, *args,
                   check=check)

    def record(self):
        return json.loads((self.target / RECORD).read_text())

    def commit_source(self):
        git(self.source, 'add', '-A')
        git(self.source, 'commit', '-q', '-m', 'change')

    def test_installs_every_skill_and_records_the_revision(self):
        self.install()
        for skill in ('just', 'jagt-protocol', 'cx'):
            self.assertTrue((self.target / '.agents/skills' / skill / 'SKILL.md').is_file())
        record = self.record()
        self.assertEqual(record['package'], 'jnterfaces')
        self.assertEqual(record['source_revision'], git(self.source, 'rev-parse', 'HEAD'))
        self.assertEqual(record['skills'], ['cx', 'jagt-protocol', 'just'])
        script = self.target / '.agents/skills/jagt-protocol/scripts/jagt_lint_agent_task.sh'
        self.assertTrue(os.access(script, os.X_OK))
        self.assertEqual(record['managed_files'][script.relative_to(self.target).as_posix()], sha(script))

    def test_dry_run_writes_nothing(self):
        result = self.install('--dry-run')
        self.assertIn('Would install', result.stdout)
        self.assertFalse((self.target / '.agents').exists())

    def test_second_install_changes_nothing(self):
        self.install()
        before = (self.target / RECORD).read_text()
        self.install()
        self.assertEqual(before, (self.target / RECORD).read_text())

    def test_refuses_to_overwrite_an_edited_file(self):
        self.install()
        skill = self.target / '.agents/skills/just/SKILL.md'
        skill.write_text('edited by the user\n')
        (self.source / '.agents/skills/just/SKILL.md').write_text('---\nname: just\ndescription: new\n---\n')
        self.commit_source()
        result = self.install(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('differs from source', result.stderr)
        self.assertEqual(skill.read_text(), 'edited by the user\n')

    def test_refuses_a_file_that_no_install_recorded(self):
        skill = self.target / '.agents/skills/cx/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('a local skill with the same name\n')
        result = self.install(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(skill.read_text(), 'a local skill with the same name\n')

    def test_removes_an_unchanged_file_that_the_source_dropped(self):
        self.install()
        (self.source / '.agents/skills/cx/references/incremental_pipelines.md').unlink()
        (self.source / '.agents/skills/cx/SKILL.md').write_text('---\nname: cx\ndescription: d\n---\n')
        self.commit_source()
        self.install()
        self.assertFalse((self.target / '.agents/skills/cx/references').exists())
        self.assertNotIn('.agents/skills/cx/references/incremental_pipelines.md', self.record()['managed_files'])

    def test_keeps_an_edited_file_that_the_source_dropped(self):
        self.install()
        kept = self.target / '.agents/skills/cx/references/incremental_pipelines.md'
        kept.write_text('my notes\n')
        (self.source / '.agents/skills/cx/references/incremental_pipelines.md').unlink()
        self.commit_source()
        result = self.install()
        self.assertEqual(kept.read_text(), 'my notes\n')
        self.assertIn('edited after install', result.stderr)
        self.assertNotIn(kept.relative_to(self.target).as_posix(), self.record()['managed_files'])

    def test_adopts_a_copy_that_another_package_recorded(self):
        old = self.target / '.agents/skills/jagt-protocol/SKILL.md'
        old.parent.mkdir(parents=True)
        old.write_text('copy shipped by another package\n')
        other = {'managed_files': {'.agents/skills/jagt-protocol/SKILL.md': sha(old)}}
        (self.target / '.agents/other-install.json').write_text(json.dumps(other))
        self.install()
        self.assertEqual(old.read_bytes(), (self.source / '.agents/skills/jagt-protocol/SKILL.md').read_bytes())

    def test_does_not_remove_a_file_that_another_package_records(self):
        self.install()
        shared = '.agents/skills/cx/references/incremental_pipelines.md'
        (self.target / '.agents/other-install.json').write_text(
            json.dumps({'managed_files': {shared: sha(self.target / shared)}}))
        (self.source / shared).unlink()
        self.commit_source()
        result = self.install()
        self.assertTrue((self.target / shared).is_file())
        self.assertIn('another package records it', result.stderr)

    def test_rejects_a_recorded_path_outside_the_skills(self):
        victim = Path(self.temp.name) / 'victim.txt'
        victim.write_text('keep me\n')
        (self.target / '.agents').mkdir()
        (self.target / RECORD).write_text(json.dumps({'managed_files': {'../victim.txt': sha(victim)}}))
        result = self.install(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(victim.exists())

    def test_rejects_a_symlink_in_a_managed_path(self):
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        (self.target / '.agents').mkdir()
        (self.target / '.agents/skills').symlink_to(outside)
        result = self.install(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])

    def test_pinned_commit_ignores_uncommitted_changes(self):
        committed = git(self.source, 'rev-parse', 'HEAD')
        (self.source / '.agents/skills/just/SKILL.md').write_text('uncommitted\n')
        self.install('--source-commit', committed)
        installed = (self.target / '.agents/skills/just/SKILL.md').read_text()
        self.assertNotEqual(installed, 'uncommitted\n')
        self.assertFalse(self.record()['source_worktree_dirty'])

    def test_one_skill_keeps_the_record_of_the_others(self):
        self.install()
        before = self.record()['managed_files']
        self.install('--skill', 'cx')
        record = self.record()
        self.assertEqual(record['managed_files'], before)
        self.assertEqual(record['skills'], ['cx', 'jagt-protocol', 'just'])
        result = self.install('--skill', 'nope', check=False)
        self.assertIn('Unknown skill', result.stderr)

    def test_exact_removes_the_skills_left_out(self):
        self.install()
        result = self.install('--skill', 'just', '--exact', check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.target / '.agents/skills/cx').exists())
        self.assertFalse((self.target / '.agents/skills/jagt-protocol').exists())
        record = self.record()
        self.assertEqual(record['skills'], ['just'])
        self.assertTrue(all(name.startswith('.agents/skills/just/') for name in record['managed_files']
                            if name.startswith('.agents/skills/')))
        self.assertIn('.agents/install.just', record['managed_files'])
        # Turning a skill on again installs it.
        self.install('--skill', 'just', '--skill', 'cx', '--exact')
        self.assertTrue((self.target / '.agents/skills/cx/SKILL.md').is_file())
        self.assertEqual(self.record()['skills'], ['cx', 'just'])

    def test_exact_keeps_a_whole_skill_that_has_an_edited_file(self):
        self.install()
        before = self.record()['managed_files']
        edited = self.target / '.agents/skills/cx/SKILL.md'
        edited.write_text(edited.read_text() + 'my note\n')
        result = self.install('--skill', 'just', '--exact', check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('the cx skill stays installed', result.stderr)
        self.assertTrue((self.target / '.agents/skills/cx/references/incremental_pipelines.md').is_file())
        self.assertFalse((self.target / '.agents/skills/jagt-protocol').exists())
        record = self.record()
        self.assertEqual(record['skills'], ['cx', 'just'])
        cx = {k: v for k, v in before.items() if k.startswith('.agents/skills/cx/')}
        self.assertEqual({k: v for k, v in record['managed_files'].items() if k.startswith('.agents/skills/cx/')}, cx)
        # After the user undoes the edit, the skill can be turned on again, or removed.
        edited.write_text(edited.read_text().replace('my note\n', ''))
        self.assertEqual(self.install('--skill', 'just', '--exact', check=False).returncode, 0)
        self.assertFalse((self.target / '.agents/skills/cx').exists())

    def test_exact_needs_a_skill(self):
        result = self.install('--exact', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--exact needs at least one --skill', result.stderr)

    def test_refuses_the_source_as_target(self):
        result = run(sys.executable, self.source / 'scripts/install_package.py', '--source', self.source, self.source,
                     check=False)
        self.assertNotEqual(result.returncode, 0)


    def test_pinned_commit_ignores_git_attributes_and_sets_plain_modes(self):
        (self.source / '.gitattributes').write_text('.agents/skills/just/SKILL.md export-subst\n.agents/skills/cx/references/* export-ignore\n')
        skill = self.source / '.agents/skills/just/SKILL.md'
        skill.write_text(skill.read_text() + 'x $Format:%H$\n')
        self.commit_source()
        self.install('--source-commit', 'HEAD')
        self.assertIn('x $Format:%H$', (self.target / '.agents/skills/just/SKILL.md').read_text())
        self.assertTrue((self.target / '.agents/skills/cx/references/incremental_pipelines.md').is_file())
        modes = {oct(p.stat().st_mode & 0o777) for p in (self.target / '.agents/skills').rglob('*') if p.is_file()}
        self.assertEqual(modes, {'0o644', '0o755'})
        before = (self.target / RECORD).read_text()
        self.install()
        self.assertEqual(json.loads(before)['managed_files'], self.record()['managed_files'])

    def test_a_case_only_rename_keeps_the_new_file(self):
        self.install()
        git(self.source, 'mv', '.agents/skills/cx/references/incremental_pipelines.md',
            '.agents/skills/cx/references/Incremental_pipelines.md')
        self.commit_source()
        self.install()
        self.assertEqual(os.listdir(self.target / '.agents/skills/cx/references'), ['Incremental_pipelines.md'])
        self.assertIn('.agents/skills/cx/references/Incremental_pipelines.md', self.record()['managed_files'])
        self.assertNotIn('.agents/skills/cx/references/incremental_pipelines.md', self.record()['managed_files'])

    def test_unpinned_install_leaves_out_ignored_files(self):
        (self.source / '.agents/skills/just/.DS_Store').write_text('finder\n')
        with (self.source / '.gitignore').open('a') as handle:
            handle.write('*.local\n')
        (self.source / '.agents/skills/just/notes.local').write_text('mine\n')
        (self.source / '.agents/skills/just/new.md').write_text('untracked but not ignored\n')
        self.install()
        self.assertFalse((self.target / '.agents/skills/just/.DS_Store').exists())
        self.assertFalse((self.target / '.agents/skills/just/notes.local').exists())
        self.assertTrue((self.target / '.agents/skills/just/new.md').is_file())

    def test_a_file_in_the_way_fails_before_any_write(self):
        (self.target / '.agents/skills/just').mkdir(parents=True)
        (self.target / '.agents/skills/just/references').write_text('in the way\n')
        self.assertNotEqual(self.install('--dry-run', check=False).returncode, 0)
        result = self.install(check=False)
        self.assertIn('in the way', result.stderr)
        self.assertEqual([p.name for p in (self.target / '.agents/skills').iterdir()], ['just'])
        self.assertEqual(os.listdir(self.target / '.agents/skills/just'), ['references'])

    def test_malformed_records_do_not_crash_the_installer(self):
        (self.target / '.agents').mkdir()
        (self.target / '.agents/other-install.json').write_text(json.dumps({'managed_files': {'x': [1]}}))
        (self.target / RECORD).write_text(json.dumps({'skills': 'abc', 'managed_files': {'y': {'z': 1}}}))
        self.install()
        self.assertEqual(self.record()['skills'], ['cx', 'jagt-protocol', 'just'])

    def test_a_committed_symlink_fails_pinned_and_unpinned(self):
        os.symlink('SKILL.md', self.source / '.agents/skills/cx/LINK.md')
        self.commit_source()
        self.assertNotEqual(self.install('--source-commit', 'HEAD', check=False).returncode, 0)
        self.assertNotEqual(self.install(check=False).returncode, 0)
        self.assertFalse((self.target / '.agents').exists())

    def test_a_mode_change_does_not_follow_a_hard_link(self):
        self.install()
        installed = self.target / '.agents/skills/jagt-protocol/scripts/run_protocol_demo.sh'
        outside = Path(self.temp.name) / 'outside.sh'
        installed.chmod(0o600)
        os.link(installed, outside)
        self.install()
        self.assertEqual(outside.stat().st_mode & 0o777, 0o600)
        self.assertEqual(installed.stat().st_mode & 0o777, 0o755)

    def test_never_removes_files_of_a_skill_that_is_not_in_the_package(self):
        other = self.target / '.agents/skills/mine/SKILL.md'
        other.parent.mkdir(parents=True)
        other.write_text('a skill of the user\n')
        (self.target / RECORD).write_text(json.dumps({'managed_files': {'.agents/skills/mine/SKILL.md': sha(other)}}))
        self.install()
        self.assertEqual(other.read_text(), 'a skill of the user\n')


    def test_a_case_only_rename_with_new_content_installs(self):
        self.install()
        old = '.agents/skills/cx/references/incremental_pipelines.md'
        new = '.agents/skills/cx/references/Incremental_pipelines.md'
        git(self.source, 'mv', old, new)
        (self.source / new).write_text('new text\n')
        self.commit_source()
        self.install()
        self.assertEqual(os.listdir(self.target / '.agents/skills/cx/references'), ['Incremental_pipelines.md'])
        self.assertEqual((self.target / new).read_text(), 'new text\n')

    def test_prunes_by_folder_when_a_skill_name_differs_from_its_folder(self):
        manifest = json.loads((self.source / 'skill-package.json').read_text())
        manifest['skills'][2]['name'] = 'cx-name'
        (self.source / 'skill-package.json').write_text(json.dumps(manifest))
        self.commit_source()
        self.install()
        (self.source / '.agents/skills/cx/references/incremental_pipelines.md').unlink()
        self.commit_source()
        self.install()
        self.assertFalse((self.target / '.agents/skills/cx/references').exists())

    def test_a_malformed_manifest_fails_with_a_message(self):
        for manifest in ({'repository': 'pkg', 'skills': []}, {'repository': {'name': 'p'}, 'skills': ['adir']}):
            (self.source / 'skill-package.json').write_text(json.dumps(manifest))
            self.commit_source()
            result = self.install(check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Install failed', result.stderr)
            self.assertNotIn('Traceback', result.stderr)


HOOKS = {'hooks': {'SessionStart': [{'matcher': 'startup', 'hooks': [{'type': 'command', 'command': 'start.sh'}]}]}}


class ExtrasTest(unittest.TestCase):
    """A package with extras: managed files outside the skills, merged hook settings and a starter file."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.source = self.base / 'hooky'
        files = {
            'skill-package.json': json.dumps({
                'repository': {'name': 'hooky'}, 'skills': [{'name': 'hk', 'path': '.agents/skills/hk'}],
                'install_extras': {'files': ['.claude/hooks', 'tools/run.sh'], 'merge_hooks': ['.claude/settings.json'],
                                   'seed': {'AGENTS.md': 'AGENTS.template.md'}}}),
            '.agents/skills/hk/SKILL.md': '---\nname: hk\ndescription: d\n---\n',
            '.claude/hooks/start.sh': '#!/bin/sh\necho start\n',
            'tools/run.sh': 'echo run\n',
            '.claude/settings.json': json.dumps(HOOKS),
            'AGENTS.template.md': '# Agents\n',
        }
        for name, text in files.items():
            (self.source / name).parent.mkdir(parents=True, exist_ok=True)
            (self.source / name).write_text(text)
        (self.source / '.claude/hooks/start.sh').chmod(0o755)
        git(self.source, 'init', '-q')
        self.commit()
        self.target = self.base / 'target'
        self.target.mkdir()
        git(self.target, 'init', '-q')

    def commit(self):
        git(self.source, 'add', '-A')
        git(self.source, 'commit', '-q', '-m', 'source')

    def install(self, *args, check=True):
        return run(sys.executable, REPO / 'scripts/install_package.py', '--source', self.source, self.target, *args,
                   check=check)

    def settings(self):
        return json.loads((self.target / '.claude/settings.json').read_text())

    def record(self, name='.agents/hooky-install.json'):
        return json.loads((self.target / name).read_text())

    def user_settings(self):
        mine = {'model': 'x', 'hooks': {'SessionStart': [{'matcher': 'startup', 'hooks': [{'type': 'command', 'command': 'mine.sh'}]}],
                                        'Stop': [{'hooks': [{'type': 'command', 'command': 'stop.sh'}]}]}}
        (self.target / '.claude').mkdir()
        (self.target / '.claude/settings.json').write_text(json.dumps(mine))
        return mine

    def test_installs_the_extras_merges_the_hooks_and_seeds_a_missing_file(self):
        mine = self.user_settings()
        self.install()
        self.assertTrue(os.access(self.target / '.claude/hooks/start.sh', os.X_OK))
        self.assertEqual((self.target / 'tools/run.sh').read_text(), 'echo run\n')
        self.assertEqual((self.target / 'AGENTS.md').read_text(), '# Agents\n')
        settings = self.settings()
        self.assertEqual(settings['model'], 'x')
        self.assertEqual(settings['hooks']['Stop'], mine['hooks']['Stop'])
        commands = [h['command'] for h in settings['hooks']['SessionStart'][0]['hooks']]
        self.assertEqual(commands, ['mine.sh', 'start.sh'])
        record = self.record()
        self.assertEqual(record['extras'], ['.claude/hooks', 'tools/run.sh'])
        self.assertEqual(record['merged_hooks'], {'.claude/settings.json': HOOKS})
        self.assertIn('.claude/hooks/start.sh', record['managed_files'])
        self.assertNotIn('AGENTS.md', record['managed_files'])
        before = {name: (self.target / name).read_bytes() for name in ('.claude/settings.json', '.agents/hooky-install.json')}
        (self.target / 'AGENTS.md').write_text('my agents\n')
        self.install()
        self.assertEqual(before, {name: (self.target / name).read_bytes() for name in before})
        self.assertEqual((self.target / 'AGENTS.md').read_text(), 'my agents\n')

    def test_a_conflicting_hook_command_stops_before_any_write(self):
        (self.target / '.claude').mkdir()
        clash = {'hooks': {'SessionStart': [{'matcher': 'startup', 'hooks': [{'type': 'other', 'command': 'start.sh'}]}]}}
        (self.target / '.claude/settings.json').write_text(json.dumps(clash))
        result = self.install(check=False)
        self.assertIn('Conflicting hook command', result.stderr)
        self.assertFalse((self.target / '.agents').exists())
        self.assertFalse((self.target / 'tools').exists())

    def test_an_unrecorded_file_in_the_place_of_an_extra_stops_the_install(self):
        (self.target / 'tools').mkdir()
        (self.target / 'tools/run.sh').write_text('my own run\n')
        result = self.install(check=False)
        self.assertIn('differs from source', result.stderr)
        self.assertEqual((self.target / 'tools/run.sh').read_text(), 'my own run\n')

    def test_an_extra_or_a_hook_that_the_source_drops_is_taken_out(self):
        mine = self.user_settings()
        self.install()
        manifest = json.loads((self.source / 'skill-package.json').read_text())
        manifest['install_extras'] = {'files': ['.claude/hooks']}
        (self.source / 'skill-package.json').write_text(json.dumps(manifest))
        (self.source / 'tools/run.sh').unlink()
        self.commit()
        self.install()
        self.assertFalse((self.target / 'tools').exists())
        self.assertEqual(self.settings(), mine)
        self.assertEqual(self.record()['merged_hooks'], {})

    def test_remove_takes_out_the_files_and_hooks_and_keeps_the_rest(self):
        mine = self.user_settings()
        self.install()
        self.install('--remove', '--dry-run')
        self.assertTrue((self.target / 'tools/run.sh').exists())
        result = self.install('--remove')
        self.assertIn('Removed hooky', result.stdout)
        self.assertEqual(self.settings(), mine)
        for gone in ('.agents/skills/hk', '.claude/hooks', 'tools', '.agents/hooky-install.json'):
            self.assertFalse((self.target / gone).exists(), gone)
        self.assertTrue((self.target / 'AGENTS.md').exists())
        self.assertIn('not installed', self.install('--remove').stdout)

    def test_remove_keeps_an_edited_file_and_marks_the_record(self):
        self.install()
        (self.target / 'tools/run.sh').write_text('my change\n')
        result = self.install('--remove')
        self.assertIn('Kept tools/run.sh', result.stderr)
        self.assertEqual((self.target / 'tools/run.sh').read_text(), 'my change\n')
        self.assertFalse((self.target / '.claude/settings.json').exists())
        record = self.record()
        self.assertTrue(record['removed'])
        self.assertEqual(list(record['managed_files']), ['tools/run.sh'])

    def test_record_names_the_file_of_the_record(self):
        self.install('--record', '.agents/old-name.json')
        self.assertEqual(self.record('.agents/old-name.json')['package'], 'hooky')
        self.assertFalse((self.target / '.agents/hooky-install.json').exists())
        for bad in ('../x.json', 'elsewhere/x.json'):
            self.assertIn('--record must be', self.install('--record', bad, check=False).stderr)

    def test_bad_extras_fail_with_a_message(self):
        for extras in ({'files': ['.agents/skills/hk']}, {'files': ['../x']}, {'files': ['.git/config']},
                       {'merge_hooks': '.claude/settings.json'}, {'seed': {'AGENTS.md': '/etc/passwd'}},
                       {'files': ['nowhere']}):
            manifest = json.loads((self.source / 'skill-package.json').read_text())
            manifest['install_extras'] = extras
            (self.source / 'skill-package.json').write_text(json.dumps(manifest))
            self.commit()
            result = self.install(check=False)
            self.assertNotEqual(result.returncode, 0, extras)
            self.assertNotIn('Traceback', result.stderr)

    def test_a_pinned_commit_installs_extras_from_the_commit(self):
        head = git(self.source, 'rev-parse', 'HEAD')
        (self.source / 'tools/run.sh').write_text('uncommitted\n')
        self.install('--source-commit', head)
        self.assertEqual((self.target / 'tools/run.sh').read_text(), 'echo run\n')
        self.assertTrue(os.access(self.target / '.claude/hooks/start.sh', os.X_OK))

    def test_remove_keeps_a_hook_that_the_user_had_before_and_the_settings_file(self):
        (self.target / '.claude').mkdir()
        (self.target / '.claude/settings.json').write_text(json.dumps(HOOKS))
        self.install()
        self.assertEqual(self.record()['merged_hooks'], {})
        self.install('--remove')
        self.assertEqual(self.settings(), HOOKS)

    def test_remove_keeps_a_settings_file_that_existed_even_when_it_ends_up_empty(self):
        (self.target / '.claude').mkdir()
        (self.target / '.claude/settings.json').write_text('{}')
        self.install()
        self.install('--remove')
        self.assertEqual(self.settings(), {})

    def test_a_recorded_path_outside_the_target_is_refused(self):
        victim = self.base / 'victim.json'
        victim.write_text(json.dumps(HOOKS))
        (self.target / '.agents').mkdir()
        for key in (str(victim), '../victim.json', '.claude/../../victim.json', '.git/config'):
            for field in ('merged_hooks', 'extras', 'created_settings'):
                value = {key: HOOKS} if field == 'merged_hooks' else [key]
                (self.target / '.agents/hooky-install.json').write_text(json.dumps({field: value}))
                for args in ((), ('--remove',)):
                    result = self.install(*args, check=False)
                    self.assertNotEqual(result.returncode, 0, (key, field, args))
                    self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(json.loads(victim.read_text()), HOOKS)

    def test_an_edited_hook_is_kept_and_its_script_stays_on_removal(self):
        runs = {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'sh .claude/hooks/start.sh'}]}]}}
        (self.source / '.claude/settings.json').write_text(json.dumps(runs))
        self.commit()
        self.install()
        settings = self.settings()
        settings['hooks']['Stop'][0]['hooks'][0]['timeout'] = 5
        (self.target / '.claude/settings.json').write_text(json.dumps(settings))
        result = self.install()
        self.assertIn('Kept your edited hook', result.stderr)
        self.assertEqual(self.settings(), settings)
        result = self.install('--remove')
        self.assertIn('a hook that stays in the settings runs it', result.stderr)
        self.assertTrue((self.target / '.claude/hooks/start.sh').is_file())
        self.assertEqual(self.settings(), settings)

    def test_an_old_record_without_merged_hooks_owns_the_source_hooks(self):
        # The old installer of agent_gest_git_skills merged the source hooks and recorded none of them.
        (self.target / '.claude/hooks').mkdir(parents=True)
        (self.target / '.claude/hooks/start.sh').write_text('#!/bin/sh\necho start\n')
        (self.target / '.claude/settings.json').write_text(json.dumps(HOOKS))
        (self.target / '.agents').mkdir()
        (self.target / '.agents/hooky-install.json').write_text(json.dumps({'managed_files': {
            '.claude/hooks/start.sh': sha(self.target / '.claude/hooks/start.sh')}}))
        self.install()
        self.assertEqual(self.record()['merged_hooks'], {'.claude/settings.json': HOOKS})
        result = self.install('--remove')
        self.assertNotIn('Kept', result.stderr)
        self.assertFalse((self.target / '.claude/hooks').exists())
        self.assertEqual(self.settings(), {})

    def test_an_old_record_takes_out_the_old_version_of_a_changed_hook(self):
        old = {'hooks': {'SessionStart': [{'matcher': 'startup', 'hooks': [{'type': 'command', 'command': 'start.sh'}]}]}}
        mine = {'type': 'command', 'command': 'mine.sh'}
        settings = json.loads(json.dumps(old))
        settings['hooks']['SessionStart'][0]['hooks'].append(mine)
        (self.target / '.claude').mkdir()
        (self.target / '.claude/settings.json').write_text(json.dumps(settings))
        (self.target / '.agents').mkdir()
        (self.target / '.agents/hooky-install.json').write_text(json.dumps({'managed_files': {}}))
        new = json.loads(json.dumps(old))
        new['hooks']['SessionStart'][0]['hooks'][0]['timeout'] = 30
        (self.source / '.claude/settings.json').write_text(json.dumps(new))
        self.commit()
        result = self.install()
        self.assertNotIn('Kept your edited hook', result.stderr)
        hooks = self.settings()['hooks']['SessionStart'][0]['hooks']
        self.assertEqual(hooks, [mine, new['hooks']['SessionStart'][0]['hooks'][0]])
        self.install('--remove')
        self.assertEqual(self.settings()['hooks']['SessionStart'][0]['hooks'], [mine])

    def test_an_old_record_names_an_edited_or_dropped_hook(self):
        (self.target / '.claude/hooks').mkdir(parents=True)
        (self.target / '.claude/hooks/start.sh').write_text('#!/bin/sh\necho start\n')
        edited = {'type': 'command', 'command': 'start.sh', 'timeout': 5}
        dropped = {'type': 'command', 'command': 'sh .claude/hooks/start.sh --old'}
        (self.target / '.claude/settings.json').write_text(json.dumps(
            {'hooks': {'SessionStart': [{'matcher': 'startup', 'hooks': [edited, dropped]}]}}))
        (self.target / '.agents').mkdir()
        (self.target / '.agents/hooky-install.json').write_text(json.dumps({'managed_files': {
            '.claude/hooks/start.sh': sha(self.target / '.claude/hooks/start.sh')}}))
        result = self.install()
        self.assertIn("The hook 'start.sh' in .claude/settings.json differs", result.stderr)
        self.assertIn("The hook 'sh .claude/hooks/start.sh --old' in .claude/settings.json runs a file of the old "
                      "install", result.stderr)

    def test_recorded_paths_are_checked_without_case_and_dot_is_refused(self):
        (self.target / '.agents').mkdir()
        for extras in (['.GIT'], ['.Agents/Skills/x'], ['.'], ['a/./b'], ['']):
            (self.target / '.agents/hooky-install.json').write_text(json.dumps({'extras': extras}))
            result = self.install(check=False)
            self.assertNotEqual(result.returncode, 0, extras)
            self.assertNotIn('Traceback', result.stderr, extras)

    def test_remove_does_not_go_with_skill(self):
        self.assertIn('does not go with --skill', self.install('--remove', '--skill', 'hk', check=False).stderr)


if __name__ == '__main__':
    unittest.main()
