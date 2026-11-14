"""Tests for scripts/install_stack.py and .agents/install.just: roles, tools and profiles in one target."""
import hashlib
import io
import json
import os
import re
import shlex
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import install_stack  # noqa: E402
from lint_steps import STEPS  # noqa: E402

RECORDS_PROFILE = {'python': '.agents/jnprofile-python-install.json',
                   'typescript': '.agents/jnprofile-typescript-install.json',
                   'rust': '.agents/jnprofile-rust-install.json'}
RECORDS = {'jnterfaces': '.agents/jnterfaces-install.json', 'agent_gest_git_skills': '.agents/gest-git-install.json',
           'nossaifd': '.agents/nossaifd-install.json', 'sklanes': '.agents/sklanes-install.json'}
SKILLS = {'agent_gest_git_skills': 'gtw', 'nossaifd': 'nossaifd', 'sklanes': 'lanes'}
# The profiles of the tests, as the profile packages jnprofile-python and jnprofile-typescript ship them. The tests
# make each package from this data, so that they run offline and do not change when the real packages change.
PROFILES = json.loads('''{
    "python": {
        "about": "a Python project with uv",
        "detect": [
            "pyproject.toml",
            "uv.lock"
        ],
        "manifest": "pyproject.toml",
        "init": {
            "file": "pyproject.toml",
            "command": [
                "uv",
                "init",
                "--bare",
                "--no-workspace"
            ]
        },
        "uv_default_groups": "all",
        "choices": {
            "tasker": true,
            "prose": true
        },
        "add": [
            "uv",
            "add",
            "--group",
            "{group}"
        ],
        "remove": [
            "uv",
            "remove",
            "--group",
            "{group}"
        ],
        "tools": {
            "lint": {
                "group": "lint",
                "packages": [
                    "ruff",
                    "pre-commit"
                ],
                "files": {
                    "ruff.toml": "profiles/python/ruff.toml",
                    ".pre-commit-config.yaml": "profiles/python/pre-commit-config.yaml"
                },
                "lines": {
                    ".gitignore": [".ruff_cache/"],
                    ".worktreedisposable": [".ruff_cache/"]
                },
                "skip_if": {
                    "ruff.toml": [
                        "pyproject.toml",
                        "[tool.ruff"
                    ]
                },
                "recipes": "lint.just",
                "verify": [
                    "fmt --check",
                    "lint"
                ],
                "before_remove": {
                    "package": "pre-commit",
                    "git_hook": "pre-commit",
                    "marker": "INSTALL_PYTHON",
                    "command": [
                        "uv",
                        "run",
                        "--group",
                        "lint",
                        "pre-commit",
                        "uninstall"
                    ]
                }
            },
            "test": {
                "group": "test",
                "packages": [
                    "pytest",
                    "pytest-cov"
                ],
                "recipes": "test.just",
                "verify": [
                    "test"
                ]
            },
            "typecheck": {
                "group": "lint",
                "packages": [
                    "ty"
                ],
                "recipes": "typecheck.just",
                "verify": [
                    "typecheck"
                ]
            }
        },
        "default_tools": [
            "lint",
            "test"
        ],
        "name": "python",
        "base": {
            "recipes": "base.just",
            "lines": {
                ".gitignore": [".venv/", "dist/", ".local/"],
                ".worktreedisposable": [".venv/", "__pycache__/"]
            }
        }
    },
    "typescript": {
        "about": "a TypeScript project with npm, which uv runs from the nodejs-wheel package",
        "detect": [
            "package.json",
            "tsconfig.json"
        ],
        "manifest": "package.json",
        "init": {
            "file": "package.json",
            "command": [
                "uvx",
                "--from",
                "nodejs-wheel==24.*",
                "npm",
                "init",
                "--yes"
            ]
        },
        "choices": {
            "tasker": true,
            "prose": true
        },
        "add": [
            "uvx",
            "--from",
            "nodejs-wheel==24.*",
            "npm",
            "install",
            "--save-dev"
        ],
        "remove": [
            "uvx",
            "--from",
            "nodejs-wheel==24.*",
            "npm",
            "uninstall"
        ],
        "tools": {
            "lint": {
                "packages": [
                    "@biomejs/biome@2"
                ],
                "files": {
                    "biome.json": "profiles/typescript/biome.json"
                },
                "recipes": "lint.just",
                "verify": [
                    "lint"
                ]
            },
            "typecheck": {
                "packages": [
                    "typescript@7",
                    "@types/node@24"
                ],
                "files": {
                    "tsconfig.json": "profiles/typescript/tsconfig.json"
                },
                "recipes": "typecheck.just",
                "verify": [
                    "typecheck"
                ]
            },
            "test": {
                "packages": [
                    "typescript@7",
                    "@types/node@24"
                ],
                "files": {
                    "tsconfig.json": "profiles/typescript/tsconfig.json"
                },
                "recipes": "test.just",
                "verify": [
                    "test"
                ]
            }
        },
        "default_tools": [
            "lint",
            "typecheck"
        ],
        "name": "typescript",
        "base": {
            "recipes": "base.just",
            "lines": {
                ".gitignore": ["node_modules/", "dist/", ".local/"],
                ".worktreedisposable": ["node_modules/"]
            }
        }
    }
}''')
# The rust profile, as jnprofile-rust ships it: each tool uses the programs of the machine, so it has no packages.
PROFILES['rust'] = {
    'name': 'rust', 'about': 'a Rust project with Cargo', 'detect': ['Cargo.toml'], 'manifest': 'Cargo.toml',
    'init': {'file': 'Cargo.toml', 'command': ['cargo', 'init', '--vcs', 'none']},
    'choices': {'tasker': True, 'prose': True},
    'add': ['cargo', 'add', '--dev'], 'remove': ['cargo', 'remove', '--dev'], 'code': ['**/*.rs'],
    'tools': {'lint': {'packages': [], 'recipes': 'lint.just', 'verify': ['fmt-check', 'lint']},
              'test': {'packages': [], 'recipes': 'test.just', 'verify': ['test', 'coverage-check']}},
    'default_tools': ['lint', 'test'],
    'base': {'recipes': 'base.just', 'lines': {'.gitignore': ['/target/'], '.worktreedisposable': ['/target/']}}}
# The recipes of each recipe file of a test profile
RECIPES = {"rust": {"lint": ["fmt-check", "lint"], "test": ["test", "coverage-check"]}, "python": {"lint": ["fmt", "lint", "hook"], "test": ["test", "cov"], "typecheck": ["typecheck"]}, "typescript": {"lint": ["fmt", "lint"], "typecheck": ["typecheck"], "test": ["build", "test"]}}


# A uv and a uvx that log each call, so that the tests of the tools run offline. Like the real ones, they write the
# project file: uv init makes pyproject.toml, uv add and uv remove edit a dependency group, and uv remove refuses a
# package that the group does not list. npm init makes package.json, and npm install and npm uninstall edit it.
FAKE_TOOL = r'''
import json, os, re, signal, sys, time, tomllib
from pathlib import Path

tool, args = Path(sys.argv[0]).name, sys.argv[1:]
with open(os.environ['TOOL_LOG'], 'a') as log:
    log.write(tool + ' ' + ' '.join(args) + '\n')
if 'VIRTUAL_ENV' in os.environ:
    with open(os.environ['TOOL_LOG'] + '.env', 'a') as log:
        log.write(tool + ' ' + ' '.join(args) + '\n')


def name_of(spec):
    if spec.startswith('@'):
        return '@' + spec[1:].split('@')[0]
    return re.split(r'[@<>=!~ ;\[]', spec)[0]


def py_name(spec):
    return re.sub(r'[-_.]+', '-', name_of(spec)).lower()


def value(item):
    if isinstance(item, bool):
        return 'true' if item else 'false'
    if isinstance(item, (int, float)):
        return str(item)
    if isinstance(item, str):
        return json.dumps(item)
    return '[' + ', '.join(value(part) for part in item) + ']'


def dump(data, prefix='', lines=None):
    lines = [] if lines is None else lines
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    tables = {k: v for k, v in data.items() if isinstance(v, dict)}
    if prefix and (scalars or not tables):
        lines.append(f'[{prefix}]')
    lines += [f'{k} = {value(v)}' for k, v in scalars.items()]
    for k, v in tables.items():
        dump(v, f'{prefix}.{k}' if prefix else k, lines)
    return '\n'.join(lines) + '\n'


def interrupt_after(word):
    if os.environ.get('INT_TOOL') == word:
        os.kill(os.getppid(), signal.SIGINT)
        time.sleep(2)
        sys.exit(130)


if tool == 'uv':
    if os.environ.get('FAIL_TOOL') and args[2:3] == [os.environ['FAIL_TOOL']]:
        sys.exit(1)
    if args[0] == 'init':
        Path('pyproject.toml').write_text('[project]\nname = "app"\n')
    elif args[0] in ('add', 'remove'):
        if not Path('pyproject.toml').is_file():
            sys.exit('No pyproject.toml found')
        data = tomllib.loads(Path('pyproject.toml').read_text())
        group, specs = args[2], args[3:]
        if os.environ.get('INT_BEFORE') == group:
            os.kill(os.getppid(), signal.SIGINT)
            time.sleep(2)
            sys.exit(130)
        entries = data.setdefault('dependency-groups', {}).setdefault(group, [])
        if args[0] == 'add':
            entries += [spec for spec in specs if py_name(spec) not in {py_name(e) for e in entries}]
        else:
            missing = [spec for spec in specs if py_name(spec) not in {py_name(e) for e in entries}]
            if missing:
                sys.exit(f'error: The dependency `{missing[0]}` could not be found in `dependency-groups.{group}`')
            data['dependency-groups'][group] = [e for e in entries if py_name(e) not in {py_name(s) for s in specs}]
        Path('pyproject.toml').write_text(dump(data))
        interrupt_after(group)
    elif args[0] == 'run' and args[-2:] == ['pre-commit', 'uninstall']:
        if os.environ.get('FAIL_HOOK'):
            sys.exit(1)
        Path('.git/hooks/pre-commit').unlink(missing_ok=True)
elif tool == 'cargo':
    # cargo init makes Cargo.toml; cargo add --dev and cargo remove --dev edit [dev-dependencies].
    if args[0] == 'init':
        Path('Cargo.toml').write_text('[package]\nname = "app"\n')
    elif args[0] in ('add', 'remove'):
        data = tomllib.loads(Path('Cargo.toml').read_text())
        table = data.setdefault('dev-dependencies', {})
        for spec in [s for s in args[1:] if not s.startswith('-')]:
            if args[0] == 'add':
                table[spec] = '*'
            else:
                table.pop(spec, None)
        Path('Cargo.toml').write_text(dump(data))
elif tool == 'uvx':
    command, rest = args[3], args[4:]
    if command == 'init':
        Path('package.json').write_text('{}')
    elif command in ('install', 'uninstall'):
        if not Path('package.json').is_file():
            sys.exit('no package.json')
        data = json.loads(Path('package.json').read_text())
        if command == 'install':
            for spec in [s for s in rest if not s.startswith('-')]:
                data.setdefault('devDependencies', {})[name_of(spec)] = '*'
        else:
            for kind in ('dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies'):
                for spec in rest:
                    data.get(kind, {}).pop(name_of(spec), None)
            if isinstance(data.get('bundleDependencies'), list):
                data['bundleDependencies'] = [n for n in data['bundleDependencies'] if n not in map(name_of, rest)]
        Path('package.json').write_text(json.dumps(data))
'''


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), '-c', 'user.name=T', '-c', 'user.email=t@example.invalid', *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def commit(path):
    git(path, 'add', '-A')
    git(path, 'commit', '-q', '-m', 'change')


# The text of a source file of a part, by its suffix.
SOURCES = {'.toml': 'line-length = 120\n', '.json': '{}\n',
           '.yaml': 'repos:\n  - repo: https://r\n    rev: v1\n    hooks: []\n'}
# The lines of the installer at the top of .gitignore: the history, the file of a run that stopped, and the run log.
JN = ['.agents/stack-log.jsonl', '.agents/stack-pending.json', '.agents/log/']


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
        for name in PROFILES:
            self.profile_package(name)
        self.target = self.base / 'target'
        self.target.mkdir()
        git(self.target, 'init', '-q')
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        for tool in ('uv', 'uvx', 'cargo'):
            (self.bin / tool).write_text(f'#!{sys.executable}\n' + FAKE_TOOL)
            (self.bin / tool).chmod(0o755)
        self.log = self.base / 'tools.log'

    def package(self, name, skill, extras=None):
        root = self.packages / name
        (root / '.agents/skills' / skill).mkdir(parents=True)
        (root / '.agents/skills' / skill / 'SKILL.md').write_text(f'---\nname: {skill}\ndescription: d\n---\n')
        manifest = {'repository': {'name': name}, 'skills': [{'name': skill, 'path': f'.agents/skills/{skill}'}]}
        if name == 'nossaifd':
            (root / '.agents/just/roles').mkdir(parents=True)
            (root / '.agents/just/roles/nossaifd.just').write_text(
                '[positional-arguments]\nprose *args:\n    @echo prose "$@"\n')
            manifest['install_extras'] = {'files': ['.agents/just/roles/nossaifd.just']}
        if name == 'agent_gest_git_skills':
            (root / '.claude/hooks').mkdir(parents=True)
            (root / '.claude/hooks/start.sh').write_text('echo start\n')
            (root / '.claude/settings.json').write_text(json.dumps(
                {'hooks': {'SessionStart': [{'hooks': [{'type': 'command', 'command': 'start.sh'}]}]}}))
            manifest['install_extras'] = {'files': ['.claude/hooks'], 'merge_hooks': ['.claude/settings.json']}
        (root / 'skill-package.json').write_text(json.dumps(manifest))
        git(root, 'init', '-q')
        commit(root)

    def profile_package(self, name, profile=None, recipes=None):
        """Make the profile package jnprofile-NAME: profile.json, a recipe file for each tool, and the config files."""
        profile = json.loads(json.dumps(PROFILES[name] if profile is None else profile))
        recipes = RECIPES[name] if recipes is None else recipes
        root = self.packages / f'jnprofile-{name}'
        if root.exists():
            shutil.rmtree(root)
        folder = root / '.agents/just' / name
        folder.mkdir(parents=True)
        (folder / 'base.just').write_text(f'# The base of {name}\nsetup:\n    @echo setup {name}\n')
        for tool, names in recipes.items():
            (folder / f'{tool}.just').write_text(''.join(f'{recipe} *args:\n    @echo {name} {recipe} "$@"\n'
                                                         for recipe in names))
        skill = root / f'.agents/skills/{name}-profile'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text(f'---\nname: {name}-profile\ndescription: d\n---\n')
        for entry in [profile.get('base', {})] + list(profile['tools'].values()):
            for origin in entry.get('files', {}).values():
                (root / origin).parent.mkdir(parents=True, exist_ok=True)
                (root / origin).write_text(f'# {origin}\n')
            for origin in entry.get('parts', {}).values():
                (root / origin).parent.mkdir(parents=True, exist_ok=True)
                if not (root / origin).exists():
                    (root / origin).write_text(SOURCES.get(Path(origin).suffix, '# rules\n'))
        (root / 'profile.json').write_text(json.dumps(profile))
        (root / 'skill-package.json').write_text(json.dumps({
            'repository': {'name': f'jnprofile-{name}'},
            'skills': [{'name': f'{name}-profile', 'path': f'.agents/skills/{name}-profile'}],
            'install_extras': {'files': [f'.agents/just/{name}']}}))
        git(root, 'init', '-q')
        commit(root)

    def edit_profile(self, name, change):
        """Change the profile.json of a test profile package, and commit it."""
        profile = json.loads(json.dumps(PROFILES[name]))
        change(profile)
        self.profile_package(name, profile)

    def stack(self, *args, env=None, target=None):
        return subprocess.run([sys.executable, self.packages / 'jnterfaces/scripts/install_stack.py',
                               '--target', target or self.target, *args],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              env=dict({k: v for k, v in os.environ.items() if k != 'SKILL_PACKAGES_DIR'},
                                       PATH=f'{self.bin}:{os.environ["PATH"]}', TOOL_LOG=str(self.log), **(env or {})))

    def saved(self):
        return json.loads((self.target / '.agents/stack.json').read_text())

    def tool_names(self, field):
        """The tool names of a saved list of profile:tool pairs, without the profiles, each once."""
        return list(dict.fromkeys(pair.partition(':')[2] for pair in self.saved().get(field, [])))

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
        saved = self.saved()
        # The prose role ships a recipe file, so the index exists, and the record holds its hash.
        self.assertEqual(list(saved.pop('generated')), ['.agents/just/index.just'])
        # The installer writes its lines of the run log, and the steps of just check, with none provided. It started
        # the project, so it also wrote its two lists.
        self.assertEqual(sorted(saved.pop('parts')), ['.agents/coverage-exclude.txt', '.agents/generated.txt',
                                                      '.gitignore', '.worktreedisposable'])
        self.assertEqual((saved.pop('steps'), saved.pop('provided_steps')), (list(STEPS), []))
        self.assertEqual(saved, {'version': 2, 'profiles': [], 'tools': [], 'started': True, 'parts_version': 1,
                                        'variants': {'tasker': 'git'}, 'choices': {
            'cx': False, 'jagt-protocol': False, 'prose': True, 'reporter': False, 'tasker': True}})
        # With no profile, the project still gets the starter Justfile, which imports the recipe file.
        self.assertIn('Wrote Justfile', run.stdout)
        self.assertIn("import? '.agents/install.just'", (self.target / 'Justfile').read_text())
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
        # The hooks of tasker are gone, and .claude keeps only the links of the skills that stay.
        self.assertEqual(os.listdir(self.target / '.claude'), ['skills'])
        self.assertEqual(sorted(os.listdir(self.target / '.claude/skills')), ['jagt-protocol', 'just', 'nossaifd'])
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

    def test_the_commands_of_the_project_do_not_get_the_virtual_env_of_the_installer(self):
        # uv run of the installer sets VIRTUAL_ENV, and uv in the project warns about it.
        run = self.stack('--defaults', '--profile', 'python', env={'VIRTUAL_ENV': '/somewhere/else'})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('uv init --bare --no-workspace', self.tools_run())
        self.assertIn('uv add --group lint ruff pre-commit', self.tools_run())
        self.assertFalse(Path(str(self.log) + '.env').exists())

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
        self.assertEqual(self.tool_names('tools'), ['lint', 'test'])
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
        self.assertEqual(self.tool_names('tools'), ['test'])
        self.log.unlink()
        self.assertEqual(self.stack('lint').returncode, 0)
        self.assertEqual(self.tools_run(), ['uv add --group lint ruff pre-commit'])

    def test_removing_lint_takes_out_the_git_hook_of_pre_commit_first(self):
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        hook = self.target / '.git/hooks/pre-commit'
        hook.write_text(f'#!/bin/sh\nINSTALL_PYTHON={self.target}/.venv/bin/python\n')
        self.log.unlink()
        run = self.stack('--without', 'lint')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.tools_run(), ['uv run --group lint pre-commit uninstall', 'uv remove --group lint ruff pre-commit'])
        self.assertFalse(hook.exists())

    def test_no_git_hook_runs_no_uninstall_and_a_failed_uninstall_keeps_the_tool(self):
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        self.log.unlink()
        self.assertEqual(self.stack('--without', 'lint', '--dry-run').returncode, 0)
        self.assertEqual(self.tools_run(), [])
        (self.target / '.git/hooks/pre-commit').write_text(f'INSTALL_PYTHON={self.target}/.venv/bin/python\n')
        run = self.stack('--without', 'lint', '--dry-run')
        self.assertIn('Would run: uv run --group lint pre-commit uninstall', run.stdout)
        run = self.stack('--without', 'lint', env={'FAIL_HOOK': '1'})
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('uv run --group lint pre-commit uninstall exited with 1', run.stderr)
        self.assertNotIn('uv remove --group lint ruff pre-commit', self.tools_run())
        self.assertIn('python:lint', self.saved()['tools'])
        self.assertEqual(self.saved()['removing_tools'], ['python:lint'])
        # The next run finishes the removal, hook first.
        self.log.unlink()
        self.assertEqual(self.stack().returncode, 0)
        self.assertEqual(self.tools_run(), ['uv run --group lint pre-commit uninstall', 'uv remove --group lint ruff pre-commit'])

    def test_a_hook_of_a_pre_commit_outside_the_project_stays(self):
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        hook = self.target / '.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\nINSTALL_PYTHON=/usr/local/bin/python3\n')
        self.log.unlink()
        self.assertEqual(self.stack('--without', 'lint').returncode, 0)
        self.assertEqual(self.tools_run(), ['uv remove --group lint ruff pre-commit'])
        self.assertTrue(hook.exists())

    def test_the_hook_check_reads_the_quoting_of_pre_commit_and_an_unreadable_hook(self):
        target = self.base / "it's proj"
        target.mkdir()
        hook = self.base / 'hook'
        python = shlex.quote(str(target / '.venv/bin/python'))
        hook.write_text(f'#!/usr/bin/env bash\nINSTALL_PYTHON={python}\n')
        self.assertIn("'\"'\"'", python)
        self.assertTrue(install_stack.hook_of_project(hook, 'INSTALL_PYTHON', target))
        self.assertFalse(install_stack.hook_of_project(hook, 'INSTALL_PYTHON', self.target))
        hook.write_text('INSTALL_PYTHON=\'unclosed\n')
        self.assertFalse(install_stack.hook_of_project(hook, 'INSTALL_PYTHON', target))
        if os.geteuid():
            hook.write_text(f'INSTALL_PYTHON={python}\n')
            hook.chmod(0)
            self.addCleanup(hook.chmod, 0o644)
            with mock.patch('sys.stderr', io.StringIO()) as err:
                self.assertFalse(install_stack.hook_of_project(hook, 'INSTALL_PYTHON', target))
            self.assertIn('so it stays', err.getvalue())

    def test_a_pre_commit_that_the_project_had_keeps_its_hook(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "app"\n\n[dependency-groups]\nlint = ["pre-commit"]\n')
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        (self.target / '.git/hooks/pre-commit').write_text('#!/bin/sh\n')
        self.log.unlink()
        self.assertEqual(self.stack('--without', 'lint').returncode, 0)
        self.assertEqual(self.tools_run(), ['uv remove --group lint ruff'])
        self.assertTrue((self.target / '.git/hooks/pre-commit').exists())

    def test_skill_packages_dir_names_the_folder_of_the_clones(self):
        other = self.base / 'other'
        shutil.copytree(self.packages / 'nossaifd', other / 'nossaifd')
        (other / 'nossaifd/.agents/skills/nossaifd/SKILL.md').write_text('---\nname: nossaifd\ndescription: other\n---\n')
        commit(other / 'nossaifd')
        run = self.stack('prose', env={'SKILL_PACKAGES_DIR': str(other)})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn(f'The package clones are in {other.resolve()}', run.stderr)
        self.assertIn('description: other', (self.target / '.agents/skills/nossaifd/SKILL.md').read_text())
        # jnterfaces itself is always the clone that runs.
        record = json.loads((self.target / RECORDS['jnterfaces']).read_text())
        self.assertEqual(record['source_revision'], git(self.packages / 'jnterfaces', 'rev-parse', 'HEAD'))

    def test_the_skills_get_claude_links(self):
        self.assertEqual(self.stack('--defaults').returncode, 0)
        for skill in ('just', 'gtw', 'nossaifd'):
            link = self.target / '.claude/skills' / skill
            self.assertEqual(os.readlink(link), f'../../.agents/skills/{skill}')
            self.assertTrue((link / 'SKILL.md').is_file())
        self.assertEqual(self.stack('--without', 'prose').returncode, 0)
        self.assertFalse((self.target / '.claude/skills/nossaifd').is_symlink())
        self.assertTrue((self.target / '.claude/skills/just').is_symlink())

    def just_in_target(self, *args):
        return subprocess.run(['just', *args], cwd=self.target, capture_output=True, text=True, stdin=subprocess.DEVNULL)

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_the_recipes_follow_the_tools(self):
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        module = (self.target / '.agents/just/python.just').read_text()
        self.assertIn("import? 'python/lint.just'", module)
        self.assertIn("import? 'python/test.just'", module)
        self.assertNotIn('typecheck.just', module)
        self.assertIn('verify: (fmt "--check") lint test', module)
        run = self.just_in_target('lint', '--fix')
        # jlog step prints its record after the output of the step.
        self.assertEqual((run.returncode, run.stdout.splitlines()[0]), (0, 'python lint --fix'), run.stderr)
        self.assertEqual(self.just_in_target('python', 'test').stdout.strip(), 'python test')
        run = self.just_in_target('verify')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.split(), ['python', 'fmt', '--check', 'python', 'lint', 'python', 'test'])
        # A tool that goes takes its recipes with it, and one that comes adds its check to verify.
        self.assertEqual(self.stack('--without', 'lint').returncode, 0)
        self.assertNotIn('python::lint', self.just_in_target('--summary').stdout.split())
        self.assertIn('\nlint:\n    jlog skip lint', (self.target / '.agents/just/index.just').read_text())
        self.assertEqual(self.stack('typecheck').returncode, 0)
        self.assertIn('verify: test typecheck', (self.target / '.agents/just/python.just').read_text())
        self.assertIn('typecheck', self.just_in_target('--summary').stdout.split())

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_a_project_started_by_jnterfaces_gets_combined_recipes_and_can_override_them(self):
        self.assertEqual(self.stack('--profile', 'python,typescript', '--defaults').returncode, 0)
        index = (self.target / '.agents/just/index.just').read_text()
        self.assertIn("mod python 'python.just'", index)
        self.assertIn("mod typescript 'typescript.just'", index)
        run = self.just_in_target('fmt', 'x')
        self.assertEqual(run.stdout.split(), ['python', 'fmt', 'x', 'typescript', 'fmt', 'x'], run.stderr)
        # A recipe of the project with the name of a recipe of the index takes its place, also in verify, and a recipe
        # of the project can depend on a recipe of the index.
        justfile = self.target / 'Justfile'
        justfile.write_text(justfile.read_text() + '\nlint:\n    @echo mine\n\nci: verify\n')
        self.assertEqual(self.stack('typecheck').returncode, 0)
        self.assertEqual(self.just_in_target('lint').stdout.strip(), 'mine')
        self.assertEqual(self.just_in_target('typescript', 'lint').stdout.strip(), 'typescript lint')
        run = self.just_in_target('ci')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('typescript typecheck', run.stdout)

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_a_started_project_gets_the_recipes_of_its_roles(self):
        self.assertEqual(self.stack('prose').returncode, 0)
        self.assertIn("import? 'roles/nossaifd.just'", (self.target / '.agents/just/index.just').read_text())
        run = self.just_in_target('prose', 'a.md')
        self.assertEqual((run.returncode, run.stdout.strip()), (0, 'prose a.md'), run.stderr)
        # The recipe goes with its role.
        self.assertEqual(self.stack('--without', 'prose').returncode, 0)
        self.assertNotIn('nossaifd', (self.target / '.agents/just/index.just').read_text())
        self.assertNotEqual(self.just_in_target('prose').returncode, 0)

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_a_project_not_started_by_jnterfaces_gets_the_import_as_a_block_and_the_role_recipes(self):
        own = 'build:\n    @echo build\n'
        justfile = self.target / 'Justfile'
        justfile.write_text(own)
        run = self.stack('prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("Changed Justfile: adds export JLOG_RUN := env(\"JLOG_RUN\", uuid()), import? '.agents/install.just'",
                      run.stderr)
        self.assertNotIn('put this line in it', run.stderr)
        text = justfile.read_text()
        self.assertTrue(text.startswith('# begin lines of jnterfaces, the installer'), text)
        self.assertIn(f"\nimport? '.agents/install.just'\n{install_stack.JLOG_LINE}\n# end lines of jnterfaces.", text)
        self.assertTrue(text.endswith('\n\n' + own), text)
        self.assertEqual(self.saved()['parts']['Justfile']["import? '.agents/install.just'"]['state'], 'added')
        self.assertIn("import? 'roles/nossaifd.just'", (self.target / '.agents/just/index.just').read_text())
        summary = self.just_in_target('--summary')
        self.assertEqual(summary.returncode, 0, summary.stderr)
        self.assertLessEqual({'build', 'install', 'prose', 'uninstall', 'check', 'lint'}, set(summary.stdout.split()))
        run = self.just_in_target('prose', 'a.md')
        self.assertEqual((run.returncode, run.stdout.strip()), (0, 'prose a.md'), run.stderr)
        # A second run changes nothing, and --check finds the block. The project commits the files first.
        self.assertEqual(self.stack().returncode, 0)
        self.assertEqual(justfile.read_text(), text)
        commit(self.target)
        run = self.stack('--check')
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn('the files agree\n', run.stdout)
        # The edits of the user stay, and the recipes of a role go with the role.
        justfile.write_text(text + '\nmore:\n    @echo more\n')
        self.assertEqual(self.stack('--without', 'prose').returncode, 0)
        self.assertEqual(justfile.read_text(), text + '\nmore:\n    @echo more\n')
        summary = set(self.just_in_target('--summary').stdout.split())
        self.assertLessEqual({'build', 'install', 'more', 'uninstall', 'check'}, summary)
        self.assertNotIn('prose', summary)

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_an_import_that_the_justfile_has_is_not_added_again(self):
        justfile = self.target / 'Justfile'
        for own in ("import? '.agents/install.just'\nimport? '.agents/just/roles/nossaifd.just'\n",
                    'import ".agents/install.just"  # mine\n', "import './.agents/install.just'\n"):
            justfile.write_text(own)
            run = self.stack('prose')
            self.assertEqual(run.returncode, 0, run.stderr)
            # The block has the line of the run ID only.
            self.assertTrue(justfile.read_text().endswith('\n\n' + own), justfile.read_text())
            self.assertEqual(justfile.read_text().count('install.just'), 1)
            self.assertEqual(list(self.saved()['parts']['Justfile']), [install_stack.JLOG_LINE])
            justfile.write_text(own)
            (self.target / '.agents/stack.json').unlink()
            self.assertIn('prose', self.just_in_target('--summary').stdout.split(), run.stderr)
            self.assertNotIn('does not parse', run.stderr)
        # A copy of the line after the block makes the block go, and the copy stays.
        justfile.write_text('x:\n    @echo x\n')
        self.assertEqual(self.stack('prose').returncode, 0)
        text = justfile.read_text()
        self.assertIn('# begin lines of jnterfaces', text)
        justfile.write_text(text + "import '.agents/install.just'\n")
        self.assertEqual(self.stack().returncode, 0)
        self.assertTrue(justfile.read_text().endswith("\n\nx:\n    @echo x\nimport '.agents/install.just'\n"))
        self.assertEqual(justfile.read_text().count('install.just'), 1)
        self.assertIn('prose', self.just_in_target('--summary').stdout.split())

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_a_block_that_you_delete_stays_deleted_and_a_moved_line_stays(self):
        justfile = self.target / 'Justfile'
        justfile.write_text('x:\n    @echo x\n')
        self.assertEqual(self.stack('prose').returncode, 0)
        justfile.write_text('x:\n    @echo x\n')
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(justfile.read_text(), 'x:\n    @echo x\n')
        self.assertIn("put this line in it: import? '.agents/install.just'", run.stderr)
        # A line that you move out of the block stays where you put it.
        justfile.write_text("x:\n    @echo x\nimport? '.agents/install.just'\n")
        for _ in range(2):
            self.assertEqual(self.stack().returncode, 0)
            self.assertEqual(justfile.read_text(), "x:\n    @echo x\nimport? '.agents/install.just'\n")

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_a_clash_with_the_import_takes_the_block_out_and_names_the_lines(self):
        own = 'install:\n    @echo mine\n'
        justfile = self.target / 'Justfile'
        justfile.write_text(own)
        run = self.stack('prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(justfile.read_text(), own)
        self.assertIn('does not parse with the block of the installer', run.stderr)
        self.assertRegex(run.stderr, r'(?i)just said: error: recipe `install` first defined')
        self.assertIn("put this line in it: import? '.agents/install.just'", run.stderr)
        self.assertNotIn('also with an index with no module', run.stderr)
        self.assertNotIn('Justfile', self.saved()['parts'])
        self.assertEqual(self.just_in_target('install').stdout.strip(), 'mine')
        # The next run does not try again while the Justfile stays as it is, and --check names the clash.
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('Changed Justfile', run.stderr)
        self.assertIn('put this line in it', run.stderr)
        run = self.stack('--check')
        self.assertRegex(run.stdout, r'(?i)did not parse with it\. just said: error: recipe `install`')
        # After the fix, the next run adds the block.
        justfile.write_text('mine:\n    @echo mine\n')
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('# begin lines of jnterfaces', justfile.read_text())
        self.assertNotIn('justfile_clash', self.saved())
        self.assertIn('prose', self.just_in_target('--summary').stdout.split())

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_a_role_recipe_that_clashes_stops_the_install_and_the_justfile_still_parses(self):
        justfile = self.target / 'Justfile'
        justfile.write_text('prose:\n    @echo mine\n')
        run = self.stack('prose')
        self.assertEqual(run.returncode, 1, run.stderr)
        self.assertIn('# begin lines of jnterfaces', justfile.read_text())
        self.assertIn('The recipe prose is in Justfile and in the recipe file of a role', run.stderr)
        self.assertEqual(self.just_in_target('prose').stdout.strip(), 'mine')
        self.assertEqual(self.just_in_target('install', '--help').returncode, 0)

    def test_a_dry_run_and_check_name_the_import_and_write_nothing(self):
        justfile = self.target / 'Justfile'
        justfile.write_text('x:\n    @echo x\n')
        run = self.stack('prose', '--dry-run')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("Would change Justfile: adds export JLOG_RUN := env(\"JLOG_RUN\", uuid()), import? "
                      "'.agents/install.just'", run.stdout)
        self.assertEqual(justfile.read_text(), 'x:\n    @echo x\n')
        run = self.stack('--check')
        self.assertIn("Justfile does not import .agents/install.just. The next just install adds the line "
                      "import? '.agents/install.just'", run.stdout)
        self.assertEqual(self.stack('prose').returncode, 0)
        # A block that you delete is a line of the record that is gone.
        justfile.write_text('x:\n    @echo x\n')
        run = self.stack('--check')
        self.assertIn("Justfile: import? '.agents/install.just' of jnterfaces is gone", run.stdout)
        self.assertNotIn('The next just install adds the line', run.stdout)

    def test_a_justfile_with_a_shebang_or_a_symlink_gets_no_block(self):
        justfile = self.target / 'Justfile'
        justfile.write_text('#!/usr/bin/env just --justfile\nx:\n    @echo x\n')
        run = self.stack('prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(justfile.read_text(), '#!/usr/bin/env just --justfile\nx:\n    @echo x\n')
        self.assertIn('before its #! line', self.stack('--check').stdout)
        justfile.unlink()
        (self.base / 'outside.just').write_text('x:\n    @echo x\n')
        justfile.symlink_to(self.base / 'outside.just')
        run = self.stack('prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('Justfile is a symlink', run.stderr)
        self.assertEqual((self.base / 'outside.just').read_text(), 'x:\n    @echo x\n')

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_a_renamed_justfile_keeps_its_import(self):
        justfile = self.target / 'Justfile'
        justfile.write_text('x:\n    @echo x\n')
        self.assertEqual(self.stack('prose').returncode, 0)
        block = justfile.read_text()
        # A rename in the letter case only, as on a disk that ignores case, then a line moved out of the block.
        for text in (block, "x:\n    @echo x\nimport? '.agents/install.just'\n"):
            current = install_stack.justfile_of(self.target)
            current.write_text(text)
            middle = self.target / 'middle.tmp'
            current.rename(middle)
            renamed = self.target / ('justfile' if current.name == 'Justfile' else 'Justfile')
            middle.rename(renamed)
            for _ in range(2):
                run = self.stack()
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertNotIn('removes', run.stderr)
                self.assertEqual(renamed.read_text(), text)
                self.assertEqual(sorted(name for name in self.saved()['parts'] if name.lower() == 'justfile'),
                                 [renamed.name])
                self.assertIn('prose', self.just_in_target('--summary').stdout.split())

    def test_a_deleted_justfile_gets_the_starter_without_the_old_record(self):
        justfile = self.target / 'Justfile'
        justfile.write_text('x:\n    @echo x\n')
        self.assertEqual(self.stack('prose').returncode, 0)
        justfile.unlink()
        self.assertEqual(self.stack().returncode, 0)
        self.assertTrue(self.saved()['started'])
        self.assertNotIn('Justfile', self.saved()['parts'])
        self.assertIn("\nimport? '.agents/install.just'\n", justfile.read_text())
        self.assertNotIn('# begin lines of jnterfaces', justfile.read_text())

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_a_new_justfile_after_a_deleted_one_gets_the_block(self):
        justfile = self.target / 'Justfile'
        justfile.write_text('x:\n    @echo x\n')
        self.assertEqual(self.stack('prose').returncode, 0)
        justfile.unlink()
        dot = self.target / '.justfile'
        dot.write_text('y:\n    @echo y\n')
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('declined', run.stderr)
        self.assertIn('# begin lines of jnterfaces', dot.read_text())
        self.assertNotIn('Justfile', self.saved()['parts'])
        self.assertEqual(self.saved()['parts']['.justfile']["import? '.agents/install.just'"]['state'], 'added')
        self.assertIn('prose', self.just_in_target('--summary').stdout.split())

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_a_module_that_clashes_keeps_the_recipes_of_the_roles(self):
        justfile = self.target / 'Justfile'
        justfile.write_text('python:\n    @echo mine\n')
        run = self.stack('--profile', 'python', 'prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('so the index has no module', run.stderr)
        self.assertEqual(self.just_in_target('python').stdout.strip(), 'mine')
        self.assertEqual(self.just_in_target('prose', 'a').stdout.strip(), 'prose a')

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_a_rollback_gives_back_the_bytes_and_a_broken_justfile_gets_no_block(self):
        justfile = self.target / 'Justfile'
        justfile.write_bytes(b'install:\n    @echo mine')
        run = self.stack('prose')
        self.assertIn('took the block out again', run.stderr)
        self.assertEqual(justfile.read_bytes(), b'install:\n    @echo mine')
        justfile.write_text('x:\n  @echo x\n y\n')
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(justfile.read_text(), 'x:\n  @echo x\n y\n')
        self.assertIn('does not parse, so the installer adds no import to it', run.stderr)
        self.assertNotIn('Changed Justfile', run.stderr)
        self.assertNotIn('took the block out again', run.stderr)

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_a_typo_elsewhere_keeps_the_block(self):
        justfile = self.target / 'Justfile'
        justfile.write_text('x:\n    @echo x\n')
        self.assertEqual(self.stack('prose').returncode, 0)
        text = justfile.read_text() + 'y:\n  @echo y\n z\n'
        justfile.write_text(text)
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(justfile.read_text(), text)
        self.assertNotIn('justfile_clash', self.saved())
        self.assertIn('does not parse, also without the block of the installer, so the block stays', run.stderr)
        self.assertNotIn('Rename the recipe', run.stderr)
        self.assertNotIn('put these lines in it', run.stderr)
        self.assertEqual(run.stderr.count('just said'), 1)
        self.assertEqual(list(self.target.glob('.jnterfaces-probe-*')), [])
        # After the fix, the block works.
        justfile.write_text(text.replace(' z\n', ''))
        self.assertEqual(self.stack().returncode, 0)
        self.assertIn('prose', self.just_in_target('--summary').stdout.split())

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_a_fault_that_needs_the_import_names_the_error_of_the_real_file(self):
        justfile = self.target / 'Justfile'
        justfile.write_text('x:\n    @echo x\n')
        self.assertEqual(self.stack('prose').returncode, 0)
        text = justfile.read_text() + 'y:\n    @echo {{jn_copier}}\ninstall:\n    @echo mine\n'
        justfile.write_text(text)
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(justfile.read_text(), text)
        self.assertRegex(run.stderr, r'(?i)just said: error: recipe `install` first defined')
        self.assertRegex(run.stderr, r'(?i)without the block, just says: error: variable `jn_copier` not defined')
        self.assertIn('(These line numbers do not count the 5 lines of the block.)', run.stderr)
        # A second recipe x: the error of the real file has the line numbers of the real file.
        justfile.write_text(justfile.read_text().replace('y:\n    @echo {{jn_copier}}\ninstall:\n    @echo mine\n',
                                                         'x:\n    @echo again\n'))
        run = self.stack()
        self.assertRegex(run.stderr, r'(?i)recipe `x` first defined on line 7 is redefined on line 9')
        self.assertNotIn('Without the block', run.stderr)

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_a_first_dry_run_and_check_see_a_clash_with_install(self):
        justfile = self.target / 'Justfile'
        justfile.write_text('install:\n    @echo mine\n')
        run = self.stack('prose', '--dry-run')
        self.assertNotIn('adds import', run.stdout)
        self.assertIn('so the real run would add it only if Justfile still parses with it', run.stdout)
        self.assertIn('only if Justfile still parses with it, and now it does not', self.stack('--check').stdout)
        self.assertFalse((self.target / '.agents').exists())

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_a_clash_error_says_that_its_line_numbers_count_the_block(self):
        (self.target / 'Justfile').write_text('install:\n    @echo mine\n')
        run = self.stack('prose')
        self.assertIn('(The line numbers count the 5 lines of the block at the start of Justfile.)', run.stderr)

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_a_dry_run_and_check_promise_no_block_that_the_run_would_not_add(self):
        justfile = self.target / 'Justfile'
        justfile.write_text('x:\n  @echo x\n y\n')
        run = self.stack('prose', '--dry-run')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('adds import', run.stdout)
        self.assertIn('does not parse, so the real run would add no import to it', run.stdout)
        run = self.stack('--check')
        self.assertNotIn('The next just install adds the line', run.stdout)
        self.assertIn('so the next just install adds no import', run.stdout)
        # A clash shows once .agents/install.just is there: the import of a missing file reads nothing.
        justfile.write_text('x:\n    @echo x\n')
        self.assertEqual(self.stack('prose').returncode, 0)
        record = self.saved()
        del record['parts']['Justfile']
        (self.target / '.agents/stack.json').write_text(json.dumps(record))
        justfile.write_text('install:\n    @echo mine\n')
        run = self.stack('prose', '--dry-run')
        self.assertNotIn('adds import', run.stdout)
        self.assertIn('so the real run would add it only if Justfile still parses with it', run.stdout)
        run = self.stack('--check')
        self.assertIn('only if Justfile still parses with it, and now it does not', run.stdout)
        self.assertEqual(list(self.target.glob('.jnterfaces-probe-*')), [])
        self.assertEqual(justfile.read_text(), 'install:\n    @echo mine\n')

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_a_rollback_keeps_a_change_that_you_saved_during_the_run(self):
        # A just on the path that saves a change of the user the first time the Justfile fails to parse, which is
        # after the installer wrote the block and before it takes the block out.
        real = shutil.which('just')
        (self.bin / 'just').write_text(f'''#!{sys.executable}
import os, subprocess, sys
from pathlib import Path
run = subprocess.run([{real!r}, *sys.argv[1:]], capture_output=True, text=True)
sys.stdout.write(run.stdout)
sys.stderr.write(run.stderr)
marker = Path(os.environ['SAVED_MARKER'])
justfile = Path(os.environ['SAVED_JUSTFILE'])
if run.returncode and not marker.exists() and 'begin lines of jnterfaces' in justfile.read_text():
    marker.write_text('')
    justfile.write_text(justfile.read_text() + '# saved by you\\n')
sys.exit(run.returncode)
''')
        (self.bin / 'just').chmod(0o755)
        justfile = self.target / 'Justfile'
        justfile.write_text('install:\n    @echo mine\n')
        run = self.stack('prose', env={'SAVED_MARKER': str(self.base / 'saved'),
                                       'SAVED_JUSTFILE': str(justfile)})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue((self.base / 'saved').exists())
        self.assertIn('took the block out again', run.stderr)
        self.assertEqual(justfile.read_text(), 'install:\n    @echo mine\n# saved by you\n')

    def test_check_names_a_started_justfile_without_the_import(self):
        self.assertEqual(self.stack('prose').returncode, 0)
        (self.target / 'Justfile').write_text('x:\n    @echo x\n')
        run = self.stack('--check')
        self.assertIn("Justfile does not import .agents/install.just. Add this line to it: import? "
                      "'.agents/install.just'", run.stdout)

    def test_a_generated_file_that_git_does_not_track_or_that_is_gone_gets_a_note(self):
        run = self.stack('prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('.agents/just/index.just', self.saved()['generated'])
        self.assertIn('git does not track .agents/just/index.just. Commit it', run.stderr)
        run = self.stack('--check')
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn('git does not track .agents/just/index.just', run.stdout)
        # After the commit, neither the run nor --check has the note.
        commit(self.target)
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('git does not track', run.stderr)
        self.assertNotIn('git does not track', self.stack('--check').stdout)
        # A clone without the file: --check fails and names it, and the next run writes it again.
        (self.target / '.agents/just/index.just').unlink()
        run = self.stack('--check')
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn('.agents/just/index.just is gone, so just does not have the recipes', run.stdout)
        self.assertEqual(self.stack().returncode, 0)
        self.assertTrue((self.target / '.agents/just/index.just').is_file())
        self.assertEqual(self.stack('--check').returncode, 0)

    def test_a_generated_key_outside_the_project_is_left_out_of_the_notes(self):
        self.assertEqual(self.stack('prose').returncode, 0)
        record = self.saved()
        record['generated'].update({'/nonexistent/x.just': 'x', '../outside.just': 'x'})
        (self.target / '.agents/stack.json').write_text(json.dumps(record))
        run = self.stack('--check')
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn('git does not track .agents/just/index.just', run.stdout)
        self.assertNotIn('outside', run.stdout)
        self.assertNotIn('nonexistent', run.stdout)

    def test_a_generated_file_outside_git_gets_no_note_of_git(self):
        shutil.rmtree(self.target / '.git')
        run = self.stack('prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('git does not track', run.stderr)
        self.assertNotIn('git does not track', self.stack('--check').stdout)

    def test_a_started_justfile_gets_no_block(self):
        self.assertEqual(self.stack('prose').returncode, 0)
        justfile = self.target / 'Justfile'
        text = justfile.read_text()
        self.assertEqual(self.stack().returncode, 0)
        self.assertEqual(justfile.read_text(), text)
        self.assertNotIn('Justfile', self.saved()['parts'])
        self.assertNotIn('does not import', self.stack('--check').stdout)

    def test_a_role_variant_is_saved_kept_and_dropped_with_the_role(self):
        run = self.stack('tasker=gitwt')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.saved()['variants'], {'tasker': 'gitwt'})
        self.assertIn('tasker: gitwt', run.stdout)
        self.assertEqual(self.stack().returncode, 0)
        self.assertEqual(self.saved()['variants'], {'tasker': 'gitwt'})
        self.assertEqual(self.stack('tasker=git').returncode, 0)
        self.assertEqual(self.saved()['variants'], {'tasker': 'git'})
        self.assertEqual(self.stack('--without', 'tasker').returncode, 0)
        self.assertNotIn('variants', self.saved())
        # A role turned on again without a variant takes the default.
        self.assertEqual(self.stack('tasker').returncode, 0)
        self.assertEqual(self.saved()['variants'], {'tasker': 'git'})

    def test_a_variant_that_is_not_ready_or_unknown_is_refused(self):
        run = self.stack('tasker=jj')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('tasker=jj is not ready yet', run.stderr)
        self.assertFalse((self.target / '.agents').exists())
        self.assertIn('tasker has no variant nope', self.stack('tasker=nope').stderr)
        self.assertIn('only a role that has variants takes one', self.stack('prose=x').stderr)
        self.assertIn('only a role that has variants takes one', self.stack('--without', 'tasker=git').stderr)

    def test_a_saved_variant_that_is_no_longer_ready_names_the_way_out(self):
        self.assertEqual(self.stack('tasker=gitwt').returncode, 0)
        registry = self.registry_with(lambda r: r['packages'][1]['variants']['gitwt'].update(ready=False))
        run = self.stack('--registry', registry)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('just install tasker=git', run.stderr)
        self.assertEqual(self.stack('tasker=git', '--registry', registry).returncode, 0)
        registry = self.registry_with(lambda r: r['packages'][1]['variants'].pop('gitwt'))
        self.assertEqual(self.stack('tasker=git').returncode, 0)
        record = self.saved()
        record['variants'] = {'tasker': 'gitwt'}
        (self.target / '.agents/stack.json').write_text(json.dumps(record))
        run = self.stack('--registry', registry)
        self.assertIn('The saved variant tasker=gitwt is gone from stack.json', run.stderr)
        self.assertEqual(self.saved()['variants'], {'tasker': 'git'})

    def test_a_project_not_started_by_jnterfaces_gets_an_index_for_roles_alone(self):
        (self.target / 'Justfile').write_text("import? '.agents/install.just'\n")
        self.assertEqual(self.stack('prose').returncode, 0)
        self.assertNotIn('mod ', (self.target / '.agents/just/index.just').read_text())
        self.assertIn("import? 'roles/nossaifd.just'", (self.target / '.agents/just/index.just').read_text())

    def test_a_malformed_variant_list_is_refused(self):
        for change in (lambda r: r['packages'][1].update(default_variant='nope'),
                       lambda r: r['packages'][1]['variants'].update(git={'about': 'x', 'ready': False}),
                       lambda r: r['packages'][1]['variants'].update(bad=[]),
                       lambda r: r['packages'][2].update(variants={'a': {'about': 'x'}})):
            with self.assertRaises(install_stack.StackError):
                install_stack.load_registry(self.registry_with(change))

    def test_the_record_holds_the_files_of_just_install_and_the_starter_justfile(self):
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        saved = self.saved()
        self.assertTrue(saved['started'])
        self.assertEqual(sorted(saved['generated']), ['.agents/just/index.just', '.agents/just/python.just'])
        for path, digest in saved['generated'].items():
            self.assertEqual(hashlib.sha256((self.target / path).read_bytes()).hexdigest(), digest)
        # The record decides, not a line in the Justfile: without the comment, the project still gets the recipes.
        justfile = self.target / 'Justfile'
        justfile.write_text('\n'.join(line for line in justfile.read_text().splitlines() if 'jnterfaces' not in line))
        self.assertEqual(self.stack('typecheck').returncode, 0)
        self.assertIn('\nverify:', (self.target / '.agents/just/index.just').read_text())

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_a_clash_falls_back_so_the_justfile_still_parses(self):
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        justfile = self.target / 'Justfile'
        start = justfile.read_text()
        cases = [(start.replace('set allow-duplicate-recipes', '') + '\nlint:\n    @echo mine\n', 'lint', 'mine'),
                 (start + '\nhello:\n    @echo hi\n\nalias lint := hello\n', 'lint', 'hi'),
                 (start + '\npython:\n    @echo own\n', 'python', 'own')]
        for text, recipe, out in cases:
            justfile.write_text(text)
            run = self.stack()
            self.assertEqual(run.returncode, 0, run.stderr)
            # The index leaves out a name of the Justfile. Only a module of that name makes the Justfile fail.
            if recipe == 'python':
                self.assertIn('Rename the recipe, alias or module of the project with that name', run.stderr)
            else:
                self.assertNotIn('does not parse', run.stderr)
                self.assertNotIn(f'\n{recipe}', (self.target / '.agents/just/index.just').read_text())
            self.assertEqual(self.just_in_target('--summary').returncode, 0, text)
            self.assertEqual(self.just_in_target(recipe).stdout.strip(), out)
            self.assertEqual(self.just_in_target('install', '--help').returncode, 0)

    def test_a_module_written_before_a_stop_is_still_the_installers(self):
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        record = self.saved()
        record['generated'] = {path: '0' * 64 for path in record['generated']}
        (self.target / '.agents/stack.json').write_text(json.dumps(record))
        run = self.stack('typecheck')
        self.assertNotIn('Kept .agents/just', run.stderr)
        self.assertIn("import? 'python/typecheck.just'", (self.target / '.agents/just/python.just').read_text())

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_an_edited_index_that_names_a_gone_module_is_reported(self):
        self.assertEqual(self.stack('--profile', 'python,typescript', '--defaults').returncode, 0)
        index = self.target / '.agents/just/index.just'
        index.write_text('# mine\n' + index.read_text())
        run = self.stack('--without', 'typescript')
        self.assertIn('The Justfile does not parse with the index that you kept', run.stderr)

    def test_a_crafted_record_entry_deletes_no_other_file(self):
        notes = self.target / '.agents/just/notes.just'
        notes.parent.mkdir(parents=True)
        notes.write_text('my notes\n')
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        record = self.saved()
        record['generated']['.agents/just/notes.just'] = hashlib.sha256(notes.read_bytes()).hexdigest()
        (self.target / '.agents/stack.json').write_text(json.dumps(record))
        self.assertEqual(self.stack().returncode, 0)
        self.assertEqual(notes.read_text(), 'my notes\n')

    def test_a_file_that_just_install_did_not_write_stays(self):
        module = self.target / '.agents/just/python.just'
        module.parent.mkdir(parents=True)
        module.write_text('# mine, with the header line of just install\n')
        run = self.stack('--profile', 'python', '--defaults')
        self.assertIn('Kept .agents/just/python.just', run.stderr)
        self.assertEqual(module.read_text(), '# mine, with the header line of just install\n')
        self.assertNotIn('.agents/just/python.just', self.saved()['generated'])
        # A removal of the profile leaves it too.
        self.assertEqual(self.stack('--without', 'python').returncode, 0)
        self.assertTrue(module.is_file())

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_a_project_not_started_by_jnterfaces_gets_the_index_without_its_names(self):
        (self.target / 'Justfile').write_text("import? '.agents/install.just'\n\nlint:\n    @echo mine\n")
        run = self.stack('--profile', 'python', '--defaults')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stderr.count('The lint of steps: lint: its last line is not jlog step lint'), 1)
        index = (self.target / '.agents/just/index.just').read_text()
        self.assertIn("mod python 'python.just'", index)
        self.assertIn('\nverify:', index)
        self.assertNotIn('\nlint', index)
        self.assertEqual(self.just_in_target('lint').stdout.strip(), 'mine')
        self.assertEqual(self.just_in_target('python', 'lint').stdout.strip(), 'python lint')
        self.assertEqual(self.just_in_target('python', 'verify').returncode, 0)

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_a_justfile_that_does_not_parse_is_named(self):
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        justfile = self.target / 'Justfile'
        justfile.write_text(justfile.read_text() + '\nbroken: nothere\n')
        run = self.stack('typecheck')
        # just writes recipe with a capital R in some versions only.
        self.assertRegex(run.stderr, r'(?i)just said: error: recipe `broken` has unknown dependency `nothere`')
        self.assertEqual(run.stderr.count('just said'), 1)

    @unittest.skipUnless(shutil.which('just'), 'needs just')
    def test_an_edited_index_still_lets_the_modules_follow_the_tools(self):
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        index = self.target / '.agents/just/index.just'
        index.write_text('# mine\n' + index.read_text())
        run = self.stack('typecheck')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('already has the name python', run.stderr)
        self.assertIn("import? 'python/typecheck.just'", (self.target / '.agents/just/python.just').read_text())
        self.assertEqual(self.just_in_target('python', 'typecheck').stdout.strip(), 'python typecheck')

    def test_naming_the_profile_again_keeps_its_tools(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        self.assertNotEqual(self.stack('--without', 'python', env={'FAIL_TOOL': 'test'}).returncode, 0)
        self.log.unlink()
        self.assertEqual(self.stack('python').returncode, 0)
        # lint went before the failure; naming the profile again brings back its default tools.
        self.assertEqual(sorted(self.tool_names('tools')), ['lint', 'test'])
        self.assertNotIn('removing_tools', self.saved())
        self.assertFalse(any(line.startswith('uv remove') for line in self.tools_run()))

    def test_a_failed_profile_removal_goes_on_at_the_next_run(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        run = self.stack('--without', 'python', env={'FAIL_TOOL': 'test'})
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(self.saved()['removing_profiles'], ['python'])
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.saved()['profiles'], [])
        self.assertNotIn('removing_profiles', self.saved())
        self.assertFalse((self.target / RECORDS_PROFILE['python']).exists())
        self.assertFalse((self.target / '.agents/just/python.just').exists())

    def test_naming_the_profile_again_cancels_its_removal(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        self.assertNotEqual(self.stack('--without', 'python', env={'FAIL_TOOL': 'test'}).returncode, 0)
        self.assertEqual(self.stack('python').returncode, 0)
        self.assertEqual(self.saved()['profiles'], ['python'])
        self.assertTrue((self.target / RECORDS_PROFILE['python']).is_file())

    def test_profile_that_leaves_out_a_saved_profile_turns_it_off(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        self.log.unlink()
        run = self.stack('--profile', 'typescript', '--defaults')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('leaves out the saved profile(s) python', run.stderr)
        self.assertIn('uv remove --group lint ruff pre-commit', self.tools_run())
        self.assertEqual(self.saved()['profiles'], ['typescript'])
        self.assertFalse((self.target / RECORDS_PROFILE['python']).exists())
        self.assertFalse((self.target / '.agents/just/python').exists())

    def test_an_empty_group_that_default_groups_names_stays(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n\n[tool.uv]\ndefault-groups = ["lint"]\n')
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        self.assertEqual(self.stack('--without', 'lint').returncode, 0)
        data = tomllib.loads((self.target / 'pyproject.toml').read_text())
        self.assertEqual(data['dependency-groups']['lint'], [])

    def test_a_clone_of_an_empty_repository_is_removed_so_a_later_run_clones_again(self):
        empty = self.base / 'empty.git'
        subprocess.run(['git', 'init', '-q', '--bare', str(empty)], check=True)
        shutil.rmtree(self.packages / 'jnprofile-python')
        registry = self.registry_with(lambda r: r['profiles']['python'].update(repository=str(empty)))
        run = self.stack('--profile', 'python', '--defaults', '--registry', registry)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('Cannot clone', run.stderr)
        self.assertFalse((self.packages / 'jnprofile-python').exists())

    def test_each_run_starts_with_no_message_said(self):
        install_stack.SAID.add('an old message')
        with self.assertRaises(install_stack.StackError):
            install_stack.main(['--target', str(self.base / 'nothere')])
        self.assertNotIn('an old message', install_stack.SAID)

    def test_an_edited_recipe_file_of_just_install_stays(self):
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        module = self.target / '.agents/just/python.just'
        module.write_text('# mine\n')
        run = self.stack('typecheck')
        self.assertIn('Kept .agents/just/python.just', run.stderr)
        self.assertEqual(module.read_text(), '# mine\n')

    def test_without_a_profile_takes_out_its_tools_recipes_and_package(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        self.assertEqual(self.stack('--profile', 'python', '--defaults', 'typecheck').returncode, 0)
        self.assertTrue((self.target / RECORDS_PROFILE['python']).is_file())
        self.log.unlink()
        run = self.stack('--without', 'python')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(sorted(self.tools_run()), ['uv remove --group lint ty ruff pre-commit',
                                                    'uv remove --group test pytest pytest-cov'])
        self.assertEqual(self.saved()['profiles'], [])
        self.assertEqual(self.saved()['tools'], [])
        self.assertFalse((self.target / RECORDS_PROFILE['python']).exists())
        self.assertFalse((self.target / '.agents/just/python.just').exists())
        self.assertFalse((self.target / '.agents/just/python').exists())
        self.assertNotIn('mod python', (self.target / '.agents/just/index.just').read_text())
        # The empty groups are gone, and the project keeps pyproject.toml.
        self.assertNotIn('dependency-groups.lint', (self.target / 'pyproject.toml').read_text())
        self.assertNotIn('lint =', (self.target / 'pyproject.toml').read_text())
        # A later run does not find the profile from pyproject.toml again, and names how to turn it on.
        run = self.stack()
        self.assertEqual(self.saved()['profiles'], [])
        self.assertIn('just install --profile python', run.stderr)
        self.assertIn('are not on', self.stack('--without', 'python').stderr)

    def test_an_empty_group_goes_only_when_it_is_empty(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        self.assertEqual(self.stack('--profile', 'python', '--defaults', 'typecheck').returncode, 0)
        self.assertEqual(self.stack('--without', 'lint').returncode, 0)
        data = tomllib.loads((self.target / 'pyproject.toml').read_text())
        self.assertEqual(data['dependency-groups']['lint'], ['ty'])
        run = self.stack('--without', 'typecheck')
        self.assertIn('Took the empty dependency group lint out', run.stderr)
        data = tomllib.loads((self.target / 'pyproject.toml').read_text())
        self.assertNotIn('lint', data['dependency-groups'])
        self.assertEqual(data['dependency-groups']['test'], ['pytest', 'pytest-cov'])

    def rules(self, path='.gitignore'):
        return [line for line in (self.target / path).read_text().splitlines() if line and not line.startswith('#')]

    def test_a_profile_may_not_name_a_line_twice_or_a_file_of_the_installer(self):
        choices = install_stack.load_registry(REPO / 'stack.json')[1]
        for change in (lambda p: p['base']['lines'].update({'.gitignore': ['.venv/', '.venv']}),
                       lambda p: p['base']['lines'].update({'.agents/stack-log.jsonl': ['x']}),
                       lambda p: p['base']['lines'].update({'.agents/just/x.just': ['x']}),
                       lambda p: p['base']['lines'].update({'.gitignore': ['  ']})):
            profile = json.loads(json.dumps(PROFILES['python']))
            change(profile)
            with self.assertRaises(install_stack.StackError):
                install_stack.check_profile('python', profile, choices)

    def test_a_profile_writes_its_lines_in_blocks_and_a_deleted_file_stays_deleted(self):
        run = self.stack('--profile', 'python', '--defaults', env={'JNTERFACES_COMMAND': 'just install --profile python'})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.rules(), JN + ['.venv/', 'dist/', '.local/', '.ruff_cache/'])
        self.assertEqual(self.rules('.worktreedisposable'), ['.agents/log/', '.venv/', '__pycache__/', '.ruff_cache/'])
        self.assertFalse((self.target / '.agents/.gitattributes').exists())
        text = (self.target / '.gitignore').read_text()
        self.assertIn('# begin lines of python:lint, the lint tool of the python profile, from jnprofile-python at ', text)
        self.assertIn('# Last written to .gitignore by `just install --profile python`. The record is parts.".gitignore" '
                      'in .agents/stack.json.', text)
        self.assertIn('# end lines of python:lint. `just uninstall lint` removes them.', text)
        self.assertEqual(self.saved()['parts']['.gitignore']['.ruff_cache']['holder'], 'python:lint')
        log = [json.loads(line) for line in (self.target / '.agents/stack-log.jsonl').read_text().splitlines()]
        self.assertEqual(log[0]['command'], 'just install --profile python')
        self.assertEqual(self.stack('--check').returncode, 0)
        gitignore = self.target / '.gitignore'
        gitignore.unlink()
        self.assertEqual(self.stack().returncode, 0)
        self.assertFalse(gitignore.exists())

    def test_two_profiles_share_a_file_and_each_takes_its_lines_out(self):
        self.assertEqual(self.stack('--profile', 'python,typescript', '--defaults').returncode, 0)
        self.assertEqual(self.rules(), JN + ['.venv/', 'dist/', '.local/', '.ruff_cache/', 'node_modules/'])
        self.assertEqual(self.rules('.worktreedisposable'), ['.agents/log/', '.venv/', '__pycache__/', '.ruff_cache/', 'node_modules/'])
        dry = self.stack('--without', 'python', '--dry-run')
        self.assertIn('Would change .gitignore: removes .ruff_cache, .venv', dry.stdout)
        self.assertEqual(self.rules(), JN + ['.venv/', 'dist/', '.local/', '.ruff_cache/', 'node_modules/'])
        run = self.stack('--without', 'python')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.rules(), JN + ['node_modules/', 'dist/', '.local/'])
        self.assertIn('# begin lines of typescript, the typescript profile', (self.target / '.gitignore').read_text())
        self.assertEqual(self.rules('.worktreedisposable'), ['.agents/log/', 'node_modules/'])

    def test_lint_takes_out_its_lines_and_an_unchanged_config_file(self):
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        self.assertTrue((self.target / 'ruff.toml').is_file())
        self.assertEqual(self.stack('--without', 'lint').returncode, 0)
        self.assertEqual(self.rules(), JN + ['.venv/', 'dist/', '.local/'])
        self.assertFalse((self.target / 'ruff.toml').exists())
        self.assertFalse((self.target / '.pre-commit-config.yaml').exists())

    def test_a_whole_file_of_two_tools_stays_until_both_go(self):
        self.assertEqual(self.stack('--profile', 'typescript', '--defaults', 'test').returncode, 0)
        tsconfig = self.target / 'tsconfig.json'
        self.assertTrue(tsconfig.is_file())
        self.assertEqual(self.stack('--without', 'typecheck').returncode, 0)
        self.assertTrue(tsconfig.is_file())
        self.assertEqual(self.stack('--without', 'test').returncode, 0)
        self.assertFalse(tsconfig.exists())

    def test_a_failed_tool_changes_no_line_file(self):
        run = self.stack('--profile', 'python', '--defaults', env={'FAIL_TOOL': 'test'})
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse((self.target / '.gitignore').exists())
        self.assertEqual(self.stack().returncode, 0)
        self.assertIn('.venv/', self.rules())

    def test_a_dry_run_names_the_recipes_and_writes_none(self):
        run = self.stack('--profile', 'python', '--defaults', '--dry-run')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('Would write the recipes of python in .agents/just', run.stdout)
        self.assertIn('Would write .gitignore', run.stdout)
        self.assertFalse((self.target / '.agents').exists())

    def test_rust_inits_cargo_adds_no_packages_and_records_the_code_globs(self):
        run = self.stack('--profile', 'rust', '--defaults')
        self.assertEqual(run.returncode, 0, run.stderr)
        # The tools have no packages, so cargo runs only for init.
        self.assertEqual(self.tools_run(), ['cargo init --vcs none'])
        self.assertIn('lint for rust needs no packages', run.stderr)
        self.assertIn('test for rust needs no packages', run.stderr)
        self.assertEqual(self.saved()['code'], {'rust': ['**/*.rs']})
        self.assertEqual(self.tool_names('tools'), ['lint', 'test'])
        module = (self.target / '.agents/just/rust.just').read_text()
        self.assertIn("import? 'rust/lint.just'", module)
        self.assertIn('verify: fmt-check lint test coverage-check', module)
        self.assertIn('/target/', (self.target / '.gitignore').read_text())
        self.assertTrue((self.target / RECORDS_PROFILE['rust']).is_file())

    def test_rust_is_found_by_cargo_toml_even_when_it_does_not_parse(self):
        # A tool with no packages does not read Cargo.toml, so a broken file does not stop the install.
        (self.target / 'Cargo.toml').write_text('[package\n')
        run = self.stack('--defaults')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.saved()['profiles'], ['rust'])
        self.assertEqual(self.tools_run(), [])
        self.assertEqual((self.target / 'Cargo.toml').read_text(), '[package\n')

    def test_a_rust_tool_with_no_packages_turns_off_with_a_broken_cargo_toml(self):
        self.assertEqual(self.stack('--profile', 'rust', '--defaults').returncode, 0)
        (self.target / 'Cargo.toml').write_text('[package\n')
        run = self.stack('--without', 'lint')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.tool_names('tools'), ['test'])
        self.assertNotIn("import? 'rust/lint.just'", (self.target / '.agents/just/rust.just').read_text())

    def test_a_dry_run_of_rust_says_each_tool_needs_no_packages(self):
        run = self.stack('--profile', 'rust', '--defaults', '--dry-run')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('lint needs no packages', run.stdout)
        self.assertNotIn('already has the packages', run.stdout)
        self.assertFalse((self.target / '.agents').exists())

    def test_a_rust_tool_with_a_crate_reads_the_dev_dependencies(self):
        self.edit_profile('rust', lambda p: p['tools']['test'].update({'packages': ['proptest']}))
        (self.target / 'Cargo.toml').write_text('[package]\nname = "app"\n\n[dev-dependencies]\nproptest = "1"\n')
        run = self.stack('--defaults')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('The project already has the packages of test for rust', run.stderr)
        self.assertEqual(self.tools_run(), [])
        # In a second project, only the crate that [dev-dependencies] lacks is added.
        self.edit_profile('rust', lambda p: p['tools']['test'].update({'packages': ['proptest', 'insta']}))
        target = self.base / 'second'
        target.mkdir()
        git(target, 'init', '-q')
        (target / 'Cargo.toml').write_text('[package]\nname = "app"\n\n[dev-dependencies]\nproptest = "1"\n')
        run = self.stack('--defaults', target=target)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.tools_run(), ['cargo add --dev insta'])

    def test_turning_rust_off_drops_its_code_globs(self):
        self.assertEqual(self.stack('--profile', 'rust', '--defaults').returncode, 0)
        (self.target / 'pyproject.toml').write_text('[project]\nname = "app"\n')
        run = self.stack('--profile', 'python')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('code', self.saved())

    def test_malformed_code_globs_are_refused(self):
        for bad in ('**/*.rs', ['/src/*.rs'], [''], [3], ['../other/*.rs'], ['src/../../*.rs']):
            self.edit_profile('rust', lambda p: p.update({'code': bad}))
            run = self.stack('--profile', 'rust', '--defaults')
            self.assertNotEqual(run.returncode, 0, bad)
            self.assertIn('profile.json of the rust profile is malformed', run.stderr)

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
        # Both owners of tsconfig.json went, and nobody changed it, so it goes too.
        self.assertFalse((self.target / 'tsconfig.json').exists())
        # The recipes follow the tools: only the base and lint are left.
        self.assertIn("mod typescript 'typescript.just'", (self.target / '.agents/just/index.just').read_text())
        module = (self.target / '.agents/just/typescript.just').read_text()
        self.assertIn("import? 'typescript/lint.just'", module)
        self.assertNotIn('typecheck.just', module)
        self.assertNotIn('test.just', module)

    def test_a_failed_tool_keeps_the_roles_and_a_later_run_tries_it_again(self):
        run = self.stack('--profile', 'python', env={'FAIL_TOOL': 'lint'})
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('run just install again, which tries the tool again', run.stderr)
        self.assertTrue(self.has('gtw'))
        self.assertTrue((self.target / 'Justfile').is_file())
        self.assertEqual(self.tool_names('tools'), [])
        self.assertEqual(self.tool_names('pending_tools'), ['lint', 'test'])
        self.log.unlink()
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.tools_run(), ['uv add --group lint ruff pre-commit', 'uv add --group test pytest pytest-cov'])
        self.assertEqual(self.tool_names('tools'), ['lint', 'test'])
        self.assertNotIn('pending_tools', self.saved())

    def test_a_new_project_gets_its_project_file_justfile_and_tools(self):
        run = self.stack('--profile', 'python')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.tools_run()[0], 'uv init --bare --no-workspace')
        self.assertTrue((self.target / 'Justfile').is_file())
        self.assertEqual(self.tool_names('tools'), ['lint', 'test'])

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
        self.assertIn('package = false\n# tool.uv.default-groups is a part of python, the python profile', text)
        self.assertIn('\ndefault-groups = "all"\n', text)
        (self.target / 'pyproject.toml').write_text('[tool.uv]\ndefault-groups = ["dev"]\n')
        self.assertEqual(self.stack('lint').returncode, 0)
        data = install_stack.tomllib.loads((self.target / 'pyproject.toml').read_text())
        self.assertEqual(data['tool']['uv']['default-groups'], ['dev'])

    def test_default_groups_are_set_only_where_the_toml_stays_the_same(self):
        # Each text either gets the key with the rest of its data the same, or stays as it is: the installer then
        # leaves the file alone and says so.
        unit = 'tool.uv.default-groups'
        for text in ('[tool.uv]  # mine\npackage = false\n', '[ tool.uv ]\npackage = false\n',
                     '[tool]\nuv.package = false\n', '["tool".uv]\npackage = false\n', 'not = [toml\n',
                     'x = """\n[tool.uv]\n"""\n', '[tool.x]\nreleased = 2024-01-01\n', 'tool = 1\n', '[tool]\nuv = 3\n',
                     '[project]\nname = "a"\n'):
            try:
                after, _ = install_stack.shared_files.edit_toml.plan(text, [(unit, 'all', None)], [])
            except install_stack.shared_files.edit_toml.EditError:
                continue
            data = install_stack.tomllib.loads(after)
            self.assertEqual(data['tool']['uv'].pop('default-groups'), 'all', text)
            before = install_stack.tomllib.loads(text)
            before.setdefault('tool', {}).setdefault('uv', {})
            self.assertEqual(data, before, text)

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
        self.assertIn('lint', self.tool_names('tools'))
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
        pb = json.loads(json.dumps(PROFILES['python']))
        pb.update(name='pb', remove=['false'], choices={})
        pb.pop('init')
        self.profile_package('pb', pb, RECIPES['python'])

        def two(r):
            r['profiles']['pb'] = dict(r['profiles']['python'], package='jnprofile-pb', detect=[])
        registry = self.registry_with(two)
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        self.assertEqual(self.stack('--profile', 'python,pb', '--defaults', '--registry', registry).returncode, 0)
        run = self.stack('--without', 'lint', '--registry', registry)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('lint', self.tool_names('tools'))

    def test_a_turned_off_tool_with_no_remove_step_leaves_the_record_even_when_another_step_fails(self):
        self.assertEqual(self.stack('--profile', 'typescript', '--defaults').returncode, 0)
        self.assertEqual(self.tool_names('tools'), ['lint', 'typecheck'])
        self.assertEqual(self.stack('test').returncode, 0)
        # Take biome out of package.json, so that the lint add has a command to run, and that command fails.
        data = json.loads((self.target / 'package.json').read_text())
        data['devDependencies'].pop('@biomejs/biome')
        (self.target / 'package.json').write_text(json.dumps(data))
        self.edit_profile('typescript', lambda p: p.update(add=['false']))
        run = self.stack('--without', 'test', 'lint')
        self.assertNotEqual(run.returncode, 0)
        self.assertNotIn('test', self.tool_names('tools'))

    def test_a_file_error_after_a_removal_still_saves_the_record(self):
        # The python profile removes test, then a file of the typescript profile fails on a symlinked folder.
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        self.assertEqual(self.stack('--profile', 'python,typescript', '--defaults', 'test').returncode, 0)
        self.assertIn('test', self.tool_names('tools'))
        self.edit_profile('typescript', lambda p: p['tools']['typecheck'].update(
            files={'sub/tsconfig.json': 'profiles/typescript/tsconfig.json'}))
        (self.target / 'sub').symlink_to(self.base)
        self.log.unlink()
        run = self.stack('typecheck', '--without', 'test')
        # A file in a symlinked folder is left alone with a note, so the run finishes.
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('uv remove --group test pytest pytest-cov', self.tools_run())
        self.assertIn('sub is a symlink', run.stderr)
        self.assertNotIn('Traceback', run.stderr)
        self.assertNotIn('test', self.tool_names('tools'))
        self.assertFalse((self.base / 'tsconfig.json').exists())

    def test_without_keeps_a_package_that_the_project_had_before_the_tool(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n\n[dependency-groups]\ntest = ["PyTest>=8"]\n')
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        self.assertIn('uv add --group test pytest-cov', self.tools_run())
        self.assertEqual(self.saved()['owned_packages']['python:test'], ['pytest-cov'])
        self.log.unlink()
        self.assertEqual(self.stack('--without', 'test').returncode, 0)
        self.assertEqual(self.tools_run(), ['uv remove --group test pytest-cov'])
        self.assertNotIn('python:test', self.saved().get('owned_packages', {}))

    def test_a_tool_whose_packages_the_project_has_runs_no_command(self):
        (self.target / 'package.json').write_text(json.dumps({'devDependencies': {'@biomejs/biome': '^2'}}))
        run = self.stack('--profile', 'typescript', '--defaults', '--without', 'typecheck', 'lint')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('already has the packages of lint', run.stderr)
        self.assertNotIn('npm install --save-dev @biomejs/biome@2', ' '.join(self.tools_run()))
        self.assertNotIn('owned_packages', self.saved())

    def test_without_keeps_a_package_that_a_kept_tool_needs_whoever_added_it(self):
        self.assertEqual(self.stack('--profile', 'typescript', '--defaults').returncode, 0)
        (self.target / 'package.json').write_text(json.dumps({'devDependencies': {
            '@biomejs/biome': '^2', 'typescript': '^7', '@types/node': '^24'}}))
        self.assertEqual(self.stack('test').returncode, 0)
        self.assertEqual(self.saved()['owned_packages']['typescript'], ['@biomejs/biome', '@types/node', 'typescript'])
        self.log.unlink()
        self.assertEqual(self.stack('--without', 'typecheck').returncode, 0)
        self.assertEqual(self.tools_run(), [])

    def test_a_profile_added_later_records_what_it_added(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        (self.target / 'package.json').write_text(json.dumps({'devDependencies': {'@biomejs/biome': '^2'}}))
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        self.assertEqual(self.stack('--profile', 'python,typescript', '--defaults').returncode, 0)
        self.assertNotIn('@biomejs/biome', self.saved()['owned_packages']['typescript'])
        self.log.unlink()
        self.assertEqual(self.stack('--without', 'lint').returncode, 0)
        self.assertNotIn('biome', ' '.join(self.tools_run()))

    def test_a_tool_with_no_recorded_packages_removes_nothing_and_names_them(self):
        # A record from before owned_packages says nothing about what its tools added.
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        record = self.saved()
        record.pop('owned_packages')
        (self.target / '.agents/stack.json').write_text(json.dumps(record))
        # The real uv would have written the packages into pyproject.toml; the fake one does not.
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n\n[dependency-groups]\nlint = ["ruff", "pre-commit"]\n')
        self.log.unlink()
        run = self.stack('--without', 'lint')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.tools_run(), [])
        self.assertIn('Left ruff, pre-commit in the project for python', run.stderr)
        self.assertNotIn('lint', self.tool_names('tools'))

    def test_a_profile_turned_on_later_records_the_tools_the_project_has(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n\n[dependency-groups]\nlint = ["ty"]\n')
        self.assertEqual(self.stack('--profile', 'typescript', '--defaults').returncode, 0)
        self.assertEqual(self.stack('python').returncode, 0)
        self.assertNotIn('ty', self.saved().get('owned_packages', {}).get('python:lint', []))
        self.log.unlink()
        self.assertEqual(self.stack('--without', 'typecheck').returncode, 0)
        self.assertNotIn('uv remove', ' '.join(self.tools_run()))
        lint = install_stack.tomllib.loads((self.target / 'pyproject.toml').read_text())['dependency-groups']['lint']
        self.assertIn('ty', lint)

    def test_a_tool_that_failed_in_one_profile_stays_pending_and_is_retried(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n\n[dependency-groups]\nlint = ["ruff"]\n')
        (self.target / 'package.json').write_text('{}')
        run = self.stack('--profile', 'typescript,python', '--defaults', env={'FAIL_TOOL': 'lint'})
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('lint', self.tool_names('pending_tools'))
        self.assertNotIn('python:lint', self.saved().get('owned_packages', {}))
        self.log.unlink()
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('uv add --group lint pre-commit', self.tools_run())
        self.assertEqual(self.saved()['owned_packages']['python:lint'], ['pre-commit'])
        self.log.unlink()
        self.assertEqual(self.stack('--without', 'lint').returncode, 0)
        self.assertIn('uv remove --group lint pre-commit', self.tools_run())
        self.assertNotIn('ruff', ' '.join(t for t in self.tools_run() if t.startswith('uv ')))

    def test_a_shared_package_stays_owned_until_the_last_tool_that_lists_it_goes(self):
        # typecheck adds typescript and @types/node. test lists the same two, so adding it later adds nothing.
        self.assertEqual(self.stack('--profile', 'typescript', '--defaults').returncode, 0)
        (self.target / 'package.json').write_text(json.dumps({'devDependencies': {
            '@biomejs/biome': '^2', 'typescript': '^7', '@types/node': '^24'}}))
        self.assertEqual(self.stack('test').returncode, 0)
        self.log.unlink()
        self.assertEqual(self.stack('--without', 'typecheck').returncode, 0)
        self.assertEqual(self.tools_run(), [])
        run = self.stack('--without', 'test')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.tools_run(), ['uvx --from nodejs-wheel==24.* npm uninstall typescript @types/node'])
        self.assertNotIn('Left', run.stderr)

    def test_an_interrupted_install_keeps_what_it_added(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        run = self.stack('--profile', 'python', '--defaults', env={'INT_TOOL': 'test'})
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(self.saved()['owned_packages']['python:lint'], ['pre-commit', 'ruff'])
        # The test add wrote pyproject.toml before the interrupt, and its claim kept the packages.
        self.assertEqual(self.saved()['owned_packages']['python:test'], ['pytest', 'pytest-cov'])
        self.assertEqual(self.tool_names('tools'), ['lint'])
        self.assertEqual(self.tool_names('pending_tools'), ['test'])

    def test_left_names_only_packages_that_the_project_has_and_a_dry_run_says_would(self):
        # The project had biome before typescript was turned on, so just install did not add it.
        (self.target / 'package.json').write_text(json.dumps({'devDependencies': {'@biomejs/biome': '^2'}}))
        self.assertEqual(self.stack('--profile', 'typescript', '--defaults').returncode, 0)
        run = self.stack('--without', 'lint', '--dry-run')
        self.assertIn('Would leave @biomejs/biome in the project for typescript', run.stderr)
        self.assertNotIn('npm uninstall', run.stdout)
        # Once the user has removed it by hand, nothing is named.
        (self.target / 'package.json').write_text('{}')
        run = self.stack('--without', 'lint', '--dry-run')
        self.assertNotIn('biome', run.stderr)

    def test_a_record_with_added_packages_moves_them_to_their_places(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        record = self.saved()
        record.pop('owned_packages')
        record['added_packages'] = {'python:lint': ['ruff', 'pre-commit'], 'python:typecheck': [], 'nope:lint': ['x']}
        (self.target / '.agents/stack.json').write_text(json.dumps(record))
        self.log.unlink()
        self.assertEqual(self.stack('--without', 'lint').returncode, 0)
        self.assertEqual(self.tools_run(), ['uv remove --group lint ruff pre-commit'])
        self.assertNotIn('added_packages', self.saved())

    def test_a_new_profile_turns_on_only_its_own_tools(self):
        # As in fafamid: python saved with its tools turned off, because pyproject.toml pins them by hand.
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        self.assertEqual(self.stack('--without', 'lint,test').returncode, 0)
        self.assertEqual(self.saved()['tools'], [])
        self.log.unlink()
        run = self.stack('typescript', '--dry-run')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('uv add', run.stdout)
        self.assertIn('npm install --save-dev @biomejs/biome@2', run.stdout)
        self.assertEqual(self.stack('typescript').returncode, 0)
        self.assertEqual(self.saved()['tools'], ['typescript:lint', 'typescript:typecheck'])
        self.assertFalse(any(line.startswith('uv ') for line in self.tools_run()))

    def test_a_record_with_bare_tool_names_means_each_saved_profile(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        record = self.saved()
        record['tools'] = ['lint']
        (self.target / '.agents/stack.json').write_text(json.dumps(record))
        self.assertEqual(self.stack('--defaults').returncode, 0)
        self.assertEqual(self.saved()['tools'], ['python:lint'])

    def set_group(self, group, names):
        data = install_stack.tomllib.loads((self.target / 'pyproject.toml').read_text())
        data['dependency-groups'][group] = names
        text = '[project]\nname = "a"\n\n[dependency-groups]\n' + ''.join(
            f'{key} = {json.dumps(value)}\n' for key, value in data['dependency-groups'].items())
        (self.target / 'pyproject.toml').write_text(text)

    def test_a_package_removed_by_hand_is_not_removed_again_and_one_added_back_is_the_users(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        self.set_group('test', ['pytest'])
        self.assertEqual(self.stack().returncode, 0)
        self.assertEqual(self.saved()['owned_packages']['python:test'], ['pytest'])
        self.set_group('test', ['pytest', 'pytest-cov'])
        self.log.unlink()
        run = self.stack('--without', 'test')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.tools_run(), ['uv remove --group test pytest'])
        self.assertIn('Left pytest-cov in the project for python', run.stderr)

    def test_a_failed_add_drops_its_claim_at_once(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        run = self.stack('--profile', 'python', '--defaults', env={'FAIL_TOOL': 'test'})
        self.assertNotEqual(run.returncode, 0)
        self.assertNotIn('python:test', self.saved()['owned_packages'])
        self.assertEqual(self.tool_names('pending_tools'), ['test'])

    def test_an_unreadable_project_file_fails_the_add_and_claims_nothing(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n\n[dependency-groups]\nlint = ["ruff"]\nbroken = [\n')
        run = self.stack('--profile', 'python', '--defaults')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('cannot read pyproject.toml', run.stderr)
        self.assertNotIn('owned_packages', self.saved())
        self.assertIn('lint', self.tool_names('pending_tools'))
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n\n[dependency-groups]\nlint = ["ruff"]\n')
        self.assertEqual(self.stack().returncode, 0)
        self.assertEqual(self.saved()['owned_packages']['python:lint'], ['pre-commit'])
        self.log.unlink()
        self.assertEqual(self.stack('--without', 'lint').returncode, 0)
        self.assertEqual(self.tools_run(), ['uv remove --group lint pre-commit'])

    def test_an_unreadable_project_file_keeps_the_tool_for_a_later_removal(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        good = (self.target / 'pyproject.toml').read_text()
        (self.target / 'pyproject.toml').write_text(good + 'broken = [\n')
        run = self.stack('--without', 'lint')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('lint', self.tool_names('tools'))
        (self.target / 'pyproject.toml').write_text(good)
        self.log.unlink()
        self.assertEqual(self.stack('--without', 'lint').returncode, 0)
        self.assertEqual(self.tools_run(), ['uv remove --group lint ruff pre-commit'])

    def test_an_interrupt_before_the_write_keeps_no_claim(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        run = self.stack('--profile', 'python', '--defaults', env={'INT_BEFORE': 'test'})
        self.assertNotEqual(run.returncode, 0)
        self.assertNotIn('python:test', self.saved()['owned_packages'])
        self.set_group('test', ['pytest'])
        self.assertEqual(self.stack().returncode, 0)
        self.assertEqual(self.saved()['owned_packages']['python:test'], ['pytest-cov'])
        self.log.unlink()
        self.assertEqual(self.stack('--without', 'test').returncode, 0)
        self.assertEqual(self.tools_run(), ['uv remove --group test pytest-cov'])

    def test_an_npm_package_moved_to_dependencies_is_the_users(self):
        self.assertEqual(self.stack('--profile', 'typescript', '--defaults').returncode, 0)
        data = json.loads((self.target / 'package.json').read_text())
        data['dependencies'] = {'typescript': data['devDependencies'].pop('typescript')}
        (self.target / 'package.json').write_text(json.dumps(data))
        self.log.unlink()
        run = self.stack('--without', 'typecheck')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.tools_run(), ['uvx --from nodejs-wheel==24.* npm uninstall @types/node'])
        self.assertIn('Left typescript in the project for typescript', run.stderr)

    def test_npm_leaves_a_package_that_the_user_also_lists_elsewhere(self):
        self.assertEqual(self.stack('--profile', 'typescript', '--defaults').returncode, 0)
        data = json.loads((self.target / 'package.json').read_text())
        data['peerDependencies'] = {'typescript': '>=5'}
        (self.target / 'package.json').write_text(json.dumps(data))
        self.log.unlink()
        run = self.stack('--without', 'typecheck')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.tools_run(), ['uvx --from nodejs-wheel==24.* npm uninstall @types/node'])
        self.assertEqual(json.loads((self.target / 'package.json').read_text())['peerDependencies'], {'typescript': '>=5'})

    def test_npm_leaves_a_package_that_the_user_bundles(self):
        self.assertEqual(self.stack('--profile', 'typescript', '--defaults').returncode, 0)
        data = json.loads((self.target / 'package.json').read_text())
        data['bundleDependencies'] = ['typescript']
        (self.target / 'package.json').write_text(json.dumps(data))
        self.log.unlink()
        self.assertEqual(self.stack('--without', 'typecheck').returncode, 0)
        self.assertEqual(self.tools_run(), ['uvx --from nodejs-wheel==24.* npm uninstall @types/node'])
        self.assertEqual(json.loads((self.target / 'package.json').read_text())['bundleDependencies'], ['typescript'])

    def test_a_dry_run_that_would_fail_exits_1(self):
        (self.target / 'pyproject.toml').write_text('broken = [\n')
        run = self.stack('--profile', 'python', '--defaults', '--dry-run')
        self.assertEqual(run.returncode, 1)
        self.assertIn('Would fail: cannot read pyproject.toml', run.stdout)
        self.assertIn('the real run would fail', run.stdout)
        self.assertNotIn('would be installed', run.stdout)

    def test_a_removal_that_failed_is_finished_by_the_next_run_or_cancelled_by_naming_the_tool(self):
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n')
        self.assertEqual(self.stack('--profile', 'python', '--defaults').returncode, 0)
        good = (self.target / 'pyproject.toml').read_text()
        (self.target / 'pyproject.toml').write_text(good + 'broken = [\n')
        self.assertNotEqual(self.stack('--without', 'lint').returncode, 0)
        self.assertEqual(self.saved()['removing_tools'], ['python:lint'])
        (self.target / 'pyproject.toml').write_text(good)
        self.log.unlink()
        self.assertEqual(self.stack().returncode, 0)
        self.assertEqual(self.tools_run(), ['uv remove --group lint ruff pre-commit'])
        self.assertNotIn('lint', self.tool_names('tools'))
        self.assertNotIn('removing_tools', self.saved())
        # Naming the tool again cancels an unfinished removal.
        self.assertEqual(self.stack('lint').returncode, 0)
        (self.target / 'pyproject.toml').write_text((self.target / 'pyproject.toml').read_text() + 'broken = [\n')
        self.assertNotEqual(self.stack('--without', 'lint').returncode, 0)
        (self.target / 'pyproject.toml').write_text((self.target / 'pyproject.toml').read_text().replace('broken = [\n', ''))
        self.assertEqual(self.stack('lint').returncode, 0)
        self.assertIn('lint', self.tool_names('tools'))
        self.assertNotIn('removing_tools', self.saved())

    def test_a_dry_run_also_stops_on_a_symlinked_project_file(self):
        (self.target / 'pyproject.toml').symlink_to(self.base / 'outside.toml')
        run = self.stack('--profile', 'python', '--defaults', '--dry-run')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('is a symlink', run.stderr)

    def test_registry_paths_refuse_dot_git_and_outside_guards(self):
        for change in (lambda r: r['packages'][1].update(record='.'),
                       lambda r: r['packages'][1].update(record='a/./b.json')):
            with self.assertRaises(install_stack.StackError):
                install_stack.load_registry(self.registry_with(change))
        _, choices, _, _ = install_stack.load_registry(REPO / 'stack.json')
        for change in (lambda p: p.update(init={'file': '.git/x', 'command': ['true']}),
                       lambda p: p['tools']['lint']['files'].update({'vendor/.git/hooks/x': 'profiles/python/ruff.toml'}),
                       lambda p: p['tools']['lint'].update(skip_if={'ruff.toml': ['../x', 'y']}),
                       lambda p: p['base'].update(files={'.gitignore': '../x'}),
                       lambda p: p['tools']['lint'].update(recipes='../lint.just'),
                       lambda p: p['tools']['lint'].update(recipes='sub/lint.just')):
            profile = json.loads(json.dumps(PROFILES['python']))
            change(profile)
            with self.assertRaises(install_stack.StackError):
                install_stack.check_profile('python', profile, choices)

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

    def test_an_existing_justfile_is_kept_and_gets_the_import_line(self):
        (self.target / 'justfile').write_text('test:\n    echo hi\n')
        run = self.stack('--profile', 'python')
        self.assertIn("Changed justfile: adds export JLOG_RUN", run.stderr)
        self.assertTrue((self.target / 'justfile').read_text().endswith("\nimport? '.agents/install.just'\n"
                                                                        + install_stack.JLOG_LINE + "\n"
                                                                        "# end lines of jnterfaces. They stay while the "
                                                                        "project keeps .agents/stack.json.\n\n"
                                                                        "test:\n    echo hi\n"))
        self.assertEqual(sorted(os.listdir(self.target)).count('Justfile') + sorted(os.listdir(self.target)).count(
            'justfile'), 1)
        self.assertIn('justfile', self.saved()['parts'])
        # A started Justfile that lost the line is named, not changed.
        record = self.saved()
        record['started'] = True
        del record['parts']['justfile']
        (self.target / '.agents/stack.json').write_text(json.dumps(record))
        (self.target / 'justfile').write_text('test:\n    echo hi\n')
        run = self.stack()
        self.assertIn("Add this line to justfile, so that just install works: import? '.agents/install.just'", run.stderr)
        self.assertEqual((self.target / 'justfile').read_text(), 'test:\n    echo hi\n')

    def test_a_tool_needs_a_profile_that_has_it(self):
        run = self.stack('lint')
        self.assertIn('no active profile has the tool: lint', run.stderr)
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
        self.assertIn('Unknown name, or no active profile has the tool: nope', run.stderr)
        self.assertIn('The roles are: cx, jagt-protocol, tasker, prose, reporter', run.stderr)
        self.assertIn('cannot be both turned on and turned off', self.stack('cx', '--without', 'cx').stderr)
        self.assertIn('--all or --defaults', self.stack('--all', '--defaults').stderr)
        self.assertIn('--clone needs NAME=DIR', self.stack('--defaults', '--clone', 'nossaifd').stderr)
        self.assertIn('--clone names no package: lanes', self.stack('--defaults', '--clone', 'lanes=/x').stderr)
        self.assertIn('does not go with --all', self.stack('--ask', '--all').stderr)
        self.assertIn('Unknown profile(s): cobol', self.stack('--profile', 'cobol').stderr)
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

    def test_a_named_role_asks_nothing_on_a_terminal(self):
        _, choices, _, _ = install_stack.load_registry(REPO / 'stack.json')
        for on, off in ((['prose'], []), ([], ['tasker'])):
            args = mock.Mock(with_=on, without=off, all=False, defaults=False, ask=False)
            with mock.patch('sys.stdin', io.StringIO('')), mock.patch('sys.stderr', io.StringIO()) as err:
                chosen = install_stack.resolve(args, choices, None, True)
            self.assertNotIn('Install ', err.getvalue())
            self.assertEqual({name for name, value in chosen.items() if value}, set(on))
        # A named tool and no role still asks about the roles.
        args = mock.Mock(with_=[], without=[], all=False, defaults=False, ask=False)
        with mock.patch('sys.stdin', io.StringIO('\n' * 5)), mock.patch('sys.stderr', io.StringIO()) as err:
            install_stack.resolve(args, choices, None, True, implied=True)
        self.assertEqual(err.getvalue().count('Install '), 5)

    def test_questions_stop_when_the_terminal_closes(self):
        _, choices, _, _ = install_stack.load_registry(REPO / 'stack.json')
        args = mock.Mock(with_=[], without=[], all=False, defaults=False, ask=True)
        with mock.patch('sys.stdin', io.StringIO('y\n')), mock.patch('sys.stderr', io.StringIO()):
            with self.assertRaises(install_stack.StackError):
                install_stack.resolve(args, choices, {}, True)

    def test_a_malformed_registry_is_refused(self):
        registry = json.loads((REPO / 'stack.json').read_text())
        for change in (lambda r: r['aliases'].update(x='nope'),
                       lambda r: r.update(packages={'a': 1}), lambda r: r['profiles'].update(python=[]),
                       lambda r: r['profiles']['python'].pop('package'),
                       lambda r: r['profiles'].update(Bad=dict(r['profiles']['python'])),
                       lambda r: r['profiles'].update(prose=dict(r['profiles']['python'])),
                       lambda r: r['packages'][0]['optional_skills'].append({'about': 'no choice'}),
                       lambda r: r['packages'][0].pop('required_skills'),
                       lambda r: r['packages'][1].update(record='/etc/x.json'),
                       lambda r: r['packages'][1].update(record='.GIT/config'),
                       lambda r: r['packages'][1].update(needs='jnterfaces'),
                       lambda r: r['packages'][2].pop('about'),
                       lambda r: r['packages'][0]['optional_skills'].append({'choice': 'tasker', 'about': 'twice'}),
                       lambda r: r['packages'][2]['drop_with'].update(nope={'files': ['x'], 'why': 'w'}),
                       lambda r: r['packages'][2]['drop_with'].update(prose={'files': ['x'], 'why': 'w'}),
                       lambda r: r['packages'][2]['drop_with']['tasker'].update(files=['../x']),
                       lambda r: r['packages'][2]['drop_with']['tasker'].update(files=[]),
                       lambda r: r['packages'][2]['drop_with']['tasker'].pop('why'),
                       lambda r: r['packages'][0].update(drop_with={'tasker': {'files': ['x'], 'why': 'w'}})):
            broken = json.loads(json.dumps(registry))
            change(broken)
            path = self.base / 'broken.json'
            path.write_text(json.dumps(broken))
            with self.assertRaises(install_stack.StackError):
                install_stack.load_registry(path)
        _, choices, _, _ = install_stack.load_registry(REPO / 'stack.json')
        for change in (lambda p: p.update(add='uv add'), lambda p: p['tools'].update(cx={'packages': ['x']}) or
                       p['tools']['cx'].update(recipes=3),
                       lambda p: p.update(default_tools=['nope']), lambda p: p.update(name='other'),
                       lambda p: p['tools']['lint']['files'].update({'../x': 'profiles/python/ruff.toml'}),
                       lambda p: p['init'].update(command=[]), lambda p: p['tools']['lint'].update(group=3),
                       lambda p: p['tools']['lint'].update(skip_if={'ruff.toml': 'x'}),
                       lambda p: p.update(uv_default_groups={'a': 1}), lambda p: p.pop('manifest'),
                       lambda p: p['tools']['lint'].update(verify=['fmt "--check"']),
                       lambda p: p['tools']['lint'].update(verify='lint'), lambda p: p['tools'].update(Bad={'packages': ['x']}),
                       lambda p: p.update(choices={'nope': True}), lambda p: p.update(tools={})):
            profile = json.loads(json.dumps(PROFILES['python']))
            change(profile)
            with self.assertRaises(install_stack.StackError):
                install_stack.check_profile('python', profile, choices)
        self.assertEqual(install_stack.check_profile('python', json.loads(json.dumps(PROFILES['python'])), choices)['name'],
                         'python')

    def test_the_readme_table_matches_stack_json(self):
        _, choices, _, _ = install_stack.load_registry(REPO / 'stack.json')
        readme = (REPO / 'README.md').read_text()
        rows = dict(re.findall(r'^\| `([a-z-]+)` \| [^|]+ \| [^|]+ \| (yes|no) \|$', readme, re.M))
        self.assertEqual(rows, {name: 'yes' if entry.get('default') else 'no' for name, entry in choices.items()})

    def test_the_starter_justfile_imports_the_recipe_file_and_parses(self):
        template = (REPO / 'templates/project.just').read_text()
        self.assertIn("import? '.agents/install.just'", template)
        if shutil.which('just'):
            run = subprocess.run(['just', '--justfile', REPO / 'templates/project.just', '--summary'],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)


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
            # The hooks of tasker go. A role that stays, such as prose, may keep its own hooks in the file.
            codex = target / '.codex/hooks.json'
            self.assertTrue(not codex.exists() or 'session-start' not in codex.read_text())


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

    def test_uninstall_works_in_a_folder_whose_name_has_a_quote_a_dollar_and_a_backtick(self):
        # A path in double quotes lets the shell expand $HOME and run `true`, so the path goes in single quotes.
        moved = self.base / 'it\'s $HOME `true` "q"'
        self.target.rename(moved)
        self.target = moved
        self.test_just_install_and_uninstall_run_the_installer()

    def test_a_project_variable_named_copier_still_works_and_jn_copier_runs_copier(self):
        (self.target / 'Justfile').write_text("import? '.agents/install.just'\ncopier := 'mine'\n\nhello:\n    @echo {{copier}}\n")
        run = self.just('hello')
        self.assertEqual((run.returncode, run.stdout.strip()), (0, 'mine'), run.stderr)
        run = self.just('--evaluate', 'jn_copier')
        self.assertEqual(run.stdout, "uvx --from 'copier==9.*' copier")

    def test_the_folder_of_packages_comes_from_skill_packages_dir(self):
        env = {key: value for key, value in os.environ.items() if key != 'JNTERFACES_DIR'}
        run = subprocess.run(['just', 'install', '--defaults', 'cx'], cwd=self.target, capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, env=dict(env, SKILL_PACKAGES_DIR=str(self.jn.parent),
                                                                HOME=str(self.base / 'home')))
        self.assertEqual(run.returncode, 0, run.stderr)
        record = json.loads((self.target / '.agents/jnterfaces-install.json').read_text())
        self.assertEqual(record['source_revision'], git(self.jn, 'rev-parse', 'HEAD'))
        self.assertFalse((self.base / 'home/.local/share/skill-packages').exists())


class PartsTests(StackTests):
    """The parts of profiles and roles, and the record of them in .agents/stack.json."""

    def role_with_parts(self):
        root = self.packages / 'agent_gest_git_skills'
        manifest = json.loads((root / 'skill-package.json').read_text())
        (root / 'rules.md').write_text('# Rules\n')
        (root / 'starter.md').write_text('# Project\n')
        manifest['install_extras'].update({'parts': {'AGENTS.md': 'rules.md', '.claude/settings.json': '.claude/settings.json'},
                                           'starter': {'AGENTS.md': 'starter.md'}})
        (root / 'skill-package.json').write_text(json.dumps(manifest))
        commit(root)

    def test_a_profile_part_adds_an_include_and_goes_with_its_tool(self):
        self.edit_profile('python', lambda p: p['tools']['lint'].update({'parts': {'ruff.toml': 'files/lint.toml'}}))
        run = self.stack('--profile', 'python', '--defaults', '--without', 'tasker,prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('extend = ".agents/config/python/lint.toml"', (self.target / 'ruff.toml').read_text())
        self.assertEqual((self.target / '.agents/config/python/lint.toml').read_text(), 'line-length = 120\n')
        parts = self.saved()['parts']
        self.assertEqual(parts['ruff.toml']['extend']['holder'], 'python:lint')
        self.assertEqual(self.stack('--check').returncode, 0)
        run = self.stack('--without', 'lint')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse((self.target / 'ruff.toml').exists())
        self.assertFalse((self.target / '.agents/config').exists())
        self.assertNotIn('ruff.toml', self.saved()['parts'])

    def test_a_skip_if_guard_leaves_the_part_out(self):
        self.edit_profile('python', lambda p: p['tools']['lint'].update({'parts': {'ruff.toml': 'files/lint.toml'}}))
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n\n[tool.ruff]\nline-length = 99\n')
        run = self.stack('--profile', 'python', '--defaults', '--without', 'tasker,prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse((self.target / 'ruff.toml').exists())
        self.assertIn('Left out the part of python:lint in ruff.toml', run.stderr)

    def pytest_part(self):
        """The test tool of the test profile gets the pythonpath part, with the guards of jnprofile-python."""
        def test(p):
            p['tools']['test']['parts'] = {'pyproject.toml': 'files/pytest.toml'}
            p['tools']['test']['skip_if'] = {'pyproject.toml': [
                ['pyproject.toml', '[tool.pytest]'], ['pytest.ini', ''], ['tox.ini', '[pytest]'],
                ['setup.cfg', '[tool:pytest]']]}
        self.edit_profile('python', test)
        (self.packages / 'jnprofile-python/files/pytest.toml').write_text(
            '[tool.pytest.ini_options]\npythonpath = ["."]\n')
        commit(self.packages / 'jnprofile-python')

    def test_a_skip_if_guard_list_leaves_out_the_part_when_any_guard_applies(self):
        self.pytest_part()
        for name, text, note in [('pytest.ini', '', 'the project has pytest.ini'),
                                 ('setup.cfg', '[tool:pytest]\naddopts = -q\n', 'setup.cfg already has [tool:pytest]'),
                                 ('pyproject.toml', '[project]\nname = "a"\n\n[tool.pytest]\naddopts = ["-q"]\n',
                                  'pyproject.toml already has [tool.pytest]')]:
            with self.subTest(name):
                self.target = self.base / f'target-{name}'
                self.target.mkdir()
                git(self.target, 'init', '-q')
                (self.target / name).write_text(text)
                run = self.stack('--profile', 'python', '--defaults', '--without', 'tasker,prose')
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertNotIn('ini_options', (self.target / 'pyproject.toml').read_text())
                self.assertIn(f'Left out the part of python:test in pyproject.toml: {note}', run.stderr)

    def test_a_symlinked_guard_file_stops_the_part(self):
        self.pytest_part()
        (self.base / 'shared-tox.ini').write_text('[pytest]\naddopts = -q\n')
        (self.target / 'tox.ini').symlink_to(self.base / 'shared-tox.ini')
        run = self.stack('--profile', 'python', '--defaults', '--without', 'tasker,prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('ini_options', (self.target / 'pyproject.toml').read_text())

    def test_a_tool_pytest_table_with_only_ini_options_does_not_stop_the_part(self):
        self.pytest_part()
        (self.target / 'pyproject.toml').write_text(
            '[project]\nname = "a"\n\n[tool.pytest.ini_options]\naddopts = "-q"\n')
        run = self.stack('--profile', 'python', '--defaults', '--without', 'tasker,prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('pythonpath = ["."]', (self.target / 'pyproject.toml').read_text())

    def test_a_pythonpath_string_of_yours_stays_and_the_rest_of_the_file_is_written(self):
        self.pytest_part()
        (self.target / 'pyproject.toml').write_text(
            '[project]\nname = "a"\n\n[tool.pytest.ini_options]\npythonpath = "src lib"\n')
        run = self.stack('--profile', 'python', '--defaults', '--without', 'tasker,prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        text = (self.target / 'pyproject.toml').read_text()
        self.assertIn('pythonpath = "src lib"', text)
        self.assertIn('default-groups = "all"', text)
        self.assertIn('tool.pytest.ini_options.pythonpath is "src lib", which is yours and stays. python:test would '
                      'add "." to a list there.', run.stderr)
        run = self.stack('--without', 'test')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('pythonpath = "src lib"', (self.target / 'pyproject.toml').read_text())

    def test_a_skip_if_guard_does_not_take_out_a_part_that_is_there(self):
        self.edit_profile('python', lambda p: p['tools']['lint'].update({'parts': {'ruff.toml': 'files/lint.toml'}}))
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n# no [tool.ruff here yet\n')
        run = self.stack('--profile', 'python', '--defaults', '--without', 'tasker,prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('extend =', (self.target / 'ruff.toml').read_text())
        with (self.target / 'pyproject.toml').open('a') as file:
            file.write('\n[tool.ruff]\nline-length = 100\n')
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('extend =', (self.target / 'ruff.toml').read_text())
        self.assertTrue((self.target / '.agents/config/python/lint.toml').exists())

    def test_an_own_file_that_git_ignores_stops_the_run_before_the_package_commands(self):
        self.edit_profile('python', lambda p: p['tools']['lint'].update({'parts': {'ruff.toml': 'files/lint.toml'}}))
        (self.target / '.gitignore').write_text('.agents/config/\n')
        run = self.stack('--profile', 'python', '--defaults', '--without', 'tasker,prose')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('git ignores the own file', run.stderr)
        self.assertFalse((self.target / 'pyproject.toml').exists())
        self.assertEqual((self.target / '.gitignore').read_text(), '.agents/config/\n')

    def test_a_guard_counts_its_text_only_at_the_start_of_a_line(self):
        guard = ('pyproject.toml', '[tool.ruff')
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n# no [tool.ruff here yet\n')
        self.assertFalse(install_stack.guard_applies(self.target, guard))
        (self.target / 'pyproject.toml').write_text('[project]\nname = "a"\n  [tool.ruff.lint]\n')
        self.assertTrue(install_stack.guard_applies(self.target, guard))
        for mine in ('[ tool.ruff ]\n', '[tool]\nruff.line-length = 99\n', 'tool.ruff.x = 1\n',
                     '["tool".ruff]\n'):
            (self.target / 'pyproject.toml').write_text(mine)
            self.assertTrue(install_stack.guard_applies(self.target, guard), mine)
        (self.target / 'pyproject.toml').write_text('[tool.ruff-x]\na = 1\n')
        self.assertFalse(install_stack.guard_applies(self.target, guard))

    WORKFLOW = '.github/workflows/nossaifd-prose.yml'

    def prose_with_workflow(self):
        """nossaifd with its prose workflow as a seed and as a starter, as the real package ships it."""
        root = self.packages / 'nossaifd'
        manifest = json.loads((root / 'skill-package.json').read_text())
        source = '.agents/skills/nossaifd/assets/prose-check.yml'
        (root / source).parent.mkdir(parents=True, exist_ok=True)
        (root / source).write_text('name: prose\n')
        manifest['install_extras'].update(seed={self.WORKFLOW: source}, starter={self.WORKFLOW: source})
        (root / 'skill-package.json').write_text(json.dumps(manifest))
        commit(root)

    def test_the_tasker_role_drops_the_prose_workflow_and_a_reinstall_does_not_write_it(self):
        self.prose_with_workflow()
        workflow = self.target / self.WORKFLOW
        run = self.stack('prose', '--without', 'tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(workflow.read_text(), 'name: prose\n')
        self.assertIn('hash', self.saved()['parts'][self.WORKFLOW]['(starter)'])
        # A dry run says so and changes nothing.
        run = self.stack('tasker', '--dry-run')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn(f'Would remove {self.WORKFLOW}', run.stdout)
        self.assertTrue(workflow.exists())
        run = self.stack('tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn(f'Removed {self.WORKFLOW}, which the installer wrote: the tasker role is on', run.stderr)
        self.assertFalse(workflow.exists())
        self.assertFalse((self.target / '.github').exists())
        self.assertNotIn(self.WORKFLOW, self.saved()['parts'])
        # A reinstall of every role, or of prose alone, while tasker is on writes no workflow.
        for args in ((), ('prose',), ('--defaults',)):
            run = self.stack(*args)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertFalse(workflow.exists(), args)
        # Without tasker, the starter of prose writes the workflow again.
        run = self.stack('--without', 'tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(workflow.read_text(), 'name: prose\n')

    def test_a_prose_workflow_that_you_deleted_stays_deleted_after_tasker_comes_and_goes(self):
        self.prose_with_workflow()
        workflow = self.target / self.WORKFLOW
        self.assertEqual(self.stack('prose', '--without', 'tasker').returncode, 0)
        workflow.unlink()
        for args in (('--without', 'tasker'), ('tasker',), ('--without', 'tasker')):
            run = self.stack(*args)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertFalse(workflow.exists(), args)

    def test_a_hashless_starter_that_differs_is_kept_without_blaming_you(self):
        self.prose_with_workflow()
        workflow = self.target / self.WORKFLOW
        self.assertEqual(self.stack('prose', '--without', 'tasker').returncode, 0)
        record = self.saved()
        del record['parts'][self.WORKFLOW]['(starter)']['hash']
        (self.target / '.agents/stack.json').write_text(json.dumps(record))
        workflow.write_text('name: other\n')
        run = self.stack('tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('the installer cannot tell whether you changed it', run.stderr)
        self.assertEqual(workflow.read_text(), 'name: other\n')

    def test_with_tasker_on_from_the_start_the_prose_workflow_is_never_written(self):
        self.prose_with_workflow()
        run = self.stack('--defaults')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse((self.target / self.WORKFLOW).exists())
        self.assertNotIn(self.WORKFLOW, self.saved()['parts'])

    def test_an_edited_prose_workflow_stays_when_tasker_comes(self):
        self.prose_with_workflow()
        workflow = self.target / self.WORKFLOW
        self.assertEqual(self.stack('prose', '--without', 'tasker').returncode, 0)
        workflow.write_text('name: mine\n')
        run = self.stack('tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn(f'Kept {self.WORKFLOW}: you changed it, so it stays yours', run.stderr)
        self.assertEqual(workflow.read_text(), 'name: mine\n')
        self.assertEqual(self.saved()['parts'][self.WORKFLOW]['(file)'], {'state': 'kept', 'needs': ['prose']})
        # The note comes once, and the file stays yours after tasker goes too.
        run = self.stack()
        self.assertNotIn('Kept ' + self.WORKFLOW, run.stderr)
        self.assertEqual(self.stack('--without', 'tasker').returncode, 0)
        self.assertEqual(workflow.read_text(), 'name: mine\n')

    def test_an_edited_prose_workflow_that_you_delete_stays_deleted_after_tasker_goes(self):
        self.prose_with_workflow()
        workflow = self.target / self.WORKFLOW
        self.assertEqual(self.stack('prose', '--without', 'tasker').returncode, 0)
        workflow.write_text('name: mine\n')
        self.assertEqual(self.stack('tasker').returncode, 0)
        # The note said that you may delete it. You do, and it stays deleted while prose is on.
        workflow.unlink()
        for args in ((), ('--without', 'tasker'), ('--without', 'tasker')):
            run = self.stack(*args)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertFalse(workflow.exists(), args)

    def test_a_kept_seed_that_you_delete_stays_deleted_after_tasker_goes(self):
        self.prose_with_workflow()
        workflow = self.target / self.WORKFLOW
        self.assertEqual(self.stack('prose', '--without', 'tasker').returncode, 0)
        record = self.saved()
        record['parts'][self.WORKFLOW] = {'(seed)': {'state': 'seeded', 'holder': 'prose', 'needs': ['prose'],
                                                      'hash': 'x'}}
        (self.target / '.agents/stack.json').write_text(json.dumps(record))
        workflow.write_text('name: mine\n')
        run = self.stack('tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn(f'Kept {self.WORKFLOW}', run.stderr)
        self.assertEqual(sorted(self.saved()['parts'][self.WORKFLOW]), ['(file)', '(starter)'])
        workflow.unlink()
        self.assertEqual(self.stack('--without', 'tasker').returncode, 0)
        self.assertFalse(workflow.exists())

    def test_a_prose_workflow_that_prose_left_behind_stays_when_tasker_comes(self):
        self.prose_with_workflow()
        workflow = self.target / self.WORKFLOW
        self.assertEqual(self.stack('prose', '--without', 'tasker').returncode, 0)
        self.assertEqual(self.stack('--without', 'prose,tasker').returncode, 0)
        self.assertTrue(workflow.exists())
        # Without prose, just check runs no prose-check, so the reason of the rule does not hold.
        run = self.stack('tasker', '--without', 'prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn(f'Removed {self.WORKFLOW}', run.stderr)
        self.assertEqual(workflow.read_text(), 'name: prose\n')

    def test_a_run_that_stopped_after_it_dropped_the_workflow_lets_the_workflow_come_back(self):
        self.prose_with_workflow()
        workflow = self.target / self.WORKFLOW
        self.assertEqual(self.stack('prose', '--without', 'tasker').returncode, 0)
        # A run with tasker that stopped after it removed the workflow and before it saved the record.
        old = self.saved()['parts'][self.WORKFLOW]
        (self.target / '.agents/stack-pending.json').write_text(
            json.dumps({'files': {self.WORKFLOW: {'old': old, 'new': {}}}}))
        workflow.unlink()
        run = self.stack('tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn(self.WORKFLOW, self.saved()['parts'])
        run = self.stack('--without', 'tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(workflow.read_text(), 'name: prose\n')

    def test_a_prose_workflow_of_yours_stays_when_tasker_comes(self):
        self.prose_with_workflow()
        workflow = self.target / self.WORKFLOW
        workflow.parent.mkdir(parents=True)
        workflow.write_text('name: prose\n')
        self.assertEqual(self.stack('prose', '--without', 'tasker').returncode, 0)
        self.assertNotIn(self.WORKFLOW, self.saved()['parts'])
        self.assertEqual(self.stack('tasker').returncode, 0)
        self.assertEqual(workflow.read_text(), 'name: prose\n')

    def test_a_starter_record_without_a_hash_counts_when_the_file_is_as_the_source_ships_it(self):
        self.prose_with_workflow()
        self.assertEqual(self.stack('prose', '--without', 'tasker').returncode, 0)
        record = self.saved()
        del record['parts'][self.WORKFLOW]['(starter)']['hash']
        (self.target / '.agents/stack.json').write_text(json.dumps(record))
        run = self.stack('tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse((self.target / self.WORKFLOW).exists())

    def test_the_tasker_role_drops_a_prose_workflow_that_the_old_seed_wrote(self):
        # An install of the old installer: the package record of nossaifd holds the seed, and the record of the
        # stack has no parts yet.
        self.prose_with_workflow()
        self.assertEqual(self.stack('prose', '--without', 'tasker').returncode, 0)
        workflow = self.target / self.WORKFLOW
        record = self.saved()
        del record['parts'], record['parts_version']
        (self.target / '.agents/stack.json').write_text(json.dumps(record))
        own = self.target / RECORDS['nossaifd']
        data = json.loads(own.read_text())
        data['seeds'] = {self.WORKFLOW: hashlib.sha256(workflow.read_bytes()).hexdigest()}
        own.write_text(json.dumps(data))
        run = self.stack('tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(workflow.exists())
        self.assertNotIn(self.WORKFLOW, self.saved()['parts'])
        self.assertFalse(json.loads(own.read_text()).get('seeds'))
        self.assertEqual(self.stack().returncode, 0)
        self.assertFalse(workflow.exists())

    def test_the_installer_writes_the_two_lists_empty_once_in_a_project_it_starts(self):
        run = self.stack('--defaults')
        self.assertEqual(run.returncode, 0, run.stderr)
        listed = [self.target / '.agents/generated.txt', self.target / '.agents/coverage-exclude.txt']
        self.assertEqual([path.read_text() for path in listed], ['', ''])
        # The lists are yours: a run keeps what you add, and a list that you delete stays deleted.
        listed[0].write_text('docs/api/\n')
        listed[1].unlink()
        for _ in range(2):
            run = self.stack()
            self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(listed[0].read_text(), 'docs/api/\n')
        self.assertFalse(listed[1].exists())

    def test_a_project_that_the_installer_did_not_start_gets_no_lists(self):
        (self.target / 'Justfile').write_text("import? '.agents/install.just'\n")
        run = self.stack('--defaults')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse((self.target / '.agents/generated.txt').exists())
        self.assertFalse((self.target / '.agents/coverage-exclude.txt').exists())

    def test_a_role_adds_its_parts_and_a_starter_and_takes_them_out(self):
        self.role_with_parts()
        settings = self.target / '.claude/settings.json'
        settings.parent.mkdir()
        settings.write_text(json.dumps({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'mine.sh'}]}]}}))
        run = self.stack('tasker', '--without', 'prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        agents = (self.target / 'AGENTS.md').read_text()
        self.assertIn('Read and follow @.agents/instructions/tasker.md', agents)
        self.assertTrue(agents.endswith('\n\n# Project\n'))
        self.assertEqual((self.target / '.agents/instructions/tasker.md').read_text(), '# Rules\n')
        data = json.loads(settings.read_text())
        self.assertEqual(data['hooks']['SessionStart'][0]['hooks'][0]['command'], 'start.sh')
        self.assertEqual(len(data['hooks']['SessionStart']), 1)
        record = json.loads((self.target / '.agents/gest-git-install.json').read_text())
        self.assertFalse(record.get('merged_hooks'))
        run = self.stack('--without', 'tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual((self.target / 'AGENTS.md').read_text(), '# Project\n')
        self.assertEqual(json.loads(settings.read_text()),
                         {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'mine.sh'}]}]}})
        self.assertFalse((self.target / '.agents/instructions').exists())

    def test_the_first_run_moves_the_old_log_and_the_hooks_of_a_role(self):
        # An install of the old installer: a log with the claims of python, and hooks that the role merged.
        self.assertEqual(self.stack('tasker', '--without', 'prose').returncode, 0)
        old = json.loads((self.target / '.agents/gest-git-install.json').read_text())
        self.assertTrue(old.get('merged_hooks'))
        record = self.saved()
        del record['parts'], record['parts_version']
        (self.target / '.agents/stack.json').write_text(json.dumps(record))
        (self.target / '.agents/stack-log.jsonl').write_text(
            json.dumps({'id': 'a', 'status': 'started', 'lines': {'.gitignore': {'jnterfaces': {'added': ['x/']}}}})
            + '\n' + json.dumps({'id': 'a', 'status': 'finished'}) + '\n')
        (self.target / '.gitignore').write_text('# begin lines of jnterfaces, the installer, from jnterfaces at abc.\n'
                                                'x/\n# end lines of jnterfaces. They stay while x.\n')
        self.role_with_parts()
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('Moved the record of the shared files', run.stderr)
        saved = self.saved()
        self.assertEqual(saved['parts_log_end'], 'a')
        self.assertNotIn('x', saved['parts']['.gitignore'])
        self.assertNotIn('x/', self.rules())
        units = saved['parts']['.claude/settings.json']
        self.assertEqual([entry['holder'] for entry in units.values()], ['tasker'])
        record = json.loads((self.target / '.agents/gest-git-install.json').read_text())
        self.assertFalse(record.get('merged_hooks'))
        # A second run reads the finished entry of the last id as part of the move, not as a newer entry.
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        # An old installer on another branch adds an entry to the log after the move: the run stops.
        with (self.target / '.agents/stack-log.jsonl').open('a') as log:
            log.write(json.dumps({'id': 'b', 'status': 'started', 'lines': {}}) + '\n')
        run = self.stack()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('an entry from an old installer after the move', run.stderr)

    def test_a_first_run_that_stops_in_the_dry_pass_does_not_save_the_move(self):
        self.edit_profile('python', lambda p: p['tools']['lint'].update({'parts': {'ruff.toml': 'files/lint.toml'}}))
        self.assertEqual(self.stack('--profile', 'python', '--defaults', '--without', 'tasker,prose').returncode, 0)
        record = self.saved()
        del record['parts'], record['parts_version']
        (self.target / '.agents/stack.json').write_text(json.dumps(record))
        (self.target / '.agents/stack-log.jsonl').write_text(
            json.dumps({'id': 'a', 'status': 'started', 'lines': {}}) + '\n'
            + json.dumps({'id': 'a', 'status': 'finished'}) + '\n')
        with (self.target / '.gitignore').open('a') as file:
            file.write('.agents/config/\n')
        run = self.stack()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('git ignores the own file', run.stderr)
        # The next run is still the first run after the old installer.
        self.assertNotIn('parts_version', self.saved())

    def test_save_keeps_the_keys_that_it_does_not_own(self):
        self.assertEqual(self.stack('tasker', '--without', 'prose').returncode, 0)
        record = self.saved()
        record['mine'] = {'kept': True}
        (self.target / '.agents/stack.json').write_text(json.dumps(record))
        self.assertEqual(self.stack().returncode, 0)
        self.assertEqual(self.saved()['mine'], {'kept': True})

    def test_a_source_with_a_folder_path_or_a_plain_tsconfig_path_stops_the_run(self):
        def ruff(p):
            p['tools']['lint']['parts'] = {'ruff.toml': 'files/lint.toml'}
        self.edit_profile('python', ruff)
        (self.packages / 'jnprofile-python/files/lint.toml').write_text('extend-exclude = ["legacy/x.py"]\n')
        commit(self.packages / 'jnprofile-python')
        run = self.stack('--profile', 'python', '--defaults', '--without', 'tasker,prose')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('names the path legacy/x.py', run.stderr)

        def ts(p):
            p['tools']['typecheck'] = {'packages': ['typescript'], 'parts': {'tsconfig.json': 'files/tsconfig.json'}}
        self.edit_profile('typescript', ts)
        (self.packages / 'jnprofile-typescript/files/tsconfig.json').write_text('{"include": ["src"]}\n')
        commit(self.packages / 'jnprofile-typescript')
        run = self.stack('--profile', 'typescript', '--defaults', '--without', 'tasker,prose', 'typecheck')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('does not start with ${configDir}', run.stderr)

    def test_a_part_of_an_unknown_file_is_rejected(self):
        self.edit_profile('python', lambda p: p['tools']['lint'].update({'parts': {'setup.cfg': 'files/x.toml'}}))
        run = self.stack('--profile', 'python', '--defaults', '--without', 'tasker,prose')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('profile.json of the python profile is malformed', run.stderr)

    def test_a_claude_md_imports_agents_md_once_and_stays_yours(self):
        self.role_with_parts()
        claude = self.target / 'CLAUDE.md'
        run = self.stack('tasker', '--without', 'prose', '--dry-run')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('Would write CLAUDE.md, which imports AGENTS.md with the line @AGENTS.md', run.stdout)
        self.assertFalse(claude.exists())
        run = self.stack('tasker', '--without', 'prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(claude.read_text(), '@AGENTS.md\n')
        self.assertIn('Wrote CLAUDE.md, which imports AGENTS.md', run.stdout)
        claude.write_text('@AGENTS.md\n\nMy notes.\n')
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('CLAUDE.md', run.stdout + run.stderr)
        # The role goes, and AGENTS.md and CLAUDE.md stay: both are the user's files.
        run = self.stack('--without', 'tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual((self.target / 'AGENTS.md').read_text(), '# Project\n')
        self.assertEqual(claude.read_text(), '@AGENTS.md\n\nMy notes.\n')

    def test_a_claude_md_of_the_user_is_named_when_it_does_not_import_agents_md(self):
        self.role_with_parts()
        claude = self.target / 'CLAUDE.md'
        claude.write_text('# Mine\n')
        run = self.stack('tasker', '--without', 'prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('CLAUDE.md does not import AGENTS.md', run.stderr)
        self.assertEqual(claude.read_text(), '# Mine\n')
        # A symlink is a choice of the user, so the installer neither writes it nor names it.
        claude.unlink()
        claude.symlink_to('AGENTS.md')
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('CLAUDE.md', run.stdout + run.stderr)
        self.assertTrue(claude.is_symlink())

    def role_with_only_a_part(self):
        """tasker with a part in AGENTS.md and no starter, so that AGENTS.md goes when the role goes."""
        root = self.packages / 'agent_gest_git_skills'
        manifest = json.loads((root / 'skill-package.json').read_text())
        (root / 'rules.md').write_text('# Rules\n')
        manifest['install_extras'].update({'parts': {'AGENTS.md': 'rules.md'}})
        manifest['install_extras'].pop('seed', None)
        (root / 'skill-package.json').write_text(json.dumps(manifest))
        commit(root)

    def test_a_dry_run_says_claude_md_only_when_agents_md_stays(self):
        self.role_with_only_a_part()
        self.assertEqual(self.stack('tasker', '--without', 'prose').returncode, 0)
        self.assertTrue((self.target / 'AGENTS.md').is_file())
        (self.target / 'CLAUDE.md').unlink()
        run = self.stack('--without', 'tasker', '--dry-run')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('Would write CLAUDE.md', run.stdout)
        run = self.stack('--without', 'tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse((self.target / 'AGENTS.md').exists())
        self.assertFalse((self.target / 'CLAUDE.md').exists())

    def test_a_claude_md_that_imports_a_gone_agents_md_is_named(self):
        self.role_with_only_a_part()
        self.assertEqual(self.stack('tasker', '--without', 'prose').returncode, 0)
        run = self.stack('--without', 'tasker', '--dry-run')
        self.assertIn('CLAUDE.md imports AGENTS.md, and the project would have no AGENTS.md', run.stderr)
        run = self.stack('--without', 'tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse((self.target / 'AGENTS.md').exists())
        self.assertIn('CLAUDE.md imports AGENTS.md, and the project has no AGENTS.md after this run', run.stderr)
        self.assertEqual((self.target / 'CLAUDE.md').read_text(), '@AGENTS.md\n')
        # Only the run that removes AGENTS.md says so.
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('CLAUDE.md', run.stdout + run.stderr)

    def test_a_claude_md_that_cannot_be_read_never_stops_the_run(self):
        self.role_with_parts()
        claude = self.target / 'CLAUDE.md'
        claude.write_text('# Mine\n')
        claude.chmod(0)
        self.addCleanup(claude.chmod, 0o644)
        run = self.stack('tasker', '--without', 'prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('Cannot read or write CLAUDE.md', run.stderr)
        self.assertNotIn('Traceback', run.stderr)
        self.assertFalse((self.target / '.agents/stack-pending.json').exists())
        self.assertTrue(self.saved()['choices']['tasker'])

    def test_an_import_in_dot_claude_counts_only_from_its_folder(self):
        self.role_with_parts()
        inner = self.target / '.claude/CLAUDE.md'
        inner.parent.mkdir()
        # An import resolves from the folder of its file, so @AGENTS.md in .claude/ is .claude/AGENTS.md.
        inner.write_text('See @AGENTS.md and @./AGENTS.md for the rules.\n')
        run = self.stack('tasker', '--without', 'prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual((self.target / 'CLAUDE.md').read_text(), '@AGENTS.md\n')
        (self.target / 'CLAUDE.md').unlink()
        inner.write_text('See @../AGENTS.md for the rules.\n')
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse((self.target / 'CLAUDE.md').exists())
        self.assertNotIn('CLAUDE.md', run.stdout + run.stderr)
        # An import in a code block is an example, not an import.
        inner.unlink()
        (self.target / 'CLAUDE.md').write_text('```\n@AGENTS.md\n```\n')
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('CLAUDE.md does not import AGENTS.md', run.stderr)
        (self.target / 'CLAUDE.md').write_text('# Mine\n\n    @AGENTS.md\n')
        run = self.stack()
        self.assertIn('CLAUDE.md does not import AGENTS.md', run.stderr)
        # An import at the end of a sentence counts.
        (self.target / 'CLAUDE.md').write_text('Read @AGENTS.md.\n')
        run = self.stack()
        self.assertNotIn('CLAUDE.md', run.stdout + run.stderr)

    def test_no_claude_md_without_agents_md(self):
        run = self.stack('cx')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse((self.target / 'AGENTS.md').exists())
        self.assertFalse((self.target / 'CLAUDE.md').exists())


@unittest.skipUnless(shutil.which('just') and shutil.which('jlog'), 'needs just and jlog')
class StepTests(StackTests):
    """The steps of just check, the no-op steps, jlog as the shell, JLOG_RUN, and the lint of steps."""

    def records(self, root=None, kind=None):
        """The records of the run log, of one kind or of all."""
        path = (root or self.target) / '.agents/log/just.jsonl'
        found = [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []
        return [record for record in found if kind is None or record['kind'] == kind]

    def just_in(self, root, *args):
        return subprocess.run(['just', *args], cwd=root, capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def index(self):
        return (self.target / '.agents/just/index.just').read_text()

    def test_a_new_repository_with_no_language_passes_check_and_logs_each_step_as_skipped(self):
        run = self.stack('--defaults', '--without', 'tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        index = self.index()
        self.assertIn(install_stack.SHELL_LINE, index)
        for step in STEPS:
            self.assertIn(f'\n{step}:\n    jlog skip {step} "', index)
        self.assertIn(f'\ncheck: {" ".join(STEPS)}\n    jlog pass\n', index)
        self.assertEqual((self.saved()['steps'], self.saved()['provided_steps']), (list(STEPS), []))
        commit(self.target)
        run = self.just_in(self.target, 'check')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(sorted(record['step'] for record in self.records(kind='skip')), sorted(STEPS))
        self.assertEqual(len(self.records(kind='pass')), 1)
        # Each line of the run shares the run ID of the root Justfile.
        self.assertEqual(len({record['run'] for record in self.records(kind='line')}), 1)
        self.assertEqual(git(self.target, 'status', '--porcelain'), '')

    def test_check_in_a_fresh_clone_leaves_no_file_that_git_does_not_ignore(self):
        self.assertEqual(self.stack('--profile', 'python', '--defaults', '--without', 'tasker').returncode, 0)
        commit(self.target)
        clone = self.base / 'clone'
        git(self.base, 'clone', '-q', str(self.target), str(clone))
        run = self.just_in(clone, 'check')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(git(clone, 'status', '--porcelain', '--untracked-files=all'), '')
        self.assertTrue(self.records(clone, 'pass'))
        # The log folder is ignored, and a worktree with only the log folder can be pruned.
        self.assertIn('.agents/log/', (clone / '.gitignore').read_text().splitlines())
        self.assertIn('.agents/log/', (clone / '.worktreedisposable').read_text().splitlines())

    def test_check_works_in_a_folder_whose_name_has_a_quote_a_dollar_and_a_backtick(self):
        self.assertEqual(self.stack('--profile', 'python', '--defaults', '--without', 'tasker').returncode, 0)
        commit(self.target)
        # A path in double quotes lets the shell expand $HOME and run `true`, so the path goes in single quotes.
        clone = self.base / 'it\'s $HOME `true` "q"'
        git(self.base, 'clone', '-q', str(self.target), str(clone))
        run = self.just_in(clone, 'check')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertTrue(self.records(clone, 'pass'))

    def test_the_steps_of_a_profile_end_with_jlog_step_and_share_the_run_of_the_root(self):
        self.assertEqual(self.stack('--profile', 'python', '--defaults', '--without', 'tasker,prose').returncode, 0)
        self.assertEqual(self.saved()['provided_steps'], ['lint', 'test', 'diff-check'])
        index = self.index()
        self.assertIn('\nlint *args:\n    just --justfile {{quote(justfile())}} python lint "$@"\n    jlog step lint\n', index)
        # fmt is no step, so its combined recipe has no jlog step, and fmt-check is a no-op step.
        self.assertNotIn('jlog step fmt\n', index)
        self.assertIn('\nfmt-check:\n    jlog skip fmt-check "', index)
        self.assertIn('\ndiff-check:\n    git diff --check "$(git rev-parse -q --verify HEAD || git hash-object -t tree '
                      '/dev/null)"\n    jlog step diff-check\n', index)
        module = (self.target / '.agents/just/python.just').read_text()
        self.assertIn(install_stack.SHELL_LINE, module)
        # Only the root Justfile makes the run ID.
        self.assertIn(install_stack.JLOG_LINE, (self.target / 'Justfile').read_text())
        self.assertNotIn('JLOG_RUN', index + module + (self.target / '.agents/install.just').read_text())
        # The repository has no commit yet, and the diff check still runs.
        run = self.just_in(self.target, 'diff-check')
        self.assertEqual(run.returncode, 0, run.stderr)
        (self.target / '.agents/log/just.jsonl').unlink()
        commit(self.target)
        run = self.just_in(self.target, 'check')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(sorted(record['step'] for record in self.records(kind='step')), ['diff-check', 'lint', 'test'])
        lines = self.records(kind='line')
        # The lines of the module ran in inner just calls, and they share the run of the root.
        self.assertIn('echo python lint "$@"', [record['line'] for record in lines])
        self.assertEqual(len({record['run'] for record in lines}), 1)

    def test_a_justfile_of_its_own_gets_the_steps_without_its_names_and_the_run_id_in_the_block(self):
        justfile = self.target / 'Justfile'
        own = 'test:\n    @echo mine\n    jlog step test\n'
        justfile.write_text(own)
        run = self.stack('--profile', 'python', '--defaults', '--without', 'tasker,prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        text = justfile.read_text()
        self.assertIn(f"\nimport? '.agents/install.just'\n{install_stack.JLOG_LINE}\n# end lines of jnterfaces.", text)
        index = self.index()
        # No recipe of the index has the name test of the Justfile, and the other steps are there.
        self.assertNotIn('\ntest', index)
        self.assertIn('\nlint *args:', index)
        self.assertIn('\ntypecheck:\n    jlog skip typecheck', index)
        self.assertEqual(self.just_in(self.target, 'test').stdout.splitlines()[0], 'mine')
        # A second run writes the same index.
        self.assertEqual(self.stack().returncode, 0)
        self.assertEqual(self.index(), index)
        commit(self.target)
        run = self.just_in(self.target, 'check')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        # A Justfile that sets JLOG_RUN itself gets no second line of it.
        justfile.write_text("import? '.agents/install.just'\nexport JLOG_RUN := 'x'\n")
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(justfile.read_text(), "import? '.agents/install.just'\nexport JLOG_RUN := 'x'\n")

    def test_a_role_recipe_and_a_recipe_of_the_justfile_with_one_name_stop_the_install(self):
        self.assertEqual(self.stack('prose').returncode, 0)
        index = self.index()
        justfile = self.target / 'Justfile'
        justfile.write_text(justfile.read_text() + '\nprose:\n    @echo mine\n')
        run = self.stack()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('The recipe prose is in Justfile and in the recipe file of a role, .agents/just/roles/'
                      'nossaifd.just', run.stderr)
        self.assertEqual(self.index(), index)

    def test_a_role_file_that_the_justfile_imports_itself_is_no_clash(self):
        justfile = self.target / 'Justfile'
        justfile.write_text("import? '.agents/install.just'\nimport? '.agents/just/roles/nossaifd.just'\n")
        run = self.stack('prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("import? 'roles/nossaifd.just'", self.index())

    def test_a_justfile_that_sets_its_shell_gets_no_shell_from_the_index(self):
        justfile = self.target / 'Justfile'
        justfile.write_text('set shell := ["bash", "-c"]\n\nbuild:\n    @echo build\n')
        run = self.stack('--defaults', '--without', 'tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('Put jlog in front of the shell: set shell := ["jlog", "bash", "-c"]', run.stderr)
        self.assertNotIn('set shell', self.index())
        self.assertEqual(self.just_in(self.target, '--summary').returncode, 0)
        # just check fails with that shell, so just install --check fails too.
        commit(self.target)
        self.assertNotEqual(self.just_in(self.target, 'check').returncode, 0)
        run = self.stack('--check')
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn('sets its own shell without jlog, so the index sets none, and just check fails', run.stdout)
        # Wrapped in jlog, the shell keeps working, and the installer says nothing more about it.
        justfile.write_text(justfile.read_text().replace('["bash", "-c"]', '["jlog", "bash", "-c"]'))
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('Put jlog in front', run.stderr)
        commit(self.target)
        run = self.just_in(self.target, 'check')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertTrue(self.records(kind='pass'))

    def test_two_imported_files_cannot_both_set_the_shell(self):
        # So the index sets the shell, and .agents/install.just does not: a project that sets its own shell would
        # clash with a fixed line in .agents/install.just.
        self.assertNotIn('set shell', (REPO / '.agents/install.just').read_text())
        (self.target / 'a.just').write_text(install_stack.SHELL_LINE + '\n')
        (self.target / 'b.just').write_text(install_stack.SHELL_LINE + '\n')
        (self.target / 'Justfile').write_text("import 'a.just'\nimport 'b.just'\nx:\n    echo x\n")
        run = self.just_in(self.target, '--summary')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('is redefined', run.stderr)

    def test_a_module_that_meets_a_name_of_the_justfile_leaves_the_steps(self):
        justfile = self.target / 'Justfile'
        justfile.write_text("import? '.agents/install.just'\n\npython:\n    @echo mine\n")
        run = self.stack('--profile', 'python', '--defaults', '--without', 'tasker,prose')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('so the index has no module', run.stderr)
        index = self.index()
        self.assertNotIn('mod python', index)
        self.assertIn('\nlint:\n    jlog skip lint', index)
        self.assertEqual(self.saved()['provided_steps'], [])

    def test_an_alias_with_the_name_of_a_step_keeps_the_justfile_parsing(self):
        justfile = self.target / 'Justfile'
        justfile.write_text("import? '.agents/install.just'\n\nhello:\n    @echo hi\n\nalias lint := hello\n")
        run = self.stack('--defaults', '--without', 'tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('lint in the project is an alias, a module, or a recipe that needs an argument', run.stderr)
        self.assertNotIn('does not parse', run.stderr)
        self.assertIn('\ncheck: fmt-check typecheck ', self.index())
        self.assertEqual(self.just_in(self.target, '--summary').returncode, 0)

    def test_the_installer_stops_with_the_install_line_when_jlog_is_missing(self):
        tools = self.base / 'tools'
        tools.mkdir()
        for name in ('git', 'just'):
            (tools / name).symlink_to(shutil.which(name))
        run = subprocess.run([sys.executable, self.packages / 'jnterfaces/scripts/install_stack.py', '--target',
                              self.target, '--defaults'], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                             env=dict(os.environ, PATH=f'{self.bin}:{tools}', TOOL_LOG=str(self.log)))
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('jlog is not on PATH', run.stderr)
        self.assertIn(install_stack.JLOG_INSTALL, run.stderr)
        self.assertFalse((self.target / '.agents').exists())

    def test_the_lint_finds_steps_that_the_run_log_cannot_see(self):
        self.assertEqual(self.stack('--profile', 'python', '--defaults', '--without', 'tasker,prose').returncode, 0)
        self.assertEqual(self.stack('--check').returncode, 0)
        justfile = self.target / 'Justfile'
        start = justfile.read_text()
        cases = {
            'typecheck:\n    #!/usr/bin/env sh\n    ty check\n': 'typecheck: it has a shebang line',
            '[script]\ntypecheck:\n    ty check\n': 'typecheck: it has a shebang line or the attribute [script]',
            'typecheck:\n    -ty check\n    jlog step typecheck\n': 'typecheck: it has a line with the prefix -',
            '[no-cd]\ntypecheck:\n    ty check\n    jlog step typecheck\n': 'typecheck: it has the attribute [no-cd]',
            "[working-directory('src')]\ntypecheck:\n    ty check\n    jlog step typecheck\n":
                'typecheck: it has the attribute [working-directory]',
            'typecheck:\n    ty check\n': 'typecheck: its last line is not jlog step typecheck',
            'check:\n    @echo done\n': 'check: its last line is not jlog pass',
            'typecheck: helper\n    jlog step typecheck\nhelper:\n    -ty check\n': 'helper: it has a line with the prefix -',
            # The last line continues the line before it, so the shell runs the two as one line.
            'typecheck:\n    echo typing \\\n    jlog step typecheck\n': 'typecheck: its last line is not jlog step',
            # A dependency after && runs after the last line.
            'typecheck: && helper\n    jlog step typecheck\nhelper:\n    ty check\n':
                'typecheck: its last line is not jlog step',
        }
        for recipe, finding in cases.items():
            justfile.write_text(start + '\n' + recipe)
            run = self.stack()
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn(f'The lint of steps: {finding}', run.stderr)
            run = self.stack('--check')
            self.assertEqual(run.returncode, 1, recipe)
            self.assertIn(f'The lint of steps: {finding}', run.stdout)
        # The setting working-directory that the installer writes into each module is allowed, and so is a line that
        # continues a line before it.
        # A comment after the line of jlog is allowed too. A module of the project is no step, because check never
        # runs it.
        (self.target / 'web.just').write_text('test:\n    #!/bin/sh\n    echo hi\n')
        for allowed in ('\ntypecheck:\n    echo a \\\n    -b\n    jlog step typecheck\n',
                        '\ntypecheck:\n    ty check\n    jlog step typecheck  # the end\n',
                        "\nmod web 'web.just'\n"):
            justfile.write_text(start + allowed)
            run = self.stack()
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertNotIn('lint of steps', run.stderr)
            run = self.stack('--check')
            self.assertEqual(run.returncode, 0, run.stdout)
            self.assertNotIn('lint of steps', run.stdout)

    def test_the_lint_reads_the_steps_of_a_module(self):
        dump = {'recipes': {}, 'modules': {'python': {'recipes': {'test': {
            'shebang': True, 'attributes': [], 'body': [['#!/bin/sh'], ['pytest']], 'dependencies': []}}}}}
        self.assertEqual(install_stack.lint_project(dump, ['python']),
                         ['python::test: it has a shebang line or the attribute [script], so its lines do not run '
                          'through jlog'])

    def test_the_lint_command_finds_a_shebang_step_in_a_justfile(self):
        justfile = self.base / 'old.just'
        justfile.write_text('test:\n    #!/usr/bin/env sh\n    pytest\n\nfmt:\n    #!/usr/bin/env sh\n    ruff\n')
        run = subprocess.run([sys.executable, REPO / 'scripts/lint_steps.py', justfile], capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertIn('the step test: it has a shebang line', run.stdout)
        # fmt is no step.
        self.assertNotIn('fmt', run.stdout)
        run = subprocess.run([sys.executable, REPO / 'scripts/lint_steps.py',
                              *sorted((REPO / '.agents/skills/just/assets/templates').glob('*.just'))],
                             capture_output=True, text=True)
        self.assertEqual((run.returncode, run.stdout), (0, ''))

    def role(self, text):
        """Give the nossaifd role of the test packages a recipe file of this text."""
        role = self.packages / 'nossaifd/.agents/just/roles/nossaifd.just'
        role.write_text(text)
        commit(self.packages / 'nossaifd')

    def parses(self):
        run = self.just_in(self.target, '--summary')
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_a_stop_after_a_profile_is_gone_leaves_a_justfile_that_parses(self):
        self.assertEqual(self.stack('prose', '--profile', 'python', '--defaults', '--without', 'tasker').returncode, 0)
        justfile = self.target / 'Justfile'
        justfile.write_text(justfile.read_text() + '\nprose:\n    @echo mine\n')
        run = self.stack('--without', 'python')
        self.assertEqual(run.returncode, 1)
        self.assertFalse((self.target / '.agents/just/python.just').exists())
        self.assertNotIn('mod python', self.index())
        self.parses()

    def test_a_justfile_that_uses_a_recipe_of_the_index_installs_and_parses(self):
        justfile = self.target / 'Justfile'
        justfile.write_text("import? '.agents/install.just'\n\nci: check\n    @echo ci\n")
        run = self.stack('--defaults', '--without', 'tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('does not parse', run.stderr)
        self.parses()

    def test_an_alias_of_a_recipe_of_the_index_does_not_freeze_the_index(self):
        self.assertEqual(self.stack('--profile', 'python', '--defaults', '--without', 'tasker,prose').returncode, 0)
        justfile = self.target / 'Justfile'
        justfile.write_text(justfile.read_text() + '\nalias t := test\n')
        run = self.stack('--profile', 'python', 'typecheck')
        self.assertEqual(run.returncode, 0, run.stderr)
        # The new tool reaches the index, and the record says the same as the index.
        self.assertIn('\ntypecheck *args:\n    just --justfile', self.index())
        self.assertIn('typecheck', self.saved()['provided_steps'])
        self.parses()

    def test_a_kept_index_keeps_the_steps_that_it_provides(self):
        self.assertEqual(self.stack('--profile', 'python', '--defaults', '--without', 'tasker,prose').returncode, 0)
        provided = self.saved()['provided_steps']
        path = self.target / '.agents/just/index.just'
        path.write_text(path.read_text() + '\n# my edit\n')
        run = self.stack('--profile', 'python', 'typecheck')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(self.index().endswith('# my edit\n'))
        self.assertEqual(self.saved()['provided_steps'], provided)

    def test_a_role_file_that_does_not_parse_alone_stops_the_install(self):
        self.role('prose-check: lint\n    @echo prose\n    jlog step prose-check\n')
        run = self.stack('prose', '--profile', 'python', '--defaults', '--without', 'tasker')
        self.assertEqual(run.returncode, 1)
        self.assertIn('roles/nossaifd.just', run.stderr)
        self.assertIn('does not parse', run.stderr)

    def test_a_role_recipe_with_the_name_of_a_step_of_a_profile_stops_the_install(self):
        self.role('prose-check:\n    @echo prose\n    jlog step prose-check\n\nlint:\n    @echo rolelint\n    '
                  'jlog step lint\n')
        run = self.stack('prose', '--profile', 'python', '--defaults', '--without', 'tasker')
        self.assertEqual(run.returncode, 1)
        self.assertIn('The recipe lint is in a recipe file of a role and in a profile', run.stderr)

    def test_a_role_step_counts_as_provided_and_runs_in_check(self):
        self.role('prose-check:\n    @echo prose\n    jlog step prose-check\n')
        run = self.stack('prose', '--defaults', '--without', 'tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.saved()['provided_steps'], ['prose-check'])
        self.assertNotIn('jlog skip prose-check', self.index())
        commit(self.target)
        run = self.just_in(self.target, 'check')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual([record['step'] for record in self.records(kind='step')], ['prose-check'])

    def test_a_jlog_run_in_a_file_that_the_justfile_imports_counts(self):
        (self.target / 'vars.just').write_text('export JLOG_RUN := "fixed"\n')
        justfile = self.target / 'Justfile'
        justfile.write_text("import 'vars.just'\n\nbuild:\n    @echo b\n")
        run = self.stack('--defaults', '--without', 'tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        text = justfile.read_text()
        self.assertIn(install_stack.IMPORT_LINE, text)
        self.assertNotIn(install_stack.JLOG_LINE, text)
        self.parses()

    def test_a_jlog_run_that_is_not_exported_gets_a_note(self):
        justfile = self.target / 'Justfile'
        justfile.write_text('JLOG_RUN := "x"\n\nbuild:\n    @echo b\n')
        run = self.stack('--defaults', '--without', 'tasker')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('sets JLOG_RUN but does not export it', run.stderr)

    def test_check_names_a_justfile_without_jlog_run(self):
        self.assertEqual(self.stack('--defaults', '--without', 'tasker').returncode, 0)
        justfile = self.target / 'Justfile'
        justfile.write_text(justfile.read_text().replace(install_stack.JLOG_LINE + '\n', ''))
        commit(self.target)
        run = self.stack('--check')
        self.assertIn(f'does not set JLOG_RUN, so the inner just calls of one just check get run IDs of their own. '
                      f'Add this line to it: {install_stack.JLOG_LINE}', run.stdout + run.stderr)

    def test_a_step_of_the_justfile_that_needs_an_argument_stays_out_of_check(self):
        self.assertEqual(self.stack('--defaults', '--without', 'tasker').returncode, 0)
        justfile = self.target / 'Justfile'
        justfile.write_text(justfile.read_text() + '\nlint path:\n    echo {{path}}\n    jlog step lint\n')
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('lint in the project is an alias, a module, or a recipe that needs an argument', run.stderr)
        self.assertNotIn(' lint ', next(line for line in self.index().splitlines() if line.startswith('check:')))
        self.parses()

    def test_a_shell_or_a_step_that_the_justfile_gets_later_is_mended_by_a_direct_run(self):
        # The Justfile then does not parse, so just install cannot run. The installer script itself can.
        self.assertEqual(self.stack('--profile', 'python', '--defaults', '--without', 'tasker,prose').returncode, 0)
        justfile = self.target / 'Justfile'
        justfile.write_text(justfile.read_text() + '\nset shell := ["jlog", "bash", "-c"]\n\nlint:\n    @echo mine\n'
                            '    jlog step lint\n')
        self.assertNotEqual(self.just_in(self.target, '--summary').returncode, 0)
        run = self.stack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.parses()
        self.assertNotIn('set shell', self.index())
        self.assertNotIn('\nlint', self.index())
        commit(self.target)
        run = self.just_in(self.target, 'check')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_check_fails_when_the_justfile_does_not_parse(self):
        self.assertEqual(self.stack('--profile', 'python', '--defaults', '--without', 'tasker,prose').returncode, 0)
        justfile = self.target / 'Justfile'
        # The index sets the shell too, so just stops with "Setting shell is redefined".
        justfile.write_text(justfile.read_text() + '\nset shell := ["bash", "-c"]\n')
        commit(self.target)
        run = self.stack('--check')
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn('Justfile does not parse, so just check and just install cannot run', run.stdout)

    def test_the_lint_command_checks_the_ends_of_steps_in_a_justfile_with_the_index(self):
        justfile = self.base / 'Justfile'
        justfile.write_text("import? './.agents/install.just'\n\nlint:\n    ruff check\n\ntest:\n    pytest\n    jlog step test\n")
        run = subprocess.run([sys.executable, REPO / 'scripts/lint_steps.py', justfile], capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertIn('the step lint: its last line is not jlog step lint', run.stdout)
        self.assertNotIn('the step test', run.stdout)
        # Without the import, check does not come from the index, so the ends do not count.
        justfile.write_text('lint:\n    ruff check\n')
        run = subprocess.run([sys.executable, REPO / 'scripts/lint_steps.py', justfile], capture_output=True, text=True)
        self.assertEqual((run.returncode, run.stdout), (0, ''))

    def test_the_log_folder_is_reserved(self):
        self.assertTrue(install_stack.shared_files.reserved('.agents/log/just.jsonl'))


# A program of a profile, for the tests of programs: fakeprog check NAME exits 0 and prints NAME 1.0 when the marker
# file NAME is in FAKE_PROGRAMS. fakeprog install NAME logs the call, and whether rust-toolchain.toml is in the
# folder that it runs in, then makes the marker, with the text INSTALL_PRINTS after NAME 1.0. BROKEN_INSTALL=NAME
# makes no marker, and FAIL_INSTALL=NAME exits with 3.
FAKE_PROGRAM = r'''
import os, sys
from pathlib import Path

kind, name = sys.argv[1], sys.argv[2]
marker = Path(os.environ['FAKE_PROGRAMS']) / name
if kind == 'check' and os.environ.get('LOG_CHECKS'):
    with open(os.environ['TOOL_LOG'], 'a') as log:
        log.write(f'fakeprog check {name}\n')
if kind == 'check':
    if marker.is_file():
        print(marker.read_text().strip() or name + ' 1.0')
        sys.exit(0)
    sys.exit(1)
with open(os.environ['TOOL_LOG'], 'a') as log:
    log.write(f'fakeprog install {name} toolchain={Path("rust-toolchain.toml").is_file()}\n')
if os.environ.get('LOG_CWD'):
    Path(os.environ['LOG_CWD']).write_text(os.getcwd())
if os.environ.get('FAIL_INSTALL') == name:
    sys.exit(3)
if os.environ.get('BROKEN_INSTALL') != name:
    marker.write_text(f'{name} 1.0 {os.environ.get("INSTALL_PRINTS", "")}')
'''
# The programs of the rust profile in these tests: cargo comes from rustup, which the installer never runs, and
# cargo-llvm-cov has an install. lint needs cargo only; test also needs llvm-cov.
PROGRAMS = [{'name': 'cargo', 'check': 'fakeprog check cargo', 'description': 'Install Rust with rustup: https://rustup.rs/'},
            {'name': 'llvm-tools', 'check': 'fakeprog check components', 'expect': 'llvm-tools',
             'install': 'fakeprog install components'},
            {'name': 'cargo-llvm-cov', 'check': 'fakeprog check cargo-llvm-cov',
             'install': 'fakeprog install cargo-llvm-cov'}]


def with_programs(profile):
    profile['programs'] = json.loads(json.dumps(PROGRAMS))
    profile['base']['programs'] = ['cargo']
    profile['tools']['test']['programs'] = ['cargo-llvm-cov']


class ProgramTests(StackTests):
    def setUp(self):
        super().setUp()
        (self.bin / 'fakeprog').write_text(f'#!{sys.executable}\n' + FAKE_PROGRAM)
        (self.bin / 'fakeprog').chmod(0o755)
        self.programs = self.base / 'programs'
        self.programs.mkdir()
        self.edit_profile('rust', with_programs)

    def have(self, *names):
        for name in names:
            (self.programs / name).write_text(name + ' 1.0')

    def run_rust(self, *args, env=None):
        return self.stack('--profile', 'rust', '--defaults', *args,
                          env=dict({'FAKE_PROGRAMS': str(self.programs)}, **(env or {})))

    def test_a_valid_programs_field_passes_the_check_of_the_profile(self):
        profile = json.loads(json.dumps(PROFILES['rust']))
        with_programs(profile)
        self.assertIs(install_stack.check_profile('rust', profile, {'tasker': {}, 'prose': {}}), profile)

    def test_a_program_with_an_install_may_have_a_description_too(self):
        profile = json.loads(json.dumps(PROFILES['rust']))
        with_programs(profile)
        profile['programs'][2]['description'] = 'Writes the coverage of the tests.'
        self.assertIs(install_stack.check_profile('rust', profile, {'tasker': {}, 'prose': {}}), profile)
        # A failed install prints the description.
        self.have('cargo')
        self.edit_profile('rust', lambda p: (with_programs(p), p['programs'][2].update(
            description='Writes the coverage of the tests.')))
        run = self.run_rust(env={'FAIL_INSTALL': 'cargo-llvm-cov'})
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('cargo-llvm-cov: Writes the coverage of the tests.', run.stderr)

    def test_a_malformed_programs_field_is_refused_with_its_reason(self):
        cases = [
            (lambda p: p.update(programs={}), 'programs must be a list'),
            (lambda p: p['programs'].append({'name': 'x y', 'check': 'x', 'install': 'x'}), 'needs a name'),
            (lambda p: p['programs'].append(dict(PROGRAMS[0])), 'cargo is in programs twice'),
            (lambda p: p['programs'][0].update(check=['cargo', '--version']), 'needs a check'),
            (lambda p: p['programs'][0].update(check=''), 'needs a check'),
            (lambda p: p['programs'][0].update(check='cargo "--version'), 'needs a check'),
            (lambda p: p['programs'][0].pop('description'), 'needs an install or a description'),
            (lambda p: p['programs'][1].update(install='  '), 'install of the program llvm-tools'),
            (lambda p: p['programs'][0].update(description=''), 'description of the program cargo'),
            (lambda p: p['programs'][1].update(expect=''), 'expect of the program llvm-tools'),
            (lambda p: p['programs'][0].update(url='https://rustup.rs/'), 'unknown key(s) url'),
            (lambda p: p['tools']['test'].update(programs=['nextest']), 'tool test names nextest'),
            (lambda p: p['base'].update(programs='cargo'), 'programs of base must be a list'),
            (lambda p: p['base'].update(programs=['cargo', 'cargo']), 'each once'),
        ]
        for change, reason in cases:
            profile = json.loads(json.dumps(PROFILES['rust']))
            with_programs(profile)
            change(profile)
            with self.assertRaises(install_stack.StackError, msg=reason) as caught:
                install_stack.check_profile('rust', profile, {'tasker': {}, 'prose': {}})
            self.assertIn(reason, str(caught.exception))

    def test_a_missing_program_without_install_stops_before_anything_is_written(self):
        run = self.run_rust()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('Nothing was written', run.stderr)
        self.assertIn('cargo (for the rust profile): fakeprog check cargo failed. Install Rust with rustup: '
                      'https://rustup.rs/', run.stderr)
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), ['.git'])
        self.assertEqual(self.tools_run(), [])

    def test_a_missing_program_with_install_is_installed_after_the_files_and_checked_again(self):
        self.have('cargo')
        self.edit_profile('rust', lambda p: (with_programs(p), p['base'].update(
            files={'rust-toolchain.toml': 'toolchain/rust-toolchain.toml'})))
        run = self.run_rust()
        self.assertEqual(run.returncode, 0, run.stderr)
        # The whole file of the base is the toolchain file, and it is in place before the install runs.
        self.assertTrue((self.target / 'rust-toolchain.toml').is_file())
        self.assertEqual(self.tools_run(), ['cargo init --vcs none', 'fakeprog install cargo-llvm-cov toolchain=True'])
        self.assertIn('fakeprog check cargo-llvm-cov failed, so the installer runs for the rust profile, in the project '
                      'folder: fakeprog install cargo-llvm-cov', run.stderr)
        # The program is there now, so a second run installs nothing.
        run = self.run_rust()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(len(self.tools_run()), 2)

    def test_a_toolchain_file_of_the_project_stays(self):
        # The whole-file mechanism writes the toolchain file of the profile only when the project has none.
        self.have('cargo', 'cargo-llvm-cov')
        (self.target / 'rust-toolchain.toml').write_text('[toolchain]\nchannel = "nightly-2026-01-01"\n')
        self.edit_profile('rust', lambda p: (with_programs(p), p['base'].update(
            files={'rust-toolchain.toml': 'toolchain/rust-toolchain.toml'})))
        run = self.run_rust()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('nightly-2026-01-01', (self.target / 'rust-toolchain.toml').read_text())

    def test_an_install_that_does_not_make_the_check_pass_fails_clearly(self):
        self.have('cargo')
        run = self.run_rust(env={'BROKEN_INSTALL': 'cargo-llvm-cov'})
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('fakeprog install cargo-llvm-cov ran, but fakeprog check cargo-llvm-cov still fails', run.stderr)

    def test_an_install_that_exits_with_an_error_fails_clearly(self):
        self.have('cargo')
        run = self.run_rust(env={'FAIL_INSTALL': 'cargo-llvm-cov'})
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('The install of the program cargo-llvm-cov failed: fakeprog install cargo-llvm-cov: it exited '
                      'with 3', run.stderr)

    def test_an_install_whose_program_is_not_on_path_stops_before_anything_is_written(self):
        self.have('cargo')
        self.edit_profile('rust', lambda p: (with_programs(p), p['programs'][2].update(
            install='no-such-installer-xyz install cargo-llvm-cov')))
        run = self.run_rust()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('needs no-such-installer-xyz, which is not on PATH', run.stderr)
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), ['.git'])

    def test_a_program_that_works_needs_no_installer_on_path(self):
        self.have('cargo', 'cargo-llvm-cov')
        self.edit_profile('rust', lambda p: (with_programs(p), p['programs'][2].update(
            install='no-such-installer-xyz install cargo-llvm-cov')))
        run = self.run_rust()
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_a_check_or_install_that_starts_with_a_path_is_refused(self):
        # A path depends on the machine, or on a file that the run writes after step 1.
        for key, command in (('install', './tools/get-cov'), ('install', '/usr/local/bin/get-cov x'),
                             ('check', 'bin/cov --version')):
            profile = json.loads(json.dumps(PROFILES['rust']))
            with_programs(profile)
            profile['programs'][2][key] = command
            with self.assertRaises(install_stack.StackError, msg=command) as caught:
                install_stack.check_profile('rust', profile, {'tasker': {}, 'prose': {}})
            self.assertIn('must start with a program on PATH, not a path', str(caught.exception))

    def test_a_check_that_times_out_stops_the_processes_that_it_started(self):
        mark = self.base / 'late'
        script = f'import subprocess, sys, time; subprocess.Popen([sys.executable, "-c", "import time, pathlib; ' \
                 f'time.sleep(2); pathlib.Path({str(mark)!r}).write_text(\'x\')"]); time.sleep(30)'
        entry = {'name': 'slow', 'check': shlex.join([sys.executable, '-c', script])}
        with mock.patch.object(install_stack, 'CHECK_SECONDS', 0.5), \
                mock.patch('sys.stderr', new_callable=io.StringIO):
            self.assertFalse(install_stack.program_works(self.target, entry))
        time.sleep(3)
        self.assertFalse(mark.exists())

    def test_install_in_picks_the_folder_of_the_install(self):
        self.have('cargo')
        home, where = self.base / 'home', self.base / 'cwd'
        home.mkdir()
        self.edit_profile('rust', lambda p: (with_programs(p), p['base'].update(
            files={'rust-toolchain.toml': 'toolchain/rust-toolchain.toml'})))
        # The default, project: the install runs in the project, with the toolchain file.
        run = self.run_rust(env={'HOME': str(home), 'LOG_CWD': str(where)})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(Path(where.read_text()).resolve(), self.target.resolve())
        self.assertIn('in the project folder', run.stderr)
        # home: the install runs in the home folder, without the toolchain file. The check runs in the project.
        (self.programs / 'cargo-llvm-cov').unlink()
        self.edit_profile('rust', lambda p: (with_programs(p), p['programs'][2].update(install_in='home'),
                                             p['base'].update(files={'rust-toolchain.toml': 'toolchain/rust-toolchain.toml'})))
        run = self.run_rust(env={'HOME': str(home), 'LOG_CWD': str(where)})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(Path(where.read_text()).resolve(), home.resolve())
        self.assertEqual(self.tools_run()[-1], 'fakeprog install cargo-llvm-cov toolchain=False')
        self.assertIn('in the home folder', run.stderr)
        self.assertNotIn(str(home), run.stderr + run.stdout)

    def test_install_in_home_without_home_uses_a_folder_outside_the_project(self):
        self.have('cargo')
        where = self.base / 'cwd'
        self.edit_profile('rust', lambda p: (with_programs(p), p['programs'][2].update(install_in='home')))
        entry = dict(PROGRAMS[2], install_in='home', profile='rust')
        env = {k: v for k, v in os.environ.items() if k != 'HOME'}
        env.update(PATH=f'{self.bin}:{os.environ["PATH"]}', TOOL_LOG=str(self.log), FAKE_PROGRAMS=str(self.programs),
                   LOG_CWD=str(where))
        with mock.patch.dict(os.environ, env, clear=True), mock.patch('sys.stderr', new_callable=io.StringIO):
            install_stack.install_programs(self.target, [entry])
        folder = Path(where.read_text()).resolve()
        self.assertFalse(folder.is_relative_to(self.target.resolve()))
        self.assertFalse(folder.exists())

    def test_install_in_takes_project_or_home_only(self):
        for change in (lambda p: p['programs'][2].update(install_in='tmp'),
                       lambda p: p['programs'][2].update(install_in=True),
                       lambda p: p['programs'][0].update(install_in='home')):
            profile = json.loads(json.dumps(PROFILES['rust']))
            with_programs(profile)
            change(profile)
            with self.assertRaises(install_stack.StackError) as caught:
                install_stack.check_profile('rust', profile, {'tasker': {}, 'prose': {}})
            self.assertIn('install_in of the program', str(caught.exception))

    def test_ctrl_c_during_a_check_stops_the_check(self):
        mark = self.base / 'late'
        script = f'import time, pathlib; time.sleep(2); pathlib.Path({str(mark)!r}).write_text("x")'
        entry = {'name': 'slow', 'check': shlex.join([sys.executable, '-c', script])}
        timer = threading.Timer(0.5, os.kill, (os.getpid(), signal.SIGINT))
        timer.start()
        with self.assertRaises(KeyboardInterrupt):
            install_stack.program_works(self.target, entry)
        timer.join()
        time.sleep(3)
        self.assertFalse(mark.exists())

    def test_a_check_that_cannot_be_stopped_does_not_hang_the_run(self):
        # A setuid process can refuse the kill, so the last wait has a limit too.
        run = mock.Mock(pid=1234)
        run.communicate.side_effect = subprocess.TimeoutExpired('x', 5)
        run.wait.side_effect = subprocess.TimeoutExpired('x', 5)
        with mock.patch.object(install_stack.os, 'killpg', side_effect=PermissionError), \
                mock.patch('sys.stderr', new_callable=io.StringIO) as err:
            install_stack.stop_check(run)
        run.wait.assert_called_once_with(timeout=5)
        self.assertIn('Could not stop process 1234 of a check', err.getvalue())

    def test_a_timed_out_check_does_not_wait_for_a_process_that_left_its_session(self):
        script = 'import os, time\nif os.fork() == 0:\n    os.setsid()\n    time.sleep(12)\nelse:\n    time.sleep(30)'
        entry = {'name': 'slow', 'check': shlex.join([sys.executable, '-c', script])}
        start = time.monotonic()
        with mock.patch.object(install_stack, 'CHECK_SECONDS', 0.5), \
                mock.patch('sys.stderr', new_callable=io.StringIO):
            self.assertFalse(install_stack.program_works(self.target, entry))
        self.assertLess(time.monotonic() - start, 9)

    def test_a_profile_shaped_like_the_rust_profile_installs_in_list_order_and_each_name_once(self):
        # As in jnprofile-rust: rustup alone has no install, stable is in two tools, and the order of programs puts
        # toolchain and stable before the programs that cargo +stable install builds.
        def fake(name, **more):
            return dict({'name': name, 'check': f'fakeprog check {name}', 'install': f'fakeprog install {name}',
                         'description': f'What {name} does.'}, **more)

        def shape(p):
            p['programs'] = [
                {'name': 'rustup', 'check': 'fakeprog check rustup',
                 'description': 'Install Rust with rustup: https://rustup.rs/'},
                fake('toolchain'), fake('rustfmt'), fake('stable', install_in='home'),
                fake('cargo-llvm-cov', install_in='home'), fake('taplo', install_in='home')]
            p['base']['programs'] = ['rustup', 'toolchain']
            p['tools']['lint']['programs'] = ['rustfmt']
            p['tools']['test']['programs'] = ['stable', 'cargo-llvm-cov']
            p['tools']['toml'] = {'packages': [], 'programs': ['taplo', 'stable']}
            p['default_tools'] = ['lint', 'test', 'toml']
        self.edit_profile('rust', shape)
        home = self.base / 'home'
        home.mkdir()
        env = {'HOME': str(home), 'LOG_CHECKS': '1'}
        # rustup has no install, so its check comes before anything is written, and stops the run.
        run = self.run_rust(env=env)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('rustup (for the rust profile): fakeprog check rustup failed. Install Rust with rustup', run.stderr)
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), ['.git'])
        self.assertEqual(self.tools_run(), ['fakeprog check rustup'])
        self.log.unlink()
        self.have('rustup')
        run = self.run_rust(env=env)
        self.assertEqual(run.returncode, 0, run.stderr)
        installs = [line.split()[2] for line in self.tools_run() if line.startswith('fakeprog install')]
        self.assertEqual(installs, ['toolchain', 'rustfmt', 'stable', 'cargo-llvm-cov', 'taplo'])
        # Each name is checked once before its install and once after it, also stable, which two tools need.
        self.assertEqual(self.tools_run().count('fakeprog check stable'), 2)

    def test_a_dry_run_runs_each_check_once(self):
        self.have('cargo')
        self.edit_profile('rust', lambda p: (with_programs(p), p['programs'][2].update(
            install='no-such-installer-xyz install cargo-llvm-cov')))
        run = self.run_rust('--dry-run', env={'LOG_CHECKS': '1'})
        self.assertEqual(run.returncode, 1)
        self.assertEqual(self.tools_run().count('fakeprog check cargo-llvm-cov'), 1)

    def test_a_check_that_takes_too_long_fails(self):
        entry = {'name': 'slow', 'check': f'{shlex.quote(sys.executable)} -c "import time; time.sleep(5)"'}
        with mock.patch.object(install_stack, 'CHECK_SECONDS', 0.5), \
                mock.patch('sys.stderr', new_callable=io.StringIO) as err:
            self.assertFalse(install_stack.program_works(self.target, entry))
        self.assertIn('took more than 0.5 seconds', err.getvalue())

    def test_the_commands_of_the_tools_run_before_the_installs(self):
        self.have('cargo')
        self.edit_profile('rust', lambda p: (with_programs(p), p['tools']['test'].update(packages=['proptest'])))
        run = self.run_rust()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.tools_run(), ['cargo init --vcs none', 'cargo add --dev proptest',
                                            'fakeprog install cargo-llvm-cov toolchain=False'])

    def test_a_missing_program_stops_a_run_that_only_removes_a_tool(self):
        self.have('cargo')
        self.assertEqual(self.run_rust().returncode, 0)
        (self.programs / 'cargo').unlink()
        run = self.stack('--without', 'test', env={'FAKE_PROGRAMS': str(self.programs)})
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('Nothing was written', run.stderr)
        self.assertEqual(self.tool_names('tools'), ['lint', 'test'])

    def test_expect_must_be_in_the_output_of_the_check(self):
        self.have('cargo')
        (self.programs / 'components').write_text('rustfmt clippy')
        self.edit_profile('rust', lambda p: (with_programs(p), p['tools']['test'].update(
            programs=['llvm-tools'])))
        # The check exits with 0, but without llvm-tools in its output, so the installer runs the install.
        run = self.run_rust()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('fakeprog install components ran, but fakeprog check components still fails or does not print '
                      'llvm-tools', run.stderr)
        run = self.run_rust(env={'INSTALL_PRINTS': 'rustfmt clippy llvm-tools'})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.tools_run().count('fakeprog install components toolchain=False'), 2)

    def test_a_program_of_a_tool_that_is_off_is_not_needed(self):
        self.have('cargo')
        run = self.stack('--profile', 'rust', '--defaults', '--without', 'test',
                         env={'FAKE_PROGRAMS': str(self.programs)})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('fakeprog', ' '.join(self.tools_run()))

    def test_programs_are_not_uninstalled_when_a_tool_goes(self):
        self.have('cargo')
        self.assertEqual(self.run_rust().returncode, 0)
        before = self.tools_run()
        run = self.stack('--without', 'test', env={'FAKE_PROGRAMS': str(self.programs)})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.tools_run(), before)
        self.assertTrue((self.programs / 'cargo-llvm-cov').is_file())

    def test_commands_run_without_a_shell(self):
        # A shell would expand $HOME and run the second command. Without one, the check gets the text as it is.
        self.have('cargo')
        (self.programs / '$HOME;touch').write_text('x')
        self.edit_profile('rust', lambda p: (with_programs(p), p['programs'][0].update(
            check='fakeprog check "$HOME;touch" pwned')))
        run = self.run_rust()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse((self.target / 'pwned').exists())

    def test_a_dry_run_says_what_it_would_install_and_what_is_missing_and_installs_nothing(self):
        run = self.run_rust('--dry-run')
        self.assertEqual(run.returncode, 1)
        self.assertIn('Missing program: cargo (for the rust profile): fakeprog check cargo failed. Install Rust with '
                      'rustup: https://rustup.rs/', run.stdout)
        self.assertIn('Would install cargo-llvm-cov for the rust profile: fakeprog install cargo-llvm-cov', run.stdout)
        self.assertIn('because a program is missing', run.stdout)
        self.assertEqual(self.tools_run(), [])
        self.assertFalse((self.target / '.agents').exists())
        self.have('cargo')
        run = self.run_rust('--dry-run')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(self.tools_run(), [])

    def role_programs(self, programs):
        """Give the tasker role the programs, in install_extras.programs of its skill-package.json."""
        root = self.packages / 'agent_gest_git_skills'
        manifest = json.loads((root / 'skill-package.json').read_text())
        manifest['install_extras']['programs'] = programs
        (root / 'skill-package.json').write_text(json.dumps(manifest))
        commit(root)

    HOOKS = [{'name': 'gate', 'check': 'fakeprog check gate', 'install': 'fakeprog install gate'},
             {'name': 'hooks', 'check': 'fakeprog check hooks', 'install': 'fakeprog install hooks'}]

    def test_a_role_program_is_installed_after_the_files_in_its_order(self):
        self.have('cargo', 'cargo-llvm-cov')
        self.role_programs(self.HOOKS)
        run = self.run_rust()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.tools_run()[-2:], ['fakeprog install gate toolchain=False',
                                                 'fakeprog install hooks toolchain=False'])
        self.assertIn('fakeprog check gate failed, so the installer runs for the tasker role, in the project folder',
                      run.stderr)
        # The files of the role are in place before its programs install.
        self.assertTrue((self.target / '.claude/hooks/start.sh').is_file())

    def test_a_missing_role_program_without_install_stops_before_anything_is_written(self):
        self.have('cargo', 'cargo-llvm-cov')
        self.role_programs([{'name': 'gate', 'check': 'fakeprog check gate', 'description': 'Install the gate.'}])
        run = self.run_rust()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('Nothing was written', run.stderr)
        self.assertIn('gate (for the tasker role): fakeprog check gate failed. Install the gate.', run.stderr)
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), ['.git'])
        run = self.run_rust('--dry-run')
        self.assertIn('Missing program: gate (for the tasker role)', run.stdout)

    def test_a_role_that_is_off_needs_no_program(self):
        self.have('cargo', 'cargo-llvm-cov')
        self.role_programs([{'name': 'gate', 'check': 'fakeprog check gate', 'description': 'Install the gate.'}])
        run = self.run_rust('--without', 'tasker')
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_a_program_that_a_profile_declares_comes_from_the_profile(self):
        self.have('cargo')
        self.role_programs([{'name': 'cargo-llvm-cov', 'check': 'fakeprog check cargo-llvm-cov',
                             'description': 'From the role.'}])
        run = self.run_rust()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('installer runs for the rust profile', run.stderr)
        self.assertNotIn('tasker role', run.stderr)

    def test_malformed_role_programs_are_refused(self):
        self.have('cargo', 'cargo-llvm-cov')
        for programs, reason in [({}, 'programs must be a list'),
                                 ([{'name': 'gate', 'check': 'fakeprog check gate'}], 'needs an install or a description'),
                                 ([self.HOOKS[0], self.HOOKS[0]], 'gate is in programs twice')]:
            self.role_programs(programs)
            run = self.run_rust()
            self.assertNotEqual(run.returncode, 0, reason)
            self.assertIn('install_extras.programs in', run.stderr)
            self.assertIn(reason, run.stderr)
            self.assertEqual(sorted(p.name for p in self.target.iterdir()), ['.git'])

    def test_check_reports_a_missing_role_program(self):
        self.have('cargo', 'cargo-llvm-cov')
        self.role_programs(self.HOOKS)
        self.assertEqual(self.run_rust().returncode, 0)
        run = self.stack('--check', env={'FAKE_PROGRAMS': str(self.programs)})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        (self.programs / 'hooks').unlink()
        run = self.stack('--check', env={'FAKE_PROGRAMS': str(self.programs)})
        self.assertEqual(run.returncode, 1)
        self.assertIn('Missing program: hooks (for the tasker role): fakeprog check hooks failed. just install runs: '
                      'fakeprog install hooks', run.stdout)
        self.assertFalse((self.programs / 'hooks').exists())

    def test_check_reports_a_missing_role_program_without_a_profile(self):
        self.role_programs(self.HOOKS)
        run = self.stack('--defaults', env={'FAKE_PROGRAMS': str(self.programs)})
        self.assertEqual(run.returncode, 0, run.stderr)
        (self.programs / 'gate').unlink()
        run = self.stack('--check', env={'FAKE_PROGRAMS': str(self.programs)})
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn('Missing program: gate (for the tasker role)', run.stdout)

    def test_an_install_may_start_with_a_program_that_an_earlier_program_installs(self):
        self.have('cargo', 'cargo-llvm-cov')
        self.role_programs([{'name': 'pc', 'check': 'fakeprog check pc', 'install': 'fakeprog install pc'},
                            {'name': 'hooks', 'check': 'fakeprog check hooks', 'install': 'pc install hooks'}])
        run = self.run_rust('--dry-run')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertNotIn('not on PATH', run.stdout + run.stderr)
        self.assertIn('Would install hooks for the tasker role: pc install hooks', run.stdout)
        # A program that comes later in the list does not count.
        self.role_programs([{'name': 'hooks', 'check': 'fakeprog check hooks', 'install': 'pc install hooks'},
                            {'name': 'pc', 'check': 'fakeprog check pc', 'install': 'fakeprog install pc'}])
        run = self.run_rust()
        self.assertIn('needs pc, which is not on PATH', run.stderr)

    def test_a_program_that_installs_nothing_does_not_put_its_name_on_path(self):
        # The check of pc passes, so nothing installs pc, and the install of hooks cannot start.
        self.have('cargo', 'cargo-llvm-cov', 'pc')
        self.role_programs([{'name': 'pc', 'check': 'fakeprog check pc', 'install': 'fakeprog install pc'},
                            {'name': 'hooks', 'check': 'fakeprog check hooks', 'install': 'pc install hooks'}])
        run = self.run_rust()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('hooks (for the tasker role): its install, pc install hooks, needs pc, which is not on PATH',
                      run.stderr)
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), ['.git'])

    def test_a_role_install_may_start_with_a_program_of_a_profile(self):
        self.have('cargo')
        self.role_programs([{'name': 'hooks', 'check': 'fakeprog check hooks',
                             'install': 'cargo-llvm-cov install hooks'}])
        run = self.run_rust('--dry-run')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertNotIn('not on PATH', run.stdout + run.stderr)
        self.assertIn('Would install hooks for the tasker role: cargo-llvm-cov install hooks', run.stdout)

    def test_check_maps_a_role_saved_under_its_old_name(self):
        self.have('cargo', 'cargo-llvm-cov')
        self.role_programs(self.HOOKS)
        self.assertEqual(self.run_rust().returncode, 0)
        record = self.saved()
        record['choices'] = {'gest': True, 'prose': False}
        (self.target / '.agents/stack.json').write_text(json.dumps(record))
        (self.programs / 'gate').unlink()
        run = self.stack('--check', env={'FAKE_PROGRAMS': str(self.programs)})
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn('Missing program: gate (for the tasker role)', run.stdout)

    def role_before_remove(self, step):
        root = self.packages / 'agent_gest_git_skills'
        manifest = json.loads((root / 'skill-package.json').read_text())
        manifest['install_extras']['before_remove'] = step
        (root / 'skill-package.json').write_text(json.dumps(manifest))
        commit(root)

    def test_the_removal_of_a_role_runs_its_step_while_the_hook_is_there(self):
        self.have('cargo', 'cargo-llvm-cov')
        self.role_before_remove({'git_hook': 'pre-commit', 'command': 'fakeprog install unhooked'})
        self.assertEqual(self.run_rust().returncode, 0)
        hook = self.target / '.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\n')
        run = self.stack('--without', 'tasker', '--dry-run', env={'FAKE_PROGRAMS': str(self.programs)})
        self.assertIn('Would run before the removal of tasker: fakeprog install unhooked', run.stdout)
        self.assertNotIn('fakeprog install unhooked toolchain=False', self.tools_run())
        run = self.stack('--without', 'tasker', env={'FAKE_PROGRAMS': str(self.programs)})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('fakeprog install unhooked toolchain=False', self.tools_run())
        self.assertIn('Before the removal of tasker, the installer runs: fakeprog install unhooked', run.stderr)

    def test_the_step_does_not_run_without_its_hook_and_a_failure_does_not_stop_the_removal(self):
        self.have('cargo', 'cargo-llvm-cov')
        self.role_before_remove({'git_hook': 'pre-commit', 'command': 'fakeprog install unhooked'})
        self.assertEqual(self.run_rust().returncode, 0)
        run = self.stack('--without', 'tasker', env={'FAKE_PROGRAMS': str(self.programs)})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('fakeprog install unhooked toolchain=False', self.tools_run())
        self.assertEqual(self.run_rust('--with', 'tasker').returncode, 0)
        (self.target / '.git/hooks/pre-commit').write_text('#!/bin/sh\n')
        run = self.stack('--without', 'tasker', env={'FAKE_PROGRAMS': str(self.programs), 'FAIL_INSTALL': 'unhooked'})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('fakeprog install unhooked failed (3)', run.stderr)
        self.assertFalse((self.target / '.claude/hooks/start.sh').exists())

    def test_a_malformed_step_before_removal_stops_the_install_of_the_role(self):
        self.have('cargo', 'cargo-llvm-cov')
        self.role_before_remove({'git_hook': 'pre-commit'})
        run = self.run_rust()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('install_extras.before_remove in', run.stderr)
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), ['.git'])

    def test_a_malformed_step_before_removal_is_refused_before_anything_changes(self):
        self.have('cargo', 'cargo-llvm-cov')
        self.assertEqual(self.run_rust().returncode, 0)
        for step in [{'git_hook': 'pre-commit'}, {'git_hook': '../x', 'command': 'fakeprog x'},
                     {'git_hook': 'pre-commit', 'command': '/bin/sh -c x'}, 'fakeprog x']:
            self.role_before_remove(step)
            run = self.stack('--without', 'tasker', env={'FAKE_PROGRAMS': str(self.programs)})
            self.assertNotEqual(run.returncode, 0, step)
            self.assertIn('install_extras.before_remove in', run.stderr)
            self.assertTrue((self.target / '.claude/hooks/start.sh').is_file())

    def test_check_reports_missing_programs(self):
        self.have('cargo')
        self.assertEqual(self.run_rust().returncode, 0)
        run = self.stack('--check', env={'FAKE_PROGRAMS': str(self.programs)})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        (self.programs / 'cargo-llvm-cov').unlink()
        (self.programs / 'cargo').unlink()
        run = self.stack('--check', env={'FAKE_PROGRAMS': str(self.programs)})
        self.assertEqual(run.returncode, 1)
        self.assertIn('Missing program: cargo (for the rust profile): fakeprog check cargo failed. Install Rust with '
                      'rustup', run.stdout)
        self.assertIn('Missing program: cargo-llvm-cov (for the rust profile): fakeprog check cargo-llvm-cov failed. '
                      'just install runs: fakeprog install cargo-llvm-cov', run.stdout)
        # --check changes nothing: it runs no install.
        self.assertFalse((self.programs / 'cargo-llvm-cov').exists())

    def test_check_counts_pending_tools_and_leaves_out_tools_being_removed(self):
        self.have('cargo', 'cargo-llvm-cov')
        self.assertEqual(self.run_rust().returncode, 0)
        (self.programs / 'cargo-llvm-cov').unlink()
        record = self.saved()
        record['tools'], record['removing_tools'] = ['rust:lint', 'rust:test'], ['rust:test']
        (self.target / '.agents/stack.json').write_text(json.dumps(record))
        run = self.stack('--check', env={'FAKE_PROGRAMS': str(self.programs)})
        self.assertNotIn('Missing program', run.stdout)
        record['tools'], record['pending_tools'] = ['rust:lint'], ['rust:test']
        record.pop('removing_tools')
        (self.target / '.agents/stack.json').write_text(json.dumps(record))
        run = self.stack('--check', env={'FAKE_PROGRAMS': str(self.programs)})
        self.assertIn('Missing program: cargo-llvm-cov', run.stdout)
        self.assertEqual(run.returncode, 1)
        record['removing_profiles'] = ['rust']
        (self.target / '.agents/stack.json').write_text(json.dumps(record))
        run = self.stack('--check', env={'FAKE_PROGRAMS': str(self.programs)})
        self.assertNotIn('Missing program', run.stdout)

    def test_check_fails_when_a_profile_is_malformed(self):
        self.have('cargo', 'cargo-llvm-cov')
        self.assertEqual(self.run_rust().returncode, 0)
        self.edit_profile('rust', lambda p: (with_programs(p), p['programs'][0].pop('description')))
        run = self.stack('--check', env={'FAKE_PROGRAMS': str(self.programs)})
        self.assertEqual(run.returncode, 1)
        self.assertIn('The programs of the rust profile are not checked', run.stdout)

    def test_check_skips_a_profile_without_a_clone_and_clones_nothing(self):
        self.have('cargo')
        self.assertEqual(self.run_rust().returncode, 0)
        shutil.rmtree(self.packages / 'jnprofile-rust')
        run = self.stack('--check', env={'FAKE_PROGRAMS': str(self.programs)})
        self.assertIn('The programs of the rust profile are not checked', run.stdout)
        self.assertNotIn('Cloning', run.stderr)
        self.assertFalse((self.packages / 'jnprofile-rust').exists())


# PartsTests, StepTests and ProgramTests use the fixtures of StackTests, and run only their own tests.
for _class in (PartsTests, StepTests, ProgramTests):
    for _name in [name for name in dir(StackTests) if name.startswith('test_') and name not in _class.__dict__]:
        setattr(_class, _name, None)
# The loader would find the class under this name too, and run its tests twice.
del _class

if __name__ == '__main__':
    unittest.main()
