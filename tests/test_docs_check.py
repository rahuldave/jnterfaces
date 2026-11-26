"""just check of jnterfaces builds its docs site with the docs-check of the docs role, so a broken page fails before a
merge, and not first in the job docs on main."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROLE_RECIPES = '.agents/skills/docs/docs.just'
# A small site: a config, a home page, and a page that links to the home page.
CONFIG = '''project:
  type: website
  output-dir: _site
website:
  title: fixture
  navbar:
    left:
      - href: index.md
        text: Home
      - href: page.md
        text: Page
'''
PAGES = {'index.md': '# Home\n\nThe [page](page.md).\n', 'page.md': '# Page\n\nBack [home](index.md).\n'}


def dump(root=REPO):
    return json.loads(subprocess.run(['just', '--dump', '--dump-format', 'json'], cwd=root, check=True,
                                     capture_output=True, text=True).stdout)['recipes']


def lines(recipe):
    return [''.join(part if isinstance(part, str) else '' for part in line).strip() for line in recipe['body']]


def workflow_steps():
    read = 'import json, sys, yaml; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))'
    out = subprocess.run(['uv', 'run', '--no-project', '--quiet', '--with', 'pyyaml==6.0.2', 'python', '-c', read,
                          str(REPO / '.github/workflows/verify.yml')], check=True, capture_output=True, text=True).stdout
    return json.loads(out)['jobs']['verify']['steps']


class StepTests(unittest.TestCase):
    def test_just_check_runs_docs_check_as_a_line_of_its_own(self):
        self.assertIn('just docs-check', lines(dump()['check']))

    def test_docs_check_runs_the_step_of_the_docs_role(self):
        # jnterfaces checks its site with the recipe that the docs role gives each project, not a copy of it.
        body = lines(dump()['docs-check'])
        self.assertEqual(len(body), 1, body)
        words = body[0].split()
        self.assertEqual(words[0], 'just')
        self.assertEqual(words[-1], 'docs-check')
        self.assertEqual(words[words.index('--justfile') + 1], ROLE_RECIPES)
        self.assertEqual(words[words.index('--working-directory') + 1], '.')
        # The step of the role ends with jlog step, which works only under the shell jlog.
        self.assertEqual(words[words.index('--shell') + 1], 'jlog')

    def test_the_step_takes_the_smalldocs_of_the_publish(self):
        # docs-check runs the pin of the docs role, and docs-publish of jnterfaces the pin of the Justfile.
        pin = re.compile(r'smalldocs@([0-9.]+)')
        self.assertEqual(pin.findall((REPO / ROLE_RECIPES).read_text()), pin.findall((REPO / 'Justfile').read_text()))

    def test_the_job_verify_runs_docs_check_after_it_installs_jlog(self):
        runs = [step.get('run') or '' for step in workflow_steps()]
        check = runs.index('just docs-check')
        jlog = next(n for n, run in enumerate(runs) if 'rahuldave/jlog' in run)
        self.assertLess(jlog, check)


# CI installs jlog, so there the tests run, and fail when jlog is missing.
@unittest.skipUnless(shutil.which('jlog') or os.environ.get('CI'), 'docs-check runs with the shell jlog of skill-package.json')
class FixtureTests(unittest.TestCase):
    """just docs-check of the Justfile of jnterfaces, on a small site in a scratch project."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        shutil.copy(REPO / 'Justfile', self.root / 'Justfile')
        (self.root / ROLE_RECIPES).parent.mkdir(parents=True)
        shutil.copy(REPO / ROLE_RECIPES, self.root / ROLE_RECIPES)
        self.docs = self.root / 'docs'
        self.docs.mkdir()
        (self.docs / '_smalldocs.yml').write_text(CONFIG)
        for name, text in PAGES.items():
            (self.docs / name).write_text(text)
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)

    def check(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith('JUST_')}
        return subprocess.run(['just', 'docs-check'], cwd=self.root, env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=300)

    def tree(self):
        return {p.relative_to(self.docs).as_posix(): p.read_bytes() for p in self.docs.rglob('*') if p.is_file()}

    def test_a_good_site_passes_and_nothing_is_written_to_docs_or_gh_pages(self):
        before = self.tree()
        run = self.check()
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(self.tree(), before)
        branches = subprocess.run(['git', 'branch', '--list', '--all'], cwd=self.root, capture_output=True, text=True)
        self.assertNotIn('gh-pages', branches.stdout)

    def test_a_broken_link_fails(self):
        (self.docs / 'page.md').write_text('# Page\n\nA [gone page](gone.md).\n')
        run = self.check()
        self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('broken link gone.md', run.stdout + run.stderr)

    def test_a_broken_page_fails(self):
        # A page whose front matter smalldocs cannot read stops the build, as it stops the publish.
        (self.docs / 'page.md').write_text('---\ntitle: [\n---\n# Page\n')
        run = self.check()
        self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('page.md', run.stdout + run.stderr)

    def test_a_missing_include_fails(self):
        (self.docs / 'page.md').write_text('# Page\n\n{{< include missing.md >}}\n')
        run = self.check()
        self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('include missing.md not found', run.stdout + run.stderr)


if __name__ == '__main__':
    unittest.main()
