"""The install hints get jlog and jagt as release binaries from their pinned install.sh, and CI never builds them with
cargo."""
import json
import os
import re
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import install_stack  # noqa: E402

WORKFLOWS = [REPO / '.github/workflows/verify.yml', REPO / '.agents/skills/docs/assets/docs-publish.yml']
# The release of each program that CI installs.
TAGS = {'jlog': 'v0.5.0', 'jagt': 'v0.6.4'}
# The release that each install hint names. CI does not install cx.
HINT_TAGS = dict(TAGS, cx='v0.2.3')
# The ref of install.sh for each program: the tag of its release.
REFS = {'jlog': 'v0.5.0', 'jagt': 'v0.6.4', 'cx': 'v0.2.3', 'gest': 'v0.6.0'}
CARGO = re.compile(r'\bcargo\s+(b?install)\b.*\b(jlog|jagt)\b')
# The line that a person runs. It takes install.sh from the pinned ref, never from main, and names the release with --tag.
HINT = 'curl -fsSL https://raw.githubusercontent.com/rahuldave/{name}/{ref}/scripts/install.sh | sh -s -- --tag {tag}'
# The release that each install line names.
RELEASES = dict(HINT_TAGS, gest='v0.6.0')
# Any program of rahuldave on a line that names install.sh, on any host, with any case.
ANY_PROGRAM = re.compile(r'rahuldave/([\w.-]+)/\S*?install\.sh', re.IGNORECASE)
# A download, and a word that makes it an install line: a program of REFS or the option --tag of install.sh.
DOWNLOAD = re.compile(r'\b(?:curl|wget)\b', re.IGNORECASE)
INSTALL_WORD = re.compile(r'\b(?:' + '|'.join(REFS) + r')\b|--tag', re.IGNORECASE)
# A run of an install.sh on disk in a clone: sh -x install.sh, ./install.sh, source install.sh, . install.sh or $SHELL.
LOCAL_RUN = re.compile(r'(?:\b(?:ba|da|z)?sh(?:\s+-\S+)*\s+|\bsource\s+|(?:^|[\s;&|])\.\s+|\$\{?SHELL\}?\s+|\./)\S*install\.sh')
# The hint up to the owner, so a match of ANY_PROGRAM shows where its hint must start.
HINT_HEAD = HINT[:HINT.index('rahuldave/')]


def logical_lines(text):
    """The lines of text after the shell joins each line that ends in a backslash to the next line."""
    return re.sub(r'\\\r?\n', '', text).splitlines()


def pin_problems(line):
    """Why a line with an install.sh of rahuldave is not the exact pinned hint, or an empty list."""
    owner = 'install.sh' in line and ('rahuldave' in line.lower() or 'scripts/install.sh' in line or '--tag' in line
                                      or LOCAL_RUN.search(line))
    # A download that names a program or --tag, from any host or through a variable, must be a hint too.
    # So must a run of scripts/install.sh, or of an install.sh with --tag, from a clone or a variable.
    if not owner and not (DOWNLOAD.search(line) and INSTALL_WORD.search(line)):
        return [], set()
    problems, programs, hints = [], set(), 0
    matches = list(ANY_PROGRAM.finditer(line))
    if len(matches) != line.count('install.sh') or not matches:
        problems.append('an install.sh that names no program of rahuldave')
    for match in matches:
        name = match.group(1).lower()
        programs.add(name)
        if name not in REFS:
            problems.append(f'no pinned ref for {name}')
            continue
        hint = HINT.format(name=name, ref=REFS[name], tag=RELEASES[name])
        # Each install.sh must sit in its own hint, so a pinned hint does not excuse a second line on the same line.
        start = match.start() - len(HINT_HEAD)
        if start < 0 or not line.startswith(hint, start):
            problems.append(f'not the pinned hint of {name}')
            continue
        hints += 1
        after = line[start + len(hint):]
        # The tag ends at the end of the line, a space or a mark, so v0.5.0-rc1 fails. The command has no second --tag.
        if not re.match(r'(?:$|[\s`"\')]|[.,](?!\w))', after) or '--tag' in re.split(r'[`;|&]', after)[0]:
            problems.append(f'more after the tag of {name}')
    # Each curl or wget on the line is the start of a hint.
    if len(DOWNLOAD.findall(line)) > hints and not problems:
        problems.append('a download that is not a pinned hint')
    return problems, programs


class WorkflowInstallTests(unittest.TestCase):
    def test_no_workflow_builds_jlog_or_jagt_with_cargo(self):
        for path in WORKFLOWS:
            for number, line in enumerate(path.read_text().splitlines(), 1):
                self.assertNotRegex(line, CARGO, f'{path.name}:{number}')

    def test_no_workflow_runs_an_install_sh_hint(self):
        # The workflows install jlog and jagt in the step "Install the pinned tools", which checks each download
        # against a SHA-256 in the file. tests/test_workflow_pins.py checks that step.
        for path in WORKFLOWS:
            for number, line in enumerate(logical_lines(path.read_text()), 1):
                self.assertNotRegex(line, r'install\.sh\s*\|\s*sh\b', f'{path.name}:{number}')

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
        marks = {'jlog': 'git checkout v0.5.0'}
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
        pages = {'README.md': ['jlog', 'jagt', 'cx'], '.agents/skills/cx/SKILL.md': ['cx'], 'docs/new-project.md': ['jlog', 'jagt'],
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
            # The design notes keep the history, and the tests hold sample lines. The workflows install from
            # pinned downloads, which tests/test_workflow_pins.py checks.
            if (not name or name.startswith(('notes/', 'tests/')) or not (REPO / name).is_file()
                    or REPO / name in WORKFLOWS):
                continue
            try:
                text = (REPO / name).read_text()
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(logical_lines(text), 1):
                problems, programs = pin_problems(line)
                self.assertEqual(problems, [], f'{name}:{number}: {line}')
                found |= programs
        self.assertEqual(found, set(REFS))

    def test_the_pin_check_catches_each_unpinned_line(self):
        good = HINT.format(name='jlog', ref='v0.5.0', tag='v0.5.0')
        self.assertEqual(pin_problems(f'Run `{good}`, then put ~/.local/bin on PATH.'), ([], {'jlog'}))
        for bad in (good.replace('/v0.5.0/', '/main/'),
                    good.replace('/v0.5.0/', '/refs/heads/main/'),
                    'curl -fsSL https://github.com/rahuldave/jlog/raw/main/scripts/install.sh | sh -s -- --tag v0.5.0',
                    good.replace('rahuldave', 'RahulDave'),
                    good + '-rc1',
                    good + ' --tag v0.2.0',
                    good.replace('--tag v0.5.0', '--tag v0.2.0'),
                    'curl -fsSL https://raw.githubusercontent.com/rahuldave/jagt/v0.6.3/scripts/install.sh | sh -s -- --tag v0.6.3',
                    # A pinned hint on the line does not excuse a second install line beside it.
                    good + ' && ' + good.replace('/v0.5.0/', '/main/'),
                    good.replace('/v0.5.0/', '/main/') + '; ' + good,
                    good + ' && curl -fsSL https://git.io/x | sh',
                    # A host, an owner or a name of install.sh that the check of rahuldave and install.sh does not see.
                    'curl -fsSL https://git.io/jlog | sh -s -- --tag v0.5.0',
                    'curl -fsSL https://raw.githubusercontent.com/$OWNER/jlog/main/scripts/install.sh | sh -s -- --tag v0.5.0',
                    good.replace('/v0.5.0/', '/main/').replace('install.sh', 'install%2esh'),
                    'wget -qO- https://tinyurl.com/abc | sh -s -- --tag v0.5.0',
                    # A line that ends in a backslash continues on the next line.
                    'curl -fsSL https://raw.githubusercontent.com/rahuldave/\\\njlog/main/scripts/install.sh | sh -s -- --tag v0.5.0',
                    good + ' \\\n  --tag v0.2.0',
                    good + '.1',
                    # A variable, a clone or a saved copy holds the owner or the ref on another line.
                    'curl -fsSL https://raw.githubusercontent.com/$REPO/main/scripts/install.sh | sh',
                    'curl -fsSL https://raw.githubusercontent.com/${{ env.REPO }}/main/scripts/install.sh | sh',
                    'sh jlog/scripts/install.sh --tag v0.5.0',
                    'sh install.sh --tag v0.2.0',
                    'cd jlog/scripts && sh install.sh',
                    'cd {{jagt_dir}}/scripts && ./install.sh',
                    'cd jlog/scripts && sh -x install.sh',
                    'cd jlog/scripts && source install.sh',
                    'cd jlog/scripts && . install.sh',
                    'cd jlog/scripts && $SHELL install.sh'):
            with self.subTest(line=bad):
                self.assertTrue(any(pin_problems(line)[0] for line in logical_lines(bad)))
        # A continued hint is still the hint, and a download of another tool is not an install line of rahuldave.
        self.assertEqual(pin_problems(logical_lines(good.replace(' | ', ' \\\n| '))[0]), ([], {'jlog'}))
        self.assertEqual(pin_problems('curl -LsSf https://astral.sh/uv/install.sh | sh'), ([], set()))
        self.assertEqual(pin_problems(f'Run `{good}`.'), ([], {'jlog'}))

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
