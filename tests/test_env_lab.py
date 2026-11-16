import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LAB = REPO / 'scripts/env_lab.py'
sys.path.insert(0, str(REPO / 'scripts'))
import env_lab  # noqa: E402


class EnvLabTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def lab(self, justfile, *recipes, env=None, through_install=False):
        (self.root / 'Justfile').write_text(justfile)
        full = dict(os.environ, **(env or {}))
        if through_install:
            # The recipe of .agents/install.just, as a project has it, with this clone as JNTERFACES_DIR.
            (self.root / '.agents').mkdir(exist_ok=True)
            (self.root / '.agents/install.just').write_text((REPO / '.agents/install.just').read_text())
            with open(self.root / 'Justfile', 'a') as handle:
                handle.write("\nimport? '.agents/install.just'\n")
            command = ['just', 'env-lab', *recipes]
            full['JNTERFACES_DIR'] = str(REPO)
        else:
            command = [sys.executable, str(LAB), '--dir', str(self.root), *recipes]
        return subprocess.run(command, cwd=self.root, env=full, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)

    def test_a_recipe_that_needs_a_setting_of_the_shell_fails_and_names_set_export_and_env(self):
        run = self.lab('need:\n    test -n "$JN_LAB_SETTING"\n', 'need', env={'JN_LAB_SETTING': 'x'})
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn('need passes in your shell, but fails with a clean environment', run.stdout)
        self.assertIn('the clean environment does not: JN_LAB_SETTING.', run.stdout)
        self.assertIn('set export', run.stdout)
        self.assertIn('.env', run.stdout)
        # The value never shows.
        self.assertNotIn("JN_LAB_SETTING=x", run.stdout)

    def test_the_lab_also_runs_through_just_env_lab(self):
        run = self.lab('need:\n    test -n "$JN_LAB_SETTING"\n', 'need', env={'JN_LAB_SETTING': 'x'},
                       through_install=True)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn('set export', run.stdout)

    def test_just_runs_with_no_option_so_that_jlog_pass_takes_the_run(self):
        # A stand-in for just logs its arguments, then runs the real just.
        bin_ = self.root / 'bin'
        bin_.mkdir()
        real = shutil.which('just')
        (bin_ / 'just').write_text(f'#!/bin/sh\necho "$@" >> {self.root / "argv.log"}\nexec {real} "$@"\n')
        (bin_ / 'just').chmod(0o755)
        run = self.lab('check:\n    true\n', env={'PATH': f'{bin_}:{os.environ["PATH"]}'})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        runs = [line for line in (self.root / 'argv.log').read_text().splitlines() if not line.startswith('--dump')]
        self.assertEqual(runs, ['check'])

    def test_a_variable_that_the_justfile_sets_is_not_blamed_on_the_shell(self):
        (self.root / '.env').write_text('JN_FROM_DOT=1\n')
        justfile = ('set export\nset dotenv-load\nJN_OWN := "1"\nexport JLOG_RUN := env("JLOG_RUN", uuid())\n'
                    'need:\n    test -n "$JN_OWN$JN_FROM_DOT$JLOG_RUN" && test -n "$JN_SHELL_ONLY"\n')
        run = self.lab(justfile, 'need', env={'JN_SHELL_ONLY': 'x', 'JN_OWN': 'y', 'JN_FROM_DOT': 'z'},
                       through_install=True)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn('the clean environment does not: JN_SHELL_ONLY.', run.stdout)

    def test_env_var_and_env_var_or_default_are_named(self):
        run = self.lab('tok := env_var("JN_TOK")\nother := env_var_or_default("JN_OTHER", "")\n'
                       'need:\n    test -n "{{other}}"\n', 'need', env={'JN_TOK': 'x', 'JN_OTHER': 'y'})
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn('the clean environment does not: JN_OTHER, JN_TOK.', run.stdout)

    def test_a_recipe_with_arguments_goes_in_quotes(self):
        run = self.lab('greet name:\n    test "{{name}}" = world && test -n "$JN_LAB_SETTING"\n',
                       'greet world', env={'JN_LAB_SETTING': 'x'})
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn('greet world passes in your shell', run.stdout)

    def test_the_path_from_before_uv_is_the_path_of_the_lab(self):
        self.assertEqual(env_lab.clean_env({'PATH': '/uv/bin:/usr/bin', 'JN_LAB_PATH': '/usr/bin', 'X': '1'}),
                         {'PATH': '/usr/bin'})

    def test_the_rerun_has_the_environment_of_the_shell_not_of_uv(self):
        # uv run sets UV_RUN_RECURSION_DEPTH, and the shell does not.
        run = self.lab('need:\n    test -z "${UV_RUN_RECURSION_DEPTH:-}" && test -n "$JN_LAB_SETTING"\n', 'need',
                       env={'JN_LAB_SETTING': 'x'}, through_install=True)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn('need passes in your shell', run.stdout)
        self.assertEqual(env_lab.shell_env({'PATH': '/uv/bin:/usr/bin', 'JN_LAB_PATH': '/usr/bin', 'UV': 'uv',
                                            'UV_RUN_RECURSION_DEPTH': '1', 'X': '1'}), {'PATH': '/usr/bin', 'X': '1'})

    def test_a_variable_of_an_imported_file_or_a_module_is_named(self):
        (self.root / 'other.just').write_text('need:\n    test -n "$JN_IMP"\n')
        (self.root / 'm.just').write_text('mneed:\n    test -n "$JN_MOD"\n')
        justfile = "import 'other.just'\nmod m\n"
        run = self.lab(justfile, 'need', 'm mneed', env={'JN_IMP': 'x', 'JN_MOD': 'y'})
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn('need passes in your shell', run.stdout)
        self.assertIn('the clean environment does not: JN_IMP.', run.stdout)
        self.assertIn('the clean environment does not: JN_MOD.', run.stdout)

    def test_a_shell_value_behind_a_default_of_the_justfile_is_named(self):
        run = self.lab('export JN_API := env("JN_API", "")\nneed:\n    test -n "$JN_API"\n', 'need',
                       env={'JN_API': 'x'})
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn('the clean environment does not: JN_API.', run.stdout)

    def test_an_unbalanced_quote_is_named_and_does_not_crash(self):
        run = self.lab('check:\n    true\n', 'test -k "a b')
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn('unbalanced quote', run.stderr)
        self.assertNotIn('Traceback', run.stderr)

    def test_only_the_variables_of_the_failing_recipe_and_its_dependencies_are_named(self):
        justfile = ('first:\n    test -n "$JN_DEP"\nneed: first\n    true\nother:\n    echo "$JN_UNRELATED"\n')
        run = self.lab(justfile, 'need', env={'JN_DEP': 'x', 'JN_UNRELATED': 'y'})
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn('the clean environment does not: JN_DEP.', run.stdout)

    def test_an_assignment_of_a_module_is_the_justfiles_own(self):
        (self.root / 'm.just').write_text('set export\nJN_MV := "1"\n'
                                          'mneed:\n    test -n "$JN_MV$JN_X" && test -n "$JN_X"\n')
        run = self.lab('mod m\n', 'm mneed', env={'JN_MV': 'a', 'JN_X': 'b'})
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn('the clean environment does not: JN_X.', run.stdout)

    def test_a_setting_in_the_justfile_or_in_env_passes(self):
        run = self.lab('set export\nJN_LAB_SETTING := "x"\nneed:\n    test -n "$JN_LAB_SETTING"\n', 'need')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('need: passes with a clean environment', run.stdout)
        (self.root / '.env').write_text('JN_LAB_SETTING=x\n')
        run = self.lab('set dotenv-load\nneed:\n    test -n "$JN_LAB_SETTING"\n', 'need')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_a_recipe_that_fails_in_the_shell_too_is_not_blamed_on_the_shell(self):
        run = self.lab('broken:\n    exit 3\n', 'broken')
        self.assertEqual(run.returncode, 1)
        self.assertIn('fails in your shell too', run.stdout)
        self.assertNotIn('set export', run.stdout)

    def test_with_no_recipe_it_runs_check(self):
        run = self.lab('check:\n    true\n')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('check: passes with a clean environment', run.stdout)


if __name__ == '__main__':
    unittest.main()
