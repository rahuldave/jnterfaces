#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Install skill roles and project tools into one repository, and save them: just install lint, just install tasker.

stack.json lists the packages, their roles and the profiles. A role, such as tasker, names what a package does, so
the package behind it can change. A profile, such as python, sets the default roles, the tools, and the Justfile.
A tool, such as lint, adds packages to the project with the command of its profile: uv for Python, npm for TypeScript.

jnterfaces and its just skill are always installed. Each package is a Git clone in the folder that SKILL_PACKAGES_DIR
names, else in the folder that holds this jnterfaces clone; a missing clone is cloned from GitHub. The choices are saved in .agents/stack.json in the target,
and a later run without names installs them again.

Names are roles, tools or profiles:

    just install                 install what .agents/stack.json saves, or ask the first time
    just install lint tasker     add the lint tool and the tasker role
    just install --without reporter,lint
    just install --profile python
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tomllib

import shared_files

ROOT = Path(__file__).resolve().parents[1]
RECORD = Path('.agents/stack.json')
JUSTFILES = ('justfile', 'Justfile', '.justfile', 'JUSTFILE')
IMPORT_LINE = "import? '.agents/install.just'"


class StackError(Exception):
    pass


def string_list(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def plain_path(value):
    """True for a relative path inside the repository: not absolute, with no empty, . or .. part, and not in .git."""
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        return False
    parts = value.split('/')
    return all(part not in ('', '.', '..') and part.casefold() != '.git' for part in parts)


def load_registry(path):
    """Read stack.json and return the packages, the choices (each with its default), the aliases and the profiles."""
    try:
        registry = json.loads(path.read_text())
        return check_registry(registry)
    except StackError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise StackError(f'Cannot read the package list {path}: {exc!r}')


def before_remove_ok(value):
    """A before_remove of a tool: a package, the Git hook that the package installs, the command that takes the
    hook out, and the marker variable that names the program of the hook. Or nothing."""
    return value is None or (isinstance(value, dict) and {'package', 'git_hook', 'command'} <= set(value)
                             <= {'package', 'git_hook', 'command', 'marker'}
                             and isinstance(value['package'], str) and plain_path(value['git_hook'])
                             and string_list(value['command']) and bool(value['command'])
                             and isinstance(value.get('marker', ''), str))


def hook_of_project(hook, marker, target):
    """True when the Git hook runs a program of the project. A hook of pre-commit names the Python that installed it
    on the line INSTALL_PYTHON=PATH, and a hook of a pre-commit outside the project keeps working after the removal.
    With no marker, every hook counts."""
    if not marker:
        return True
    try:
        text = hook.read_text(errors='replace')
    except OSError as exc:
        print(f'Cannot read the Git hook {hook}, so it stays: {exc}', file=sys.stderr)
        return False
    for line in text.splitlines():
        name, sep, value = line.partition('=')
        if sep and name.strip() == marker:
            # pre-commit writes the value with shlex.quote.
            try:
                value = (shlex.split(value) or [''])[0]
            except ValueError:
                return False
            # The folder of the program, resolved: .venv/bin/python is itself a link to a Python outside the project.
            return bool(value) and Path(value).parent.resolve().is_relative_to(target.resolve())
    return False


def git_hook(target, name):
    """The path of a Git hook of the target, or None when the target is not a Git repository."""
    run = subprocess.run(['git', '-C', str(target), 'rev-parse', '--git-path', f'hooks/{name}'],
                         capture_output=True, text=True)
    return None if run.returncode else (target / run.stdout.strip())


def check_registry(registry):
    packages = registry['packages']
    if not isinstance(packages, list) or not packages or not all(isinstance(p, dict) for p in packages):
        raise StackError('packages in stack.json must be a list of objects')
    if packages[0].get('name') != 'jnterfaces' or not string_list(packages[0].get('required_skills')):
        raise StackError('The first package in stack.json must be jnterfaces, with its required_skills')
    choices = {}
    for package in packages:
        if not isinstance(package.get('name'), str) or not isinstance(package.get('repository'), str):
            raise StackError('Each package in stack.json needs a name and a repository')
        entries = list(package.get('optional_skills', [])) + ([package] if 'choice' in package else [])
        for entry in entries:
            if not isinstance(entry.get('choice'), str) or not isinstance(entry.get('about'), str):
                raise StackError(f'Each choice of {package["name"]} in stack.json needs a choice name and an about')
            if entry['choice'] in choices:
                raise StackError(f'The choice {entry["choice"]} is twice in stack.json')
            choices[entry['choice']] = entry
        if 'record' in package and not plain_path(package['record']):
            raise StackError(f'The record of {package["name"]} in stack.json must be a path inside the target')
        if not string_list(package.get('needs', [])):
            raise StackError(f'needs of {package["name"]} in stack.json must be a list of names')
        variants = package.get('variants')
        if variants is not None and not (
                'choice' in package and isinstance(variants, dict) and variants
                and all(re.fullmatch(r'[a-z][a-z0-9_-]*', key) and isinstance(value, dict)
                        and isinstance(value.get('about'), str) and isinstance(value.get('ready', True), bool)
                        for key, value in variants.items())
                and package.get('default_variant') in variants
                and variants[package['default_variant']].get('ready', True)):
            raise StackError(f'The variants of {package["name"]} in stack.json need an about each, and a default '
                             f'variant that is ready')
    aliases = registry.get('aliases', {})
    profiles = registry.get('profiles', {})
    if not isinstance(aliases, dict) or any(value not in choices for value in aliases.values()):
        raise StackError('Each alias in stack.json must name a choice')
    if not isinstance(profiles, dict):
        raise StackError('profiles in stack.json must be an object')
    for name, entry in profiles.items():
        if not (isinstance(entry, dict) and isinstance(entry.get('package'), str) and isinstance(entry.get('repository'), str)
                and isinstance(entry.get('about'), str) and string_list(entry.get('detect', []))
                and re.fullmatch(r'[a-z][a-z0-9_]*', name)):
            raise StackError(f'The profile {name} in stack.json needs a package, a repository, an about and a detect '
                             f'list, and its name must be a plain lower-case word')
    clash = (set(choices) | set(aliases)) & set(profiles)
    if clash:
        raise StackError(f'A name in stack.json is both a role and a profile: {", ".join(sorted(clash))}')
    return packages, choices, aliases, profiles


def check_profile(name, profile, choices):
    """Check the profile.json of a profile package. A tool names its packages, its config files, its recipe file and
    the recipes that just verify runs."""
    ok = isinstance(profile, dict)
    tools = profile.get('tools', {}) if ok else {}
    init = profile.get('init', {}) if ok else {}
    base = profile.get('base', {}) if ok else {}
    ok = (ok and profile.get('name') == name and string_list(profile.get('add'))
          and string_list(profile.get('remove')) and isinstance(tools, dict) and tools
          and string_list(profile.get('default_tools', [])) and set(profile.get('default_tools', [])) <= set(tools)
          and isinstance(profile.get('choices', {}), dict) and set(profile.get('choices', {})) <= set(choices)
          and profile.get('manifest') in ('pyproject.toml', 'package.json')
          and isinstance(init, dict)
          and (not init or (plain_path(init.get('file')) and string_list(init.get('command')) and init['command']))
          and (profile.get('uv_default_groups') is None or isinstance(profile['uv_default_groups'], str)
               or string_list(profile['uv_default_groups']))
          and isinstance(base, dict) and recipe_file_ok(base.get('recipes'))
          and isinstance(base.get('files', {}), dict)
          and all(plain_path(k) and plain_path(v) for k, v in base.get('files', {}).items())
          and lines_ok(base)
          and all(re.fullmatch(r'[a-z][a-z0-9_-]*', tool_name) for tool_name in tools)
          and all(isinstance(tool, dict) and string_list(tool.get('packages')) and tool['packages']
                  and isinstance(tool.get('files', {}), dict)
                  and all(plain_path(k) and plain_path(v) for k, v in tool.get('files', {}).items())
                  and lines_ok(tool)
                  and isinstance(tool.get('group', ''), str)
                  and isinstance(tool.get('skip_if', {}), dict)
                  and all(string_list(v) and len(v) == 2 and plain_path(v[0]) for v in tool.get('skip_if', {}).values())
                  and before_remove_ok(tool.get('before_remove'))
                  and recipe_file_ok(tool.get('recipes'))
                  and string_list(tool.get('verify', []))
                  and all(re.fullmatch(r'[a-z][a-z0-9_-]*( [^\s"\\]+)*', step) for step in tool.get('verify', []))
                  for tool in tools.values()))
    if not ok:
        raise StackError(f'The profile.json of the {name} profile is malformed')
    return profile


def lines_ok(part):
    """The lines of a base or a tool: {line file: [line, ...]}. A line has no line break and is not a comment of the
    blocks. A path is in lines or in files, not in both."""
    lines = part.get('lines', {})
    return (isinstance(lines, dict) and not set(lines) & set(part.get('files', {}))
            and not any(shared_files.reserved(path) for path in list(lines) + list(part.get('files', {})))
            and all(isinstance(rows, list) and len({shared_files.normal(r) for r in rows if isinstance(r, str)
                                                    and shared_files.is_rule(r)})
                    == len([r for r in rows if isinstance(r, str) and shared_files.is_rule(r)])
                    for rows in lines.values())
            and all(plain_path(path) and string_list(rows)
                    and all(row.strip() and '\n' not in row and '\r' not in row and not shared_files.BEGIN.match(row)
                            and not shared_files.END.match(row) and not shared_files.WRITTEN.match(row)
                            for row in rows)
                    for path, rows in lines.items()))


def commit_of(clone):
    """The commit of a clone, or ? when it is not a Git clone."""
    run = subprocess.run(['git', '-C', str(clone), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    return run.stdout.strip() if run.returncode == 0 and run.stdout.strip() else '?'


def owners_of(profiles, names, pairs, commits):
    """The owners of the line files and the whole files, in order: each profile in names, followed by each of its
    tools in pairs, in the order of its profile.json."""
    found = []
    for name in names:
        profile = profiles[name]
        package, root = profile['package']['name'], profile['root']
        base = profile.get('base', {})
        found.append(shared_files.Owner(
            name, f'the {name} profile', package, commits[name], f'`just uninstall {name}` removes them.',
            base.get('lines', {}), {path: (root / origin, None) for path, origin in base.get('files', {}).items()}))
        for tool, entry in profile['tools'].items():
            if f'{name}:{tool}' in pairs:
                guards = entry.get('skip_if', {})
                found.append(shared_files.Owner(
                    f'{name}:{tool}', f'the {tool} tool of the {name} profile', package, commits[name],
                    f'`just uninstall {tool}` removes them.', entry.get('lines', {}),
                    {path: (root / origin, guards.get(path)) for path, origin in entry.get('files', {}).items()}))
    return found


def sync_files(target, profiles, kept, kept_tools, every, dry_run):
    """Bring the line files and the whole files to the owners that stay: kept profiles and kept_tools. every holds
    each profile of the run, also the ones that go."""
    commits = {name: commit_of(profiles[name]['root']) for name in every}
    known = owners_of(profiles, every, {f'{n}:{t}' for n in every for t in profiles[n]['tools']}, commits)
    active = owners_of(profiles, kept, set(kept_tools), commits)
    if any(owner.lines or owner.files for owner in known) or (target / shared_files.LOG).exists():
        installer = shared_files.Owner('jnterfaces', 'the installer', 'jnterfaces', commit_of(ROOT),
                                       'They stay while the project keeps .agents/stack-log.jsonl.',
                                       shared_files.JNTERFACES_LINES)
        active, known = [installer] + active, [installer] + known
    command = os.environ.get('JNTERFACES_COMMAND') or shlex.join(['install_stack.py', *sys.argv[1:]])
    sources = {profiles[name]['package']['name']: commits[name] for name in every}
    sources['jnterfaces'] = commit_of(ROOT)
    try:
        notes = shared_files.sync(target, active, known, command, sources, dry_run)
    except (OSError, shared_files.SharedError) as exc:
        raise StackError(f'Cannot update the shared files: {exc}')
    for note in notes:
        print(note, file=sys.stdout if dry_run else sys.stderr)


def recipe_file_ok(value):
    """A recipe file of a profile: a plain file name in the profile's folder of .agents/just, or nothing."""
    return value is None or (isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]+\.just', value) is not None)


def load_profiles(names, registry_profiles, choices, overrides):
    """Read the profile.json of each named profile from the clone of its package. Each profile gets its root and
    its package."""
    loaded = {}
    for name in names:
        entry = registry_profiles[name]
        package = {'name': entry['package'], 'repository': entry['repository']}
        root = clone_dir(package, overrides)
        try:
            profile = json.loads((root / 'profile.json').read_text())
        except (OSError, ValueError) as exc:
            raise StackError(f'Cannot read profile.json of the {name} profile in {root}: {exc}')
        profile = check_profile(name, profile, choices)
        profile['root'], profile['package'] = root, package
        loaded[name] = profile
    return loaded


def read_record(target, choices, aliases):
    """Return the saved record as {profiles, choices, tools}, or None. Version 1 had choices only, under old names."""
    path = target / RECORD
    if path.is_symlink():
        raise StackError(f'{path} must not be a symlink')
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text())
        chosen = record['choices']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise StackError(f'Cannot read {path}: {exc}. Fix it, or remove it and pass the choices again.')
    if not isinstance(chosen, dict) or not all(isinstance(v, bool) for v in chosen.values()):
        raise StackError(f'{path} has choices that are not true or false')
    profiles, tools, pending = record.get('profiles', []), record.get('tools', []), record.get('pending_tools', [])
    removing = record.get('removing_tools', [])
    removing_profiles = record.get('removing_profiles', [])
    variants = record.get('variants', {})
    if not isinstance(variants, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in variants.items()):
        raise StackError(f'{path} has variants that are not a map of roles to names')
    generated = record.get('generated', {})
    if not isinstance(generated, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in generated.items()):
        raise StackError(f'{path} has generated files that are not a map of paths to hashes')
    owned, added = record.get('owned_packages', {}), record.get('added_packages', {})
    for field, value in (('owned_packages', owned), ('added_packages', added)):
        if not isinstance(value, dict) or not all(string_list(names) for names in value.values()):
            raise StackError(f'{path} has {field} that are not lists of names')
    if not all(string_list(value) for value in (profiles, tools, pending, removing, removing_profiles)):
        raise StackError(f'{path} has profiles or tools that are not a list of names')
    renamed = {}
    for name, value in chosen.items():
        renamed[aliases.get(name, name)] = value
    return {'profiles': profiles, 'choices': renamed, 'tools': tools, 'pending': pending, 'owned': owned,
            'added_old': added, 'removing': removing, 'removing_profiles': removing_profiles,
            'generated': generated, 'started': record.get('started') is True, 'variants': variants}


def record_path(package):
    return Path(package.get('record', f'.agents/{package["name"]}-install.json'))


def installed(target, packages):
    """Return the choices that the install records of the packages show, for a target without saved choices."""
    found = {}
    for package in packages:
        path = target / record_path(package)
        if not path.is_file():
            continue
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            record = {}
        if not isinstance(record, dict):
            record = {}
        if 'choice' in package and not record.get('removed'):
            found[package['choice']] = True
        skills = record.get('skills', [])
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
                raise StackError('No answer on the terminal; name the roles, or pass --defaults')
            answer = answer.strip().lower()
            if answer in ('', 'y', 'yes', 'n', 'no'):
                break
            print('Answer y or n.', file=sys.stderr)
        chosen[name] = default if not answer else answer.startswith('y')
    return chosen


def resolve(args, choices, recorded, interactive, found=None, defaults=None, implied=False):
    """Return the choices for this run.

    Each choice starts from the saved record, else from what is installed, else from the default of the profile or
    of stack.json. Then the questions or the options change it. On a first run with a named profile or tool and no
    named role, a terminal asks about the roles. A named role asks nothing, with or without a terminal. Then a named
    profile or tool takes the defaults, and named roles alone turn on only those roles.
    """
    on, off = names(args.with_), names(args.without)
    both = sorted(set(on) & set(off))
    if both:
        raise StackError(f'A name cannot be both turned on and turned off: {", ".join(both)}')
    if args.ask and (args.all or args.defaults):
        raise StackError('--ask asks each question, so it does not go with --all or --defaults')
    if defaults is None:
        defaults = {name: bool(entry.get('default')) for name, entry in choices.items()}
    saved = {k: v for k, v in (recorded or {}).items() if k in choices}
    start = {**defaults, **(found or {}), **saved}
    given = on or off or args.all or args.defaults
    named = set(on + off)
    new = [name for name in choices if name not in saved and name not in named]
    if args.all:
        start = dict.fromkeys(choices, True)
    elif recorded is None and not args.ask and not args.defaults and (given or implied):
        others = [name for name in choices if name not in named]
        if interactive and not named:
            start = ask(choices, start, only=others)
        elif not implied:
            start = {name: name in found_on(found) for name in choices}
            print('Only the named roles are turned on. Pass --defaults to take the default roles too.', file=sys.stderr)
    elif args.ask or (recorded is None and not given):
        if not interactive:
            if args.ask:
                raise StackError('--ask needs a terminal to ask on')
            raise StackError('No choices are saved in the target, and there is no terminal to ask on. '
                             'Name the roles to install, or pass --defaults or --all.')
        start = ask(choices, start)
    elif recorded is not None and new and not args.defaults:
        # A choice that came after the record was saved: ask about it, or say which value it takes.
        if interactive and not given:
            start = ask(choices, start, only=new)
        else:
            print('New role(s) that the saved record does not have: '
                  + ', '.join(f'{name}={"yes" if start[name] else "no"}' for name in new)
                  + '. Name a role to turn it on, or pass --without to turn it off.', file=sys.stderr)
    start.update(dict.fromkeys(on, True))
    start.update(dict.fromkeys(off, False))
    return start


def found_on(found):
    return {name for name, value in (found or {}).items() if value}


def sort_names(args, choices, aliases, profiles):
    """Split the names on the command line and in --with and --without into roles, profiles and tools. A name that
    is no role and no profile is a tool, which an active profile must have."""
    split = {'roles_on': [], 'roles_off': [], 'tools_on': [], 'tools_off': [], 'profiles': [], 'profiles_off': [],
             'variants': {}}
    for raw, where in [(name, 'on') for name in args.names + names(args.with_)] + [(n, 'off') for n in names(args.without)]:
        raw, sep, variant = raw.partition('=')
        name = aliases.get(raw, raw)
        if sep:
            # A role with a variant, such as tasker=gitwt
            offered = choices.get(name, {}).get('variants')
            if where == 'off' or not offered:
                raise StackError(f'{raw}={variant}: only a role that has variants takes one, and only to turn it on. '
                                 f'The roles with variants are: '
                                 f'{", ".join(n for n, entry in choices.items() if entry.get("variants")) or "none"}.')
            if variant not in offered:
                raise StackError(f'{name} has no variant {variant}. Its variants are: {", ".join(offered)}.')
            split['variants'][name] = variant
        if name in choices:
            split[f'roles_{where}'].append(name)
        elif name in profiles:
            split['profiles' if where == 'on' else 'profiles_off'].append(name)
        elif re.fullmatch(r'[a-z][a-z0-9_-]*', name):
            split[f'tools_{where}'].append(name)
        else:
            raise StackError(f'Unknown name: {raw}. The roles are: {", ".join(choices)}. The profiles are: '
                             f'{", ".join(profiles)}.')
    return split


def pick_profiles(args, split, recorded, profiles, target):
    """Return the profiles of this run: --profile, else the saved ones plus the named ones, else the detected ones."""
    if args.profile is not None:
        chosen = names([args.profile])
        unknown = sorted(set(chosen) - set(profiles))
        if unknown:
            raise StackError(f'Unknown profile(s): {", ".join(unknown)}. The profiles are: {", ".join(profiles)}')
        return list(dict.fromkeys(chosen + split['profiles']))
    saved = [name for name in (recorded or {}).get('profiles', []) if name in profiles]
    found = [name for name, profile in profiles.items() if any((target / f).exists() for f in profile.get('detect', []))]
    if recorded is not None or split['profiles']:
        # A saved record keeps its profiles, also none: the files of the project no longer choose one.
        hint = [name for name in found if name not in saved + split['profiles']]
        if hint and recorded is not None and not saved:
            print(f'The files of the project suggest the profile(s) {", ".join(hint)}. To turn one on, run: '
                  f'just install --profile {hint[0]}', file=sys.stderr)
        return list(dict.fromkeys(saved + split['profiles']))
    if found:
        print(f'Profile(s) found from the files of the project: {", ".join(found)}. Pass --profile to change them.',
              file=sys.stderr)
    return found


def check_overrides(packages, overrides):
    known = {p['name'] for p in packages} | {p['choice'] for p in packages if 'choice' in p}
    unknown = sorted(set(overrides) - known)
    if unknown:
        raise StackError(f'--clone names no package: {", ".join(unknown)}. The packages are: '
                         + ', '.join(p['name'] for p in packages))


def packages_dir():
    """The folder of the package clones: SKILL_PACKAGES_DIR, else the folder that holds this jnterfaces clone."""
    named = os.environ.get('SKILL_PACKAGES_DIR')
    return Path(named).expanduser().resolve() if named else ROOT.parent


def clone_dir(package, overrides):
    """Return the clone of a package. A missing clone in the folder of the packages is cloned from GitHub."""
    name = package['name']
    named = overrides.get(name) or overrides.get(package.get('choice', ''))
    path = Path(named) if named else packages_dir() / name
    if name == 'jnterfaces' and not named:
        path = ROOT
    manifest = path / 'skill-package.json'
    if not manifest.is_file() and not named and not path.exists():
        print(f'Cloning {package["repository"]} into {path}', file=sys.stderr)
        run = subprocess.run(['git', 'clone', '--quiet', package['repository'], str(path)], capture_output=True, text=True)
        if run.returncode or not manifest.is_file():
            # A clone that failed, or one of an empty repository, goes again, so that a later run clones once more.
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            raise StackError(f'Cannot clone {package["repository"]} into {path}: '
                             f'{run.stderr.strip() if run.returncode else "the repository has no skill-package.json"}')
    if not manifest.is_file():
        raise StackError(f'No {name} clone at {path}. Run: git clone {package["repository"]} {path}, '
                         f'or pass --clone {name}=DIR.')
    try:
        actual = json.loads(manifest.read_text())['repository']['name']
    except (OSError, ValueError, KeyError, TypeError):
        actual = None
    if actual != name:
        raise StackError(f'{path} is not the {name} package')
    return path.resolve()


def plan(packages, chosen):
    """Return (package, installer options) for each package to install, in the order of stack.json."""
    steps = []
    selected = {p['name'] for p in packages if p['name'] == 'jnterfaces' or chosen.get(p.get('choice'))}
    for package in packages:
        if package['name'] not in selected:
            continue
        missing = [need for need in package.get('needs', []) if need not in selected]
        if missing:
            raise StackError(f'{package["name"]} needs {", ".join(missing)}')
        options = ['--record', str(record_path(package))]
        if package['name'] == 'jnterfaces':
            skills = package['required_skills'] + [s['choice'] for s in package.get('optional_skills', []) if chosen[s['choice']]]
            for skill in skills:
                options += ['--skill', skill]
            options.append('--exact')
        steps.append((package, options))
    return steps


def head_of(clone):
    try:
        return subprocess.check_output(['git', '-C', str(clone), 'rev-parse', 'HEAD'], text=True,
                                       stderr=subprocess.PIPE).strip()
    except (OSError, subprocess.CalledProcessError):
        raise StackError(f'--clean needs a Git clone with a commit: {clone}')


def update(clone):
    """Pull a clone that follows a branch upstream. A clone without an upstream stays as it is."""
    upstream = subprocess.run(['git', '-C', str(clone), 'rev-parse', '--abbrev-ref', '@{upstream}'], capture_output=True)
    if upstream.returncode:
        print(f'Not updated: {clone} follows no upstream branch', file=sys.stderr)
        return
    run = subprocess.run(['git', '-C', str(clone), 'pull', '--ff-only', '--quiet'], capture_output=True, text=True)
    if run.returncode:
        raise StackError(f'Cannot update {clone} with git pull --ff-only: {run.stderr.strip()}')


def missing_tools(clone):
    """The required executables that a package names in skill-package.json and that are not on PATH."""
    try:
        manifest = json.loads((clone / 'skill-package.json').read_text())
        required = manifest.get('executables', {}).get('required', [])
        return [entry['name'] for entry in required if not shutil.which(entry['name'])]
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        return []


def run_installer(clone, options, target, head, dry_run, quiet=False):
    """Run the one installer, here in jnterfaces, for one package. A quiet run shows its output only when it fails."""
    command = [sys.executable, str(ROOT / 'scripts/install_package.py'), '--source', str(clone), *options]
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


def package_name(spec):
    """The name of a package without its version: @biomejs/biome@2 is @biomejs/biome, ruff>=0.17 is ruff."""
    if spec.startswith('@'):
        return '@' + spec[1:].split('@')[0]
    return re.split(r'[@<>=!~ ;\[]', spec)[0]


def normal_name(spec, profile):
    """The name of a package as the project file lists it: Python names compare without case, - and _."""
    name = package_name(spec)
    return re.sub(r'[-_.]+', '-', name).lower() if profile.get('manifest') == 'pyproject.toml' else name


def present_packages(target, profile, group, place_only=False):
    """The names of the packages that the project file already has, where the add command of the profile puts them.

    For pyproject.toml that is the dependency group of the tool. For package.json it is any kind of dependency, or,
    with place_only, the devDependencies that no other list names: the place where npm install --save-dev puts a
    package. Ownership counts that place only, so a package that the user also lists elsewhere is the user's.
    A missing file has no packages. A file that cannot be read gives None: nothing is known about it.
    """
    manifest = profile.get('manifest')
    path = target / manifest if manifest else None
    if path is None or not path.exists():
        return set()
    if path.is_symlink() or not path.is_file():
        return None
    try:
        if manifest == 'pyproject.toml':
            groups = tomllib.loads(path.read_text()).get('dependency-groups', {})
            entries = groups.get(group, []) if isinstance(groups, dict) else []
            return {normal_name(item, profile) for item in entries if isinstance(item, str)}
        data = json.loads(path.read_text())
        lists = {kind: set(data[kind]) if isinstance(data.get(kind), dict) else set()
                 for kind in ('dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies')}
        if place_only:
            # npm uninstall takes a name out of every list, so a package that the user also lists elsewhere is theirs.
            others = lists['dependencies'] | lists['optionalDependencies'] | lists['peerDependencies']
            bundled = data.get('bundleDependencies', data.get('bundledDependencies'))
            others |= {name for name in bundled if isinstance(name, str)} if isinstance(bundled, list) else set()
            return lists['devDependencies'] - others
        return set().union(*lists.values())
    except (OSError, ValueError, tomllib.TOMLDecodeError, AttributeError):
        return None


def owner_key(name, profile, tool):
    """Where a package of the tool lives in the project file: profile:group when the add command of the profile takes
    a group, as uv does, else the whole profile, as for npm. Ownership is recorded by this place, not by tool, because
    two tools can list one package."""
    if any('{group}' in part for part in profile['add']):
        return f'{name}:{profile["tools"][tool].get("group", tool)}'
    return name


def tool_steps(target, profiles, active, tools_add, tools_remove, keep, owned, dry_run=False):
    """Return the steps that add and remove the packages of tools, with the config files to copy for each.

    An add step leaves out each package that the project already has, and names the packages that it adds. A remove
    step takes out only packages that just install recorded as added in that place, and that no kept tool there
    lists. Each step ends with (owner key, package names). A listed package that the project has and that just
    install did not add is named, so that the user can remove it.
    """
    steps = []

    def of(name, pair_list):
        return [pair.partition(':')[2] for pair in pair_list if pair.partition(':')[0] == name]
    for name in active:
        profile = profiles[name]
        keep_here = of(name, keep)
        for tool in of(name, tools_add):
            if tool in profile['tools']:
                entry = profile['tools'][tool]
                group = entry.get('group', tool)
                present = present_packages(target, profile, group)
                if present is None:
                    steps.append(('add', name, tool, None, {}, entry, (owner_key(name, profile, tool), []),
                                  f'cannot read {profile["manifest"]}, so {tool} for {name} is not added'))
                    continue
                specs = [spec for spec in entry['packages'] if normal_name(spec, profile) not in present]
                command = [part.replace('{group}', group) for part in profile['add']] + specs if specs else None
                steps.append(('add', name, tool, command, entry.get('files', {}), entry,
                              (owner_key(name, profile, tool), [normal_name(spec, profile) for spec in specs]), None))
        by_owner = {}
        for tool in of(name, tools_remove):
            if tool not in profile['tools']:
                continue
            owner = owner_key(name, profile, tool)
            group = profile['tools'][tool].get('group', tool)
            # A kept tool in the same place needs each package that its profile lists, whoever added it.
            needed = {normal_name(spec, profile) for other in keep_here if other in profile['tools']
                      and owner_key(name, profile, other) == owner for spec in profile['tools'][other]['packages']}
            listed = [normal_name(spec, profile) for spec in profile['tools'][tool]['packages']]
            mine = set(owned.get(owner, []))
            present = present_packages(target, profile, group)
            placed = present_packages(target, profile, group, place_only=True)
            if present is None or placed is None:
                # The tool stays in the record, so that a later --without tries again.
                steps.append(('remove', name, group, None, {}, {}, (owner, []),
                              f'cannot read {profile["manifest"]}, so {tool} for {name} is not removed'))
                continue
            left = [n for n in listed if n not in mine and n not in needed and n in present]
            if left:
                them = 'it' if len(left) == 1 else 'them'
                print(f'{"Would leave" if dry_run else "Left"} {", ".join(left)} in the project for {name}: just '
                      f'install did not add {them}. Remove {them} yourself if the project no longer needs {them}.',
                      file=sys.stderr)
            going = [n for n in listed if n in mine and n in placed and n not in needed]
            by_owner.setdefault(owner, (group, [], []))[1].extend(going)
            before = profile['tools'][tool].get('before_remove')
            # The Git hook of a package that goes would fail at each commit, so its command takes the hook out first.
            if before and normal_name(before['package'], profile) in going:
                hook = git_hook(target, before['git_hook'])
                if hook is not None and hook.is_file() and hook_of_project(hook, before.get('marker'), target):
                    by_owner[owner][2].append(before['command'])
        for owner, (group, pkgs, befores) in by_owner.items():
            pkgs = list(dict.fromkeys(pkgs))
            if pkgs:
                command = [part.replace('{group}', group) for part in profile['remove']] + pkgs
                steps.append(('remove', name, group, command, {}, {'before': befores}, (owner, pkgs), None))
    return steps


def write_new(target, relative, data):
    """Write a new file in the target. Never through a symlink, and never over a file that is there."""
    current = target
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise StackError(f'{current} is a symlink; write {relative} yourself')
        current.mkdir(exist_ok=True)
    try:
        fd = os.open(target / relative, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o644)
    except FileExistsError:
        raise StackError(f'{target / relative} appeared while the installer ran; it is left alone')
    with os.fdopen(fd, 'wb') as handle:
        handle.write(data)


def prepare(target, profile, steps):
    """Before a profile adds tools: make its project file when it is missing, and set the uv default groups."""
    if not steps:
        return
    init = profile.get('init')
    if init and (target / init['file']).is_symlink():
        raise StackError(f'{target / init["file"]} is a symlink, so the installer leaves it alone. The roles are '
                         f'installed; make it a file and run just install again.')
    if init and not (target / init['file']).exists():
        print(f'No {init["file"]}, so the installer runs: {" ".join(init["command"])}', file=sys.stderr)
        try:
            failed = subprocess.run(init['command'], cwd=target).returncode
        except OSError as exc:
            failed = str(exc)
        if failed or not (target / init['file']).exists():
            raise StackError(f'{" ".join(init["command"])} did not make {init["file"]}. The roles are installed; '
                             f'make {init["file"]} and run just install again.')
    groups = profile.get('uv_default_groups')
    if groups:
        set_default_groups(target / 'pyproject.toml', groups)


def set_default_groups(pyproject, groups):
    """Set [tool.uv] default-groups, so that uv sync and uv run install the groups of the tools.

    The file changes only when the result parses as TOML and holds the same data plus the one new value. Otherwise
    the installer names the line to add.
    """
    if not pyproject.is_file() or pyproject.is_symlink():
        return
    text = pyproject.read_text()
    line = f'default-groups = {json.dumps(groups)}'
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        print(f'pyproject.toml does not parse, so add this line under [tool.uv] yourself: {line}', file=sys.stderr)
        return
    uv = data.get('tool', {}).get('uv', {}) if isinstance(data.get('tool', {}), dict) else None
    if not isinstance(uv, dict):
        print(f'pyproject.toml has an unusual tool table, so add this line under [tool.uv] yourself: {line}',
              file=sys.stderr)
        return
    if 'default-groups' in uv:
        return
    note = '# uv sync and uv run install these dependency groups, the groups of the tools of just install\n'
    if re.search(r'^\[tool\.uv\]\s*$', text, re.M):
        new = re.sub(r'^\[tool\.uv\]\s*$\n?', lambda m: '[tool.uv]\n' + note + line + '\n', text, count=1, flags=re.M)
    else:
        new = text.rstrip('\n') + '\n\n[tool.uv]\n' + note + line + '\n'
    expected = copy.deepcopy(data)
    expected.setdefault('tool', {}).setdefault('uv', {})['default-groups'] = groups
    try:
        good = tomllib.loads(new) == expected
    except tomllib.TOMLDecodeError:
        good = False
    if not good:
        print(f'The installer cannot place the setting safely, so add this line under [tool.uv] yourself: {line}',
              file=sys.stderr)
        return
    pyproject.write_text(new)
    print(f'Set {line} in pyproject.toml, so that uv run has the tools.', file=sys.stderr)


def drop_empty_group(target, group):
    """Take an empty dependency group, such as lint = [], out of pyproject.toml after its last package went. The file
    changes only when the result parses and holds the same data without that group."""
    path = target / 'pyproject.toml'
    if not path.is_file() or path.is_symlink():
        return
    text = path.read_text()
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return
    groups = data.get('dependency-groups')
    if not isinstance(groups, dict) or groups.get(group) != []:
        return
    uv = data.get('tool', {}).get('uv', {}) if isinstance(data.get('tool'), dict) else {}
    named = uv.get('default-groups') if isinstance(uv, dict) else None
    if isinstance(named, list) and group in named:
        # uv sync fails on a default group that is not defined, so the empty group stays.
        return
    pattern = re.compile(r'(?m)^[ \t]*(' + re.escape(group) + r'|"' + re.escape(group) + r'")[ \t]*=[ \t]*\[[ \t]*\][ \t]*(#[^\n]*)?\n?')
    new, count = pattern.subn('', text)
    expected = copy.deepcopy(data)
    del expected['dependency-groups'][group]
    try:
        good = count == 1 and tomllib.loads(new) == expected
    except tomllib.TOMLDecodeError:
        good = False
    if good:
        path.write_text(new)
        print(f'Took the empty dependency group {group} out of pyproject.toml.', file=sys.stderr)


def justfile_of(target):
    """The Justfile of the target, under the name that the folder has, on a disk that ignores case too."""
    try:
        entries = set(os.listdir(target))
    except OSError:
        return None
    for name in JUSTFILES:
        if name in entries and ((target / name).is_file() or (target / name).is_symlink()):
            return target / name
    return None


def write_justfile(target, dry_run):
    """Write the starter Justfile when the target has none, else check that it imports the recipe file."""
    existing = justfile_of(target)
    if existing is not None:
        if existing.is_symlink():
            print(f'{existing.name} is a symlink, so the installer leaves it alone. Make sure that it has the line '
                  f'{IMPORT_LINE}', file=sys.stderr)
            return False
        elif '.agents/install.just' not in existing.read_text(errors='replace'):
            print(f'Add this line to {existing.name}, so that just install works: {IMPORT_LINE}', file=sys.stderr)
        return False
    template = ROOT / 'templates/project.just'
    if dry_run:
        print('Would write Justfile from templates/project.just')
        return False
    write_new(target, Path('Justfile'), template.read_bytes().replace(b'PROJECT', target.name.encode()))
    print('Wrote Justfile; it is yours now. Put the recipes of the project in it.')
    return True


RECIPES = Path('.agents/just')
HEADER = '# Written by just install. Do not edit: the next just install writes it again.'



def module_text(name, profile, installed):
    """The module of one profile: its settings, an import of the recipe file of each installed tool, and verify."""
    lines = [HEADER, f'# The recipes of the {name} profile: just {name} NAME. They run in the folder of the project.',
             'set positional-arguments', "set working-directory := '../..'", '']
    files = [profile.get('base', {}).get('recipes')] + [profile['tools'][tool].get('recipes') for tool in installed]
    lines += [f"import? '{name}/{file}'" for file in dict.fromkeys(f for f in files if f)]
    steps = [step for tool in installed for step in profile['tools'][tool].get('verify', [])]
    # A step with arguments is a dependency with arguments: fmt --check becomes (fmt "--check").
    deps = ' '.join(step if ' ' not in step else '(' + step.split(' ')[0] + ' '
                    + ' '.join(json.dumps(arg) for arg in step.split(' ')[1:]) + ')' for step in steps)
    lines += ['', f'# Every check of the {name} profile that is installed: {", ".join(steps) or "none yet"}']
    lines += [f'verify: {deps}'.rstrip()] if steps else ['verify:', f'    @echo "just {name} verify: no checks are installed"']
    return '\n'.join(lines) + '\n'


def owned_by_install(target, path, generated):
    """True when the file is missing, or just install wrote it as it is now: .agents/stack.json records its hash."""
    if path.is_symlink():
        return False
    if not path.exists():
        return True
    try:
        return generated.get(path.relative_to(target).as_posix()) == hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return False


def write_generated(target, path, text, generated):
    """Write a file of just install, and record its hash."""
    if not path.exists() or path.read_text() != text:
        path.write_text(text)
    generated[path.relative_to(target).as_posix()] = hashlib.sha256(text.encode()).hexdigest()


def remove_generated(target, path, generated):
    path.unlink(missing_ok=True)
    generated.pop(path.relative_to(target).as_posix(), None)


SAID = set()


def say(message):
    """Print a message once in a run, although the recipes are written after each step."""
    if message not in SAID:
        SAID.add(message)
        print(message, file=sys.stderr)


def just_error(target, justfile):
    """The first error line of just for a Justfile, or None when it parses or just is missing."""
    if not shutil.which('just'):
        return None
    run = subprocess.run(['just', '--justfile', str(justfile), '--working-directory', str(target), '--summary'],
                         capture_output=True, text=True)
    if run.returncode == 0:
        return None
    lines = [line for line in run.stderr.splitlines() if line.startswith('error')] or run.stderr.splitlines() or ['']
    return lines[0]


def just_dump(target, justfile):
    """The JSON dump of a Justfile, or None when just is missing or the file does not parse."""
    if not shutil.which('just'):
        return None
    run = subprocess.run(['just', '--justfile', str(justfile), '--working-directory', str(target), '--dump',
                          '--dump-format', 'json'], capture_output=True, text=True)
    try:
        return json.loads(run.stdout) if run.returncode == 0 else None
    except ValueError:
        return None


def index_text(modules, recipes, aggregate=True, roles=()):
    """The index: a module for each profile, the recipe file of each role, and, for each recipe name of the profiles,
    a recipe that runs it in each profile that has it. verify also runs git diff --check. A project that jnterfaces
    did not start gets the modules only."""
    lines = [HEADER, '# The recipes of the profiles and roles of this project. .agents/install.just imports this file.',
             '']
    lines += [f"mod {name} '{name}.just'" for name in modules]
    if not aggregate:
        return '\n'.join(lines) + '\n'
    lines += [f"import? '{role}'" for role in roles]
    names = sorted({recipe for name in modules for recipe in recipes.get(name, {})} - {'verify'})
    for recipe in names:
        having = [name for name in modules if recipe in recipes.get(name, {})]
        takes = [name for name in having if recipes[name][recipe]]
        lines += ['', f'# just {recipe} for each language that has it: {", ".join(having)}']
        if takes:
            lines += ['[positional-arguments]', f'{recipe} *args:']
        else:
            lines += [f'{recipe}:']
        lines += [f'    just --justfile "{{{{justfile()}}}}" {name} {recipe}' + (' "$@"' if name in takes else '')
                  for name in having]
    if modules:
        lines += ['', f'# Every installed check of each language, then the check of git for whitespace errors',
                  'verify:'] + [f'    just --justfile "{{{{justfile()}}}}" {name} verify' for name in modules]
        lines += ['    git diff --check']
    if modules and 'diff-check' not in names:
        lines += ['', '# Find whitespace errors in the changes', 'diff-check:', '    git diff --check']
    return '\n'.join(lines) + '\n'


def write_recipes(target, profiles, active, tools, dry_run, generated, started, known=(), roles=()):
    """Write .agents/just: a module for each active profile with the recipes of its installed tools, and the index.
    A file that the user changed stays as it is. If the Justfile does not parse with the index, the index gets the
    modules only, then nothing."""
    folder = target / RECIPES
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        print(f'{folder} is not a plain folder, so the recipes of the profiles are not written.', file=sys.stderr)
        return
    modules = {}
    for name in active:
        installed = [tool for tool in profiles[name]['tools'] if f'{name}:{tool}' in tools]
        modules[name] = module_text(name, profiles[name], installed)
    if dry_run:
        print('Would write the recipes of ' + (', '.join(modules) if modules else 'no profile') + ' in .agents/just')
        return
    kept = {}
    for name, text in modules.items():
        path = folder / f'{name}.just'
        # A file with the text that just install writes now is its own, also after a run that stopped before the record.
        same = path.is_file() and not path.is_symlink() and path.read_text(errors='replace') == text
        if not same and not owned_by_install(target, path, generated):
            say(f'Kept {path.relative_to(target)}: just install did not write it as it is now, so it leaves it alone.')
            kept[name] = path
            continue
        folder.mkdir(parents=True, exist_ok=True)
        write_generated(target, path, text, generated)
        kept[name] = path
    # The module of a profile that left goes, when just install wrote it as it is now.
    for relative in list(generated):
        path = target / relative
        if (Path(relative).parent == RECIPES and path.suffix == '.just' and path.stem in known
                and path.stem not in modules and owned_by_install(target, path, generated)):
            remove_generated(target, path, generated)
    recipes = {}
    for name, path in kept.items():
        dump = just_dump(target, path)
        recipes[name] = {} if dump is None else {
            recipe: bool(info.get('parameters')) for recipe, info in dump.get('recipes', {}).items()
            if not info.get('private')}
    index = folder / 'index.just'
    justfile = justfile_of(target)
    started = started or justfile is None
    roles = [role for role in roles if (folder / role).is_file()]
    texts = {index_text(kept, recipes, aggregate=True, roles=roles), index_text(kept, recipes, aggregate=False),
             index_text({}, recipes, aggregate=False)}
    same = index.is_file() and not index.is_symlink() and index.read_text(errors='replace') in texts
    if not same and not owned_by_install(target, index, generated):
        say(f'Kept {index.relative_to(target)}: just install did not write it as it is now, so it leaves it alone.')
        error = just_error(target, justfile) if justfile is not None else None
        if error:
            say(f'The Justfile does not parse with the index that you kept. just said: {error}')
        return
    if kept and not started:
        say(f'{justfile.name} was not started by jnterfaces, so just install adds the recipes of each profile as a '
            f'module only: just {next(iter(kept))} NAME.')
    if roles and not started:
        say(f'{justfile.name} was not started by jnterfaces, so the recipes of the roles are not added. To add them, '
            'put these lines in it: ' + '; '.join(f"import? '.agents/just/{role}'" for role in roles))
        roles = []
    if not (kept or roles or index.exists()):
        return
    folder.mkdir(parents=True, exist_ok=True)
    # The Justfile must still parse, so that just install keeps working. If a name of the project meets a name of
    # the index, the index gets the modules only, then nothing.
    tries = [('all', kept, started)] if started else []
    if not started:
        roles = []
    tries += [('modules', kept, False), ('none', {}, False)]
    first = None
    for kind, chosen, aggregate in tries:
        write_generated(target, index, index_text(chosen, recipes, aggregate=aggregate, roles=roles), generated)
        error = just_error(target, justfile) if justfile is not None else None
        if error is None:
            if first:
                say(f'The Justfile does not parse with the {"combined recipes" if first[0] == "all" else "modules"} '
                    f'of the profiles and roles, so the index has {"the modules only" if kind == "modules" else "none of them"}. '
                    f'Rename the recipe, alias or module of the project with that name. just said: {first[1]}')
            return
        first = first or (kind, error)
    say(f'The Justfile does not parse, also with an empty index. just said: {error}')


def main(argv=None):
    SAID.clear()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('names', nargs='*', help='roles, tools or profiles to turn on')
    parser.add_argument('--target', type=Path, default=Path('.'), help='the repository to install into; default: here')
    parser.add_argument('--without', action='append', default=[], metavar='A,B', help='turn these roles or tools off')
    parser.add_argument('--with', dest='with_', action='append', default=[], metavar='A,B', help=argparse.SUPPRESS)
    parser.add_argument('--profile', metavar='A,B', help='set the profiles, such as python or typescript')
    parser.add_argument('--all', action='store_true', help='turn every role on')
    parser.add_argument('--defaults', action='store_true',
                        help='ask no questions: keep the saved or installed state, else take the defaults')
    parser.add_argument('--ask', action='store_true', help='ask about each role, even when the target has saved choices')
    parser.add_argument('--clone', action='append', default=[], metavar='NAME=DIR',
                        help='use this clone of a package; default: a folder in SKILL_PACKAGES_DIR, else next to '
                             'the jnterfaces clone')
    parser.add_argument('--update', action='store_true', help='git pull --ff-only each clone of a package first')
    parser.add_argument('--clean', action='store_true', help='install the committed HEAD of each clone')
    parser.add_argument('--dry-run', action='store_true', help='check everything and write nothing in the target')
    parser.add_argument('--check', action='store_true',
                        help='compare .agents/stack-log.jsonl with the line files and the whole files, and change nothing')
    parser.add_argument('--registry', type=Path, default=ROOT / 'stack.json', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.all and args.defaults:
        raise StackError('Use --all or --defaults, not both')
    if Path(str(args.target).rstrip('/') or '/').is_symlink():
        raise StackError('The target repository root must not be a symlink')
    target = args.target.resolve()
    if not target.is_dir():
        raise StackError(f'Target does not exist: {target}')
    if target == ROOT:
        raise StackError('The target must not be this jnterfaces clone')
    if args.check:
        return shared_files.check(target)
    overrides = {}
    for item in args.clone:
        name, sep, path = item.partition('=')
        if not sep or not name or not path:
            raise StackError(f'--clone needs NAME=DIR: {item}')
        overrides[name] = Path(path).expanduser()
    packages, choices, aliases, registry_profiles = load_registry(args.registry)
    check_overrides(packages + [{'name': entry['package']} for entry in registry_profiles.values()], overrides)
    split = sort_names(args, choices, aliases, registry_profiles)
    record = read_record(target, choices, aliases)
    active = pick_profiles(args, split, record, registry_profiles, target)
    saved_on = [name for name in (record or {}).get('profiles', []) if name in registry_profiles]
    # --profile sets the profiles, so a saved profile that it leaves out is turned off.
    dropped = [name for name in saved_on if name not in active] if args.profile is not None else []
    if dropped:
        print(f'--profile leaves out the saved profile(s) {", ".join(dropped)}, so they are turned off.', file=sys.stderr)
    # A removal of a profile that an earlier run did not finish goes on, unless this run names the profile again.
    unfinished = [name for name in (record or {}).get('removing_profiles', [])
                  if name in saved_on and name not in split['profiles'] and name not in (args.profile or '').split(',')]
    # A profile that is turned off stays active in this run, so that its tools can be removed.
    leaving = list(dict.fromkeys([name for name in split['profiles_off'] if name in active or name in saved_on]
                                 + dropped + unfinished))
    active = list(dict.fromkeys(active + [name for name in leaving if name not in active]))
    unknown_off = [name for name in split['profiles_off'] if name not in active]
    if unknown_off:
        print(f'The profile(s) {", ".join(unknown_off)} are not on, so there is nothing to remove.', file=sys.stderr)
    profiles = load_profiles(active, registry_profiles, choices, overrides)
    defaults = {name: bool(entry.get('default')) for name, entry in choices.items()}
    for name in active:
        if name not in leaving:
            defaults.update({choice: bool(value) for choice, value in profiles[name].get('choices', {}).items()})
    args.with_, args.without = split['roles_on'], split['roles_off']
    implied = bool(split['tools_on'] or split['profiles'] or args.profile)
    chosen = resolve(args, choices, record and record['choices'], sys.stdin.isatty(), installed(target, packages),
                     defaults, implied)
    # The variant of each role that is on: named now, else saved, else the default of the role.
    variants = {}
    for name, entry in choices.items():
        offered = entry.get('variants')
        if offered and chosen.get(name):
            saved = (record or {}).get('variants', {}).get(name)
            if saved and saved not in offered and name not in split['variants']:
                print(f'The saved variant {name}={saved} is gone from stack.json, so {name} takes '
                      f'{entry["default_variant"]}.', file=sys.stderr)
            variant = split['variants'].get(name) or (saved if saved in offered else None) or entry['default_variant']
            if not offered[variant].get('ready', True):
                raise StackError(f'{name}={variant} is not ready yet: {offered[variant]["about"]}. Name another '
                                 f'variant, for example: just install {name}={entry["default_variant"]}')
            variants[name] = variant
    available = {tool for name in active for tool in profiles[name]['tools']}
    wanted = set(split['tools_on']) | set(split['tools_off'])
    if wanted - available:
        raise StackError(f'Unknown name, or no active profile has the tool: {", ".join(sorted(wanted - available))}. '
                         f'The roles are: {", ".join(choices)}. The profiles here are: {", ".join(active) or "none"}, '
                         f'and their tools are: {", ".join(sorted(available)) or "none"}. The profiles are: '
                         f'{", ".join(registry_profiles)}.')
    # Each tool belongs to one profile, so the lists hold profile:tool pairs: python and typescript both have a lint.
    saved_profiles = (record or {}).get('profiles', [])

    def pairs(entries):
        found = []
        for entry in entries:
            name, sep, tool = entry.partition(':')
            # A record from before the pairs lists bare tool names: the tool of each saved profile that has it.
            candidates = [(name, tool)] if sep else [(name, entry) for name in saved_profiles]
            found += [f'{n}:{t}' for n, t in candidates if n in active and t in profiles[n]['tools']]
        return list(dict.fromkeys(found))
    saved_tools = pairs((record or {}).get('tools', []))
    pending = pairs((record or {}).get('pending', []))
    # A profile turned on now gets its own default tools. A tool that a saved profile turned off stays off.
    # A profile named again while its removal is unfinished starts again, with its default tools.
    revived = {name for name in (record or {}).get('removing_profiles', []) if name in split['profiles']
               or name in (args.profile or '').split(',')}
    new_defaults = [f'{name}:{tool}' for name in active if (name not in saved_profiles or name in revived)
                    and name not in leaving for tool in profiles[name].get('default_tools', [])]
    # A named tool, on or off, is the tool of each active profile that has it.
    named_on = [f'{name}:{tool}' for tool in split['tools_on'] for name in active if tool in profiles[name]['tools']]
    off = {f'{name}:{tool}' for tool in split['tools_off'] for name in active if tool in profiles[name]['tools']}
    off |= {f'{name}:{tool}' for name in leaving for tool in profiles[name]['tools']}
    # A removal that an earlier run did not finish goes on, unless this run names the tool again.
    # Naming a profile again also cancels the unfinished removal of its tools.
    again = set(split['profiles']) | set((args.profile or '').split(','))
    off |= {pair for pair in pairs((record or {}).get('removing', []))
            if pair not in named_on and pair.partition(':')[0] not in again}
    tools_add = [pair for pair in dict.fromkeys(named_on + new_defaults + pending) if pair not in off]
    tools = [pair for pair in dict.fromkeys(saved_tools + tools_add) if pair not in off]
    tools_remove = [pair for pair in saved_tools if pair in off]
    steps = plan(packages, chosen)
    # The package of each profile is installed like a package of a role: its skills and its recipe files.
    steps += [(profiles[name]['package'], ['--record', str(record_path(profiles[name]['package']))])
              for name in active if name not in leaving]
    found = installed(target, packages)
    removals = [p for p in packages if p.get('choice') and not chosen[p['choice']] and found.get(p['choice'])]
    removals += [profiles[name]['package'] for name in leaving
                 if (target / record_path(profiles[name]['package'])).is_file()]
    clones = {p['name']: clone_dir(p, overrides) for p in [s[0] for s in steps] + removals}
    clones.update({profiles[name]['package']['name']: profiles[name]['root'] for name in active})
    print(f'The package clones are in {packages_dir()}; set SKILL_PACKAGES_DIR to use another folder.', file=sys.stderr)
    if args.update:
        for name, clone in clones.items():
            if name != 'jnterfaces':
                update(clone)
    heads = {name: head_of(clone) for name, clone in clones.items()} if args.clean else {}
    for package, _ in steps:
        lacking = missing_tools(clones[package['name']])
        if lacking:
            print(f'{package["name"]} needs {", ".join(lacking)} on PATH; install it to use those skills.', file=sys.stderr)
    # Every package checks its install, or its removal, before any package writes a file.
    for package, options in steps:
        if run_installer(clones[package['name']], options, target, heads.get(package['name']), True,
                         quiet=not args.dry_run):
            raise StackError(f'The dry run of {package["name"]} failed; nothing was written')
    for package in removals:
        options = ['--record', str(record_path(package)), '--remove']
        if run_installer(clones[package['name']], options, target, None, True, quiet=not args.dry_run):
            raise StackError(f'The dry run of the removal of {package["name"]} failed; nothing was written')
    owned = {key: list(names) for key, names in (record or {}).get('owned', {}).items()}
    # The added_packages of a record from earlier, keyed profile:tool, move to their owner keys.
    for key, names in (record or {}).get('added_old', {}).items():
        name, _, tool = key.partition(':')
        if name in profiles and tool in profiles[name]['tools']:
            place = owner_key(name, profiles[name], tool)
            owned[place] = sorted(set(owned.get(place, [])) | set(names))
    # The record can drift from the project file: a package removed by hand, or claimed by an add that was cut off.
    # So each run keeps only the owned packages that the project file has now, where it can read that file.
    for key in list(owned):
        name, _, group = key.partition(':')
        if name in profiles and name in active:
            present = present_packages(target, profiles[name], group, place_only=True)
            if present is not None:
                owned[key] = sorted(set(owned[key]) & present)
    commands = tool_steps(target, profiles, active, tools_add, tools_remove, tools, owned, args.dry_run)
    for name in active:
        init = profiles[name].get('init')
        if init and any(step[1] == name for step in commands) and (target / init['file']).is_symlink():
            raise StackError(f'{target / init["file"]} is a symlink, so the installer leaves it alone. Make it a file '
                             f'and run just install again. Nothing was written.')
    if args.dry_run:
        for name in active:
            init = profiles[name].get('init')
            if init and any(step[1] == name for step in commands) and not (target / init['file']).exists():
                print('Would run: ' + ' '.join(init['command']))
        for kind, profile, tool, command, files, entry, _, problem in commands:
            if problem:
                print(f'Would fail: {problem}')
                continue
            for first in entry.get('before', []):
                print('Would run: ' + ' '.join(first))
            print('Would run: ' + ' '.join(command) if command else f'The project already has the packages of {tool}')
        sync_files(target, profiles, [n for n in active if n not in leaving], tools, active, True)
        write_justfile(target, True)
        write_recipes(target, profiles, [n for n in active if n not in leaving], tools, True, {}, False)
        if any(step[7] for step in commands):
            print('Dry run: the real run would fail at the steps above.')
            return 1
        print('Dry run: ' + ', '.join(p['name'] for p, _ in steps) + f' would be installed in {target}'
              + (', and ' + ', '.join(p['name'] for p in removals) + ' removed' if removals else ''))
        return 0
    for package, options in steps:
        if run_installer(clones[package['name']], options, target, heads.get(package['name']), False):
            raise StackError(f'The install of {package["name"]} failed. The packages before it are installed.')
    for package in removals:
        options = ['--record', str(record_path(package)), '--remove']
        if run_installer(clones[package['name']], options, target, None, False):
            raise StackError(f'The removal of {package["name"]} failed.')
    record_file = target / RECORD

    # The files that just install writes in .agents/just, with their hashes, and whether it wrote the Justfile. A
    # file is the installer's to change only while its hash is the recorded one.
    generated = dict((record or {}).get('generated', {}))
    state = {'started': bool((record or {}).get('started'))}

    def save(tool_list, waiting, going=()):
        record_file.parent.mkdir(exist_ok=True)
        # A profile that is turned off leaves the record once none of its tools is left.
        kept_profiles = [name for name in active if name not in leaving
                         or any(pair.partition(':')[0] == name for pair in tool_list)]
        record = {'version': 2, 'profiles': kept_profiles, 'choices': dict(sorted(chosen.items())), 'tools': tool_list}
        if variants:
            record['variants'] = dict(sorted(variants.items()))
        kept = {key: sorted(names) for key, names in sorted(owned.items()) if names}
        if kept:
            record['owned_packages'] = kept
        if waiting:
            record['pending_tools'] = waiting
        if going:
            record['removing_tools'] = list(going)
        unfinished_profiles = [name for name in leaving if name in kept_profiles]
        if unfinished_profiles:
            record['removing_profiles'] = unfinished_profiles
        write_recipes(target, profiles, [name for name in kept_profiles if name not in leaving], tool_list, False,
                      generated, state['started'], registry_profiles,
                      [f'roles/{package["name"]}.just' for package, _ in steps])
        if generated:
            record['generated'] = dict(sorted(generated.items()))
        if state['started']:
            record['started'] = True
        record_file.write_text(json.dumps(record, indent=2) + '\n')

    # Save the roles before the tools run, so that a failed tool keeps the roles that were installed.
    save(saved_tools, [t for t in tools_add if t not in saved_tools], tools_remove)
    if write_justfile(target, False):
        state['started'] = True
    for name in active:
        prepare(target, profiles[name], [step for step in commands if step[1] == name])
    done = list(saved_tools)
    # The add steps of each tool, by index. A tool is installed only when all of them succeeded.
    adding = {pair: {i for i, step in enumerate(commands) if step[0] == 'add' and f'{step[1]}:{step[2]}' == pair}
              for pair in tools_add}
    # The remove steps that cover each turned-off tool, by index. A tool leaves the record only when they all ran.
    covering = {pair: {i for i, (kind, profile, _, _, _, _, (owner, _), _) in enumerate(commands) if kind == 'remove'
                       and profile == pair.partition(':')[0]
                       and owner_key(profile, profiles[profile], pair.partition(':')[2]) == owner}
                for pair in tools_remove}

    def progress():
        # A tool whose removal did not run stays in the record, so that --without can try again. A tool whose adds
        # did not all run stays pending, so that just install tries it again.
        save([t for t in done if t not in tools_remove or covering[t]], [t for t in tools_add if adding[t]],
             [t for t in tools_remove if covering[t]])
    def settle(kind, profile, tool, entry, owner, names):
        # The claim holds only while the command runs. After it, the packages that it added are the claimed ones
        # that the project file has where just install puts them; an unreadable file keeps none.
        if kind == 'add' and names:
            after = present_packages(target, profiles[profile], entry.get('group', tool), place_only=True)
            kept = set(names) & after if after is not None else set()
            owned[owner] = sorted((set(owned.get(owner, [])) - set(names)) | kept)

    for index, (kind, profile, tool, command, files, entry, (owner, names), problem) in enumerate(commands):
        if kind == 'add' and names:
            # Claim the packages before the command runs, so that a run that is killed keeps them. The next run
            # drops any claimed package that the project file does not have.
            owned[owner] = sorted(set(owned.get(owner, [])) | set(names))
            progress()
        failed = problem
        try:
            if not problem:
                for first in entry.get('before', []):
                    print(f'Before removing {tool} for {profile}: {" ".join(first)}', file=sys.stderr)
                    code = subprocess.run(first, cwd=target).returncode
                    if code:
                        failed = f'{" ".join(first)} exited with {code}'
                        break
                if failed:
                    pass
                elif command:
                    print(f'{"Adding" if kind == "add" else "Removing"} {tool} for {profile}: {" ".join(command)}',
                          file=sys.stderr)
                    failed = subprocess.run(command, cwd=target).returncode
                else:
                    print(f'The project already has the packages of {tool} for {profile}', file=sys.stderr)
        except (OSError, StackError) as exc:
            failed = str(exc)
        except BaseException:
            # Ctrl-C: settle the claim and save, then stop.
            settle(kind, profile, tool, entry, owner, names)
            progress()
            raise
        settle(kind, profile, tool, entry, owner, names)
        if failed:
            progress()
            reason = failed if isinstance(failed, str) else f'{" ".join(command)} exited with {failed}'
            raise StackError(f'A step for {profile} failed: {reason}. The roles are installed; fix the cause and run '
                             f'just install again, which tries the tool again.')
        if kind == 'add':
            pair = f'{profile}:{tool}'
            adding[pair].discard(index)
            if not adding[pair] and pair not in done:
                done.append(pair)
        else:
            owned[owner] = sorted(set(owned.get(owner, [])) - set(names))
            if profiles[profile]['manifest'] == 'pyproject.toml' and command:
                drop_empty_group(target, tool)
        for steps_left in covering.values():
            steps_left.discard(index)
        # Save after each step, so that an interrupted run keeps what it added and removed.
        progress()
    # The line files and the whole files change after the package commands succeeded, as D12 of
    # docs/plan-shared-files.md says.
    sync_files(target, profiles, [name for name in active if name not in leaving], tools, active, False)
    save(tools, [])
    print(f'Installed in {target}:')
    for package, _ in steps:
        own = target / record_path(package)
        revision = json.loads(own.read_text()).get('source_revision', '?') if own.is_file() else '?'
        print(f'  {package["name"]} at {revision}')
    if tools:
        print(f'  tools: {", ".join(tools)}')
    for name, variant in sorted(variants.items()):
        print(f'  {name}: {variant} ({choices[name]["variants"][variant]["about"]})')
    print(f'Saved in {RECORD}. Run just install again without names to install the same set.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except StackError as exc:
        print(f'Install failed: {exc}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('Stopped. The record keeps what was done; run just install again to finish.', file=sys.stderr)
        sys.exit(130)
