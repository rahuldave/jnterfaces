"""The Rust steps of the docs-publish workflow: the toolchain comes from the file of the project, and the cache has the
key, the folders and the save rule of the cache design of the Rust workflows."""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ruamel.yaml import YAML

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / '.agents/skills/docs/assets/docs-publish.yml'
PATHS = ['~/.cargo/registry/index', '~/.cargo/registry/cache', '~/.cargo/git/db', 'target/']
PREFIX = '${{ runner.os }}-${{ runner.arch }}-rust-${{ steps.rust.outputs.toolchain }}-${{ github.job }}-'
KEY = PREFIX + "${{ hashFiles('**/Cargo.lock') }}"
# A value that changes on each run. A CARGO or RUST variable with such a value changes the fingerprints of cargo, so
# each run would build everything again.
PER_RUN = re.compile(r'github\.(run_id|run_number|run_attempt|sha)|github\.event\.|steps\.|\$\(|`|date|RANDOM')
# A rustup fake: rustup toolchain install reads the file, so it only logs.
FAKE_RUSTUP = '#!/bin/sh\necho "rustup $*" >> "$RUSTUP_LOG"\n'


def load():
    return YAML(typ='safe').load(WORKFLOW.read_text())


def steps():
    return load()['jobs']['publish']['steps']


def step(name):
    found = [item for item in steps() if item.get('id') == name]
    assert len(found) == 1, name
    return found[0]


def uses(prefix):
    found = [item for item in steps() if str(item.get('uses', '')).startswith(prefix)]
    assert len(found) == 1, prefix
    return found[0]


class RustCacheTests(unittest.TestCase):
    def test_the_restore_has_the_key_the_restore_key_and_the_folders(self):
        restore = uses('actions/cache/restore@')
        self.assertEqual(restore['id'], 'rust-cache')
        self.assertEqual(restore['with']['key'], KEY)
        self.assertEqual(restore['with']['restore-keys'].strip().splitlines(), [PREFIX])
        self.assertEqual(restore['with']['path'].strip().splitlines(), PATHS)
        self.assertEqual(restore['if'], "steps.rust.outputs.toolchain != ''")

    def test_only_a_push_to_main_saves_the_cache_with_the_same_folders_and_key(self):
        save = uses('actions/cache/save@')
        self.assertEqual(save['with']['path'].strip().splitlines(), PATHS)
        self.assertEqual(save['with']['key'], '${{ steps.rust-cache.outputs.cache-primary-key }}')
        condition = ' '.join(save['if'].split())
        for part in ("steps.rust.outputs.toolchain != ''", "github.event_name == 'push'",
                     "github.ref == 'refs/heads/main'", "steps.rust-cache.outputs.cache-hit != 'true'"):
            self.assertIn(part, condition)
        # Each part must hold: no or, so that a pull request never saves.
        self.assertNotIn('||', condition)
        self.assertNotIn('always()', condition)
        # No other step uses the cache, so nothing else saves one.
        self.assertEqual([item['uses'] for item in steps() if 'cache' in str(item.get('uses', ''))],
                         ['actions/cache/restore@v4', 'actions/cache/save@v4'])

    def test_the_save_comes_after_the_publish_and_the_restore_before_it(self):
        names = [item.get('uses') or item.get('name') for item in steps()]
        self.assertLess(names.index('actions/cache/restore@v4'), names.index('Publish'))
        self.assertGreater(names.index('actions/cache/save@v4'), names.index('Publish'))

    def test_each_action_is_pinned_to_a_major_tag_as_the_other_steps_are(self):
        for item in steps():
            if 'uses' in item:
                self.assertRegex(item['uses'], r'^[\w.-]+/[\w./-]+@v\d+$')


class RustToolchainTests(unittest.TestCase):
    def test_the_rust_steps_run_only_in_a_project_with_a_toolchain_file(self):
        self.assertEqual(step('rust')['if'], "hashFiles('rust-toolchain.toml', 'rust-toolchain') != ''")
        others = [item for item in steps() if item.get('id') != 'rust'
                  and ('rust' in str(item.get('name', '')).lower() or 'cache' in str(item.get('uses', '')))]
        self.assertEqual(len(others), 2)
        for item in others:
            self.assertIn("steps.rust.outputs.toolchain != ''", item['if'])

    def test_the_workflow_names_no_channel(self):
        text = WORKFLOW.read_text()
        self.assertNotRegex(text, r'RUSTUP_TOOLCHAIN|dtolnay|rust-toolchain@|\+(stable|beta|nightly)\b')
        self.assertNotRegex(text, r'rustup (default|override)')
        for item in steps():
            for line in str(item.get('run', '')).splitlines():
                if line.strip().startswith('rustup '):
                    self.assertEqual(line.strip(), 'rustup toolchain install')
            self.assertNotIn('toolchain', item.get('with') or {})

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
        # A run step can set a variable for the later steps through GITHUB_ENV too.
        for item in steps():
            for line in str(item.get('run', '')).splitlines():
                if 'GITHUB_ENV' in line:
                    self.assertNotRegex(line, r'\b(CARGO|RUST)\w*=', line)

    def test_no_tool_is_built_with_cargo_install(self):
        self.assertNotRegex(WORKFLOW.read_text(), r'cargo\s+(\+\S+\s+)?(b?install)\b')


class ChannelScriptTests(unittest.TestCase):
    """Run the script of the step rust in bash, with a fake rustup, on each kind of toolchain file."""

    def run_step(self, files):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'bin').mkdir()
            (root / 'bin/rustup').write_text(FAKE_RUSTUP)
            (root / 'bin/rustup').chmod(0o755)
            project = root / 'project'
            project.mkdir()
            for name, text in files.items():
                (project / name).write_text(text)
            output, log = root / 'output', root / 'rustup.log'
            output.write_text('')
            env = dict(os.environ, PATH=f'{root / "bin"}:{os.environ["PATH"]}', GITHUB_OUTPUT=str(output),
                       RUSTUP_LOG=str(log))
            # python3 in the script is the one of the runner; here it is the one of the tests.
            (root / 'bin/python3').symlink_to(sys.executable)
            run = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', step('rust')['run']], cwd=project, env=env,
                                 capture_output=True, text=True)
            return run, output.read_text(), log.read_text() if log.exists() else ''

    def test_an_exact_version_in_rust_toolchain_toml_is_the_key(self):
        run, output, log = self.run_step({'rust-toolchain.toml': '[toolchain]\nchannel = "1.95.0"\n'})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(output, 'toolchain=1.95.0\n')
        self.assertEqual(log, 'rustup toolchain install\n')
        self.assertNotIn('::warning::', run.stdout)

    def test_the_old_file_holds_a_plain_channel_and_wins_over_the_toml_file(self):
        run, output, _ = self.run_step({'rust-toolchain': '1.90.0\n'})
        self.assertEqual(output, 'toolchain=1.90.0\n')
        run, output, _ = self.run_step({'rust-toolchain': '[toolchain]\nchannel = "nightly-2026-11-12"\n',
                                        'rust-toolchain.toml': '[toolchain]\nchannel = "1.95.0"\n'})
        self.assertEqual(output, 'toolchain=nightly-2026-11-12\n')

    def test_a_moving_channel_warns(self):
        for channel in ('stable', 'nightly', '1.95', 'stable-x86_64-unknown-linux-gnu', 'beta'):
            with self.subTest(channel=channel):
                run, output, _ = self.run_step({'rust-toolchain.toml': f'[toolchain]\nchannel = "{channel}"\n'})
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(output, f'toolchain={channel}\n')
                self.assertIn('::warning::', run.stdout)

    def test_an_exact_channel_does_not_warn(self):
        for channel in ('1.100.0', 'nightly-2026-11-12', 'beta-2026-11-01'):
            with self.subTest(channel=channel):
                run, output, _ = self.run_step({'rust-toolchain.toml': f'[toolchain]\nchannel = "{channel}"\n'})
                self.assertEqual(output, f'toolchain={channel}\n')
                self.assertNotIn('::warning::', run.stdout)

    def test_a_file_with_no_channel_or_a_bad_one_fails(self):
        for text in ('[toolchain]\npath = "/opt/rust"\n', '[toolchain]\nchannel = "1.95.0\\nx=y"\n',
                     '[toolchain]\nchannel = "a b"\n'):
            with self.subTest(text=text):
                run, output, _ = self.run_step({'rust-toolchain.toml': text})
                self.assertNotEqual(run.returncode, 0)
                self.assertIn('::error::', run.stdout)
                self.assertEqual(output, '')


if __name__ == '__main__':
    unittest.main()
