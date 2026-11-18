"""CI and the install hints get jlog and jagt as release binaries from their pinned install.sh, not with cargo."""
import json
import os
import re
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

from ruamel.yaml import YAML

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import install_stack  # noqa: E402

WORKFLOWS = [REPO / '.github/workflows/verify.yml', REPO / '.agents/skills/docs/assets/docs-publish.yml']
# The release of each program that CI installs.
TAGS = {'jlog': 'v0.3.0', 'jagt': 'v0.6.3'}
# The release that each install hint names. CI does not install cx.
HINT_TAGS = dict(TAGS, cx='v0.2.2')
# The tag v0.6.3 of jagt has no install.sh, so CI takes install.sh from the commit of jagt that adds it.
JAGT_INSTALLER = '7ea80a99492fe5eed5d7fa9b9a4e1ba08599463a'
CARGO = re.compile(r'\bcargo\s+(b?install)\b.*\b(jlog|jagt)\b')
INSTALLER = re.compile(r'curl -fsSL https://raw\.githubusercontent\.com/rahuldave/(jlog|jagt)/([^/\s]+)/scripts/install\.sh'
                       r' \| sh -s -- --tag (\S+)$')
# The line that a person runs. It takes install.sh from main and names the release with --tag.
HINT = 'curl -fsSL https://raw.githubusercontent.com/rahuldave/{name}/main/scripts/install.sh | sh -s -- --tag {tag}'


def steps(path):
    return [step for job in YAML(typ='safe').load(path.read_text())['jobs'].values() for step in job['steps']]


class WorkflowInstallTests(unittest.TestCase):
    def test_no_workflow_builds_jlog_or_jagt_with_cargo(self):
        for path in WORKFLOWS:
            for number, line in enumerate(path.read_text().splitlines(), 1):
                self.assertNotRegex(line, CARGO, f'{path.name}:{number}')

    def test_each_install_is_pinned_fails_on_a_failed_download_and_adds_the_folder_to_the_path(self):
        found = {}
        for path in WORKFLOWS:
            for step in steps(path):
                lines = [line.strip() for line in str(step.get('run', '')).splitlines() if line.strip()]
                installs = [INSTALLER.match(line) for line in lines if 'install.sh' in line]
                if not installs:
                    continue
                with self.subTest(workflow=path.name, step=step.get('name')):
                    self.assertTrue(all(installs), lines)
                    # The default shell of a run step has no pipefail, so curl could fail and sh still pass.
                    self.assertEqual(step.get('shell'), 'bash')
                    self.assertIn('echo "$HOME/.local/bin" >> "$GITHUB_PATH"', lines)
                    for match in installs:
                        name, ref, tag = match.groups()
                        self.assertEqual(tag, TAGS[name])
                        self.assertEqual(ref, tag if name == 'jlog' else JAGT_INSTALLER)
                        found.setdefault(path.name, set()).add(name)
        self.assertEqual(found, {'verify.yml': {'jlog', 'jagt'}, 'docs-publish.yml': {'jlog'}})

    def test_each_version_on_a_line_of_jlog_or_jagt_is_its_pin(self):
        for path in WORKFLOWS:
            for number, line in enumerate(path.read_text().splitlines(), 1):
                for name, tag in TAGS.items():
                    if name in line:
                        for version in re.findall(r'(?<![\w.])v?(\d+\.\d+\.\d+)(?![\w.])', line):
                            self.assertEqual(f'v{version}', tag, f'{path.name}:{number}')


class InstallHintTests(unittest.TestCase):
    """Each hint gives the install line of the release binary first, and the build from source second."""

    def assert_binary_then_source(self, text, name):
        line = HINT.format(name=name, tag=HINT_TAGS[name])
        self.assertIn(line, text)
        self.assertIn('from source', text)
        self.assertLess(text.index(line), text.index('from source'))

    def assert_each_command_ends_at_its_tag(self, text):
        # A command sits in backticks, so a copy of it never takes the comma or the period of the sentence.
        commands = re.findall(r'`(curl [^`]*)`', text)
        self.assertTrue(commands, text)
        for command in commands:
            argv = shlex.split(command)
            self.assertEqual(argv[-2:], ['--tag', HINT_TAGS[argv[2].split('/')[4]]], command)

    def assert_each_build_names_its_tag(self, text):
        # A build of jagt or cx from source names the release of the hint and the crate.
        for command in re.findall(r'`(cargo install [^`]*--git [^`]*)`', text):
            argv = shlex.split(command)
            self.assertIn(argv[-1], HINT_TAGS, command)
            self.assertEqual(argv[-3:], ['--tag', HINT_TAGS[argv[-1]], argv[-1]], command)

    def source_of(self, text, name):
        """Where the build of name from source starts in text, or -1."""
        marks = {'jlog': 'git checkout v0.3.0'}
        mark = marks.get(name, f'--git https://github.com/rahuldave/{name} --tag')
        return text.find(mark) if name == 'jlog' else max(text.find(mark), text.find(mark.replace(' ', '\n', 1)))

    def test_the_manifest_hints(self):
        executables = json.loads((REPO / 'skill-package.json').read_text())['executables']
        entries = {entry['name']: entry for kind in ('required', 'optional') for entry in executables[kind]}
        for name in HINT_TAGS:
            with self.subTest(name=name):
                self.assert_binary_then_source(entries[name]['install'], name)
                self.assert_each_command_ends_at_its_tag(entries[name]['install'])
                self.assert_each_build_names_its_tag(entries[name]['install'])
        # The floors stay as they were.
        self.assertEqual(entries['jlog']['version'], '>=0.2.0')
        self.assertEqual(entries['jagt']['version'], '>=0.3.0')
        self.assertEqual(entries['cx']['version'], '>=0.1.0')

    def test_the_installer_hint(self):
        self.assert_binary_then_source(install_stack.JLOG_INSTALL, 'jlog')
        self.assert_each_command_ends_at_its_tag(install_stack.JLOG_INSTALL)

    def test_the_docs(self):
        pages = {'README.md': ['jlog', 'jagt', 'cx'], '.agents/skills/cx/SKILL.md': ['cx'], 'docs/new-project.md': ['jlog'],
                 'docs/stack-guide.md': ['jlog'], '.agents/skills/just/SKILL.md': ['jlog'], '.agents/skills/jagt-protocol/SKILL.md': ['jagt']}
        for page, names in pages.items():
            text = (REPO / page).read_text()
            for name in names:
                with self.subTest(page=page, name=name):
                    line = HINT.format(name=name, tag=HINT_TAGS[name])
                    self.assertIn(line, text)
                    # The release binary first, the build from source second.
                    self.assertLess(text.index(line), self.source_of(text, name))
                    self.assertNotIn('git checkout v0.2.0', text)
            self.assert_each_build_names_its_tag(text)
            # A build of jagt from source names its tag, so it cannot build a jagt under the floor.
            self.assertNotRegex(text, r'cargo install --locked --git https://github\.com/rahuldave/(jagt jagt|cx cx)')

    def test_the_gest_hint(self):
        # gest takes install.sh from its tag, the binary first and the build from source second.
        text = (REPO / 'docs/new-project.md').read_text()
        line = 'curl -fsSL https://raw.githubusercontent.com/rahuldave/gest/v0.6.0/scripts/install.sh | sh -s -- --tag v0.6.0'
        self.assertIn(line, text)
        self.assertLess(text.index(line), text.index('git checkout v0.6.0'))
        self.assertIn('| `gest` 0.6.0 or later |', text)
        self.assertIn('| `tasker` | `gest` 0.6.0, whose `gest --version` prints `(rahuldave)` |', (REPO / 'docs/stack-guide.md').read_text())
        self.assertNotIn('0.5.6', text + (REPO / 'docs/stack-guide.md').read_text() + (REPO / 'docs/stack-files.md').read_text())

    def test_the_cx_skill_still_asks_before_an_install(self):
        self.assertIn('ask the user before you install it', (REPO / '.agents/skills/cx/SKILL.md').read_text())

    @unittest.skipUnless(os.environ.get('NOSSAIFD_CHECK'), 'needs NOSSAIFD_CHECK, the path of nossaifd check.py')
    def test_the_changed_docs_pass_the_writing_checker(self):
        for page in ('docs/stack-guide.md', 'docs/new-project.md', 'README.md', '.agents/skills/cx/SKILL.md'):
            run = subprocess.run(['uv', 'run', '--no-project', '--quiet', 'python', os.environ['NOSSAIFD_CHECK'],
                                  str(REPO / page)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == '__main__':
    unittest.main()
