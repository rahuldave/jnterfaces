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

    def test_keeps_an_edited_file_and_installs_the_rest(self):
        self.install()
        skill = self.target / '.agents/skills/just/SKILL.md'
        recorded = self.record()['managed_files']['.agents/skills/just/SKILL.md']
        skill.write_text('edited by the user\n')
        (self.source / '.agents/skills/just/SKILL.md').write_text('---\nname: just\ndescription: new\n---\n')
        (self.source / '.agents/skills/cx/SKILL.md').write_text('---\nname: cx\ndescription: new\n---\n')
        self.commit_source()
        result = self.install()
        self.assertIn('Kept .agents/skills/just/SKILL.md: edited after install', result.stderr)
        self.assertEqual(result.stderr.count('.agents/skills/just/SKILL.md'), 1)
        self.assertEqual(skill.read_text(), 'edited by the user\n')
        self.assertIn('description: new', (self.target / '.agents/skills/cx/SKILL.md').read_text())
        # The record keeps the hash of the install, so the edit still shows on the next run and on removal.
        self.assertEqual(self.record()['managed_files']['.agents/skills/just/SKILL.md'], recorded)
        self.assertIn('edited after install', self.install().stderr)
        self.assertEqual(skill.read_text(), 'edited by the user\n')
        result = self.install('--remove')
        self.assertIn('Kept .agents/skills/just/SKILL.md', result.stderr)
        self.assertEqual(skill.read_text(), 'edited by the user\n')

    def test_an_undone_edit_gets_the_new_version(self):
        self.install()
        skill = self.target / '.agents/skills/just/SKILL.md'
        original = skill.read_bytes()
        skill.write_text('edited by the user\n')
        (self.source / '.agents/skills/just/SKILL.md').write_text('---\nname: just\ndescription: new\n---\n')
        self.commit_source()
        self.install()
        skill.write_bytes(original)
        self.install()
        self.assertIn('description: new', skill.read_text())
        self.assertEqual(self.record()['managed_files']['.agents/skills/just/SKILL.md'], sha(skill))

    def test_links_each_skill_for_claude_and_takes_the_links_out(self):
        mine = self.target / '.claude/skills/cx'
        mine.mkdir(parents=True)
        (mine / 'notes.md').write_text('my own folder\n')
        foreign = self.target / '.claude/skills/jagt-protocol'
        foreign.symlink_to('../../somewhere/else')
        self.install()
        just = self.target / '.claude/skills/just'
        self.assertEqual(os.readlink(just), '../../.agents/skills/just')
        self.assertTrue((just / 'SKILL.md').is_file())
        self.assertEqual((mine / 'notes.md').read_text(), 'my own folder\n')
        self.assertEqual(os.readlink(foreign), '../../somewhere/else')
        # --exact drops cx and jagt-protocol: only a link that points at a dropped skill goes.
        self.install('--skill', 'just', '--exact')
        self.assertTrue(just.is_symlink())
        self.assertTrue(foreign.is_symlink())
        self.install('--remove')
        self.assertFalse(just.is_symlink())
        self.assertTrue(foreign.is_symlink())
        self.assertTrue((mine / 'notes.md').is_file())

    def test_an_edited_skill_keeps_its_link_on_removal_and_a_linked_skills_folder_is_left_alone(self):
        self.install()
        (self.target / '.agents/skills/cx/SKILL.md').write_text('edited\n')
        self.install('--remove')
        self.assertTrue((self.target / '.claude/skills/cx').is_symlink())
        self.assertFalse((self.target / '.claude/skills/just').exists())
        other = Path(self.temp.name) / 'other'
        other.mkdir()
        git(other, 'init', '-q')
        (other / '.claude').mkdir()
        (other / '.agents/skills').mkdir(parents=True)
        (other / '.claude/skills').symlink_to('../.agents/skills')
        run(sys.executable, self.source / 'scripts/install_package.py', '--source', self.source, other)
        self.assertEqual(sorted(p.name for p in (other / '.agents/skills').iterdir()), ['cx', 'jagt-protocol', 'just'])
        self.assertFalse(any(p.is_symlink() for p in (other / '.agents/skills').iterdir()))

    def test_a_claude_skills_file_is_left_alone_and_the_install_succeeds(self):
        (self.target / '.claude').mkdir()
        (self.target / '.claude/skills').write_text('not a folder\n')
        result = self.install(check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.target / '.claude/skills').read_text(), 'not a folder\n')
        self.assertEqual(self.record()['claude_links'], [])

    def test_a_dry_run_names_the_links_it_would_make_and_remove(self):
        result = self.install('--dry-run')
        self.assertIn('Would link .claude/skills to cx, jagt-protocol, just', result.stdout)
        self.assertFalse((self.target / '.claude').exists())
        self.install()
        result = self.install('--remove', '--dry-run')
        self.assertIn('Would remove the .claude/skills link of cx, jagt-protocol, just', result.stdout)
        self.assertTrue((self.target / '.claude/skills/just').is_symlink())

    def test_a_link_that_the_user_removed_stays_removed(self):
        self.install()
        (self.target / '.claude/skills/cx').unlink()
        for _ in range(3):
            self.install()
            self.assertFalse((self.target / '.claude/skills/cx').is_symlink())
            self.assertIn('cx', self.record()['claude_links'])
        # A removal never makes a link, also for a skill that stays because the user edited it.
        (self.target / '.agents/skills/cx/SKILL.md').write_text('edited\n')
        self.install('--remove')
        self.assertFalse((self.target / '.claude/skills/cx').exists())

    def test_a_removal_with_an_old_record_makes_no_link(self):
        self.install()
        record = self.record()
        del record['claude_links']
        (self.target / RECORD).write_text(json.dumps(record))
        shutil.rmtree(self.target / '.claude')
        (self.target / '.agents/skills/cx/SKILL.md').write_text('edited\n')
        self.install('--remove')
        self.assertFalse((self.target / '.claude').exists())

    def test_the_link_of_a_skill_uses_its_folder_not_its_name(self):
        manifest = json.loads((self.source / 'skill-package.json').read_text())
        manifest['skills'][2]['name'] = 'cx-name'
        (self.source / 'skill-package.json').write_text(json.dumps(manifest))
        self.commit_source()
        self.install()
        self.assertEqual(os.readlink(self.target / '.claude/skills/cx'), '../../.agents/skills/cx')
        self.assertFalse((self.target / '.claude/skills/cx-name').exists())

    def test_an_edited_file_with_a_case_only_rename_is_kept(self):
        self.install()
        old = '.agents/skills/cx/references/incremental_pipelines.md'
        new = '.agents/skills/cx/references/Incremental_pipelines.md'
        (self.target / old).write_text('my notes\n')
        git(self.source, 'mv', old, new)
        (self.source / new).write_text('new text\n')
        self.commit_source()
        result = self.install(check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        names = os.listdir(self.target / '.agents/skills/cx/references')
        edited = [name for name in names if (self.target / '.agents/skills/cx/references' / name).read_text() == 'my notes\n']
        self.assertEqual(len(edited), 1, names)
        self.assertIn('edited after install', result.stderr)

    def case_insensitive(self):
        probe = self.target / 'Probe'
        probe.write_text('')
        try:
            return (self.target / 'probe').exists()
        finally:
            probe.unlink()

    def test_an_edited_case_rename_is_recorded_under_the_name_on_disk(self):
        if not self.case_insensitive():
            self.skipTest('needs a disk that ignores case')
        self.install()
        refs = self.target / '.agents/skills/cx/references'
        old = '.agents/skills/cx/references/incremental_pipelines.md'
        new = '.agents/skills/cx/references/Incremental_pipelines.md'
        original = (self.target / old).read_bytes()
        (self.target / old).write_text('my notes\n')
        git(self.source, 'mv', old, new)
        (self.source / new).write_text('new text\n')
        self.commit_source()
        self.install()
        managed = self.record()['managed_files']
        self.assertIn(old, managed)
        self.assertNotIn(new, managed)
        # Renamed back in the source: the edit is still kept.
        git(self.source, 'mv', new, old)
        self.commit_source()
        self.assertIn('edited after install', self.install().stderr)
        self.assertEqual((self.target / old).read_text(), 'my notes\n')
        # Renamed again, and the edit undone: the new version arrives under the new name.
        git(self.source, 'mv', old, new)
        self.commit_source()
        self.install()
        (self.target / old).write_bytes(original)
        self.install()
        self.assertEqual(os.listdir(refs), ['Incremental_pipelines.md'])
        self.assertEqual((self.target / new).read_text(), 'new text\n')

    def old_record_without_skills(self):
        record = self.record()
        del record['skills']
        del record['claude_links']
        (self.target / RECORD).write_text(json.dumps(record))

    def test_a_partial_install_links_no_skill_whose_folder_is_gone(self):
        self.install()
        self.old_record_without_skills()
        shutil.rmtree(self.target / '.claude')
        shutil.rmtree(self.target / '.agents/skills/cx')
        self.install('--skill', 'just')
        self.assertEqual(sorted(os.listdir(self.target / '.claude/skills')), ['jagt-protocol', 'just'])
        # The old record listed no skills: the partial install keeps the skills that it left.
        self.assertEqual(self.record()['skills'], ['cx', 'jagt-protocol', 'just'])

    def test_a_skill_folder_that_the_package_never_shipped_gets_no_link(self):
        mine = self.target / '.agents/skills/mine/SKILL.md'
        mine.parent.mkdir(parents=True)
        mine.write_text('my skill\n')
        self.install()
        record = self.record()
        record['managed_files']['.agents/skills/mine/SKILL.md'] = sha(mine)
        (self.target / RECORD).write_text(json.dumps(record))
        self.old_record_without_skills()
        self.install('--skill', 'just')
        self.assertFalse((self.target / '.claude/skills/mine').exists())
        self.install()
        self.assertFalse((self.target / '.claude/skills/mine').exists())
        self.assertEqual(mine.read_text(), 'my skill\n')

    def test_a_partial_install_of_an_old_record_drops_a_skill_of_the_user_so_no_removal_takes_it(self):
        mine = self.target / '.agents/skills/mine/SKILL.md'
        mine.parent.mkdir(parents=True)
        mine.write_text('my skill\n')
        self.install()
        record = self.record()
        record['managed_files']['.agents/skills/mine/SKILL.md'] = sha(mine)
        (self.target / RECORD).write_text(json.dumps(record))
        self.old_record_without_skills()
        (self.target / '.claude/skills/mine').symlink_to('../../.agents/skills/mine')
        self.install('--skill', 'just')
        self.assertNotIn('.agents/skills/mine/SKILL.md', self.record()['managed_files'])
        self.install('--remove')
        self.assertEqual(mine.read_text(), 'my skill\n')
        self.assertEqual(os.readlink(self.target / '.claude/skills/mine'), '../../.agents/skills/mine')

    def test_a_removal_keeps_the_link_of_a_skill_that_another_package_records(self):
        self.install()
        cx = {name: value for name, value in self.record()['managed_files'].items() if name.startswith('.agents/skills/cx/')}
        (self.target / '.agents/other-install.json').write_text(json.dumps({'managed_files': cx, 'claude_links': ['cx']}))
        result = self.install('--remove')
        self.assertIn('another package records it', result.stderr)
        self.assertEqual(os.readlink(self.target / '.claude/skills/cx'), '../../.agents/skills/cx')
        self.assertFalse((self.target / '.claude/skills/just').exists())

    def test_a_dropped_skill_with_an_edited_skill_md_keeps_its_link(self):
        self.install()
        (self.target / '.agents/skills/cx/SKILL.md').write_text('edited\n')
        manifest = json.loads((self.source / 'skill-package.json').read_text())
        manifest['skills'] = [skill for skill in manifest['skills'] if skill['name'] != 'cx']
        (self.source / 'skill-package.json').write_text(json.dumps(manifest))
        shutil.rmtree(self.source / '.agents/skills/cx')
        self.commit_source()
        result = self.install()
        self.assertIn('Kept .agents/skills/cx/SKILL.md', result.stderr)
        self.assertEqual(os.readlink(self.target / '.claude/skills/cx'), '../../.agents/skills/cx')

    def test_a_dropped_skill_whose_folder_goes_later_loses_its_link(self):
        self.install()
        (self.target / '.agents/skills/cx/SKILL.md').write_text('edited\n')
        manifest = json.loads((self.source / 'skill-package.json').read_text())
        manifest['skills'] = [skill for skill in manifest['skills'] if skill['name'] != 'cx']
        (self.source / 'skill-package.json').write_text(json.dumps(manifest))
        shutil.rmtree(self.source / '.agents/skills/cx')
        self.commit_source()
        self.install()
        self.install()
        shutil.rmtree(self.target / '.agents/skills/cx')
        self.install()
        self.assertFalse((self.target / '.claude/skills/cx').is_symlink())
        self.assertNotIn('cx', self.record()['claude_links'])

    def test_odd_names_in_claude_links_are_ignored(self):
        self.install()
        outside = self.target / '.claude/evil'
        outside.symlink_to('../../.agents/skills/../evil')
        record = self.record()
        record['claude_links'] += ['..', '../evil', 7]
        (self.target / RECORD).write_text(json.dumps(record))
        self.install('--remove')
        self.assertTrue(outside.is_symlink())

    def test_a_removal_after_an_older_partial_install_keeps_a_skill_of_the_user(self):
        # The installer on main wrote this record: a list of skills without the user's folder, which it still listed.
        mine = self.target / '.agents/skills/mine/SKILL.md'
        mine.parent.mkdir(parents=True)
        mine.write_text('my skill\n')
        self.install()
        record = self.record()
        record['managed_files']['.agents/skills/mine/SKILL.md'] = sha(mine)
        record['skills'] = ['just']
        (self.target / RECORD).write_text(json.dumps(record))
        self.install('--remove')
        self.assertEqual(mine.read_text(), 'my skill\n')
        self.assertFalse((self.target / '.agents/skills/just').exists())

    def test_a_removal_takes_a_dropped_skill_whose_name_differs_from_its_folder(self):
        manifest = json.loads((self.source / 'skill-package.json').read_text())
        manifest['skills'][2]['name'] = 'cx-name'
        (self.source / 'skill-package.json').write_text(json.dumps(manifest))
        self.commit_source()
        self.install()
        self.assertIn('cx', self.record()['skill_folders'])
        manifest['skills'] = manifest['skills'][:2]
        (self.source / 'skill-package.json').write_text(json.dumps(manifest))
        shutil.rmtree(self.source / '.agents/skills/cx')
        self.commit_source()
        # The clone no longer has the revision of the record, as after a force push.
        record = self.record()
        record['source_revision'] = '0' * 40
        (self.target / RECORD).write_text(json.dumps(record))
        result = self.install('--remove')
        self.assertNotIn('never shipped', result.stderr)
        self.assertFalse((self.target / '.agents/skills/cx').exists())
        self.assertFalse((self.target / '.claude').exists())

    def test_skills_that_come_back_after_claude_was_a_file_get_links(self):
        self.install()
        shutil.rmtree(self.target / '.claude')
        (self.target / '.claude').write_text('a file\n')
        self.install('--skill', 'just', '--exact')
        self.assertEqual(self.record()['claude_links'], ['just'])
        (self.target / '.claude').unlink()
        self.install()
        self.assertEqual(sorted(os.listdir(self.target / '.claude/skills')), ['cx', 'jagt-protocol', 'just'])

    def test_an_empty_claude_folder_goes_with_the_last_link(self):
        self.install()
        self.install('--remove')
        self.assertFalse((self.target / '.claude').exists())

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
        for gone in ('.agents/skills/hk', '.claude/hooks', 'tools', '.agents/hooky-install.json', 'AGENTS.md'):
            self.assertFalse((self.target / gone).exists(), gone)
        self.assertIn('not installed', self.install('--remove').stdout)

    def test_a_starter_file_goes_only_when_nobody_changed_it(self):
        self.install()
        self.assertEqual(self.record()['seeds'], {'AGENTS.md': sha(self.target / 'AGENTS.md')})
        (self.target / 'AGENTS.md').write_text('my agents\n')
        result = self.install('--remove')
        self.assertIn('Kept AGENTS.md: you edited this starter file of hooky', result.stderr)
        self.assertEqual((self.target / 'AGENTS.md').read_text(), 'my agents\n')

    def test_a_starter_file_that_the_project_had_stays(self):
        (self.target / 'AGENTS.md').write_text('the project wrote this\n')
        self.install()
        self.assertEqual(self.record()['seeds'], {})
        self.install('--remove')
        self.assertEqual((self.target / 'AGENTS.md').read_text(), 'the project wrote this\n')

    def test_a_starter_file_of_the_project_equal_to_the_template_stays(self):
        (self.target / 'AGENTS.md').write_text('# Agents\n')
        self.install()
        result = self.install('--remove')
        self.assertEqual((self.target / 'AGENTS.md').read_text(), '# Agents\n')
        self.assertIn('0 files kept', result.stdout)

    def test_a_kept_starter_file_counts_as_kept(self):
        self.install()
        (self.target / 'AGENTS.md').write_text('my agents\n')
        self.assertIn('1 files kept', self.install('--remove').stdout)

    def test_an_old_record_without_seeds_removes_a_starter_file_equal_to_the_source(self):
        self.install()
        record = self.record()
        del record['seeds']
        (self.target / '.agents/hooky-install.json').write_text(json.dumps(record))
        self.install('--remove')
        self.assertFalse((self.target / 'AGENTS.md').exists())

    def test_a_starter_file_whose_source_changed_still_goes_by_the_recorded_hash(self):
        self.install()
        (self.source / 'AGENTS.template.md').write_text('# Agents, new\n')
        self.commit()
        self.install()
        self.install('--remove')
        self.assertFalse((self.target / 'AGENTS.md').exists())

    def test_odd_paths_in_recorded_seeds_are_refused(self):
        self.install()
        record = self.record()
        record['seeds'] = {'../outside.md': 'x'}
        (self.target / '.agents/hooky-install.json').write_text(json.dumps(record))
        self.assertNotEqual(self.install('--remove', check=False).returncode, 0)

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

    def old_install(self, settings, managed=None):
        """A target as the old installer of agent_gest_git_skills left it: a record with no skills and no hooks."""
        (self.target / '.claude').mkdir(exist_ok=True)
        (self.target / '.claude/settings.json').write_text(json.dumps(settings))
        (self.target / '.agents').mkdir(exist_ok=True)
        (self.target / '.agents/hooky-install.json').write_text(json.dumps(
            {'source_revision': git(self.source, 'rev-parse', 'HEAD'), 'managed_files': managed or {}}))

    def test_an_old_record_uses_the_hooks_of_its_own_revision(self):
        v1 = {'type': 'command', 'command': 'start.sh'}
        mine = {'type': 'command', 'command': 'mine.sh'}
        self.old_install({'hooks': {'SessionStart': [{'matcher': 'startup', 'hooks': [v1, mine]}]}})
        v2 = {'type': 'command', 'command': 'start.sh --v2'}
        (self.source / '.claude/settings.json').write_text(json.dumps(
            {'hooks': {'SessionStart': [{'matcher': 'startup', 'hooks': [v2]}]}}))
        self.commit()
        result = self.install()
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(self.settings()['hooks']['SessionStart'][0]['hooks'], [mine, v2])
        self.install('--remove')
        self.assertEqual(self.settings()['hooks']['SessionStart'][0]['hooks'], [mine])

    def test_an_old_record_keeps_a_hook_that_the_user_edited(self):
        edited = {'type': 'command', 'command': 'start.sh', 'timeout': 5}
        self.old_install({'hooks': {'SessionStart': [{'matcher': 'startup', 'hooks': [edited]}]}})
        result = self.install()
        self.assertIn('Kept your edited hook', result.stderr)
        self.assertEqual(self.settings()['hooks']['SessionStart'][0]['hooks'], [edited])

    def test_an_old_record_removes_a_settings_file_that_ends_up_empty(self):
        self.old_install(HOOKS)
        self.install('--remove')
        self.assertFalse((self.target / '.claude/settings.json').exists())

    def test_an_old_record_removes_a_skill_copy_that_the_source_once_shipped(self):
        moved = self.source / '.agents/skills/lanes/SKILL.md'
        moved.parent.mkdir(parents=True)
        moved.write_text('lanes\n')
        manifest = json.loads((self.source / 'skill-package.json').read_text())
        manifest['skills'].append({'name': 'lanes', 'path': '.agents/skills/lanes'})
        (self.source / 'skill-package.json').write_text(json.dumps(manifest))
        self.commit()
        copy = self.target / '.agents/skills/lanes/SKILL.md'
        copy.parent.mkdir(parents=True)
        copy.write_text('lanes\n')
        mine = self.target / '.agents/skills/mine/SKILL.md'
        mine.parent.mkdir(parents=True)
        mine.write_text('my skill\n')
        self.old_install({}, {'.agents/skills/lanes/SKILL.md': sha(copy), '.agents/skills/mine/SKILL.md': sha(mine)})
        manifest['skills'].pop()
        (self.source / 'skill-package.json').write_text(json.dumps(manifest))
        git(self.source, 'rm', '-q', '-r', '.agents/skills/lanes')
        self.commit()
        self.install()
        self.assertFalse(copy.exists())
        self.assertEqual(mine.read_text(), 'my skill\n')

    def test_a_hook_that_the_user_moved_to_another_group_is_not_added_twice(self):
        self.install()
        settings = self.settings()
        settings['hooks']['SessionStart'][0]['matcher'] = 'startup|resume'
        (self.target / '.claude/settings.json').write_text(json.dumps(settings))
        result = self.install()
        self.assertIn('is in another group of SessionStart', result.stderr)
        self.assertEqual(self.settings(), settings)
        self.install('--remove')
        self.assertEqual(self.settings(), settings)

    def test_removing_an_old_record_keeps_the_link_of_a_skill_that_the_source_never_shipped(self):
        mine = self.target / '.agents/skills/mine/SKILL.md'
        mine.parent.mkdir(parents=True)
        mine.write_text('my skill\n')
        self.old_install(HOOKS, {'.agents/skills/mine/SKILL.md': sha(mine)})
        (self.target / '.claude/skills').mkdir()
        (self.target / '.claude/skills/mine').symlink_to('../../.agents/skills/mine')
        self.install('--remove')
        self.assertEqual(os.readlink(self.target / '.claude/skills/mine'), '../../.agents/skills/mine')

    def test_removing_an_old_record_leaves_a_skill_that_the_source_never_shipped(self):
        mine = self.target / '.agents/skills/mine/SKILL.md'
        mine.parent.mkdir(parents=True)
        mine.write_text('my skill\n')
        self.old_install(HOOKS, {'.agents/skills/mine/SKILL.md': sha(mine)})
        result = self.install('--remove')
        self.assertIn('the package never shipped that skill', result.stderr)
        self.assertEqual(mine.read_text(), 'my skill\n')
        self.assertFalse((self.target / '.agents/hooky-install.json').exists())

    def test_an_old_record_takes_out_the_hooks_of_a_file_that_the_source_no_longer_merges(self):
        self.old_install(HOOKS)
        manifest = json.loads((self.source / 'skill-package.json').read_text())
        manifest['install_extras']['merge_hooks'] = []
        (self.source / 'skill-package.json').write_text(json.dumps(manifest))
        self.commit()
        self.install()
        self.assertEqual(self.settings(), {})

    def test_an_old_record_claims_only_skills_of_its_exact_revision(self):
        # The source once committed a skill named mine, then deleted it before the old revision. The clone also has
        # an uncommitted folder of that name. Neither shows that the old install shipped the user's mine.
        theirs = self.source / '.agents/skills/mine/SKILL.md'
        theirs.parent.mkdir(parents=True)
        theirs.write_text('once\n')
        self.commit()
        git(self.source, 'rm', '-q', '-r', '.agents/skills/mine')
        self.commit()
        theirs.parent.mkdir(parents=True)
        theirs.write_text('scratch\n')
        mine = self.target / '.agents/skills/mine/SKILL.md'
        mine.parent.mkdir(parents=True)
        mine.write_text('my skill\n')
        self.old_install(HOOKS, {'.agents/skills/mine/SKILL.md': sha(mine)})
        record = json.loads((self.target / '.agents/hooky-install.json').read_text())
        record['source_worktree_dirty'] = True
        (self.target / '.agents/hooky-install.json').write_text(json.dumps(record))
        self.install()
        self.assertEqual(mine.read_text(), 'my skill\n')
        result = self.install('--remove')
        self.assertEqual(mine.read_text(), 'my skill\n')
        self.assertNotIn('Left', result.stderr)

    def test_an_old_record_without_its_commit_in_the_clone_claims_no_skill(self):
        copy = self.target / '.agents/skills/hk2/SKILL.md'
        copy.parent.mkdir(parents=True)
        copy.write_text('x\n')
        self.old_install(HOOKS, {'.agents/skills/hk2/SKILL.md': sha(copy)})
        record = json.loads((self.target / '.agents/hooky-install.json').read_text())
        record['source_revision'] = '0' * 40
        (self.target / '.agents/hooky-install.json').write_text(json.dumps(record))
        result = self.install('--remove')
        self.assertIn('the package never shipped that skill', result.stderr)
        self.assertEqual(copy.read_text(), 'x\n')

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
