#!/usr/bin/env python3
"""The steps of just check, the reason of each no-op step, and the lint of steps.

jlog logs each line of a recipe that runs through its shell. The lint finds step recipes that the run log cannot see:

- a shebang line or the attribute [script]: the recipe does not run through jlog;
- a line with the prefix -: just goes on after the line fails;
- the attribute [no-cd] or [working-directory]: jlog looks for the log folder in another folder.

The setting set working-directory, which the installer writes into each module, is not an attribute, so the lint
allows it. A dependency of a step runs in the step, so the lint checks it too.

    lint_steps.py JUSTFILE ...    lint the steps of each Justfile, and exit 1 on a finding

A Justfile that imports .agents/install.just gets check from the index, so each of its steps must also end with
jlog step NAME, and its own check with jlog pass.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

# The import line of the recipe file of jnterfaces, which brings the index and its check.
IMPORTS = re.compile(r'''^import\??\s+['"](\./)?\.agents/install\.just['"]''', re.M)
# The steps of just check, in their order.
STEPS = ('fmt-check', 'lint', 'typecheck', 'test', 'coverage-check', 'integration', 'docstring-check', 'docs-check',
         'prose-check', 'diff-check')
# The reason that a no-op step logs with jlog skip.
REASONS = {
    'fmt-check': 'no format checker in this repository',
    'lint': 'no linter in this repository',
    'typecheck': 'no type checker in this repository',
    'test': 'no tests in this repository',
    'coverage-check': 'no coverage check in this repository',
    'integration': 'no integration tests in this repository',
    'docstring-check': 'no docstring check in this repository',
    'docs-check': 'no docs site in this repository',
    'prose-check': 'no prose checker in this repository',
    'diff-check': 'no language profile in this repository',
}


def text_of(line):
    """The text of one line of a recipe from a dump, or None when the line holds an interpolation."""
    if not all(isinstance(fragment, str) for fragment in line):
        return None
    return ''.join(line)


def ends_well(name, recipe):
    """True when the last line of the recipe is the line of jlog that the pass needs: jlog step NAME or jlog skip NAME
    for a step, jlog pass for check. The prefix @ does not count."""
    body = recipe.get('body') or []
    last = text_of(body[-1]) if body else None
    if last is None:
        return False
    # A line after a line that ends with a backslash is part of that line, so it is not a line of its own.
    before = body[-2] if len(body) > 1 else []
    if before and isinstance(before[-1], str) and before[-1].endswith('\\'):
        return False
    # A dependency after && runs after the last line, so the step would log its end too early.
    if len(recipe.get('dependencies') or []) > (recipe.get('priors') or 0):
        return False
    words = last.lstrip('@').split('#')[0].split()
    if name == 'check':
        return words == ['jlog', 'pass']
    return words[:3] in (['jlog', 'step', name], ['jlog', 'skip', name]) and (words[1] == 'skip' or len(words) == 3)


def problems(recipe):
    """The problems of one recipe that hide its lines from the run log."""
    found = []
    if recipe.get('shebang'):
        found.append('it has a shebang line or the attribute [script], so its lines do not run through jlog')
    for attribute in recipe.get('attributes') or []:
        if attribute == 'no-cd':
            found.append('it has the attribute [no-cd], so jlog looks for the log folder in another folder')
        elif isinstance(attribute, dict) and 'working-directory' in attribute:
            found.append('it has the attribute [working-directory], so jlog looks for the log folder in another folder')
    if not recipe.get('shebang'):
        continued = False
        for line in recipe.get('body') or []:
            first = line[0] if line and isinstance(line[0], str) else ''
            # A line after a line that ends with a backslash continues it, so a - at its start is no prefix.
            if not continued and re.match(r'^@?-', first):
                found.append('it has a line with the prefix -, so just goes on when the line fails')
                break
            last = line[-1] if line and isinstance(line[-1], str) else ''
            continued = last.endswith('\\')
    return found


def lint(dump, names, ending=()):
    """The findings for the recipes in names of one dump, and for the dependencies of each. A recipe in ending must
    also end with its line of jlog. Each finding is a line of text that names the recipe."""
    recipes = (dump or {}).get('recipes') or {}
    findings, seen = [], set()
    todo = [name for name in names if name in recipes]
    while todo:
        name = todo.pop(0)
        if name in seen:
            continue
        seen.add(name)
        recipe = recipes[name]
        findings += [f'{name}: {problem}' for problem in problems(recipe)]
        if name in ending and not ends_well(name, recipe):
            line = 'jlog pass' if name == 'check' else f'jlog step {name}'
            findings.append(f'{name}: its last line is not {line}, so the run log does not show that it passed')
        todo += [dep.get('recipe') for dep in recipe.get('dependencies') or [] if dep.get('recipe') in recipes]
    return findings


def dump_of(justfile):
    """The JSON dump of a Justfile, or None with the error of just."""
    run = subprocess.run(['just', '--justfile', str(justfile), '--working-directory', str(Path(justfile).parent),
                          '--dump', '--dump-format', 'json'], capture_output=True, text=True)
    if run.returncode:
        return None, (run.stderr.strip().splitlines() or ['just failed'])[0]
    return json.loads(run.stdout), None


def main(argv):
    if not argv or not shutil.which('just'):
        print('Usage: lint_steps.py JUSTFILE ...; it needs just on PATH', file=sys.stderr)
        return 2
    failed = False
    for path in argv:
        dump, error = dump_of(path)
        if error:
            print(f'{path}: {error}')
            failed = True
            continue
        # In a Justfile that imports the recipe file of jnterfaces, check of the index runs its steps, so each step must
        # end with its line of jlog.
        ending = STEPS + ('check',) if IMPORTS.search(Path(path).read_text()) else ()
        for finding in lint(dump, STEPS + ('check',), ending):
            print(f'{path}: the step {finding}')
            failed = True
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
