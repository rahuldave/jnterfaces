#!/usr/bin/env python3
"""The environment lab: run recipes of a Justfile with a clean environment, as a git hook, a GUI program or a CI
runner runs them. A recipe that passes in your shell but fails here needs a setting of your shell, for example from
.zshrc. The lab says to put the setting in the Justfile with set export, or in an ignored .env file with set
dotenv-load.

Usage: env_lab.py [--dir FOLDER] [RECIPE ...]. It runs the Justfile of FOLDER, by default the current folder.
With no recipe, it runs check. A recipe with arguments goes in quotes, for example "test -k name". It runs each
recipe that fails twice, so use it on recipes that can run again.
The clean environment keeps PATH, so a PATH that .zshrc sets is not tested. just env-lab passes the PATH from before
uv in JN_LAB_PATH.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

# The variables that the clean environment keeps. A login gives them to every program, also to one that does not
# read .zshrc.
KEPT = ('PATH', 'HOME', 'USER', 'LOGNAME', 'SHELL', 'TMPDIR', 'LANG', 'LC_ALL', 'TERM')
# The variables that the stack sets for each run. The root Justfile has export JLOG_RUN := env("JLOG_RUN", uuid()),
# so a run never needs it from the shell.
STACK = {'JLOG_RUN'}


def clean_env(env):
    """The environment of the lab: only the variables in KEPT, from env. JN_LAB_PATH, when set, is the PATH."""
    out = {name: env[name] for name in KEPT if name in env}
    if env.get('JN_LAB_PATH'):
        out['PATH'] = env['JN_LAB_PATH']
    return out


def shell_env(env):
    """The environment of your shell, from env, the environment of the lab: without what uv and just env-lab add,
    and with the PATH from before uv."""
    out = {name: value for name, value in env.items() if name not in ('UV', 'UV_RUN_RECURSION_DEPTH', 'JN_LAB_PATH')}
    if env.get('JN_LAB_PATH'):
        out['PATH'] = env['JN_LAB_PATH']
    return out


def env_calls(node):
    """The names that env("NAME"), env_var("NAME") and env_var_or_default("NAME", ...) read, in a part of the JSON
    dump. The dump writes a call as ["call", FUNCTION, ARGUMENT, ...]."""
    if isinstance(node, dict):
        return {name for value in node.values() for name in env_calls(value)}
    if not isinstance(node, list):
        return set()
    found = {name for value in node for name in env_calls(value)}
    if (len(node) >= 3 and node[0] == 'call' and node[1] in ('env', 'env_var', 'env_var_or_default')
            and isinstance(node[2], str)):
        found.add(node[2])
    return found


def recipe_parts(data, line):
    """The parts of the JSON dump that a recipe line can read: the recipe, each recipe that it depends on, and the
    assignments of their module. A line such as "m test" names the recipe test of the module m."""
    try:
        words = shlex.split(line)
    except ValueError:
        return []
    module = data
    while words and isinstance(module.get('modules'), dict) and words[0] in module['modules']:
        module = module['modules'][words.pop(0)]
    recipes = module.get('recipes') if isinstance(module.get('recipes'), dict) else {}
    parts, todo, seen = [module.get('assignments', {})], words[:1], set()
    while todo:
        name = todo.pop()
        if name in seen or not isinstance(recipes.get(name), dict):
            continue
        seen.add(name)
        parts.append(recipes[name])
        todo += [entry.get('recipe') for entry in recipes[name].get('dependencies', []) if isinstance(entry, dict)]
    return parts


def parsed(dump):
    """The JSON dump as data, or {} when it does not parse."""
    try:
        data = json.loads(dump)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def json_dump(justfile, env):
    """The JSON dump of the Justfile, as text. It holds the recipes of the files that it imports and of its modules."""
    return subprocess.run(['just', '--dump', '--dump-format', 'json'], cwd=justfile.parent, env=env,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout


def run(justfile, line, env):
    """Run one recipe line, such as "test -k name", in the folder of the Justfile, and return its exit code and its
    output. just finds the Justfile itself: an option such as --justfile would make jlog pass refuse the pass."""
    done = subprocess.run(['just', *shlex.split(line)], cwd=justfile.parent, env=env,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return done.returncode, done.stdout + done.stderr


def own_names(justfile, env, dump):
    """The variables that the Justfile sets itself: the assignments of the Justfile and of its modules, and the keys
    of the .env file of its folder. An assignment that reads a variable with env() takes it from the shell, so it does
    not count."""
    data = parsed(dump)
    names, todo = set(), [data]
    while todo:
        module = todo.pop()
        if isinstance(module.get('assignments'), dict):
            names |= set(module['assignments'])
        if isinstance(module.get('modules'), dict):
            todo += [value for value in module['modules'].values() if isinstance(value, dict)]
    names -= env_calls(data)
    try:
        lines = (justfile.parent / '.env').read_text(errors='replace').splitlines()
    except OSError:
        lines = []
    for line in lines:
        key = line.strip().removeprefix('export ').partition('=')[0].strip()
        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
            names.add(key)
    return names


def named_variables(justfile, env, line):
    """The variables that your shell has, that the clean environment does not, that the Justfile does not set
    itself, and that the recipe of line, a recipe that it depends on, or an assignment of its module names as $NAME,
    ${NAME}, env("NAME"), env_var("NAME") or env_var_or_default("NAME", ...). The recipe can be in a file that the
    Justfile imports, or in a module. Only names, never values."""
    dump = json_dump(justfile, env)
    extra = set(env) - set(KEPT) - STACK - own_names(justfile, env, dump)
    parts = recipe_parts(parsed(dump), line)
    found = set(re.findall(r'\$\{?([A-Za-z_][A-Za-z0-9_]*)', json.dumps(parts))) | env_calls(parts)
    return sorted(found & extra)


def advice(recipe, names):
    """The message for a recipe that needs a setting of the shell."""
    which = (f' Variables it may need, which your shell sets and the clean environment does not: {", ".join(names)}.'
             if names else '')
    return (f'{recipe} passes in your shell, but fails with a clean environment, as in a git hook, a GUI program or '
            f'CI.{which} A recipe never depends on .zshrc. Put the setting in the Justfile with set export, for '
            f'example: set export, then NAME := "value". Or put it in an ignored .env file, and add set dotenv-load '
            f'to the Justfile.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--dir', type=Path, default=Path('.'), help='the folder of the Justfile; default: here')
    parser.add_argument('recipes', nargs='*', default=['check'],
                        help='the recipes to run, each with its arguments in quotes; default: check')
    args = parser.parse_args(argv)
    folder = args.dir.resolve()
    found = ([path for path in folder.iterdir() if path.name.lower() in ('justfile', '.justfile')]
             if folder.is_dir() else [])
    if not found:
        print(f'There is no Justfile in {folder}', file=sys.stderr)
        return 2
    justfile = found[0]
    status = 0
    for recipe in args.recipes:
        try:
            shlex.split(recipe)
        except ValueError:
            print(f'{recipe}: the recipe line has an unbalanced quote', file=sys.stderr)
            return 2
        code, output = run(justfile, recipe, clean_env(os.environ))
        if code == 0:
            print(f'{recipe}: passes with a clean environment')
            continue
        status = 1
        again, _ = run(justfile, recipe, shell_env(os.environ))
        if again:
            print(f'{recipe}: fails in your shell too, so the lab cannot tell whether it needs a setting of your '
                  f'shell. Fix it first. The output with a clean environment was:\n{output.rstrip()[-4000:]}')
            continue
        print(advice(recipe, named_variables(justfile, shell_env(os.environ), recipe)))
        print(f'The output with a clean environment was:\n{output.rstrip()[-4000:]}')
    return status


if __name__ == '__main__':
    sys.exit(main())
