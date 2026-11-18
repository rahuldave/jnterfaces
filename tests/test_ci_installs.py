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
# The tag v0.6.3 of jagt has no install.sh, so CI and the hints take install.sh from the commit of jagt that adds it.
JAGT_INSTALLER = '7ea80a99492fe5eed5d7fa9b9a4e1ba08599463a'
# The ref of install.sh for each program: the tag of its release, or for jagt the commit that adds install.sh.
REFS = {'jlog': 'v0.3.0', 'jagt': JAGT_INSTALLER, 'cx': 'v0.2.2', 'gest': 'v0.6.0'}
CARGO = re.compile(r'\bcargo\s+(b?install)\b.*\b(jlog|jagt)\b')
INSTALLER = re.compile(r'curl -fsSL https://raw\.githubusercontent\.com/rahuldave/(jlog|jagt)/([^/\s]+)/scripts/install\.sh'
                       r' \| sh -s -- --tag (\S+)$')
# The line that a person runs. It takes install.sh from the pinned ref, never from main, and names the release with --tag.
HINT = 'curl -fsSL https://raw.githubusercontent.com/rahuldave/{name}/{ref}/scripts/install.sh | sh -s -- --tag {tag}'
# The release that each install line names.
RELEASES = dict(HINT_TAGS, gest='v0.6.0')
# Any program of rahuldave on a line that names install.sh, on any host, with any case.
ANY_PROGRAM = re.compile(r'rahuldave/([\w.-]+)/\S*?install\.sh', re.IGNORECASE)


def pin_problems(line):
    """Why a line with an install.sh of rahuldave is not the exact pinned hint, or an empty list."""
    if 'install.sh' not in line or 'rahuldave' not in line.lower():
        return [], set()
    problems, programs = [], set()
    names = ANY_PROGRAM.findall(line)
    if len(names) != line.count('install.sh'):
        problems.append('an install.sh that names no program of rahuldave')
    for name in names:
        name = name.lower()
        programs.add(name)
        if name not in REFS:
            problems.append(f'no pinned ref for {name}')
            continue
        hint = HINT.format(name=name, ref=REFS[name], tag=RELEASES[name])
        if hint not in line:
            problems.append(f'not the pinned hint of {name}')
            continue
        after = line[line.index(hint) + len(hint):]
        # The tag ends at the end of the line, a space or a mark, so v0.3.0-rc1 fails. The command has no second --tag.
        if not re.match(r'(?:$|[\s`"\'.,)])', after) or '--tag' in re.split(r'[`;|&]', after)[0]:
            problems.append(f'more after the tag of {name}')
    return problems, programs


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
                        self.assertEqual(ref, REFS[name])
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
        line = HINT.format(name=name, ref=REFS[name], tag=HINT_TAGS[name])
        self.assertIn(line, text)
        self.assertIn('from source', text)
        self.assertLess(text.index(line), text.index('from source'))

    def assert_each_command_ends_at_its_tag(self, text):
        # A command sits in backticks, so a copy of it never takes the comma or the period of the sentence.
        commands = re.findall(r'`(curl [^`]*)`', text)
        self.assertTrue(commands, text)
        for command in commands:
            argv = shlex.split(command)
            name, ref = argv[2].split('/')[4:6]
            self.assertEqual(ref, REFS[name], command)
            self.assertEqual(argv[-2:], ['--tag', HINT_TAGS[name]], command)

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
                    line = HINT.format(name=name, ref=REFS[name], tag=HINT_TAGS[name])
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

    def test_each_install_sh_in_the_repository_comes_from_its_pinned_ref(self):
        # A hint that takes install.sh from main gets a later install.sh than the release that --tag names.
        files = subprocess.run(['git', 'ls-files', '-z'], cwd=REPO, capture_output=True, text=True, check=True).stdout
        found = set()
        for name in files.split('\0'):
            # The design notes keep the history, and the tests hold sample lines.
            if not name or name.startswith(('notes/', 'tests/')) or not (REPO / name).is_file():
                continue
            try:
                text = (REPO / name).read_text()
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                problems, programs = pin_problems(line)
                self.assertEqual(problems, [], f'{name}:{number}: {line}')
                found |= programs
        self.assertEqual(found, set(REFS))

    def test_the_pin_check_catches_each_unpinned_line(self):
        good = HINT.format(name='jlog', ref='v0.3.0', tag='v0.3.0')
        self.assertEqual(pin_problems(f'Run `{good}`, then put ~/.local/bin on PATH.'), ([], {'jlog'}))
        for bad in (good.replace('/v0.3.0/', '/main/'),
                    good.replace('/v0.3.0/', '/refs/heads/main/'),
                    'curl -fsSL https://github.com/rahuldave/jlog/raw/main/scripts/install.sh | sh -s -- --tag v0.3.0',
                    good.replace('rahuldave', 'RahulDave'),
                    good + '-rc1',
                    good + ' --tag v0.2.0',
                    good.replace('--tag v0.3.0', '--tag v0.2.0'),
                    'curl -fsSL https://raw.githubusercontent.com/rahuldave/jagt/v0.6.3/scripts/install.sh | sh -s -- --tag v0.6.3'):
            with self.subTest(line=bad):
                self.assertNotEqual(pin_problems(bad)[0], [])

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
