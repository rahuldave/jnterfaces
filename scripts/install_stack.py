#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Install skill roles and project tools into one repository, and save them: just install lint, just install tasker.

stack.json lists the packages, their roles and the profiles. A role, such as tasker, names what a package does, so
the package behind it can change. A profile, such as python, sets the default roles, the tools, and the Justfile.
A tool, such as lint, adds packages to the project with the command of its profile: uv for Python, npm for TypeScript.

jnterfaces and its just skill are always installed. Each package is a Git clone in the folder that holds this
jnterfaces clone; a missing clone is cloned from GitHub. The choices are saved in .agents/stack.json in the target,
and a later run without names installs them again.

Names are roles, tools or profiles:

    just install                 install what .agents/stack.json saves, or ask the first time
    just install lint tasker     add the lint tool and the tasker role
    just install --without reporter,lint
    just install --profile python
"""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib

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
    aliases = registry.get('aliases', {})
    profiles = registry.get('profiles', {})
    if not isinstance(aliases, dict) or any(value not in choices for value in aliases.values()):
        raise StackError('Each alias in stack.json must name a choice')
    if not isinstance(profiles, dict):
        raise StackError('profiles in stack.json must be an object')
    for name, profile in profiles.items():
        ok = isinstance(profile, dict)
        tools = profile.get('tools', {}) if ok else {}
        init = profile.get('init', {}) if ok else {}
        ok = (ok and string_list(profile.get('detect', [])) and string_list(profile.get('add'))
              and string_list(profile.get('remove')) and isinstance(tools, dict) and plain_path(profile.get('justfile'))
              and string_list(profile.get('default_tools', [])) and set(profile.get('default_tools', [])) <= set(tools)
              and isinstance(profile.get('choices', {}), dict) and set(profile.get('choices', {})) <= set(choices)
              and profile.get('manifest') in ('pyproject.toml', 'package.json')
              and isinstance(init, dict)
              and (not init or (plain_path(init.get('file')) and string_list(init.get('command')) and init['command']))
              and (profile.get('uv_default_groups') is None or isinstance(profile['uv_default_groups'], str)
                   or string_list(profile['uv_default_groups']))
              and all(isinstance(tool, dict) and string_list(tool.get('packages')) and tool['packages']
                      and isinstance(tool.get('files', {}), dict)
                      and all(plain_path(k) and plain_path(v) for k, v in tool.get('files', {}).items())
                      and isinstance(tool.get('group', ''), str)
                      and isinstance(tool.get('skip_if', {}), dict)
                      and all(string_list(v) and len(v) == 2 and plain_path(v[0]) for v in tool.get('skip_if', {}).values())
                      for tool in tools.values()))
        if not ok:
            raise StackError(f'The profile {name} in stack.json is malformed')
    clash = (set(choices) | set(aliases)) & ({tool for p in profiles.values() for tool in p['tools']} | set(profiles))
    if clash:
        raise StackError(f'A name in stack.json is both a role and a tool or profile: {", ".join(sorted(clash))}')
    return packages, choices, aliases, profiles


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
    owned, added = record.get('owned_packages', {}), record.get('added_packages', {})
    for field, value in (('owned_packages', owned), ('added_packages', added)):
        if not isinstance(value, dict) or not all(string_list(names) for names in value.values()):
            raise StackError(f'{path} has {field} that are not lists of names')
    if not all(string_list(value) for value in (profiles, tools, pending, removing)):
        raise StackError(f'{path} has profiles or tools that are not a list of names')
    renamed = {}
    for name, value in chosen.items():
        renamed[aliases.get(name, name)] = value
    return {'profiles': profiles, 'choices': renamed, 'tools': tools, 'pending': pending, 'owned': owned,
            'added_old': added, 'removing': removing}


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
    of stack.json. Then the questions or the options change it. On a first run with names, a terminal asks about the
    other roles. Without a terminal, a named profile or tool takes the defaults, and named roles alone turn on only
    those roles.
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
        if interactive:
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
    """Split the names on the command line and in --with and --without into roles, tools and profiles."""
    tools = {tool for profile in profiles.values() for tool in profile['tools']}
    split = {'roles_on': [], 'roles_off': [], 'tools_on': [], 'tools_off': [], 'profiles': []}
    for raw, where in [(name, 'on') for name in args.names + names(args.with_)] + [(n, 'off') for n in names(args.without)]:
        name = aliases.get(raw, raw)
        if name in choices:
            split[f'roles_{where}'].append(name)
        elif name in tools:
            split[f'tools_{where}'].append(name)
        elif name in profiles and where == 'on':
            split['profiles'].append(name)
        else:
            raise StackError(f'Unknown name: {raw}. The roles are: {", ".join(choices)}. The tools are: '
                             f'{", ".join(sorted(tools))}. The profiles are: {", ".join(profiles)}.')
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
    if saved or split['profiles']:
        return list(dict.fromkeys(saved + split['profiles']))
    found = [name for name, profile in profiles.items() if any((target / f).exists() for f in profile.get('detect', []))]
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


def clone_dir(package, overrides):
    """Return the clone of a package. A missing clone in the folder of the packages is cloned from GitHub."""
    name = package['name']
    named = overrides.get(name) or overrides.get(package.get('choice', ''))
    path = Path(named) if named else ROOT.parent / name
    if name == 'jnterfaces' and not named:
        path = ROOT
    manifest = path / 'skill-package.json'
    if not manifest.is_file() and not named and not path.exists():
        print(f'Cloning {package["repository"]} into {path}', file=sys.stderr)
        run = subprocess.run(['git', 'clone', '--quiet', package['repository'], str(path)], capture_output=True, text=True)
        if run.returncode:
            raise StackError(f'Cannot clone {package["repository"]} into {path}: {run.stderr.strip()}')
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
            by_owner.setdefault(owner, (group, []))[1].extend(
                n for n in listed if n in mine and n in placed and n not in needed)
        for owner, (group, pkgs) in by_owner.items():
            pkgs = list(dict.fromkeys(pkgs))
            if pkgs:
                command = [part.replace('{group}', group) for part in profile['remove']] + pkgs
                steps.append(('remove', name, group, command, {}, {}, (owner, pkgs), None))
    return steps


def copy_tool_files(target, files, entry, dry_run):
    """Copy the config files of a tool from jnterfaces, each only when the target does not have it."""
    skip_if = entry.get('skip_if', {})
    for destination, origin in files.items():
        path = target / destination
        guard = skip_if.get(destination)
        if guard and (target / guard[0]).is_file() and guard[1] in (target / guard[0]).read_text(errors='replace'):
            print(f'Left out {destination}: {guard[0]} already has {guard[1]}', file=sys.stderr)
            continue
        if path.exists() or path.is_symlink():
            continue
        if dry_run:
            print(f'Would write {destination}')
            continue
        write_new(target, Path(destination), (ROOT / origin).read_bytes())
        print(f'Wrote {destination}; it is yours now, and later installs leave it alone.')


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


def justfile_of(target):
    for name in JUSTFILES:
        if (target / name).is_file() or (target / name).is_symlink():
            return target / name
    return None


def write_justfile(target, profiles, active, dry_run):
    """Write the Justfile of the first profile when the target has none, else check that it imports the recipe."""
    existing = justfile_of(target)
    if existing is not None:
        if existing.is_symlink():
            print(f'{existing.name} is a symlink, so the installer leaves it alone. Make sure that it has the line '
                  f'{IMPORT_LINE}', file=sys.stderr)
        elif '.agents/install.just' not in existing.read_text(errors='replace'):
            print(f'Add this line to {existing.name}, so that just install works: {IMPORT_LINE}', file=sys.stderr)
        return
    if not active:
        print(f'No Justfile and no profile. Write a Justfile with the line {IMPORT_LINE}, or pass --profile.',
              file=sys.stderr)
        return
    template = ROOT / profiles[active[0]]['justfile']
    if dry_run:
        print(f'Would write Justfile from {template.name}')
        return
    write_new(target, Path('Justfile'), template.read_bytes())
    print(f'Wrote Justfile from the {active[0]} profile ({template.name}); it is yours now.')


def main(argv=None):
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
                        help='use this clone of a package; default: a folder next to the jnterfaces clone')
    parser.add_argument('--update', action='store_true', help='git pull --ff-only each clone of a package first')
    parser.add_argument('--clean', action='store_true', help='install the committed HEAD of each clone')
    parser.add_argument('--dry-run', action='store_true', help='check everything and write nothing in the target')
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
    overrides = {}
    for item in args.clone:
        name, sep, path = item.partition('=')
        if not sep or not name or not path:
            raise StackError(f'--clone needs NAME=DIR: {item}')
        overrides[name] = Path(path).expanduser()
    packages, choices, aliases, profiles = load_registry(args.registry)
    check_overrides(packages, overrides)
    split = sort_names(args, choices, aliases, profiles)
    record = read_record(target, choices, aliases)
    active = pick_profiles(args, split, record, profiles, target)
    defaults = {name: bool(entry.get('default')) for name, entry in choices.items()}
    for name in active:
        defaults.update({choice: bool(value) for choice, value in profiles[name].get('choices', {}).items()})
    args.with_, args.without = split['roles_on'], split['roles_off']
    implied = bool(split['tools_on'] or split['profiles'] or args.profile)
    chosen = resolve(args, choices, record and record['choices'], sys.stdin.isatty(), installed(target, packages),
                     defaults, implied)
    available = {tool for name in active for tool in profiles[name]['tools']}
    wanted = set(split['tools_on']) | set(split['tools_off'])
    if wanted - available:
        raise StackError(f'No active profile has the tool(s) {", ".join(sorted(wanted - available))}. The profiles '
                         f'here are: {", ".join(active) or "none"}. Pass --profile to set them.')
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
    new_defaults = [f'{name}:{tool}' for name in active if name not in saved_profiles
                    for tool in profiles[name].get('default_tools', [])]
    # A named tool, on or off, is the tool of each active profile that has it.
    named_on = [f'{name}:{tool}' for tool in split['tools_on'] for name in active if tool in profiles[name]['tools']]
    off = {f'{name}:{tool}' for tool in split['tools_off'] for name in active if tool in profiles[name]['tools']}
    # A removal that an earlier run did not finish goes on, unless this run names the tool again.
    off |= set(pairs((record or {}).get('removing', []))) - set(named_on)
    tools_add = [pair for pair in dict.fromkeys(named_on + new_defaults + pending) if pair not in off]
    tools = [pair for pair in dict.fromkeys(saved_tools + tools_add) if pair not in off]
    tools_remove = [pair for pair in saved_tools if pair in off]
    steps = plan(packages, chosen)
    found = installed(target, packages)
    removals = [p for p in packages if p.get('choice') and not chosen[p['choice']] and found.get(p['choice'])]
    clones = {p['name']: clone_dir(p, overrides) for p in [s[0] for s in steps] + removals}
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
        for kind, _, tool, command, files, entry, _, problem in commands:
            if problem:
                print(f'Would fail: {problem}')
                continue
            copy_tool_files(target, files, entry, True)
            print('Would run: ' + ' '.join(command) if command else f'The project already has the packages of {tool}')
        write_justfile(target, profiles, active, True)
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

    def save(tool_list, waiting, leaving=()):
        record_file.parent.mkdir(exist_ok=True)
        record = {'version': 2, 'profiles': active, 'choices': dict(sorted(chosen.items())), 'tools': tool_list}
        kept = {key: sorted(names) for key, names in sorted(owned.items()) if names}
        if kept:
            record['owned_packages'] = kept
        if waiting:
            record['pending_tools'] = waiting
        if leaving:
            record['removing_tools'] = list(leaving)
        record_file.write_text(json.dumps(record, indent=2) + '\n')

    # Save the roles before the tools run, so that a failed tool keeps the roles that were installed.
    save(saved_tools, [t for t in tools_add if t not in saved_tools], tools_remove)
    write_justfile(target, profiles, active, False)
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
                copy_tool_files(target, files, entry, False)
                if command:
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
        for steps_left in covering.values():
            steps_left.discard(index)
        # Save after each step, so that an interrupted run keeps what it added and removed.
        progress()
    save(tools, [])
    print(f'Installed in {target}:')
    for package, _ in steps:
        own = target / record_path(package)
        revision = json.loads(own.read_text()).get('source_revision', '?') if own.is_file() else '?'
        print(f'  {package["name"]} at {revision}')
    if tools:
        print(f'  tools: {", ".join(tools)}')
    print(f'Saved in {RECORD}. Run just install again without names to install the same set.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except StackError as exc:
        print(f'Install failed: {exc}', file=sys.stderr)
        sys.exit(1)
