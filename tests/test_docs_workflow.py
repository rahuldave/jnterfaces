"""The Rust step of the docs-publish workflow. just docs-publish runs smalldocs. smalldocs runs cargo only for a section
api with language: rust, with cargo +TOOLCHAIN of that section, nightly by default, never the toolchain of the project,
and it builds in a temporary folder. So the workflow keeps no cache, reads no toolchain file of the project, and installs only the
toolchain of each such section."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from ruamel.yaml import YAML

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / '.agents/skills/docs/assets/docs-publish.yml'
RECIPES = REPO / '.agents/skills/docs/docs.just'
NAME = 'Install the Rust toolchain of the API pages'
# A value that changes on each run. A CARGO or RUST variable with such a value changes the fingerprints of cargo, so
# each run would build everything again.
PER_RUN = re.compile(r'github\.(run_id|run_number|run_attempt|sha)|github\.event\.|steps\.|\$\(|`|date|RANDOM')
# A rustup fake: it only logs.
FAKE_RUSTUP = '#!/bin/sh\necho "rustup $*" >> "$RUSTUP_LOG"\n'
# A uvx fake: it runs the code after -c with the test's Python, and a module smalldocs.config whose load gives the api
# section of FAKE_API, a Python literal. So the tests need no network and no smalldocs.
FAKE_UVX = textwrap.dedent('''\
    #!{python}
    import ast, os, sys, types
    args = sys.argv[1:]
    assert args[:3] == ['--quiet', '--from', '{pin}'], args
    assert args[3:5] == ['python', '-I'] and args[5] == '-c', args
    config = types.ModuleType('smalldocs.config')
    class Project:
        smalldocs = {{'api': ast.literal_eval(os.environ['FAKE_API'])}} if os.environ['FAKE_API'] not in ('none', 'broken') else {{}}
    def load(site):
        assert site == 'docs', site
        if os.environ['FAKE_API'] == 'broken':
            raise ValueError('docs has no _smalldocs.yml or _quarto.yml')
        return Project()
    config.load = load
    sys.modules['smalldocs'] = types.ModuleType('smalldocs')
    sys.modules['smalldocs.config'] = config
    exec(compile(args[6], '<step>', 'exec'), {{'__name__': '__main__'}})
''')


def load():
    return YAML(typ='safe').load(WORKFLOW.read_text())


def steps():
    return load()['jobs']['publish']['steps']


def step(name):
    found = [item for item in steps() if item.get('name') == name]
    assert len(found) == 1, name
    return found[0]


def pin():
    """The smalldocs of the recipes of the role, such as smalldocs@0.5.13."""
    [found] = re.findall(r'^jn_smalldocs := "uvx --quiet (smalldocs@[0-9.]+)"$', RECIPES.read_text(), re.M)
    return found


class NoCacheTests(unittest.TestCase):
    def test_the_workflow_keeps_no_cache_of_rust(self):
        # The steps, without the comments, which explain why there is no cache. The step that installs the pinned
        # tools names $target/ in its downloads, and tests/test_workflow_pins.py checks it.
        text = repr([item for item in steps() if item.get('name') != 'Install the pinned tools'])
        self.assertNotIn('actions/cache', WORKFLOW.read_text())
        self.assertNotIn('actions/cache', text)
        for word in ('target/', 'Cargo.lock', '.cargo/registry', 'cache-primary-key', 'llvm-cov'):
            self.assertNotIn(word, text)

    def test_the_workflow_reads_no_toolchain_file(self):
        text = WORKFLOW.read_text()
        self.assertNotRegex(text, r"hashFiles\('rust-toolchain")
        self.assertNotRegex(text, r'RUSTUP_TOOLCHAIN|dtolnay|rust-toolchain@|rustup (default|override)')
        for item in steps():
            self.assertNotIn('toolchain', item.get('with') or {})
            for line in str(item.get('run', '')).splitlines():
                if line.strip().startswith('rustup '):
                    self.assertEqual(line.strip(), 'rustup toolchain install --profile minimal "$toolchain"')

    def test_the_toolchain_comes_before_the_publish(self):
        names = [item.get('name') or item.get('uses') for item in steps()]
        self.assertLess(names.index(NAME), names.index('Publish'))
        self.assertLess(names.index('Install the pinned tools'), names.index(NAME))
        self.assertEqual(step('Publish')['run'], 'just docs-publish')

    def test_the_step_takes_the_smalldocs_of_the_recipes(self):
        self.assertIn(f'uvx --quiet --from {pin()} python -I -c', step(NAME)['run'])

    def test_no_cargo_or_rust_variable_changes_from_run_to_run(self):
        data = load()
        envs = [data.get('env') or {}]
        for job in data['jobs'].values():
            envs.append(job.get('env') or {})
            envs += [item.get('env') or {} for item in job['steps']]
        for env in envs:
            for key, value in env.items():
                if key.upper().startswith(('CARGO', 'RUST')):
                    self.assertNotEqual(key, 'RUSTUP_TOOLCHAIN')
                    self.assertNotRegex(str(value), PER_RUN, key)
        for item in steps():
            for line in str(item.get('run', '')).splitlines():
                if 'GITHUB_ENV' in line:
                    self.assertNotRegex(line, r'\b(CARGO|RUST)\w*=', line)

    def test_no_tool_is_built_with_cargo_install(self):
        self.assertNotRegex(WORKFLOW.read_text(), r'cargo\s+(\+\S+\s+)?(b?install)\b')


class ApiToolchainTests(unittest.TestCase):
    """Run the script of the step in bash, with a fake uvx and a fake rustup, on each kind of api section."""

    def run_step(self, api, uvx=None):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'bin').mkdir()
            (root / 'bin/rustup').write_text(FAKE_RUSTUP)
            (root / 'bin/rustup').chmod(0o755)
            if uvx is None:
                (root / 'bin/uvx').write_text(FAKE_UVX.format(python=sys.executable, pin=pin()))
                (root / 'bin/uvx').chmod(0o755)
            project = root / 'project'
            (project / 'docs').mkdir(parents=True)
            log = root / 'rustup.log'
            env = dict(os.environ, PATH=f'{root / "bin"}:{os.environ["PATH"]}', RUSTUP_LOG=str(log),
                       FAKE_API=api)
            run = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', step(NAME)['run']], cwd=project, env=env,
                                 capture_output=True, text=True)
            return run, log.read_text() if log.exists() else ''

    def test_a_site_without_rust_pages_installs_nothing(self):
        for api in ('none', '{}', '[]', "{'language': 'python'}", "[{'language': 'typescript'}]"):
            with self.subTest(api=api):
                run, log = self.run_step(api)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(log, '')

    def test_a_rust_section_installs_nightly_by_default(self):
        run, log = self.run_step("{'language': 'rust'}")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(log, 'rustup toolchain install --profile minimal nightly\n')

    def test_each_rust_section_installs_its_toolchain(self):
        run, log = self.run_step("[{'language': 'rust', 'toolchain': 'nightly-2026-11-11'}, {'language': 'python'},"
                                 " {'language': 'rust', 'toolchain': '1.95.0'}]")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(log, 'rustup toolchain install --profile minimal nightly-2026-11-11\n'
                              'rustup toolchain install --profile minimal 1.95.0\n')

    def test_a_toolchain_name_that_is_not_plain_is_skipped(self):
        # smalldocs then says that the toolchain is not installed. No text of the config goes to the shell.
        for name in ('a b', 'x;touch pwned', '$(id)', 1.95, '../x', '-v', '--help'):
            with self.subTest(name=name):
                run, log = self.run_step(repr({'language': 'rust', 'toolchain': name}))
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(log, '')

    def test_a_config_that_smalldocs_cannot_read_installs_nothing(self):
        # The publish skips with docs-publish=no without a read of the config, so this step must not fail first.
        run, log = self.run_step('broken')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(log, '')

    def test_a_failed_uvx_fails_the_step(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'uvx').write_text('#!/bin/sh\nexit 3\n')
            (Path(temp) / 'uvx').chmod(0o755)
            run = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', step(NAME)['run']], cwd=temp,
                                 env=dict(os.environ, PATH=f'{temp}:{os.environ["PATH"]}'),
                                 capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)


@unittest.skipUnless(shutil.which('uvx'), 'needs uvx')
class RealSmalldocsTests(unittest.TestCase):
    """The script reads the config with the load of the real smalldocs of the pin."""

    def test_the_real_load_gives_the_toolchain_of_the_rust_section(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'bin').mkdir()
            (root / 'bin/rustup').write_text(FAKE_RUSTUP)
            (root / 'bin/rustup').chmod(0o755)
            (root / 'docs').mkdir()
            (root / 'docs/_smalldocs.yml').write_text(
                'project:\n  type: website\napi:\n  language: rust\n  toolchain: nightly-2026-11-11\n')
            log = root / 'rustup.log'
            run = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', step(NAME)['run']], cwd=root,
                                 env=dict(os.environ, PATH=f'{root / "bin"}:{os.environ["PATH"]}',
                                          RUSTUP_LOG=str(log)),
                                 capture_output=True, text=True, timeout=300)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(log.read_text(), 'rustup toolchain install --profile minimal nightly-2026-11-11\n')


if __name__ == '__main__':
    unittest.main()
