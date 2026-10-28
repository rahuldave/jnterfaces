#!/usr/bin/env python3
"""Install jnterfaces and the skill packages that you choose into one target repository.

The packages and their choices are in stack.json. jnterfaces and its just skill are always installed.
Each package runs its own scripts/install.sh, so each records its own source revision in the target.
The choices are saved in .agents/stack.json in the target, and a later run without options reuses them.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RECORD = Path('.agents/stack.json')


class StackError(Exception):
    pass


def load_registry(path):
    """Read stack.json and return the packages and the choices, each choice with its default."""
    try:
        registry = json.loads(path.read_text())
        packages = registry['packages']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise StackError(f'Cannot read the package list {path}: {exc}')
    choices = {}
    for package in packages:
        for skill in package.get('optional_skills', []):
            choices[skill['choice']] = skill
        if 'choice' in package:
            choices[package['choice']] = package
    if not packages or packages[0].get('name') != 'jnterfaces':
        raise StackError('The first package in stack.json must be jnterfaces')
    return packages, choices


def read_record(target):
    path = target / RECORD
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text())
        chosen = record['choices']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise StackError(f'Cannot read {path}: {exc}. Fix it or pass the choices with --with and --without.')
    if not isinstance(chosen, dict) or not all(isinstance(v, bool) for v in chosen.values()):
        raise StackError(f'{path} has choices that are not true or false')
    return chosen


def record_path(package):
    return Path(package.get('record', f'.agents/{package["name"]}-install.json'))


def installed(target, packages):
    """Return the choices that the install records of the packages show, for a target without saved choices."""
    found = {}
    for package in packages:
        path = target / record_path(package)
        if not path.is_file():
            continue
        if 'choice' in package:
            found[package['choice']] = True
        try:
            skills = json.loads(path.read_text()).get('skills', [])
        except (OSError, ValueError, AttributeError):
            skills = []
        for skill in package.get('optional_skills', []):
            found[skill['choice']] = isinstance(skills, list) and skill['choice'] in skills
    return found


def names(value):
    return [item for part in value for item in part.split(',') if item]


def ask(choices, start, only=None):
    """Ask about each choice on the terminal, or only about the named ones, with the current value as the default."""
    chosen = dict(start)
    for name, entry in choices.items():
        if only is not None and name not in only:
            continue
        default = chosen[name]
        hint = 'Y/n' if default else 'y/N'
        while True:
            print(f'Install {name} ({entry["about"]})? [{hint}] ', end='', file=sys.stderr, flush=True)
            answer = sys.stdin.readline()
            if not answer:
                raise StackError('No answer on the terminal; pass the choices with --with and --without')
            answer = answer.strip().lower()
            if answer in ('', 'y', 'yes', 'n', 'no'):
                break
            print('Answer y or n.', file=sys.stderr)
        chosen[name] = default if not answer else answer.startswith('y')
    return chosen


def resolve(args, choices, recorded, interactive, found=None):
    """Return the choices for this run.

    Each choice starts from the saved record, else from what is installed, else from the default of stack.json.
    Then the questions or the options change it.
    """
    unknown = sorted(set(names(args.with_) + names(args.without)) - set(choices))
    if unknown:
        raise StackError(f'Unknown choice(s): {", ".join(unknown)}. The choices are: {", ".join(choices)}')
    both = sorted(set(names(args.with_)) & set(names(args.without)))
    if both:
        raise StackError(f'A choice cannot be in --with and in --without: {", ".join(both)}')
    if args.ask and (args.all or args.defaults):
        raise StackError('--ask asks each question, so it does not go with --all or --defaults')
    defaults = {name: bool(entry.get('default')) for name, entry in choices.items()}
    saved = {k: v for k, v in (recorded or {}).items() if k in choices}
    start = {**defaults, **(found or {}), **saved}
    given = args.with_ or args.without or args.all or args.defaults
    named = set(names(args.with_) + names(args.without))
    new = [name for name in choices if name not in saved and name not in named]
    if args.all:
        start = dict.fromkeys(choices, True)
    elif args.ask or (recorded is None and not given):
        if not interactive:
            if args.ask:
                raise StackError('--ask needs a terminal to ask on')
            raise StackError('No choices are saved in the target, and there is no terminal to ask on. '
                             'Pass --defaults, --all, or --with and --without.')
        start = ask(choices, start)
    elif recorded is not None and new and not args.defaults:
        # A choice that came after the record was saved: ask about it, or say which value it takes.
        if interactive and not given:
            start = ask(choices, start, only=new)
        else:
            print('New choice(s) that the saved record does not have: '
                  + ', '.join(f'{name}={"yes" if start[name] else "no"}' for name in new)
                  + '. Pass --with or --without to change them.', file=sys.stderr)
    start.update(dict.fromkeys(names(args.with_), True))
    start.update(dict.fromkeys(names(args.without), False))
    return start


def check_overrides(packages, overrides):
    known = {p['name'] for p in packages} | {p['choice'] for p in packages if 'choice' in p}
    unknown = sorted(set(overrides) - known)
    if unknown:
        raise StackError(f'--clone names no package: {", ".join(unknown)}. The packages are: '
                         + ', '.join(p['name'] for p in packages))


def clone_dir(package, overrides):
    name = package['name']
    path = Path(overrides.get(name) or overrides.get(package.get('choice', '')) or ROOT.parent / name)
    if name == 'jnterfaces' and name not in overrides:
        path = ROOT
    manifest = path / 'skill-package.json'
    if not manifest.is_file() or not (path / 'scripts/install.sh').is_file():
        raise StackError(f'No {name} clone at {path}. Run: git clone {package["repository"]} {path}, '
                         f'or pass --clone {name}=DIR.')
    try:
        actual = json.loads(manifest.read_text())['repository']['name']
    except (OSError, ValueError, KeyError, TypeError):
        actual = None
    if actual != name:
        raise StackError(f'{path} is not the {name} package')
    return path.resolve()


def plan(packages, chosen, overrides):
    """Return (package, clone, installer options) for each package to install, in the order of stack.json."""
    steps = []
    selected = {p['name'] for p in packages if p['name'] == 'jnterfaces' or chosen.get(p.get('choice'))}
    for package in packages:
        if package['name'] not in selected:
            continue
        missing = [need for need in package.get('needs', []) if need not in selected]
        if missing:
            raise StackError(f'{package["name"]} needs {", ".join(missing)}')
        options = []
        if package['name'] == 'jnterfaces':
            skills = package['required_skills'] + [s['choice'] for s in package.get('optional_skills', []) if chosen[s['choice']]]
            for skill in skills:
                options += ['--skill', skill]
            options.append('--exact')
        steps.append((package, clone_dir(package, overrides), options))
    return steps


def head_of(clone):
    try:
        return subprocess.check_output(['git', '-C', str(clone), 'rev-parse', 'HEAD'], text=True,
                                       stderr=subprocess.PIPE).strip()
    except (OSError, subprocess.CalledProcessError):
        raise StackError(f'--clean needs a Git clone with a commit: {clone}')


def run_installer(clone, options, target, head, dry_run, quiet=False):
    """Run the installer of one package. A quiet run shows its output only when it fails."""
    command = ['bash', str(clone / 'scripts/install.sh'), *options]
    if head:
        command += ['--source-commit', head]
    if dry_run:
        command.append('--dry-run')
    command.append(str(target))
    if not quiet:
        return subprocess.run(command).returncode
    run = subprocess.run(command, capture_output=True, text=True)
    if run.returncode:
        sys.stdout.write(run.stdout)
        sys.stderr.write(run.stderr)
    return run.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('target', type=Path)
    parser.add_argument('--with', dest='with_', action='append', default=[], metavar='A,B',
                        help='turn these choices on')
    parser.add_argument('--without', action='append', default=[], metavar='A,B', help='turn these choices off')
    parser.add_argument('--all', action='store_true', help='turn every choice on')
    parser.add_argument('--defaults', action='store_true',
                        help='ask no questions: keep the saved or installed state, else take the default of stack.json')
    parser.add_argument('--ask', action='store_true', help='ask about each choice, even when the target has saved choices')
    parser.add_argument('--clone', action='append', default=[], metavar='NAME=DIR',
                        help='use this clone of a package; default: a folder next to the jnterfaces clone')
    parser.add_argument('--clean', action='store_true', help='install the committed HEAD of each clone')
    parser.add_argument('--dry-run', action='store_true', help='check every package and write nothing')
    parser.add_argument('--registry', type=Path, default=ROOT / 'stack.json', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.all and args.defaults:
        raise StackError('Use --all or --defaults, not both')
    if args.target.is_symlink():
        raise StackError('The target repository root must not be a symlink')
    target = args.target.resolve()
    if not target.is_dir():
        raise StackError(f'Target does not exist: {target}')
    overrides = {}
    for item in args.clone:
        name, sep, path = item.partition('=')
        if not sep or not name or not path:
            raise StackError(f'--clone needs NAME=DIR: {item}')
        overrides[name] = Path(path).expanduser()
    packages, choices = load_registry(args.registry)
    check_overrides(packages, overrides)
    recorded = read_record(target)
    chosen = resolve(args, choices, recorded, sys.stdin.isatty(), installed(target, packages))
    steps = plan(packages, chosen, overrides)
    for package in packages:
        choice = package.get('choice')
        if choice and not chosen[choice] and (target / record_path(package)).is_file():
            print(f'Note: {package["name"]} is turned off but stays installed. This script does not remove a '
                  f'package; remove its skills from .agents/skills and {record_path(package)} yourself.',
                  file=sys.stderr)
    heads = {clone: head_of(clone) for _, clone, _ in steps} if args.clean else {}
    # Every package checks its install before any package writes a file.
    for package, clone, options in steps:
        # The real run repeats the notes of the installers, so the check before it stays quiet unless it fails.
        if run_installer(clone, options, target, heads.get(clone), True, quiet=not args.dry_run):
            raise StackError(f'The dry run of {package["name"]} failed; nothing was written')
    if args.dry_run:
        print('Dry run: ' + ', '.join(p['name'] for p, _, _ in steps) + f' would be installed in {target}')
        return 0
    for package, clone, options in steps:
        if run_installer(clone, options, target, heads.get(clone), False):
            raise StackError(f'The install of {package["name"]} failed. The packages before it are installed.')
    record = target / RECORD
    record.parent.mkdir(exist_ok=True)
    record.write_text(json.dumps({'version': 1, 'choices': dict(sorted(chosen.items()))}, indent=2) + '\n')
    print(f'Installed in {target}:')
    for package, _, _ in steps:
        own = target / record_path(package)
        revision = json.loads(own.read_text()).get('source_revision', '?') if own.is_file() else '?'
        print(f'  {package["name"]} at {revision}')
    print(f'Choices saved in {record}. Run the same command without options to install them again.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except StackError as exc:
        print(f'Stack install failed: {exc}', file=sys.stderr)
        sys.exit(1)
