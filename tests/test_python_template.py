"""Run the test and cov recipes of the python-uv.just template with real just, uv and pytest."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / '.agents/skills/just/assets/templates/python-uv.just'
PYPROJECT = '''[project]
name = "app"
version = "0"
requires-python = ">=3.12"

[tool.uv]
package = false
default-groups = "all"

[dependency-groups]
test = ["pytest", "pytest-cov"]

[tool.pytest.ini_options]
testpaths = ["tests"]
{extra}'''
PASS = 'def test_pass():\n    assert True\n'


@unittest.skipUnless(shutil.which('just') and shutil.which('uv'), 'needs just and uv')
class TestRecipeTests(unittest.TestCase):
    def project(self, files, extra=''):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        shutil.copy(TEMPLATE, root / 'Justfile')
        (root / 'pyproject.toml').write_text(PYPROJECT.format(extra=extra))
        (root / 'tests').mkdir()
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        return root

    def just(self, root, *args, env=None):
        run = subprocess.run(['just', *args], cwd=root, capture_output=True, text=True,
                             env=dict(os.environ, **(env or {})))
        return run.returncode, run.stdout + run.stderr

    def test_a_project_with_no_tests_yet_passes_alone(self):
        root = self.project({})
        for recipe in ('test', 'cov'):
            code, out = self.just(root, recipe)
            self.assertEqual(code, 0, out)
            self.assertIn('no tests yet', out)

    def test_a_selection_that_runs_nothing_fails(self):
        # testpaths leaves out integ/, so a collection without the selection would see no test.
        root = self.project({'integ/test_integ.py': PASS})
        for recipe in ('test', 'cov'):
            self.assertEqual(self.just(root, recipe, 'integ', '-k', 'nomatch')[0], 5, recipe)
        root = self.project({'tests/test_a.py': PASS})
        self.assertEqual(self.just(root, 'test', '-k', 'nomatch')[0], 5)

    def test_addopts_that_deselect_every_test_fail(self):
        root = self.project({'tests/test_a.py': PASS}, extra='addopts = "-k nomatch"\n')
        for recipe in ('test', 'cov'):
            self.assertEqual(self.just(root, recipe)[0], 5, recipe)

    def test_pytest_addopts_that_deselect_every_test_fail(self):
        root = self.project({'tests/test_a.py': PASS})
        for recipe in ('test', 'cov'):
            self.assertEqual(self.just(root, recipe, env={'PYTEST_ADDOPTS': '-k nomatch'})[0], 5, recipe)
        # A harmless PYTEST_ADDOPTS keeps the pass of a project with no tests yet.
        self.assertEqual(self.just(self.project({}), 'test', env={'PYTEST_ADDOPTS': '-q'})[0], 0)

    def test_a_conftest_hook_that_removes_every_test_fails(self):
        loud = ('def pytest_collection_modifyitems(config, items):\n'
                '    config.hook.pytest_deselected(items=items[:])\n    items[:] = []\n')
        quiet = 'def pytest_collection_modifyitems(config, items):\n    items[:] = []\n'
        for hook in (loud, quiet):
            root = self.project({'tests/test_a.py': PASS, 'tests/conftest.py': hook})
            for recipe in ('test', 'cov'):
                self.assertEqual(self.just(root, recipe)[0], 5, recipe)

    def test_pytest_still_says_no_tests_collected(self):
        # The recipes rely on this summary line of pytest for a project with no tests.
        run = subprocess.run(['uv', 'run', 'pytest', '--collect-only', '-q'], cwd=self.project({}),
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 5)
        self.assertRegex(run.stdout, r'(?m)^no tests collected in ')

    def test_a_pass_a_failure_and_a_broken_file(self):
        self.assertEqual(self.just(self.project({'tests/test_a.py': PASS}), 'test')[0], 0)
        self.assertEqual(self.just(self.project({'tests/test_a.py': 'def test_f():\n    assert False\n'}), 'test')[0], 1)
        self.assertEqual(self.just(self.project({'tests/test_a.py': 'def test_x(:\n'}), 'test')[0], 2)

    def test_verify_runs_the_tests_with_no_arguments(self):
        # So that verify passes on a project with no tests yet.
        self.assertIn('\n    just test\n', TEMPLATE.read_text())


if __name__ == '__main__':
    unittest.main()
