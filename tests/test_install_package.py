"""Install the package into disposable target repositories and check every rule of the installer."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
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
        return run('bash', self.source / 'scripts/install.sh', self.target, *args, check=check)

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
        self.assertTrue(all(name.startswith('.agents/skills/just/') for name in record['managed_files']))
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
        result = run('bash', self.source / 'scripts/install.sh', self.source, check=False)
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


if __name__ == '__main__':
    unittest.main()
