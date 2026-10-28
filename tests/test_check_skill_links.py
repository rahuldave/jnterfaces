"""Tests for scripts/check_skill_links.py, which keeps the skills of a package composable."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
from check_skill_links import problems  # noqa: E402


def package(root, name, skills):
    root.mkdir(parents=True)
    entries = []
    for skill in skills:
        (root / '.agents/skills' / skill / 'references').mkdir(parents=True)
        (root / '.agents/skills' / skill / 'SKILL.md').write_text('x')
        entries.append({'name': skill, 'path': f'.agents/skills/{skill}'})
    (root / 'skill-package.json').write_text(json.dumps({'repository': {'name': name}, 'skills': entries}))
    return root


class SkillLinkTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.jn = package(self.base / 'jnterfaces', 'jnterfaces', ['just', 'cx', 'jagt-protocol'])
        self.gest = package(self.base / 'gest', 'agent_gest_git_skills', ['gtw', 'gim'])
        self.optional = {'cx', 'jagt-protocol'}

    def check(self, text, where, pkg=None, resolve=()):
        return problems(where, text, pkg or self.jn, self.optional if (pkg or self.jn) == self.jn else set(), resolve)

    def test_a_required_skill_of_the_same_package_needs_no_fallback(self):
        self.assertEqual(self.check('Read `.agents/skills/just/references/`.', self.jn / '.agents/skills/cx/SKILL.md'), [])
        self.assertEqual(problems(self.gest / '.agents/skills/gtw/SKILL.md', 'See `.agents/skills/gim/SKILL.md`.', self.gest), [])
        self.assertEqual(self.check('See `.agents/skills/cx/SKILL.md`.', self.jn / '.agents/skills/cx/references/a.md'), [])

    def test_an_optional_skill_needs_a_fallback_in_each_paragraph(self):
        where = self.jn / '.agents/skills/just/SKILL.md'
        text = ('Read `.agents/skills/cx/SKILL.md`. If the cx skill is not installed, go on.\n\n'
                'Then read `.agents/skills/jagt-protocol/SKILL.md`.\n')
        found = self.check(text, where)
        self.assertEqual(len(found), 1)
        self.assertIn('jagt-protocol/SKILL.md', found[0])
        self.assertIn('If the jagt-protocol skill is not installed', found[0])

    def test_a_fallback_must_say_not_installed_and_name_the_skill(self):
        where = self.jn / '.agents/skills/just/SKILL.md'
        for text in ('Read `.agents/skills/cx/SKILL.md` when it is installed.',
                     'If cx is installed, follow `.agents/skills/cx/SKILL.md`.',
                     'Run cx per `.agents/skills/cx/SKILL.md`. If the gest skill is not installed, skip.',
                     # jnterfaces may be installed without its optional cx skill
                     'Read `.agents/skills/cx/SKILL.md`. If jnterfaces is not installed, go on.',
                     'Use `.agents/skills/cx/SKILL.md` for stages; cx reruns a stage when an output is missing.',
                     'Read `.agents/skills/cx/SKILL.md`. Do not run cx if an input is absent.'):
            self.assertEqual(len(self.check(text, where)), 1, text)
        for text in ('Read `.agents/skills/cx/SKILL.md`. If the cx skill is missing, go on.',
                     'Read the skill, `.agents/skills/cx/SKILL.md`. If that skill is not installed, go on.',
                     'If the cx skill (in `.agents/skills/cx`) is not installed, go on.'):
            self.assertEqual(self.check(text, where), [], text)

    def test_each_list_item_needs_its_own_fallback(self):
        where = self.gest / '.agents/skills/gtw/SKILL.md'
        text = '- Read `.agents/skills/cx/SKILL.md`.\n- When the cx skill is not installed, go on.\n'
        self.assertEqual(len(problems(where, text, self.gest)), 1)
        wrapped = '1. Read `.agents/skills/cx/SKILL.md`. If the cx\n   skill is not installed, go on.\n'
        self.assertEqual(problems(where, wrapped, self.gest), [])

    def test_the_fallback_may_wrap_across_lines(self):
        where = self.jn / '.agents/skills/just/SKILL.md'
        self.assertEqual(self.check('Read `.agents/skills/cx/SKILL.md`. If the cx\nskill is not installed, go on.', where), [])

    def test_a_link_to_another_package_needs_a_fallback(self):
        where = self.gest / '.agents/skills/gtw/SKILL.md'
        owner = {'just': 'jnterfaces', 'cx': 'jnterfaces'}
        self.assertEqual(len(problems(where, 'Read `.agents/skills/just/SKILL.md`.', self.gest, set(), (), owner)), 1)
        ok = 'Read `.agents/skills/just/SKILL.md`. If jnterfaces is not installed, go on.'
        self.assertEqual(problems(where, ok, self.gest, set(), (), owner), [])
        outside = self.gest / 'AGENTS.template.md'
        self.assertEqual(len(problems(outside, 'Read `.agents/skills/cx/SKILL.md`.', self.gest)), 1)

    def test_other_forms_of_a_link_are_found(self):
        where = self.jn / '.agents/skills/just/references/a.md'
        self.assertEqual(len(self.check('See `../cx/SKILL.md`.', self.jn / '.agents/skills/just/SKILL.md')), 1)
        for text in ('See [cx](../../cx/references/).', 'Run `./.agents/skills/cx/SKILL.md`.',
                     'Run `$root/.agents/skills/cx/SKILL.md`.', 'See `.claude/skills/cx/SKILL.md`.'):
            self.assertEqual(len(self.check(text, where)), 1, text)
        self.assertEqual(self.check('See `../other/x.md` and `../just/SKILL.md`.', where), [])
        # A relative link into a skill that no listed package has is found too.
        gsu = self.jn / '.agents/skills/cx/SKILL.md'
        self.assertEqual(len(self.check('Read `../gsu/SKILL.md` for setup.', gsu)), 1)
        self.assertEqual(self.check('Read `../gsu/SKILL.md`. If the gsu skill is not installed, go on.', gsu), [])
        # From a file outside the skills, ../X/ is about the folders of the repository.
        self.assertEqual(self.check('See `../nossaifd/check.py`.', self.jn / 'README.md'), [])

    def test_paths_must_exist_in_the_package_or_a_resolved_clone(self):
        where = self.gest / '.agents/skills/gtw/SKILL.md'
        bad = 'See `.agents/skills/gim/NOPE.md` and `.agents/skills/cx/NOPE.md`. If the cx skill is not installed, go on.'
        self.assertEqual(len(problems(where, bad, self.gest)), 1)
        self.assertEqual(len(problems(where, bad, self.gest, set(), [self.jn])), 2)
        ok = ('See `.agents/skills/cx/references/` and `.agents/skills/unknown/x.md`. If the cx skill is not '
              'installed, go on. If the unknown skill is not installed, stop.')
        self.assertEqual(problems(where, ok, self.gest, set(), [self.jn]), [])

    def test_boundaries_urls_home_paths_and_traversal(self):
        where = self.gest / 'README.md'
        self.assertEqual(problems(where, 'https://example.org/master/skills/just/SKILL.md', self.gest), [])
        self.assertEqual(problems(where, 'Run `~/.agents/skills/tool/x.py`.', self.gest), [])
        self.assertEqual(problems(where, 'See `.claude/skills/gtw`.', self.gest), [])
        self.assertEqual(len(problems(where, 'See `skills/just/SKILL.md`.', self.gest)), 1)
        (self.base / 'README.md').write_text('x')
        text = 'See `.agents/skills/gim/../../../README.md`.'
        self.assertEqual(len(problems(where, text, self.gest)), 1)

    def test_the_command_line_checks_the_packages(self):
        script = REPO / 'scripts/check_skill_links.py'
        good = self.gest / '.agents/skills/gtw/SKILL.md'
        good.write_text('Read `.agents/skills/cx/SKILL.md`. If the cx skill is not installed, go on.\n')
        run = subprocess.run([sys.executable, script, '--package', self.gest, '--resolve', self.jn, good],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        good.write_text('Read `.agents/skills/cx/SKILL.md`.\n')
        run = subprocess.run([sys.executable, script, '--package', self.gest, good], capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        run = subprocess.run([sys.executable, script, '--package', self.base, good], capture_output=True, text=True)
        self.assertIn('No skill package', run.stderr)

    def test_the_skills_of_this_repository_pass(self):
        files = [p for p in (REPO / '.agents/skills').rglob('*') if p.is_file() and p.suffix in ('.md', '.sh', '.just', '.yaml')]
        found = [line for path in files
                 for line in problems(path, path.read_text(), REPO, {'cx', 'jagt-protocol'})]
        self.assertEqual(found, [])


if __name__ == '__main__':
    unittest.main()
