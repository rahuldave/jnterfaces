import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PUBLISH = REPO / '.agents/skills/docs/docs_publish.py'
# A stand-in for gh: it prints the visibility in GH_VISIBILITY, or fails when that is empty.
FAKE_GH = '''import os, sys
with open(os.environ['PUBLISH_LOG'] + '.gh', 'a') as log:
    log.write(' '.join(sys.argv[1:]) + '\\n')
if os.environ.get('GH_EMPTY'):
    sys.exit(0)
seen = os.environ.get('GH_VISIBILITY', '')
if not seen:
    sys.stderr.write('gh: To use GitHub CLI in a GitHub Actions workflow, set the GH_TOKEN environment variable\\n')
    sys.exit(4)
print(seen)
'''
# A stand-in for smalldocs: it writes its arguments to a log.
FAKE_SMALLDOCS = '''import os, sys
with open(os.environ['PUBLISH_LOG'], 'a') as log:
    log.write(' '.join(sys.argv[1:]) + '\\n')
'''


class DocsPublishTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        (self.bin / 'gh').write_text(f'#!{sys.executable}\n' + FAKE_GH)
        (self.bin / 'gh').chmod(0o755)
        (self.root / 'smalldocs.py').write_text(FAKE_SMALLDOCS)
        self.log = self.root / 'publish.log'
        self.project = self.root / 'project'
        self.project.mkdir()
        subprocess.run(['git', 'init', '-q'], cwd=self.project, check=True)
        for name, url in (('origin', 'https://github.com/me/mine.git'), ('upstream', 'https://github.com/cli/cli.git')):
            subprocess.run(['git', 'remote', 'add', name, url], cwd=self.project, check=True)

    def publish(self, visibility, ci=None, **extra):
        env = {k: v for k, v in os.environ.items() if k not in ('CI', 'GH_TOKEN', 'GITHUB_TOKEN')}
        env.update(PATH=f'{self.bin}:{os.environ["PATH"]}', GH_VISIBILITY=visibility, PUBLISH_LOG=str(self.log))
        env.update(extra)
        if ci is not None:
            env['CI'] = ci
        command = f'{sys.executable} {self.root / "smalldocs.py"}'
        return subprocess.run([sys.executable, str(PUBLISH), command], cwd=self.project, env=env,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def published(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_gh_asks_about_origin_where_smalldocs_pushes_not_upstream(self):
        self.assertEqual(self.publish('PUBLIC').returncode, 0)
        asked = Path(f'{self.log}.gh').read_text()
        self.assertIn('repo view https://github.com/me/mine.git --json visibility', asked)

    def test_gh_asks_about_the_push_url_when_origin_has_one(self):
        subprocess.run(['git', 'config', 'remote.origin.pushurl', 'https://github.com/me/private.git'],
                       cwd=self.project, check=True)
        self.assertEqual(self.publish('PUBLIC').returncode, 0)
        self.assertIn('repo view https://github.com/me/private.git', Path(f'{self.log}.gh').read_text())

    def test_an_empty_answer_of_gh_fails_in_ci_and_skips_otherwise(self):
        run = self.publish('PUBLIC', 'true', GH_EMPTY='1')
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn('gh gave no visibility', run.stderr)
        self.assertEqual(self.publish('PUBLIC', GH_EMPTY='1').returncode, 0)
        self.assertEqual(self.published(), [])

    def test_a_repository_with_no_origin_is_not_published(self):
        subprocess.run(['git', 'remote', 'remove', 'origin'], cwd=self.project, check=True)
        run = self.publish('PUBLIC')
        self.assertEqual(run.returncode, 0)
        self.assertIn('no remote origin', run.stdout)
        self.assertEqual(self.published(), [])

    def test_a_public_repository_is_published_to_gh_pages(self):
        for ci in (None, 'true'):
            run = self.publish('PUBLIC', ci)
            self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.published(), ['publish gh-pages docs'] * 2)

    def test_a_private_or_internal_repository_is_skipped(self):
        for seen in ('PRIVATE', 'INTERNAL'):
            for ci in (None, 'true'):
                run = self.publish(seen, ci)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertIn(f'skipped, because the repository is {seen}', run.stdout)
        self.assertEqual(self.published(), [])

    def test_an_error_of_gh_fails_in_ci_and_skips_on_a_machine(self):
        run = self.publish('', 'true')
        self.assertEqual(run.returncode, 1)
        self.assertIn('GH_TOKEN', run.stderr)
        for ci in (None, 'false', '0'):
            run = self.publish('', ci)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('skipped, because gh cannot read the visibility', run.stdout)
        self.assertEqual(self.published(), [])

    def test_the_answer_no_skips_a_public_repository(self):
        (self.project / '.agents').mkdir()
        (self.project / '.agents/stack.json').write_text(json.dumps({'answers': {'docs-publish': 'no'}}))
        run = self.publish('PUBLIC', 'true')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('docs-publish=no', run.stdout)
        self.assertEqual(self.published(), [])

    def test_a_failed_publish_fails(self):
        (self.root / 'smalldocs.py').write_text('import sys\nsys.exit(7)\n')
        self.assertEqual(self.publish('PUBLIC', 'true').returncode, 7)


class PinTests(unittest.TestCase):
    def test_the_docs_role_pins_a_smalldocs_that_reads_typescript_and_rust(self):
        # Before 0.5.10, smalldocs read each api section as Python, so a TypeScript section stopped docs-check.
        import re
        pins = {path: re.findall(r'smalldocs@([0-9.]+)', (REPO / path).read_text())
                for path in ('Justfile', '.agents/skills/docs/docs.just', '.agents/skills/docs/docs_publish.py')}
        versions = {version for found in pins.values() for version in found}
        self.assertEqual(len(versions), 1, pins)
        self.assertGreaterEqual(tuple(map(int, versions.pop().split('.'))), (0, 5, 10))


class ThemeTests(unittest.TestCase):
    ROLE = REPO / '.agents/skills/docs/assets'

    def test_the_starter_config_names_atelier_and_the_role_has_its_files(self):
        self.assertIn('theme: atelier', (self.ROLE / '_smalldocs.yml').read_text().splitlines())
        theme = self.ROLE / '_extensions/atelier'
        config = (theme / 'smalldocs.yml').read_text()
        for name in ('atelier.css', 'atelier-dark.css'):
            self.assertIn(name, config)
            self.assertTrue((theme / name).is_file(), name)

    def test_the_skill_names_no_path_of_the_repository_root(self):
        # package-lint stops on a SKILL.md that names a folder of the root of jnterfaces, such as docs/_extensions.
        self.assertNotIn('docs/_extensions', (REPO / '.agents/skills/docs/SKILL.md').read_text())

    def test_the_role_ships_the_same_atelier_as_the_site_of_jnterfaces(self):
        # An update of the theme with smalldocs add changes docs/_extensions/atelier. Copy it to the role too.
        def files(root):
            return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()}
        self.assertEqual(files(self.ROLE / '_extensions/atelier'), files(REPO / 'docs/_extensions/atelier'))


if __name__ == '__main__':
    unittest.main()
