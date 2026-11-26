"""The release tags: just check runs release-check, CI tags each release, and the script makes the next tag.

scripts/release_tag.py is a copy of the script of the gpa skill of agent_gest_git_skills. These tests check that the
copy works here, and that only the job release of CI can write to the repository.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts' / 'release_tag.py'
WORKFLOW = ROOT / '.github' / 'workflows' / 'verify.yml'
# The Git blob of release_tag.py at the commit 26ef730 of agent_gest_git_skills. A change of the copy comes from there.
UPSTREAM_BLOB = '7869b962ca04da0efb9d6756c79a15da587fcaa8'
# A git hook sets these for the repository of the hook, and the scratch repository must not use them.
HOOK_VARIABLES = ('GIT_DIR', 'GIT_INDEX_FILE', 'GIT_WORK_TREE', 'GIT_OBJECT_DIRECTORY')
# Git with no config of the user, such as a signing key, and a fixed author.
GIT_ENV = {key: value for key, value in os.environ.items() if key not in HOOK_VARIABLES}
GIT_ENV |= {'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.com',
            'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.com'}


def run(args, cwd, check=True):
    result = subprocess.run(args, cwd=cwd, env=GIT_ENV, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise AssertionError(f'{args} failed: {result.stdout}{result.stderr}')
    return result


def workflow():
    """The workflow verify.yml as data, read with PyYAML through uv."""
    read = 'import json, sys, yaml; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))'
    out = run(['uv', 'run', '--no-project', '--quiet', '--with', 'pyyaml==6.0.2', 'python', '-c', read, str(WORKFLOW)],
              ROOT).stdout
    return json.loads(out)


def checkout_depth(job):
    return next(step.get('with', {}).get('fetch-depth') for step in job['steps']
                if step.get('uses', '').startswith('actions/checkout@'))


class CheckTest(unittest.TestCase):
    def test_check_runs_release_check(self):
        dump = json.loads(run(['just', '--dump', '--dump-format', 'json'], ROOT).stdout)
        check = dump['recipes']['check']
        dependencies = [dependency['recipe'] for dependency in check['dependencies']]
        lines = [''.join(part if isinstance(part, str) else '' for part in line) for line in check['body']]
        self.assertTrue('release-check' in dependencies or 'just release-check' in lines,
                        'just check does not run release-check')


def recipe_lines(recipe):
    """The text of each line of a recipe from a dump, without the prefix @."""
    return [''.join(part if isinstance(part, str) else '' for part in line).strip().lstrip('@') for line in recipe['body']]


def check_steps():
    """Each recipe that just check runs, through dependencies and lines just NAME, at any depth."""
    recipes = json.loads(run(['just', '--dump', '--dump-format', 'json'], ROOT).stdout)['recipes']
    seen, todo = set(), ['check']
    while todo:
        name = todo.pop()
        if name in seen or name not in recipes:
            continue
        seen.add(name)
        todo += [dependency['recipe'] for dependency in recipes[name]['dependencies']]
        todo += [line.split()[1] for line in recipe_lines(recipes[name]) if re.fullmatch(r'just [A-Za-z0-9_-]+( .*)?', line)]
    return seen - {'check'}


# A line of CI that runs a recipe is the bare line just NAME, so the test can read each recipe that CI runs.
BARE = re.compile(r'just ([A-Za-z0-9_-]+)')


def ci_recipes(job):
    """The recipes that the bare lines just NAME of a job run. Any other use of just raises ValueError, so CI cannot
    hide a recipe from the test with a prefix, an argument or a shell."""
    names = []
    for step in job['steps']:
        lines = (step.get('run') or '').splitlines()
        # The step that installs the pinned tools names just in its pins and downloads, and runs no recipe.
        if step.get('name') == 'Install the pinned tools':
            # Only a fetch or a tar of just may name it, and a word after just on any other line is a run of just.
            if any(re.search(r'\bjust\b["\')]*(\s+\S|\s*$)', line) and not re.match(r'\s*(fetch|tar)\s', line)
                   for line in lines):
                raise ValueError('the step that installs the pinned tools runs just')
            continue
        for line in lines:
            if not re.search(r'\bjust\b', line):
                continue
            bare = BARE.fullmatch(line.strip())
            if not bare:
                raise ValueError(f'{line.strip()}: CI runs a recipe only as the bare line just NAME')
            names.append(bare.group(1))
    return names


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.flow = workflow()
        self.jobs = self.flow['jobs']

    def test_only_the_jobs_release_and_docs_can_write(self):
        self.assertEqual(self.flow['permissions'], {'contents': 'read'})
        for name, job in self.jobs.items():
            if name not in ('release', 'docs'):
                self.assertNotIn('permissions', job, name)
        self.assertEqual(self.jobs['release']['permissions'], {'contents': 'write'})
        self.assertEqual(self.jobs['docs']['permissions'], {'contents': 'write'})

    def test_release_runs_after_verify_on_main(self):
        job = self.jobs['release']
        self.assertEqual(job['needs'], 'verify')
        self.assertIn("github.event_name == 'push'", job['if'])
        self.assertIn("github.ref == 'refs/heads/main'", job['if'])
        self.assertEqual(job['steps'][-1]['run'], 'just release')
        self.assertEqual(checkout_depth(job), 0)

    def test_each_recipe_of_the_job_verify_is_in_just_check(self):
        # CI runs no recipe that just check does not run, so a developer finds each failure of CI with just check.
        steps, names = check_steps(), ci_recipes(self.jobs['verify'])
        self.assertTrue(names, 'the job verify runs no recipe')
        for name in names:
            self.assertTrue(name == 'check' or name in steps, f'just check does not run just {name}')

    def test_ci_recipes_refuses_each_use_of_just_but_the_bare_line(self):
        for line in ('FOO=1 just lint', 'cd . && just lint', 'uv run just lint', "bash -c 'just lint'", 'just\tlint',
                     'just lint # syntax', 'just test -x', '  if true; then just lint; fi'):
            with self.subTest(line=line), self.assertRaises(ValueError):
                ci_recipes({'steps': [{'run': f'echo start\n{line}'}]})
        self.assertEqual(ci_recipes({'steps': [{'run': 'just lint\n  just test  '}, {'uses': 'x'}]}), ['lint', 'test'])
        install = {'name': 'Install the pinned tools', 'run': 'fetch just-1.59.0.tar.gz https://x\n"$bin/just" lint'}
        with self.assertRaises(ValueError):
            ci_recipes({'steps': [install]})
        for line in ('true && just release', 'JUST=1 just release', '( just release )', 'x=$(just --summary)',
                     '"$bin/just"'):
            with self.subTest(line=line), self.assertRaises(ValueError):
                ci_recipes({'steps': [dict(install, run=f'fetch just-1.59.0.tar.gz https://x\n{line}')]})
        install['run'] = 'fetch just-1.59.0.tar.gz https://x\ntar -xzf "$work/just.tar.gz" -C "$bin" just'
        self.assertEqual(ci_recipes({'steps': [install]}), [])

    def test_check_steps_follows_the_recipes_of_the_steps(self):
        steps = check_steps()
        self.assertLessEqual({'tools-check', 'release-check', 'lint', 'package-lint', 'test'}, steps)

    def test_verify_checks_the_release_with_the_whole_history(self):
        job = self.jobs['verify']
        self.assertEqual(checkout_depth(job), 0)
        runs = [step.get('run') for step in job['steps']]
        self.assertTrue('just release-check' in runs or 'just check' in runs, 'CI does not run release-check')


# The lines that name github-actions[bot] as the author of a commit or a tag.
BOT_LINES = ['git config user.name "github-actions[bot]"',
             'git config user.email "41898282+github-actions[bot]@users.noreply.github.com"']
TEMPLATE = ROOT / '.agents' / 'skills' / 'docs' / 'assets' / 'docs-publish.yml'


def step_lines(step):
    return [line.strip() for line in (step.get('run') or '').splitlines() if line.strip()]


class DocsJobTest(unittest.TestCase):
    """The job docs publishes the site of the tip of main to gh-pages, after the job verify passed, and no agent."""

    def setUp(self):
        self.job = workflow()['jobs']['docs']

    def test_docs_runs_after_verify_on_a_push_to_main(self):
        self.assertEqual(self.job['needs'], 'verify')
        self.assertEqual(self.job['if'], "github.event_name == 'push' && github.ref == 'refs/heads/main'")

    def test_one_publish_at_a_time_and_the_newer_wins(self):
        self.assertEqual(self.job['concurrency'], {'group': 'docs', 'cancel-in-progress': True})

    def test_the_bot_is_the_author_of_the_commit_of_gh_pages(self):
        lines = [line for step in self.job['steps'] for line in step_lines(step)]
        for line in BOT_LINES:
            self.assertIn(line, lines)

    def test_the_bot_is_the_author_in_the_template_of_the_docs_role_too(self):
        # The job and the workflow that the docs role writes for a project commit to gh-pages as the same author.
        lines = [line.strip() for line in TEMPLATE.read_text().splitlines()]
        for line in BOT_LINES:
            self.assertIn(line, lines)

    def test_docs_publishes_the_tip_of_main_and_not_its_own_commit(self):
        # A run that publishes its own commit, after the run of a newer push, would put an older site on gh-pages.
        last = self.job['steps'][-1]
        self.assertEqual(last.get('shell'), 'bash', 'bash in Actions stops at the first failed line')
        self.assertEqual(step_lines(last)[:2], ['git fetch --quiet origin main', 'git checkout --quiet --detach FETCH_HEAD'])
        self.assertEqual(step_lines(last)[-1], 'just docs-publish')
        for step in self.job['steps'][:-1]:
            self.assertNotIn('docs-publish', step.get('run') or '', step.get('name'))

    def test_the_author_comes_before_the_publish(self):
        names = [step.get('name') for step in self.job['steps']]
        self.assertLess(names.index('Name the author of the commit of gh-pages'), len(names) - 1)

    def test_docs_publish_builds_the_site_in_strict_mode_first(self):
        recipes = json.loads(run(['just', '--dump', '--dump-format', 'json'], ROOT).stdout)['recipes']
        self.assertIn('docs-build', [dependency['recipe'] for dependency in recipes['docs-publish']['dependencies']])
        self.assertIn('--strict', ' '.join(recipe_lines(recipes['docs-build'])))

    def test_no_failure_of_docs_is_hidden(self):
        self.assertNotIn('continue-on-error', self.job)
        for step in self.job['steps']:
            self.assertNotIn('continue-on-error', step, step.get('name') or step.get('uses'))


class CopyTest(unittest.TestCase):
    def test_the_script_is_the_copy_of_upstream(self):
        blob = run(['git', 'hash-object', str(SCRIPT)], ROOT).stdout.strip()
        self.assertEqual(blob, UPSTREAM_BLOB, 'scripts/release_tag.py differs from agent_gest_git_skills 26ef730')


class HookTest(unittest.TestCase):
    def test_git_env_has_no_variable_of_a_hook(self):
        self.assertFalse(set(HOOK_VARIABLES) & set(GIT_ENV))


class ScriptTest(unittest.TestCase):
    """The copy of release_tag.py tags a scratch repository and pushes the tag to a bare remote."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.remote, self.repo = base / 'remote.git', base / 'repo'
        run(['git', 'init', '-q', '--bare', str(self.remote)], base)
        run(['git', 'init', '-q', '-b', 'main', str(self.repo)], base)
        run(['git', 'remote', 'add', 'origin', str(self.remote)], self.repo)

    def tearDown(self):
        self.tmp.cleanup()

    def commit(self, message):
        run(['git', 'commit', '-q', '--allow-empty', '-m', message], self.repo)

    def script(self, *args, check=True):
        return run([sys.executable, str(SCRIPT), *args], self.repo, check=check)

    def remote_tags(self):
        out = run(['git', 'ls-remote', '--tags', 'origin'], self.repo).stdout
        return sorted(line.split('refs/tags/')[1] for line in out.splitlines() if not line.endswith('^{}'))

    def test_release_tags_and_pushes_the_next_version(self):
        self.commit('feat: start')
        self.assertIn('tagged v0.1.0', self.script().stdout)
        self.commit('fix: a bug')
        self.assertIn('the next tag is v0.1.1', self.script('--check').stdout)
        self.assertIn('next tag: v0.1.1', self.script('--dry-run').stdout)
        self.assertIn('tagged v0.1.1', self.script().stdout)
        self.assertEqual(self.remote_tags(), ['v0.1.0', 'v0.1.1'])
        self.assertIn('no release', self.script().stdout)
        self.commit('docs: a page')
        self.assertIn('no release', self.script().stdout)
        self.commit('feat: a feature')
        self.assertIn('tagged v0.2.0', self.script().stdout)
        self.assertEqual(self.remote_tags(), ['v0.1.0', 'v0.1.1', 'v0.2.0'])


if __name__ == '__main__':
    unittest.main()
