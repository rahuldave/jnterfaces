"""Check the shipped skills: their front matter, their links, the jagt wrappers and the house-style template."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / '.agents/skills'
NAMES = ('just', 'jagt-protocol', 'cx')
WRAPPERS = {
    'jagt_lint_agent_task.sh': ['lint'],
    'jagt_lint_agent_result.sh': ['result', 'lint'],
    'jagt_lint_agent_task_draft.sh': ['draft', 'lint'],
}


def shipped_text():
    for path in sorted(SKILLS.rglob('*')):
        if path.is_file() and path.suffix in ('.md', '.sh', '.yaml', '.just'):
            yield path, path.read_text()


def jagt_with_drafts():
    """A jagt binary that has the draft commands, or None."""
    for candidate in (os.environ.get('JAGT_BIN'), shutil.which('jagt')):
        if candidate and subprocess.run([candidate, 'draft', 'lint', '--help'], capture_output=True).returncode == 0:
            return candidate
    return None


class SkillTest(unittest.TestCase):
    def test_each_skill_names_itself_and_says_when_to_use_it(self):
        self.assertEqual(sorted(p.name for p in SKILLS.iterdir()), sorted(NAMES))
        for name in NAMES:
            text = (SKILLS / name / 'SKILL.md').read_text()
            front = re.match(r'^---\nname: (.+)\ndescription: (.+)\nlicense: (.+)\n---\n', text)
            self.assertIsNotNone(front, name)
            self.assertEqual(front.group(1), name)
            self.assertIn('Use when', front.group(2))
            # Installed copies carry the licence here, because no installer copies LICENSE.
            self.assertRegex(front.group(3), r'^MIT, copyright \(c\) 2026 Rahul Dave\. .*rahuldave/jnterfaces$')
            self.assertTrue((SKILLS / name / 'agents/openai.yaml').is_file())
        self.assertTrue((REPO / 'LICENSE').read_text().startswith('MIT License\n\nCopyright (c) 2026 Rahul Dave\n'))

    def test_every_installed_path_and_relative_link_exists(self):
        for path, text in shipped_text():
            for ref in re.findall(r'\.agents/skills/[A-Za-z0-9_./-]+\.(?:md|sh|just)', text):
                self.assertTrue((REPO / ref).is_file(), f'{path}: {ref}')
            for link in re.findall(r'\]\(([^)#:]+\.md)\)', text):
                self.assertTrue((path.parent / link).is_file(), f'{path}: {link}')
            for ref in re.findall(r'`((?:references|scripts|assets)/[A-Za-z0-9_./-]+\.[a-z]+)`', text):
                skill = path.relative_to(SKILLS).parts[0]
                self.assertTrue((SKILLS / skill / ref).is_file(), f'{path}: {ref}')

    def test_no_path_of_one_machine_or_legacy_validator(self):
        for path, text in shipped_text():
            self.assertNotRegex(text, r'/(Users|home)/[A-Za-z0-9._-]+/', str(path))
        self.assertEqual(list(SKILLS.rglob('validate_agent_*.sh')), [])

    def test_scripts_are_executable_and_parse(self):
        for script in SKILLS.rglob('*.sh'):
            self.assertTrue(os.access(script, os.X_OK), script)
            subprocess.run(['bash', '-n', script], check=True)

    def test_wrappers_print_help_and_need_jagt(self):
        scripts = SKILLS / 'jagt-protocol/scripts'
        for name in WRAPPERS:
            result = subprocess.run(['bash', scripts / name, '--help'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertIn('Usage:', result.stderr)
            env = dict(os.environ, JAGT_BIN='no-such-jagt-binary')
            result = subprocess.run(['bash', scripts / name, 'file'], capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 127)

    def test_wrappers_pass_every_argument_to_jagt(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = Path(temp) / 'jagt'
            fake.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
            fake.chmod(0o755)
            for name, command in WRAPPERS.items():
                result = subprocess.run(['bash', SKILLS / 'jagt-protocol/scripts' / name, '--expect-count', '1', 'a file'],
                                        capture_output=True, text=True, env=dict(os.environ, JAGT_BIN=str(fake)), check=True)
                self.assertEqual(result.stdout.splitlines(), [*command, '--expect-count', '1', 'a file'])

    def test_protocol_demo_runs_with_a_current_jagt(self):
        jagt = jagt_with_drafts()
        if not jagt:
            self.skipTest('no jagt with draft lint; set JAGT_BIN')
        result = subprocess.run(['bash', SKILLS / 'jagt-protocol/scripts/run_protocol_demo.sh', jagt],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('jagt protocol demo passed', result.stdout)

    def test_cx_skill_and_contract_match_what_cx_writes(self):
        for page in ('cx/SKILL.md', 'just/references/command_contract.md'):
            text = (SKILLS / page).read_text()
            self.assertIn('`.cx/state.json` and `.cx/tmp/`', text, page)
            self.assertNotIn('graph.json', text, page)
            self.assertIn('If the project later adds `.cx/config.toml`', text, page)
            for sentence in re.split(r'(?<=[.!?])\s+', text):
                if '.cx/state.json' in sentence or '.cx/tmp' in sentence:
                    self.assertNotIn('version control', sentence, page)

    def test_shipped_markdown_passes_the_writing_checker(self):
        checker = Path(os.environ.get('NOSSAIFD_CHECK', REPO.parent / 'nossaifd/check.py'))
        if not checker.is_file():
            self.skipTest('no nossaifd check.py; set NOSSAIFD_CHECK')
        pages = [REPO / 'README.md', REPO / 'AGENTS.md', *sorted(SKILLS.glob('*/SKILL.md')),
                 *sorted(SKILLS.glob('*/references/*.md'))]
        self.assertEqual(sorted(pages[2:]), sorted(SKILLS.rglob('*.md')), 'just prose must check every shipped page')
        with tempfile.TemporaryDirectory() as temp:
            for page in pages:
                body = re.sub(r'\A---\n.*?\n---\n', '', page.read_text(), flags=re.S)
                copy = Path(temp) / page.name
                copy.write_text(body)
                result = subprocess.run(['python3', checker, copy], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, f'{page}: {result.stdout}{result.stderr}')

    def test_house_style_template_follows_the_house_style(self):
        template = SKILLS / 'just/assets/templates/house-style.just'
        text = template.read_text()
        self.assertIn('set positional-arguments', text)
        self.assertRegex(text, r'\[private\]\ndefault:\n    @just --list --unsorted')
        for recipe in re.finditer(r'^([a-z][a-z-]*)(?: [^:\n]*)?:\s*$', text, re.M):
            before = text[:recipe.start()].rstrip('\n').splitlines()[-1]
            if recipe.group(1) != 'default':
                self.assertTrue(before.startswith('# '), f'{recipe.group(1)} has no comment')
        check = re.search(r'^check:\n((?:    .+\n)+)', text, re.M).group(1)
        self.assertEqual([line.split()[1] for line in check.splitlines()], ['fmt', 'lint', 'types', 'test'])
        self.assertIn('--check', check.splitlines()[0])
        if shutil.which('just'):
            listed = subprocess.run(['just', '--justfile', template, '--list', '--unsorted'],
                                    capture_output=True, text=True, check=True).stdout
            self.assertNotIn('default', listed)
            self.assertIn('check', listed)


if __name__ == '__main__':
    unittest.main()
