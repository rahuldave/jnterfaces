"""Tests for the recipe just test of this repository: the words it gives to pytest."""
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
# In place of the Python of the tests, a command that prints the words it gets, as JSON.
ECHO = shlex.join([sys.executable, '-c', 'import json, sys; print(json.dumps(sys.argv[1:]))'])


@unittest.skipUnless(shutil.which('just') and shutil.which('bash'), 'needs just and bash')
class TestRecipeWordsTests(unittest.TestCase):
    def words(self, *args):
        run = subprocess.run(['just', '--justfile', REPO / 'Justfile', '--set', 'test_python', ECHO, 'test', *args],
                             capture_output=True, text=True, cwd=REPO)
        self.assertEqual(run.returncode, 0, run.stderr)
        words = json.loads(run.stdout)
        # pytest reads no configuration file from a folder above this one, and runs in parallel.
        self.assertEqual(words[:10], ['-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-c', '/dev/null',
                                      '--rootdir', '.', '-o'])
        self.assertEqual(words[10:13], ['testpaths=tests', '-n', 'auto'])
        return words[13:]

    def test_no_words_runs_every_test(self):
        self.assertEqual(self.words(), [])

    def test_a_unittest_name_becomes_a_pytest_name(self):
        self.assertEqual(self.words('tests.test_skills'), ['tests/test_skills.py'])
        self.assertEqual(self.words('tests.test_skills.SkillTest.test_x'), ['tests/test_skills.py::SkillTest::test_x'])

    def test_a_path_and_the_options_stay_as_they_are(self):
        self.assertEqual(self.words('tests.test_skills.py', 'tests/test_skills.py', '-k', 'a and b', '-x'),
                         ['tests.test_skills.py', 'tests/test_skills.py', '-k', 'a and b', '-x'])


if __name__ == '__main__':
    unittest.main()
