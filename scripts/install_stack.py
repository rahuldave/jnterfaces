#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["tomlkit==0.15.2", "ruamel.yaml==0.19.1"]
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
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import tomllib

import lint_steps
from lint_steps import REASONS, STEPS
import shared_files

ROOT = Path(__file__).resolve().parents[1]
RECORD = Path('.agents/stack.json')
JUSTFILES = ('justfile', 'Justfile', '.justfile', 'JUSTFILE')
IMPORT_LINE = "import? '.agents/install.just'"
# One run ID for a just call and each just call inside it, for the run log of jlog. Only the root Justfile has it: a
# module would make an ID of its own.
JLOG_LINE = 'export JLOG_RUN := env("JLOG_RUN", uuid())'
JLOG_RUN = re.compile(r'^\s*(export\s+)?JLOG_RUN\s*:=')
# jlog is the shell of each recipe that the installer writes, so it logs each line.
SHELL_LINE = 'set shell := ["jlog", "sh", "-cu"]'
# The note for a Justfile that sets a shell without jlog: jlog step, jlog skip and jlog pass then fail.
SHELL_NOTE = ('{name} sets its own shell without jlog, so the index sets none, and just check fails: jlog step, jlog '
              'skip and jlog pass work only in a recipe that runs through jlog. Put jlog in front of the shell: '
              'set shell := {line}')
JLOG_INSTALL = 'clone https://github.com/rahuldave/jlog, run git checkout v0.1.1 in the clone, then run just install'


class StackError(Exception):
    pass


def string_list(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def code_ok(value):
    """True for the globs of the key code of a profile.json: the source files of the language, such as **/*.rs.
    The gate reads them for @code."""
    return string_list(value) and all(item and not item.startswith('/') and '..' not in item.split('/')
                                      for item in value)


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
    for package in packages:
        drop = package.get('drop_with', {})
        if not (isinstance(drop, dict) and (not drop or 'choice' in package) and all(
                role in choices and role != package.get('choice') and isinstance(rule, dict)
                and set(rule) == {'files', 'why'} and isinstance(rule['why'], str) and rule['why']
                and string_list(rule['files']) and rule['files'] and all(plain_path(path) for path in rule['files'])
                for role, rule in drop.items())):
            raise StackError(f'drop_with of {package["name"]} in stack.json must map other roles to their files, '
                             f'plain paths, and a why')
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
          and profile.get('manifest') in ('pyproject.toml', 'package.json', 'Cargo.toml')
          and code_ok(profile.get('code', []))
          and isinstance(init, dict)
          and (not init or (plain_path(init.get('file')) and string_list(init.get('command')) and init['command']))
          and (profile.get('uv_default_groups') is None or isinstance(profile['uv_default_groups'], str)
               or string_list(profile['uv_default_groups']))
          and isinstance(base, dict) and recipe_file_ok(base.get('recipes'))
          and parts_ok(base)
          and isinstance(base.get('files', {}), dict)
          and all(plain_path(k) and plain_path(v) for k, v in base.get('files', {}).items())
          and lines_ok(base)
          and all(re.fullmatch(r'[a-z][a-z0-9_-]*', tool_name) for tool_name in tools)
          and all(isinstance(tool, dict) and string_list(tool.get('packages'))
                  and isinstance(tool.get('files', {}), dict)
                  and all(plain_path(k) and plain_path(v) for k, v in tool.get('files', {}).items())
                  and lines_ok(tool) and parts_ok(tool)
                  and isinstance(tool.get('group', ''), str)
                  and isinstance(tool.get('skip_if', {}), dict)
                  and all(guards_of(v) for v in tool.get('skip_if', {}).values())
                  and before_remove_ok(tool.get('before_remove'))
                  and recipe_file_ok(tool.get('recipes'))
                  and string_list(tool.get('verify', []))
                  and all(re.fullmatch(r'[a-z][a-z0-9_-]*( [^\s"\\]+)*', step) for step in tool.get('verify', []))
                  for tool in tools.values()))
    if not ok:
        raise StackError(f'The profile.json of the {name} profile is malformed')
    check_programs(name, profile)
    return profile


PROGRAM_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+-]*')
PROGRAM_KEYS = {'name', 'check', 'install', 'description', 'expect', 'install_in'}


def command_of(text):
    """The argv list of a check or install command of a program: the text split as a shell splits it, but run
    without a shell. None when the text is not a string, does not split, or is empty."""
    if not isinstance(text, str):
        return None
    try:
        argv = shlex.split(text)
    except ValueError:
        return None
    return argv or None


def check_programs(name, profile):
    """Check the key programs of a profile.json and the names in base.programs and tools.X.programs. Each program
    has a name, a check command, and an install command or a description. A program without an install needs the
    description, the hint for a person. With an install, the description is optional, and a failed install prints
    it. expect is a text that the output of the check must hold."""
    def fail(reason):
        raise StackError(f'The profile.json of the {name} profile is malformed: {reason}')
    declared = check_program_list(profile.get('programs', []), fail)
    places = [('base', profile.get('base', {}))] + [(f'the tool {tool}', entry)
                                                    for tool, entry in profile['tools'].items()]
    for place, entry in places:
        used = entry.get('programs', [])
        if not string_list(used) or len(set(used)) != len(used):
            fail(f'programs of {place} must be a list of names, each once')
        unknown = [program for program in used if program not in declared]
        if unknown:
            fail(f'programs of {place} names {", ".join(unknown)}, which programs does not declare')


def check_program_list(programs, fail):
    """Check a list of programs, of a profile.json or of a role, and return their names. fail gets the reason."""
    if not isinstance(programs, list):
        fail('programs must be a list')
    declared = []
    for entry in programs:
        if not isinstance(entry, dict) or not isinstance(entry.get('name'), str) \
                or not PROGRAM_NAME.fullmatch(entry['name']):
            fail('each program needs a name of letters, digits and the signs _ . + -')
        program = entry['name']
        if program in declared:
            fail(f'the program {program} is in programs twice')
        declared.append(program)
        if set(entry) - PROGRAM_KEYS:
            fail(f'the program {program} has the unknown key(s) {", ".join(sorted(set(entry) - PROGRAM_KEYS))}')
        if command_of(entry.get('check')) is None:
            fail(f'the program {program} needs a check, a command as a string')
        # A program on PATH, not a path: a path would depend on the machine, or on a file that the run writes later.
        if any('/' in command_of(entry[key])[0] for key in ('check', 'install') if command_of(entry.get(key))):
            fail(f'the check and the install of the program {program} must start with a program on PATH, not a path')
        if 'install' not in entry and 'description' not in entry:
            fail(f'the program {program} needs an install or a description')
        if 'install' in entry and command_of(entry['install']) is None:
            fail(f'the install of the program {program} must be a command as a string')
        if 'description' in entry and not (isinstance(entry['description'], str) and entry['description'].strip()):
            fail(f'the description of the program {program} must be a text')
        if 'expect' in entry and not (isinstance(entry['expect'], str) and entry['expect']):
            fail(f'the expect of the program {program} must be a text')
        if entry.get('install_in', 'project') not in ('project', 'home') or ('install_in' in entry
                                                                             and 'install' not in entry):
            fail(f'install_in of the program {program} must be project or home, and only with an install')
    return declared


def needed_programs(profiles, names, pairs):
    """The programs that the base of each profile in names and each of its tools in pairs need: each once, in the
    order of programs in its profile.json. A program that two profiles declare is the one of the first profile."""
    found = {}
    for name in names:
        profile = profiles[name]
        used = set(profile.get('base', {}).get('programs', []))
        for tool, entry in profile['tools'].items():
            if f'{name}:{tool}' in pairs:
                used |= set(entry.get('programs', []))
        for entry in profile.get('programs', []):
            if entry['name'] in used and entry['name'] not in found:
                found[entry['name']] = dict(entry, profile=name)
    return list(found.values())


def owner_of(entry):
    """Who needs a program, for the messages: the profile, or the role that the entry names."""
    return entry.get('owner') or f'the {entry["profile"]} profile'


# The seconds that a check of a program may take. A check of cargo can make rustup download a toolchain first.
CHECK_SECONDS = 900


def stop_check(run):
    """Stop each process of a check. A process that left the session can keep the output open, so the wait for the
    output ends after 5 seconds."""
    try:
        os.killpg(run.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        run.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        run.stdout.close()
        # A process that the installer may not signal, such as a setuid one, can outlive the kill.
        try:
            run.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print(f'Could not stop process {run.pid} of a check; it may still run.', file=sys.stderr)


def program_works(target, entry):
    """Run the check of a program in the target, without a shell. True when it exits with 0 and its output holds the
    text of expect. The output is not shown. A check that takes more than CHECK_SECONDS fails."""
    try:
        # A session of its own, so that a timeout stops each process that the check started, not only the first.
        run = subprocess.Popen(command_of(entry['check']), cwd=target, env=project_env(), stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors='replace',
                               start_new_session=True)
    except OSError:
        return False
    try:
        out, _ = run.communicate(timeout=CHECK_SECONDS)
    except subprocess.TimeoutExpired:
        stop_check(run)
        print(f'{entry["check"]} took more than {CHECK_SECONDS} seconds, so it counts as failed', file=sys.stderr)
        return False
    except BaseException:
        # Ctrl-C does not reach a session of its own, so the check stops here.
        stop_check(run)
        raise
    return run.returncode == 0 and entry.get('expect', '') in out


def missing_expected(target, programs, works=None, earlier=()):
    """The problems with the programs before the run writes anything: each program without an install whose check
    fails, and each missing program whose install command is not on PATH. One line each. works, when given, gets the
    result of each check that ran, by the name of the program. earlier are the programs that install before these."""
    problems = []
    works = {} if works is None else works

    def check(entry):
        works[entry['name']] = program_works(target, entry)
        return works[entry['name']]
    # A program that an earlier program installs in this run counts as on PATH when a later install starts with its
    # name. Only a program whose check fails is installed, so only such a program counts.
    firsts = {command_of(entry['install'])[0] for entry in programs if 'install' in entry}
    coming = set()

    def installs(entry):
        if 'install' in entry and entry['name'] in firsts and not shutil.which(entry['name']):
            if not (works[entry['name']] if entry['name'] in works else check(entry)):
                coming.add(entry['name'])
    for entry in earlier:
        installs(entry)
    for entry in programs:
        if 'install' in entry:
            first = command_of(entry['install'])[0]
            if first not in coming and not shutil.which(first) and not check(entry):
                problems.append(f'{entry["name"]} (for {owner_of(entry)}): its install, {entry["install"]}, '
                                f'needs {first}, which is not on PATH.')
            installs(entry)
        elif not check(entry):
            problems.append(f'{entry["name"]} (for {owner_of(entry)}): {entry["check"]} failed'
                            + (f' or did not print {entry["expect"]}' if entry.get('expect') else '')
                            + f'. {entry["description"]}')
    return problems


def install_programs(target, programs):
    """Check each program that has an install, in the target, so that a toolchain file there counts. Install each one
    whose check fails, then check it again. Stop at the first one that still fails."""
    for entry in programs:
        if 'install' not in entry or program_works(target, entry):
            continue
        home = entry.get('install_in') == 'home'
        print(f'{entry["check"]} failed, so the installer runs for {owner_of(entry)}, in the '
              f'{"home folder" if home else "project folder"}: {entry["install"]}', file=sys.stderr)
        # In the home folder, a toolchain file of the project does not count: cargo install builds with the default
        # toolchain, not with an old one that the project pins. Without HOME, an empty folder outside the project.
        spare = None
        if home and not os.environ.get('HOME'):
            spare = tempfile.TemporaryDirectory()
        folder = Path(spare.name) if spare else Path(os.environ['HOME']) if home else target
        try:
            code = subprocess.run(command_of(entry['install']), cwd=folder, env=project_env(),
                                  stdin=subprocess.DEVNULL).returncode
        except OSError as exc:
            code = str(exc)
        finally:
            if spare:
                spare.cleanup()
        about = f' {entry["name"]}: {entry["description"]}' if entry.get('description') else ''
        if code:
            reason = code if isinstance(code, str) else f'it exited with {code}'
            raise StackError(f'The install of the program {entry["name"]} failed: {entry["install"]}: {reason}. The '
                             f'rest of the install is done. Install {entry["name"]} yourself, or fix the cause, and run '
                             f'just install again.{about}')
        if not program_works(target, entry):
            raise StackError(f'{entry["install"]} ran, but {entry["check"]} still fails'
                             + (f' or does not print {entry["expect"]}' if entry.get('expect') else '')
                             + f'. The rest of the install is done. Install {entry["name"]} yourself, and run just '
                             f'install again.{about}')


def parts_ok(part):
    """The parts of a base, a tool or a role: {shared file: source file}. scripts/formats.json must name each shared
    file, and a shared file is in lines or in parts, not in both."""
    parts = part.get('parts', {})
    return (isinstance(parts, dict) and all(isinstance(path, str) and path in shared_files.FORMATS and plain_path(source)
                                            for path, source in parts.items())
            and not set(parts) & set(part.get('lines', {})))


def check_sources(what, root, parts):
    """Check each source file of the parts: its parser reads it, a ruff source names no folder path, and each path
    of a tsconfig source starts with ${configDir}, so that it stays at the top of the project."""
    for path, origin in parts.items():
        source = root / origin
        if not source.is_file() or source.is_symlink():
            raise StackError(f'The source {origin} of {what} for {path} is not a file in {root}')
        text = source.read_text(errors='replace')
        kind = shared_files.FORMATS[path]
        try:
            if kind.get('include') == 'pointer':
                continue
            if kind['format'] == 'toml':
                data = shared_files.edit_toml.plain(shared_files.edit_toml.load(text))
            elif kind['format'] == 'yaml':
                shared_files.edit_yaml.load(text)
                data = None
            else:
                data = json.loads(text)
        except (ValueError, shared_files.edit_toml.EditError, shared_files.edit_yaml.EditError) as exc:
            raise StackError(f'The source {origin} of {what} for {path} does not parse: {exc}')
        if path == 'ruff.toml':
            folders = [value for value in leaves(data) if isinstance(value, str) and '/' in value]
            if folders:
                raise StackError(f'The source {origin} of {what} names the path {folders[0]}. ruff reads a path of '
                                 f'the own file from .agents/config, so name a folder, such as .agents, not a path.')
        if path == 'tsconfig.json':
            options = data.get('compilerOptions', {}) if isinstance(data, dict) else {}
            paths = [data.get(key, []) for key in ('include', 'exclude', 'files')] if isinstance(data, dict) else []
            paths = [item for listed in paths if isinstance(listed, list) for item in listed]
            paths += [options.get(key) for key in ('outDir', 'rootDir', 'baseUrl', 'declarationDir', 'tsBuildInfoFile')
                      if isinstance(options, dict) and isinstance(options.get(key), str)]
            wrong = [item for item in paths if isinstance(item, str) and not item.startswith('${configDir}')]
            if wrong:
                raise StackError(f'The source {origin} of {what} has the path {wrong[0]}, which does not start with '
                                 f'${{configDir}}. tsc reads it from .agents/config, so write ${{configDir}}/{wrong[0]}.')


def leaves(data):
    if isinstance(data, dict):
        for value in data.values():
            yield from leaves(value)
    elif isinstance(data, list):
        for value in data:
            yield from leaves(value)
    else:
        yield data


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


def project_env():
    """The environment of a command in the project. uv run of this script sets VIRTUAL_ENV to the environment of
    the script, so uv in the project would warn that it is not .venv of the project. The command does not get it."""
    return {key: value for key, value in os.environ.items() if key != 'VIRTUAL_ENV'}


def owners_of(profiles, names, pairs, commits, target=None, record=None):
    """The owners of the shared files, in order: each profile in names, followed by each of its tools in pairs, in
    the order of its profile.json. A package that has parts for a file leaves out its old whole file of that name,
    and a skip_if guard that applies leaves out a new part. A part that the record already holds stays: a guard
    stops a new part only, as it stopped a new whole file before."""
    found = []
    for name in names:
        profile = profiles[name]
        package, root = profile['package']['name'], profile['root']
        base = profile.get('base', {})
        units = {}
        if profile.get('uv_default_groups'):
            units['pyproject.toml'] = {'tool.uv.default-groups': profile['uv_default_groups']}
        parts = base.get('parts', {})
        found.append(shared_files.Owner(
            name, f'the {name} profile', package, commits[name], f'`just uninstall {name}` removes them.',
            copy.deepcopy(base.get('lines', {})),
            {path: (root / origin, None) for path, origin in base.get('files', {}).items() if path not in parts},
            {path: root / origin for path, origin in parts.items()}, units))
        for tool, entry in profile['tools'].items():
            if f'{name}:{tool}' in pairs:
                guards = entry.get('skip_if', {})
                parts = entry.get('parts', {})
                key = f'{name}:{tool}'
                applying = {path: next((guard for guard in guards_of(guards[path]) if target is not None
                                        and guard_applies(target, guard)), None) for path in guards}
                skipped = {path: applying[path] for path in parts if applying.get(path) and not any(
                               entry.get('state') == 'added' and key in entry.get('needs', [])
                               for entry in (record or {}).get(path, {}).values())}
                found.append(shared_files.Owner(
                    f'{name}:{tool}', f'the {tool} tool of the {name} profile', package, commits[name],
                    f'`just uninstall {tool}` removes them.', copy.deepcopy(entry.get('lines', {})),
                    {path: (root / origin, applying.get(path) or (guards_of(guards[path]) or [None])[0]
                            if path in guards else None) for path, origin in entry.get('files', {}).items()
                     if path not in parts},
                    {path: root / origin for path, origin in parts.items()}, skipped=skipped))
    return found


def guards_of(value):
    """The guards of a skip_if entry: one guard [file, text], or a list of them, of which any one applies. An empty
    list when the entry has neither form."""
    def ok(guard):
        return string_list(guard) and len(guard) == 2 and plain_path(guard[0])
    if ok(value):
        return [value]
    return value if isinstance(value, list) and value and all(ok(guard) for guard in value) else []


def guard_applies(target, guard):
    """True when the file of a skip_if guard has its text, such as [tool.ruff in pyproject.toml. An empty text means
    that the file is there. In a TOML file a guard of the form [a.b means that the table a.b is there, in any form:
    [tool.ruff], [ tool.ruff.lint ], tool.ruff.x = 1 or ruff.x = 1 under [tool]. A guard of the form [a.b] means that
    the table a.b holds a value of its own, not only tables: [tool.pytest] with addopts, but not with ini_options
    alone."""
    file = target / guard[0]
    if shared_files.linked(target, guard[0]):
        # The installer does not read through a symbolic link. A linked file may hold the config, so the guard holds.
        return True
    if not file.is_file():
        return False
    if not guard[1]:
        return True
    text = file.read_text(errors='replace')
    table = re.fullmatch(r'\[([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)(\]?)', guard[1])
    if guard[0].endswith('.toml') and table:
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            data = None
        if data is not None:
            for part in table.group(1).split('.'):
                if not isinstance(data, dict) or part not in data:
                    return False
                data = data[part]
            if table.group(2):
                return isinstance(data, dict) and any(not isinstance(value, dict) for value in data.values())
            return True
    # The text must start a line, so that a comment that names it does not count.
    return any(line.lstrip().startswith(guard[1]) for line in text.splitlines())


def role_extras(clone):
    """The parts and the starter of a role package: install_extras.parts and install_extras.starter."""
    try:
        extras = json.loads((clone / 'skill-package.json').read_text()).get('install_extras', {})
    except (OSError, ValueError, AttributeError) as exc:
        raise StackError(f'Cannot read skill-package.json in {clone}: {exc}')
    extras = extras if isinstance(extras, dict) else {}
    parts, starter = extras.get('parts', {}), extras.get('starter', {})
    if not parts_ok({'parts': parts}) or not (isinstance(starter, dict) and all(
            plain_path(path) and plain_path(origin) and not shared_files.reserved(path)
            for path, origin in starter.items())):
        raise StackError(f'install_extras.parts and install_extras.starter in {clone}/skill-package.json must map '
                         f'files that scripts/formats.json names, and plain paths, to source files')
    return parts, starter


def role_programs(packages, clones, known=()):
    """The programs of each role package, from install_extras.programs, in the order of stack.json. A role needs all
    of its programs while it is on. A program that a profile, or an earlier role, declares is not taken again."""
    found = {name: None for name in known}
    for package in packages:
        clone = clones.get(package['name'])
        if 'choice' not in package or clone is None:
            continue
        try:
            extras = json.loads((clone / 'skill-package.json').read_text()).get('install_extras', {})
        except (OSError, ValueError, AttributeError) as exc:
            raise StackError(f'Cannot read skill-package.json in {clone}: {exc}')
        programs = extras.get('programs', []) if isinstance(extras, dict) else []

        def fail(reason):
            raise StackError(f'install_extras.programs in {clone}/skill-package.json is malformed: {reason}')
        check_program_list(programs, fail)
        for entry in programs:
            if entry['name'] not in found:
                found[entry['name']] = dict(entry, profile=package['choice'], owner=f'the {package["choice"]} role')
    return [entry for entry in found.values() if entry is not None]


def role_before_remove(package, clone):
    """The step of a role package that runs before its removal, from install_extras.before_remove, or None. It is
    {git_hook, command}: the command runs in the project when the Git hook is there. A hook that the role installed
    would fail at each commit once the files of the role are gone."""
    try:
        extras = json.loads((clone / 'skill-package.json').read_text()).get('install_extras', {})
    except (OSError, ValueError, AttributeError) as exc:
        raise StackError(f'Cannot read skill-package.json in {clone}: {exc}')
    step = extras.get('before_remove') if isinstance(extras, dict) else None
    if step is None:
        return None
    command = command_of(step.get('command')) if isinstance(step, dict) else None
    if not (isinstance(step, dict) and set(step) == {'git_hook', 'command'} and plain_path(step['git_hook'])
            and command and '/' not in command[0]):
        raise StackError(f'install_extras.before_remove in {clone}/skill-package.json must be {{git_hook, command}}: '
                         f'the name of a Git hook, and a command as a string that starts with a program on PATH')
    return step


def run_before_remove(target, package, step, dry_run):
    """Run the step of a role before its removal, when its Git hook is there. A failed step does not stop the
    removal: the person can take the hook out."""
    hook = git_hook(target, step['git_hook'])
    if hook is None or not hook.is_file():
        return
    if dry_run:
        print(f'Would run before the removal of {package["choice"]}: {step["command"]}')
        return
    print(f'Before the removal of {package["choice"]}, the installer runs: {step["command"]}', file=sys.stderr)
    try:
        code = subprocess.run(command_of(step['command']), cwd=target, env=project_env(),
                              stdin=subprocess.DEVNULL).returncode
    except OSError as exc:
        code = str(exc)
    if code:
        print(f'{step["command"]} failed ({code}), so {hook} may stay. Take it out yourself if each commit fails.',
              file=sys.stderr)


def role_owners(packages, clones):
    """The owner of each role package that has parts or a starter, in the order of stack.json."""
    found = []
    for package in packages:
        clone = clones.get(package['name'])
        if 'choice' not in package or clone is None:
            continue
        parts, starter = role_extras(clone)
        if not parts and not starter:
            continue
        check_sources(f'the {package["choice"]} role', clone, parts)
        found.append(shared_files.Owner(
            package['choice'], f'the {package["choice"]} role', package['name'], commit_of(clone),
            f'`just uninstall {package["choice"]}` removes them.',
            parts={path: clone / origin for path, origin in parts.items()},
            starter={path: clone / origin for path, origin in starter.items()}))
    return found


# The starter files of the installer in a project that it starts: the lists of generated files and of the files
# that coverage leaves out. test-review of the tasker role judges each path that you or an agent add.
INSTALLER_STARTERS = {'.agents/generated.txt': ROOT / 'templates/generated.txt',
                      '.agents/coverage-exclude.txt': ROOT / 'templates/coverage-exclude.txt'}


def dropped_files(packages, chosen, clones):
    """The starter files that a role leaves out while another role is on, from drop_with in stack.json: {path:
    (reason, source files)}. The source files are the starter files of the package now, so that a starter that the
    record holds without a hash still counts as the installer's while it is unchanged."""
    found = {}
    for package in packages:
        for role, rule in package.get('drop_with', {}).items():
            # The rule holds while both roles are on. A file that a role left behind after it went is yours.
            if not (chosen.get(role) and chosen.get(package.get('choice'))):
                continue
            clone = clones.get(package['name'])
            starter = role_extras(clone)[1] if clone is not None else {}
            for path in rule['files']:
                found.setdefault(path, (f'the {role} role is on, and {rule["why"]}',
                                        [clone / starter[path]] if path in starter else []))
    return found


def sync_files(target, profiles, kept, kept_tools, every, dry_run, parts, roles=(), gone_roles=(), unsure=(),
               migrating=False, quiet=False, after=None, justfile=None, dropped=None, started=False):
    """Bring the shared files to the owners that stay: kept profiles, kept_tools and roles. every holds each profile
    of the run, also the ones that go, and gone_roles each role that goes. Return the new parts. quiet prints no
    notes, for the dry pass that checks the files before the package commands run. after gets the files that the
    run writes or removes, as shared_files.sync says. justfile holds the lines of the installer in the Justfile, as
    justfile_lines gives them. dropped names the starter files that no owner writes, as dropped_files says. started
    says that the installer started the project, so it writes its own starter files."""
    commits = {name: commit_of(profiles[name]['root']) for name in every}
    parts = move_justfile_parts(target, parts)
    known = owners_of(profiles, every, {f'{n}:{t}' for n in every for t in profiles[n]['tools']}, commits, target,
                      parts)
    active = owners_of(profiles, kept, set(kept_tools), commits, target, parts) + list(roles)
    known += list(roles) + list(gone_roles)
    # The installer always has its lines: each project has the index, whose recipes write the run log of jlog. In a
    # project that the installer started, it also has its starter files.
    installer = shared_files.Owner('jnterfaces', 'the installer', 'jnterfaces', commit_of(ROOT),
                                   'They stay while the project keeps .agents/stack.json.',
                                   dict(copy.deepcopy(shared_files.JNTERFACES_LINES), **(justfile or {})),
                                   starter=dict(INSTALLER_STARTERS) if started else None)
    active, known = [installer] + active, [installer] + known
    command = os.environ.get('JNTERFACES_COMMAND') or shlex.join(['install_stack.py', *sys.argv[1:]])
    sources = {profiles[name]['package']['name']: commits[name] for name in every}
    sources.update({owner.package: owner.commit for owner in roles})
    sources['jnterfaces'] = commit_of(ROOT)
    try:
        notes, new_parts, _ = shared_files.sync(target, active, known, command, sources, parts, unsure, dry_run,
                                                migrating, after, dropped)
    except (OSError, shared_files.SharedError) as exc:
        raise StackError(f'Cannot update the shared files: {exc}')
    for note in [] if quiet else notes:
        print(note, file=sys.stdout if dry_run else sys.stderr)
    return new_parts


def recipe_file_ok(value):
    """A recipe file of a profile: a plain file name in the profile's folder of .agents/just, or nothing."""
    return value is None or (isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]+\.just', value) is not None)


def load_profiles(names, registry_profiles, choices, overrides, fetch=True):
    """Read the profile.json of each named profile from the clone of its package. Each profile gets its root and
    its package."""
    loaded = {}
    for name in names:
        entry = registry_profiles[name]
        package = {'name': entry['package'], 'repository': entry['repository']}
        root = clone_dir(package, overrides, fetch)
        try:
            profile = json.loads((root / 'profile.json').read_text())
        except (OSError, ValueError) as exc:
            raise StackError(f'Cannot read profile.json of the {name} profile in {root}: {exc}')
        profile = check_profile(name, profile, choices)
        check_sources(f'the {name} profile', root, profile.get('base', {}).get('parts', {}))
        for tool, entry in profile['tools'].items():
            check_sources(f'the {tool} tool of the {name} profile', root, entry.get('parts', {}))
        profile['root'], profile['package'] = root, package
        loaded[name] = profile
    return loaded


def check_record_programs(target, record, registry, overrides):
    """For just install --check: print each program that the profiles, tools and roles of the record need and that
    is missing. True when one is missing. A profile or role without a clone is not checked, and --check clones
    nothing."""
    record = record if isinstance(record, dict) else {}

    def listed(key):
        value = record.get(key, [])
        return value if string_list(value) else []
    # The profiles and tools that a run without names keeps: the saved and pending ones, less those being removed.
    names = [name for name in listed('profiles') if name not in listed('removing_profiles')]
    saved = record.get('choices')
    if not names and not (isinstance(saved, dict) and True in saved.values()):
        return False
    try:
        packages, choices, aliases, registry_profiles = load_registry(registry)
    except StackError as exc:
        print(f'The programs are not checked: {exc}')
        return False
    # A record from before the renames saves a role under its old name.
    roles = [aliases.get(name, name) for name, on in saved.items() if on is True] if isinstance(saved, dict) else []
    tools = [pair for pair in listed('tools') + listed('pending_tools') if pair not in listed('removing_tools')]
    profiles, kept, unchecked = {}, [], False
    for name in names:
        if name not in registry_profiles:
            continue
        entry = registry_profiles[name]
        try:
            clone_dir({'name': entry['package'], 'repository': entry['repository']}, overrides, fetch=False)
        except StackError as exc:
            # A profile without a clone is not an error here: a clone of the project, as in CI, has none.
            print(f'The programs of the {name} profile are not checked: {exc}')
            continue
        try:
            profiles.update(load_profiles([name], registry_profiles, choices, overrides, fetch=False))
            kept.append(name)
        except StackError as exc:
            print(f'The programs of the {name} profile are not checked: {exc}')
            unchecked = True
    # A record from before the pairs lists bare tool names: the tool of each saved profile that has it.
    pairs = {pair if ':' in pair else f'{name}:{pair}' for pair in tools for name in kept}
    missing = unchecked
    programs = needed_programs(profiles, kept, pairs)
    role_packages, clones = [p for p in packages if p.get('choice') in roles], {}
    for package in role_packages:
        try:
            clones[package['name']] = clone_dir(package, overrides, fetch=False)
        except StackError as exc:
            print(f'The programs of the {package["choice"]} role are not checked: {exc}')
    try:
        programs += role_programs(role_packages, clones, [entry['name'] for entry in programs])
    except StackError as exc:
        print(f'The programs of the roles are not checked: {exc}')
        missing = True
    for entry in programs:
        if program_works(target, entry):
            continue
        missing = True
        if 'install' in entry:
            print(f'Missing program: {entry["name"]} (for {owner_of(entry)}): {entry["check"]} failed. '
                  f'just install runs: {entry["install"]}')
        else:
            print(f'Missing program: {entry["name"]} (for {owner_of(entry)}): {entry["check"]} failed. '
                  f'{entry["description"]}')
    return missing


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
    parts = record.get('parts') if 'parts_version' in record else None
    if parts is not None and not parts_record_ok(parts):
        raise StackError(f'{path} has parts that are not a map of files to units. Fix it, or take the key parts and '
                         f'parts_version out to make the record again from the old log.')
    return {'profiles': profiles, 'choices': renamed, 'tools': tools, 'pending': pending, 'owned': owned,
            'added_old': added, 'removing': removing, 'removing_profiles': removing_profiles,
            'generated': generated, 'started': record.get('started') is True, 'variants': variants,
            'parts': parts, 'parts_log_end': record.get('parts_log_end'), 'raw': record}


def parts_record_ok(parts):
    """True when the parts of the record have the shape that the installer writes."""
    return isinstance(parts, dict) and all(
        shared_files.plain(path) and isinstance(units, dict) and all(
            isinstance(unit, str) and isinstance(entry, dict) and entry.get('state') in shared_files.UNIT_STATES
            and string_list(entry.get('needs', [])) for unit, entry in units.items())
        for path, units in parts.items())


def read_parts(target):
    """The parts of the record, or None when the record has none yet."""
    path = target / RECORD
    try:
        record = json.loads(path.read_text()) if path.is_file() and not path.is_symlink() else {}
    except (OSError, ValueError) as exc:
        raise StackError(f'Cannot read {path}: {exc}')
    parts = record.get('parts') if isinstance(record, dict) and 'parts_version' in record else None
    if parts is not None and not parts_record_ok(parts):
        raise StackError(f'{path} has parts that are not a map of files to units')
    return parts


def write_record(target, record):
    """Write .agents/stack.json through a file next to it and a rename, so that a run that stops leaves it whole."""
    path = target / RECORD
    if path.is_symlink():
        raise StackError(f'{path} must not be a symlink')
    shared_files.write(path, (json.dumps(record, indent=2) + '\n').encode())


def move_from_log(target, record, packages, clones, dry_run):
    """The first run after the old installer: the record of the parts from the old log and from the records of the
    role packages. Return the parts, the files with an unsure claim, and whether this run moved them.

    The run saves the parts first, then takes the keys seeds and merged_hooks out of the record of each package, so
    that a run that stops between the two saves loses no hook."""
    if record is not None and record['parts'] is not None:
        last = record.get('parts_log_end')
        try:
            entries = shared_files.read_log(target)
        except shared_files.SharedError as exc:
            raise StackError(str(exc))
        ids = [entry['id'] for entry in entries]
        # The old log writes a started and a finished entry with one id, so the move ends at the last of them.
        newer = ids[len(ids) - ids[::-1].index(last):] if last in ids else ids
        if newer:
            raise StackError(f'{shared_files.LOG} has an entry from an old installer after the move to the record: '
                             f'{newer[0]}. Run the new installer on the branch that wrote it first, or delete that '
                             f'entry, and run just install again.')
        return record['parts'], set(), False
    # The starter files and hooks of a role move to the record only when its package now ships them as parts. A
    # package from before the parts still merges its hooks itself.
    package_records, taken = {}, {}
    for package in packages:
        if 'choice' not in package or package['name'] not in clones:
            continue
        parts_of, starter = role_extras(clones[package['name']])
        taken[package['name']] = set(parts_of) | set(starter)
        own = target / record_path(package)
        try:
            data = json.loads(own.read_text()) if own.is_file() and not own.is_symlink() else {}
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict) and not data.get('removed'):
            package_records[package['choice']] = {
                key: {path: value for path, value in data.get(key, {}).items() if path in taken[package['name']]}
                for key in ('seeds', 'merged_hooks') if isinstance(data.get(key), dict)}
    try:
        parts, unsure, last = shared_files.from_log(target, package_records)
    except shared_files.SharedError as exc:
        raise StackError(str(exc))
    if dry_run:
        return parts, unsure, True
    raw = dict(record['raw']) if record is not None else {'version': 2, 'choices': {}}
    raw['parts'], raw['parts_version'] = parts, 1
    if last:
        raw['parts_log_end'] = last
    if record is not None or parts:
        write_record(target, raw)
    for package in packages:
        own = target / record_path(package)
        if not taken.get(package['name']) or not own.is_file() or own.is_symlink():
            continue
        try:
            data = json.loads(own.read_text())
        except (OSError, ValueError):
            continue
        changed = False
        for key in ('seeds', 'merged_hooks'):
            if isinstance(data.get(key), dict) and set(data[key]) & taken[package['name']]:
                data[key] = {path: value for path, value in data[key].items() if path not in taken[package['name']]}
                changed = True
        if changed:
            shared_files.write(own, (json.dumps(data, indent=2) + '\n').encode())
    tracked = subprocess.run(['git', '-C', str(target), 'ls-files', '--error-unmatch', str(shared_files.LOG)],
                             capture_output=True)
    if tracked.returncode == 0:
        print(f'{shared_files.LOG} is now a history that git does not keep. Take it out of git once: '
              f'git rm --cached {shared_files.LOG}', file=sys.stderr)
    if parts:
        print(f'Moved the record of the shared files from {shared_files.LOG} to parts in {RECORD}.', file=sys.stderr)
    return parts, unsure, True


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


def clone_dir(package, overrides, fetch=True):
    """Return the clone of a package. A missing clone in the folder of the packages is cloned from GitHub, unless
    fetch is False."""
    name = package['name']
    named = overrides.get(name) or overrides.get(package.get('choice', ''))
    path = Path(named) if named else packages_dir() / name
    if name == 'jnterfaces' and not named:
        path = ROOT
    manifest = path / 'skill-package.json'
    if fetch and not manifest.is_file() and not named and not path.exists():
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
        if manifest == 'Cargo.toml':
            # cargo add --dev puts a crate in [dev-dependencies]. A tool of the rust profile may have no crate.
            table = tomllib.loads(path.read_text()).get('dev-dependencies', {})
            return set(table) if isinstance(table, dict) else None
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
                if not entry['packages']:
                    # A tool with no packages, as each tool of the rust profile, needs no read of the project file.
                    steps.append(('add', name, tool, None, entry.get('files', {}), entry,
                                  (owner_key(name, profile, tool), []), None))
                    continue
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
            if not profile['tools'][tool]['packages']:
                # A tool with no packages, as each tool of the rust profile, removes nothing from the project file.
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
            failed = subprocess.run(init['command'], cwd=target, env=project_env()).returncode
        except OSError as exc:
            failed = str(exc)
        if failed or not (target / init['file']).exists():
            raise StackError(f'{" ".join(init["command"])} did not make {init["file"]}. The roles are installed; '
                             f'make {init["file"]} and run just install again.')


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


def write_justfile(target, dry_run, started=False):
    """Write the starter Justfile when the target has none. A Justfile that jnterfaces did not start gets the import
    of the recipe file as a block, from sync_files; for a started one, check that it imports the recipe file."""
    existing = justfile_of(target)
    if existing is not None:
        if existing.is_symlink():
            print(f'{existing.name} is a symlink, so the installer leaves it alone. Make sure that it has the line '
                  f'{IMPORT_LINE}', file=sys.stderr)
        elif started:
            text = existing.read_text(errors='replace')
            if not imports_install(text):
                print(f'Add this line to {existing.name}, so that just install works: {IMPORT_LINE}', file=sys.stderr)
            if not any(JLOG_RUN.match(row) for row in text.splitlines()):
                print(f'Add this line to {existing.name}, so that the just calls of one just check share one run in '
                      f'the run log: {JLOG_LINE}', file=sys.stderr)
        return False
    template = ROOT / 'templates/project.just'
    if dry_run:
        print('Would write Justfile from templates/project.just')
        return False
    write_new(target, Path('Justfile'), template.read_bytes().replace(b'PROJECT', target.name.encode()))
    print('Wrote Justfile; it is yours now. Put the recipes of the project in it.')
    return True


# An import of the recipe file in a Justfile: import or import?, in single or double quotes, with or without ./ at
# the start, and a comment after it or not.
INSTALL_IMPORT = re.compile(r'''^import\??[ \t]+(['"])(\./)?\.agents/install\.just\1[ \t]*(#.*)?$''')


def user_rows(text):
    """The lines of a Justfile outside the block of the installer."""
    _, user, _ = shared_files.parse(text, {'jnterfaces'}, shared_files.HASH)
    return [shared_files.bare(row) for row in user]


def imports_install(text, own=True):
    """Whether a Justfile imports .agents/install.just. With own False, a line in the block of the installer does not
    count."""
    rows = text.splitlines() if own else user_rows(text)
    return any(INSTALL_IMPORT.match(row.rstrip('\r')) for row in rows)


def has_block(text):
    """Whether a Justfile has the block of the installer with the import of the recipe file or the run ID."""
    blocks, _, _ = shared_files.parse(text, {'jnterfaces'}, shared_files.HASH)
    return any(shared_files.bare(row).strip() in (IMPORT_LINE, JLOG_LINE) for row in blocks.get('jnterfaces', []))


def justfile_text(justfile):
    """The text of a plain Justfile without a byte-order mark, or None for a symlink or a file that cannot be read."""
    if justfile is None or justfile.is_symlink() or not justfile.is_file():
        return None
    try:
        return justfile.read_bytes().decode('utf-8', errors='surrogateescape').removeprefix('\ufeff')
    except OSError:
        return None


def justfile_lines(target, started, clash=None, parts=None):
    """The lines that the installer adds to the Justfile, as {name of the Justfile: [line]}, for a Justfile that
    jnterfaces did not start: the import of the recipe file, when the Justfile has no import of it of its own, and
    the line of JLOG_RUN, when the Justfile does not set JLOG_RUN. The index imports the recipes of the profiles and
    the roles, so the one import is enough. clash is the record of a run that took the
    line out again, because the Justfile did not parse with it: while the Justfile stays as it was then, the line
    stays out. parts is the record of the parts: a line that the installer added and that a person moved out of the
    block stays the installer's line, so the installer lists it while it is there, and never takes it out."""
    justfile = justfile_of(target)
    text = justfile_text(justfile)
    if text is None:
        return {}
    claims = (move_justfile_parts(target, parts) or {}).get(justfile.name) or {}
    rows = user_rows(text)
    moved = [line for line in (IMPORT_LINE, JLOG_LINE) if ((claims.get(line) or {}).get('state') == 'added'
                                                            and any(shared_files.normal(row) == line for row in rows))]
    # just reads a #! line only at the very start, and a block comes before every line.
    if (started or text.startswith('#!') or (isinstance(clash, dict) and clash.get('hash')
                                             == hashlib.sha256(justfile.read_bytes()).hexdigest())):
        return {justfile.name: moved} if moved else {}
    lines = [IMPORT_LINE] if IMPORT_LINE in moved or not imports_install(text, own=False) else []
    # The line of the run ID goes into the block too, unless the Justfile sets JLOG_RUN itself, also in a file that it
    # imports.
    if JLOG_LINE in moved or not sets_jlog_run(target, text, rows):
        lines.append(JLOG_LINE)
    return {justfile.name: lines} if lines else {}


def sets_jlog_run(target, text, rows):
    """Whether a Justfile sets JLOG_RUN itself, outside the block of the installer: in its own lines or in a file that
    it imports. A JLOG_RUN that is not exported does not reach the inner just calls, so the installer says so."""
    dump = text_dump(target, without_block(text)) or {}
    assignment = (dump.get('assignments') or {}).get('JLOG_RUN')
    if assignment and not assignment.get('export') and not (dump.get('settings') or {}).get('export'):
        say(f'The Justfile sets JLOG_RUN but does not export it, so each inner just call makes a run ID of its own. '
            f'Use this line: {JLOG_LINE}')
    return assignment is not None or any(JLOG_RUN.match(row) for row in rows)


def move_justfile_parts(target, parts):
    """The parts with the record of the Justfile under the name that the Justfile has now. A Justfile that you renamed,
    also only in its letter case, keeps the lines of the installer: the old name is no other file now."""
    justfile = justfile_of(target)
    if not isinstance(parts, dict) or justfile is None or justfile.name in parts:
        return parts
    for name in JUSTFILES:
        old = target / name
        if name == justfile.name or name not in parts:
            continue
        try:
            same = old.exists() and os.path.samefile(old, justfile)
        except OSError:
            same = False
        if same or not old.exists():
            moved = {key: value for key, value in parts.items() if key != name}
            # A name that is gone was renamed only when the new file still has the block or the line. Else the old
            # file was deleted, and its record goes with it: a new file is not one where you deleted the line.
            text = justfile_text(justfile) or ''
            if same or has_block(text) or any(shared_files.normal(row) == IMPORT_LINE for row in user_rows(text)):
                moved[justfile.name] = parts[name]
            return moved
    return parts


def drop_justfile_parts(parts):
    """Take the record of each Justfile name out of the parts, in place, for a new starter Justfile: the lines of the
    old file went with it."""
    for name in JUSTFILES:
        if isinstance(parts, dict):
            parts.pop(name, None)


def text_dump(target, text):
    """The JSON dump of a Justfile with this text, or None, read as text_error reads it."""
    if not shutil.which('just'):
        return None
    probe = target / f'.jnterfaces-probe-{secrets.token_hex(4)}.just'
    try:
        probe.write_bytes(text.encode('utf-8', errors='surrogateescape'))
        return just_dump(target, probe)
    except OSError:
        return None
    finally:
        probe.unlink(missing_ok=True)


def text_error(target, text):
    """The first error line of just for a Justfile with this text, or None when it parses or just is missing. just
    reads a temporary file next to the Justfile, so the imports of the text find the same files."""
    if not shutil.which('just'):
        return None
    probe = target / f'.jnterfaces-probe-{secrets.token_hex(4)}.just'
    try:
        probe.write_bytes(text.encode('utf-8', errors='surrogateescape'))
        return just_error(target, probe)
    except OSError:
        return None
    finally:
        probe.unlink(missing_ok=True)


def without_block(text):
    """The text of a Justfile without the block of the installer."""
    _, user, _ = shared_files.parse(text, {'jnterfaces'}, shared_files.HASH)
    return ''.join(user)


def import_verdict(target, justfile, text):
    """Whether the Justfile can take the import: ('broken', error) when it does not parse as it is, ('clash', error)
    when it parses without the import and not with it, else (None, None). The index stays as it is now, so a clash
    may still go away when the real run writes the index with less."""
    error = just_error(target, justfile)
    if error:
        return 'broken', error
    # Before the first install, the project has no .agents/install.just, so the copy imports the one of jnterfaces.
    line = IMPORT_LINE
    if not (target / '.agents/install.just').is_file():
        line = 'import? ' + json.dumps(str(ROOT / '.agents/install.just'))
    error = text_error(target, line + '\n' + text)
    return ('clash', error) if error else (None, None)


def raw_record(target):
    """The record in .agents/stack.json as it is, or {} when it is missing, a symlink or not JSON."""
    path = target / RECORD
    try:
        return json.loads(path.read_text()) if path.is_file() and not path.is_symlink() else {}
    except (OSError, ValueError):
        return {}


def generated_notes(target, record):
    """The notes on the files that just install wrote in .agents/just, which the project commits: one for each file
    that is gone, and one for each file that git does not track. Return the notes and True when a file is gone."""
    generated = record.get('generated') if isinstance(record, dict) else None
    if not isinstance(generated, dict) or not generated:
        return [], False
    # A key that an edit of the record points outside the project is not a file of the project.
    inside = [path for path in generated if isinstance(path, str) and not Path(path).is_absolute()
              and (target / path).resolve().is_relative_to(target.resolve())]
    gone = sorted(path for path in inside if not (target / path).is_file())
    notes = [f'{path} is gone, so just does not have the recipes of the profiles and roles, and it gives no error. '
             f'Run just install to write it again, then commit it.' for path in gone]
    here = sorted(path for path in inside if path not in gone)
    if here:
        run = subprocess.run(['git', '-C', str(target), 'ls-files', '-z', '--', *here], capture_output=True)
        if run.returncode == 0:
            tracked = set(run.stdout.decode(errors='surrogateescape').split('\0'))
            loose = [path for path in here if path not in tracked]
            if loose:
                notes.append(f'git does not track {", ".join(loose)}. Commit {"them" if len(loose) > 1 else "it"}: '
                             f'a clone without {"them" if len(loose) > 1 else "it"} does not have the recipes of the '
                             f'profiles and roles, and just gives no error.')
    return notes, bool(gone)


def jlog_note(target):
    """For just install --check: the note when a Justfile that imports the recipe file does not set JLOG_RUN, or
    None. The installer does not add the line to a started Justfile or before a #! line."""
    justfile = justfile_of(target)
    text = justfile_text(justfile)
    if text is None or not imports_install(text):
        return None
    if any(JLOG_RUN.match(row) for row in text.splitlines()) or sets_jlog_run(target, text, user_rows(text)):
        return None
    return (f'{justfile.name} does not set JLOG_RUN, so the inner just calls of one just check get run IDs of their '
            f'own. Add this line to it: {JLOG_LINE}')


def justfile_note(target, record):
    """For just install --check: the note when the Justfile lacks the import of the recipe file and the record has no
    line of it, or None."""
    record = record if isinstance(record, dict) else {}
    justfile = justfile_of(target)
    text = justfile_text(justfile)
    if text is None or imports_install(text):
        return None
    if record.get('started') is True:
        return f'{justfile.name} does not import .agents/install.just. Add this line to it: {IMPORT_LINE}'
    parts = move_justfile_parts(target, record.get('parts') if isinstance(record.get('parts'), dict) else {})
    if isinstance(parts.get(justfile.name), dict) and IMPORT_LINE in parts[justfile.name]:
        return None
    clash = record.get('justfile_clash')
    if (isinstance(clash, dict) and isinstance(clash.get('error'), str)
            and clash.get('hash') == hashlib.sha256(justfile.read_bytes()).hexdigest()):
        return (f'{justfile.name} does not import .agents/install.just. A run of just install took the line out, '
                f'because {justfile.name} did not parse with it. just said: {clash["error"]}')
    if text.startswith('#!'):
        return (f'{justfile.name} does not import .agents/install.just, and the installer does not add the line '
                f'before its #! line. Add this line to it: {IMPORT_LINE}')
    kind, error = import_verdict(target, justfile, text)
    if kind == 'broken':
        return (f'{justfile.name} does not import .agents/install.just, and it does not parse, so the next just '
                f'install adds no import. Fix it first. just said: {error}')
    if kind == 'clash':
        return (f'{justfile.name} does not import .agents/install.just. The next just install adds the line '
                f'{IMPORT_LINE} only if {justfile.name} still parses with it, and now it does not. just said: {error}')
    return f'{justfile.name} does not import .agents/install.just. The next just install adds the line {IMPORT_LINE}'


CLAUDE_IMPORT = '@AGENTS.md'
# An import of AGENTS.md in a CLAUDE.md, outside a code block: @AGENTS.md or @./AGENTS.md. An import resolves from the
# folder of its file, so .claude/CLAUDE.md imports AGENTS.md as @../AGENTS.md.
CLAUDE_IMPORTS = re.compile(r'(^|\s)@(\./)?AGENTS\.md([\s.,;:)]|$)')
INNER_IMPORTS = re.compile(r'(^|\s)@\.\./AGENTS\.md([\s.,;:)]|$)')


def imports_agents(path, imports=CLAUDE_IMPORTS):
    """Whether a CLAUDE.md imports AGENTS.md. A line in a code block, fenced or indented, is not an import."""
    fenced = False
    for line in path.read_text(errors='replace').splitlines():
        if line.lstrip().startswith(('```', '~~~')):
            fenced = not fenced
        elif not fenced and not line.startswith(('\t', '    ')) and imports.search(line):
            return True
    return False


def write_claude_md(target, dry_run, after):
    """Write a CLAUDE.md that imports AGENTS.md, when the target has AGENTS.md and no CLAUDE.md. Claude Code reads
    CLAUDE.md and not AGENTS.md, so the import gives it the instructions of the roles, from one copy. A CLAUDE.md that
    is there is the user's: the installer only says when it does not import AGENTS.md. after holds the shared files
    that the run writes or removes, as shared_files.sync says, so a dry run knows whether AGENTS.md stays. The file is
    not needed for the install, so a problem with it is a warning and never stops the run."""
    claude, inner = target / 'CLAUDE.md', target / '.claude/CLAUDE.md'
    agents = after.get('AGENTS.md', (target / 'AGENTS.md').is_file())
    try:
        if claude.is_symlink():
            return False
        mine = claude.is_file() and claude.read_text(errors='replace').strip() == CLAUDE_IMPORT
        if not agents:
            # Only the run that removes AGENTS.md says so, once.
            if mine and after.get('AGENTS.md') is False:
                print(f'CLAUDE.md imports AGENTS.md, and the project {"would have" if dry_run else "has"} no AGENTS.md '
                      f'after this run. Delete CLAUDE.md if you do not need it.', file=sys.stderr)
            return False
        # Claude Code also reads .claude/CLAUDE.md, so an import there is enough.
        if any(path.is_file() and not path.is_symlink() and imports_agents(path, imports)
               for path, imports in ((claude, CLAUDE_IMPORTS), (inner, INNER_IMPORTS))):
            return False
        if claude.exists():
            print(f'CLAUDE.md does not import AGENTS.md, so Claude Code does not read the instructions of the roles. '
                  f'Add this line to CLAUDE.md: {CLAUDE_IMPORT}', file=sys.stderr)
            return False
        if dry_run:
            print(f'Would write CLAUDE.md, which imports AGENTS.md with the line {CLAUDE_IMPORT}')
            return False
        write_new(target, Path('CLAUDE.md'), (CLAUDE_IMPORT + '\n').encode())
    except (OSError, StackError) as exc:
        print(f'Cannot read or write CLAUDE.md: {exc}. Claude Code needs the line {CLAUDE_IMPORT} in it.',
              file=sys.stderr)
        return False
    print('Wrote CLAUDE.md, which imports AGENTS.md for Claude Code; it is yours now.')
    return True


RECIPES = Path('.agents/just')
HEADER = '# Written by just install. Do not edit: the next just install writes it again.'



def module_text(name, profile, installed):
    """The module of one profile: its settings, an import of the recipe file of each installed tool, and verify."""
    lines = [HEADER, f'# The recipes of the {name} profile: just {name} NAME. They run in the folder of the project.',
             'set positional-arguments', "set working-directory := '../..'",
             '# A module has settings of its own, so it sets jlog as its shell too', SHELL_LINE, '']
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


def recipe_line(name, recipe, takes):
    """The line of a combined recipe that runs the recipe of one module."""
    return f'    just --justfile {{{{quote(justfile())}}}} {name} {recipe}' + (' "$@"' if takes else '')


def index_text(modules, recipes, roles=(), full=True, taken=(), shell=True, runnable=None):
    """The index. Its form with full False has the modules only: the installer reads the names of the project with
    it. The whole index also has the setting of the shell, the recipe file of each role, and for each recipe name of
    the profiles a combined recipe that runs it in each profile that has it. A combined recipe of a step ends with
    jlog step. Each step that nothing defines is a no-op step that logs a skip, and check runs the steps and ends
    with jlog pass. The index leaves out each name in taken: the names of the project and of the roles. runnable
    holds the names in taken that are recipes. check depends only on a recipe, because just cannot depend on an
    alias or a module."""
    lines = [HEADER, '# The recipes of the profiles and roles of this project. .agents/install.just imports this file.',
             '']
    if full and shell:
        lines += ['# Each line of a recipe runs through jlog, which adds it to the run log in .agents/log', SHELL_LINE,
                  '']
    lines += [f"mod {name} '{name}.just'" for name in modules]
    if not full:
        return '\n'.join(lines) + '\n'
    lines += [f"import? '{role}'" for role in roles]
    taken = set(taken)
    names = sorted({recipe for name in modules for recipe in recipes.get(name, {})} - {'verify'} - taken)
    for recipe in names:
        having = [name for name in modules if recipe in recipes.get(name, {})]
        takes = [name for name in having if recipes[name][recipe]]
        lines += ['', f'# just {recipe} for each language that has it: {", ".join(having)}']
        lines += ['[positional-arguments]', f'{recipe} *args:'] if takes else [f'{recipe}:']
        lines += [recipe_line(name, recipe, name in takes) for name in having]
        lines += [f'    jlog step {recipe}'] if recipe in STEPS else []
    if modules and 'verify' not in taken:
        lines += ['', '# Every installed check of each language, then the check of git for whitespace errors',
                  'verify:'] + [f'    just --justfile {{{{quote(justfile())}}}} {name} verify' for name in modules]
        lines += ['    git diff --check']
    real = set(names)
    if modules and 'diff-check' not in real | taken:
        # git diff HEAD also checks the changes that are not staged. A repository with no commit compares with the
        # empty tree.
        lines += ['', '# Find whitespace errors in the changes against the last commit', 'diff-check:',
                  '    git diff --check "$(git rev-parse -q --verify HEAD || git hash-object -t tree /dev/null)"',
                  '    jlog step diff-check']
        real.add('diff-check')
    for step in STEPS:
        if step not in real | taken:
            lines += ['', f'# No profile or role of this repository has {step}, so the step logs a skip', f'{step}:',
                      f'    jlog skip {step} {json.dumps(REASONS[step])}']
            real.add(step)
    runnable = taken if runnable is None else set(runnable)
    if 'check' not in taken:
        steps = [step for step in STEPS if step in real or step in runnable]
        lines += ['', '# Everything a commit must pass: each step, then the pass of jlog for the worktree',
                  f'check: {" ".join(steps)}', '    jlog pass']
    return '\n'.join(lines) + '\n'


# The line of a stub of the index, in the form with which the installer reads the names of the project.
STUB = 'jnterfaces-stub-of-the-index'


def probe_text(modules, stubs=(), setting=False):
    """The index in the form with which the installer reads the names of the project: the modules, and a stub of each
    name in stubs. With setting, the form also sets allow-duplicate-recipes, so that a recipe of the Justfile takes
    the place of a stub of the same name."""
    lines = [HEADER, '# The installer reads the names of the project with this form. The next step replaces it.', '']
    lines += ['set allow-duplicate-recipes'] if setting and stubs else []
    lines += [f"mod {name} '{name}.just'" for name in modules]
    for name in stubs:
        lines += ['', f'{name} *args:', f'    {STUB}']
    return '\n'.join(lines) + '\n'


def is_stub(recipe):
    """True for a recipe of the dump that is a stub of probe_text."""
    return (recipe.get('body') or []) == [[f'{STUB}']]


def needs_arguments(recipe):
    """True for a recipe of a dump with a parameter that has no default, other than a parameter of the form *args."""
    return any(param.get('default') is None and param.get('kind') != 'star' for param in recipe.get('parameters') or [])


def names_of(dump):
    """The names that a Justfile defines at its top: its recipes, its aliases and its modules."""
    dump = dump or {}
    return set(dump.get('recipes') or {}) | set(dump.get('aliases') or {}) | set(dump.get('modules') or {})


def provided_of(modules, recipes, role_recipes):
    """The steps that an installed profile or role provides, in the order of the steps. With a profile, the index
    also provides diff-check."""
    having = {recipe for name in modules for recipe in recipes.get(name, {})}
    having |= {recipe for dump in role_recipes.values() for recipe in (dump or {}).get('recipes') or {}}
    return [step for step in STEPS if step in having or (step == 'diff-check' and modules)]


def lint_project(dump, modules=()):
    """The findings of the lint of steps for the dump of a root Justfile that imports the index: each step and check
    at the top, which must also end with their lines of jlog, and the steps of each module of a profile in modules.
    check never runs another module of the project, so the lint leaves it alone."""
    findings = lint_steps.lint(dump, STEPS + ('check',), ending=STEPS + ('check',))
    for name, module in sorted(((dump or {}).get('modules') or {}).items()):
        if name in modules:
            findings += [f'{name}::{finding}' for finding in lint_steps.lint(module, STEPS)]
    return findings


def step_findings(target, record):
    """For just install --check: the findings of the lint of steps, when the Justfile imports the recipe file."""
    record = record if isinstance(record, dict) else {}
    justfile = justfile_of(target)
    text = justfile_text(justfile)
    if text is None or not (record.get('started') is True or imports_install(text)):
        return []
    profiles = [name for name in record.get('profiles') or [] if isinstance(name, str)]
    dump = just_dump(target, justfile)
    if dump is None:
        error = just_error(target, justfile)
        if not error:
            return []
        return [f'{justfile.name} does not parse, so just check and just install cannot run. Run the installer itself, '
                f'as the section "When a later edit breaks the Justfile" of docs/stack-guide.md in jnterfaces says. '
                f'just said: {error}']
    shell = shell_of(dump)
    found = []
    if shell and Path(shell[0] or '').name != 'jlog':
        found.append(SHELL_NOTE.format(name=justfile.name, line=json.dumps(['jlog'] + shell)))
    return found + lint_project(dump, profiles)


def shell_of(dump):
    """The shell that a dump sets, as a list, or None."""
    shell = ((dump or {}).get('settings') or {}).get('shell')
    return [shell.get('command')] + list(shell.get('arguments') or []) if isinstance(shell, dict) else None


def write_recipes(target, profiles, active, tools, dry_run, generated, started, known=(), roles=(), adding=False,
                  out=None):
    """Write .agents/just: a module for each active profile with the recipes of its installed tools, and the index.
    A file that the user changed stays as it is. The index comes in the order of the spec of the commit gates: the
    installer keeps the old index, writes the index with the modules only, reads the names of the project from a
    dump of the Justfile, then writes the whole index without those names. When it stops before the end, it puts the
    old index back. adding is True while the run is still to add the import of the recipe file to the Justfile, so
    that the run does not tell the user to add it. out gets the steps and the provided steps for .agents/stack.json.
    Return the error of just when the Justfile does not parse."""
    out = {} if out is None else out
    out['steps'] = list(STEPS)
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
    text = justfile_text(justfile) if justfile is not None else None
    # A Justfile that jnterfaces did not start reads the index when it imports the recipe file: through the block of
    # the installer, or through a line of its own.
    imported = started or (text is not None and imports_install(text))
    present = [role for role in roles if (folder / role).is_file()]
    roles = present if imported else []
    candidates = {index_text(kept, recipes, full=False), index_text({}, recipes, full=False)}
    same = index.is_file() and not index.is_symlink() and index.read_text(errors='replace') in candidates

    def keep():
        out.pop('provided_steps', None)
        say(f'Kept {index.relative_to(target)}: just install did not write it as it is now, so it leaves it alone.')
        error = just_error(target, justfile) if justfile is not None else None
        if error:
            say(f'The Justfile does not parse with the index that you kept. just said: {error}')
        return error

    if index.is_symlink() or (index.exists() and not index.is_file()):
        return keep()
    # An index with no recorded hash can still be the whole index of a run that stopped before its record. The
    # installer knows that only after it wrote the index again, so it compares at the end and puts the file back.
    mine = same or owned_by_install(target, index, generated)
    if (kept or present) and not imported and text is not None and not adding:
        say(f'{justfile.name} does not import .agents/install.just, so the recipes of the profiles and roles are not '
            f'in it. To add them, put this line in it: {IMPORT_LINE}')
    folder.mkdir(parents=True, exist_ok=True)
    key = index.relative_to(target).as_posix()
    old, old_hash = (index.read_bytes() if index.is_file() else None), generated.get(key)

    def put_back():
        if old is None:
            remove_generated(target, index, generated)
            return
        index.write_bytes(old)
        if old_hash is None:
            generated.pop(key, None)
        else:
            generated[key] = old_hash

    def restore():
        # Step 5: put the old index back, so that just check keeps the steps that it had, and the record keeps its
        # old provided steps. An old index that no longer parses, for example because it names a module that is
        # gone, gives way to an index with no recipe, so that just install still runs.
        out.pop('provided_steps', None)
        put_back()
        if mine and old is not None and justfile is not None and just_error(target, justfile):
            write_generated(target, index, index_text({}, recipes, full=False), generated)
            if just_error(target, justfile):
                put_back()
            else:
                out['provided_steps'] = []
                say('The old index did not parse either, so the index has no recipe now. just check comes back with '
                    'the next just install that works.')

    # Step 3 of the order: each role file alone, as each module. A role file must parse alone, or the installer
    # cannot know its names.
    role_recipes = {}
    for role in roles:
        role_recipes[role] = just_dump(target, folder / role)
        if role_recipes[role] is None:
            raise StackError(f'The recipe file of a role, .agents/just/{role}, does not parse alone, so the installer '
                             f'cannot read its names. just said: {just_error(target, folder / role)}. The index stays '
                             f'as it was.')
    role_names = {name for dump in role_recipes.values() for name in names_of(dump)}
    for name in sorted(role_names & {recipe for module in kept for recipe in recipes.get(module, {})} & set(STEPS)):
        raise StackError(f'The recipe {name} is in a recipe file of a role and in a profile, so the role would hide '
                         f'the step of the profile. Take the role or the tool out. The index stays as it was.')
    # Steps 1 and 2: the index with the modules only, then the names of the project from a dump of the Justfile. The
    # Justfile can name a recipe of the index, for example ci: check, so the first forms also hold a stub of each
    # name that the index can write. A recipe of the Justfile takes the place of a stub, so the stubs that stay show
    # which names are not the Justfile's own.
    stubs = sorted(set(STEPS) | {'check', 'verify'} | {recipe for module in kept for recipe in recipes.get(module, {})})
    forms = [(kept, True, True), (kept, True, False), (kept, False, False), ({}, True, True), ({}, True, False),
             ({}, False, False)]
    mods, root, first, of_setting = kept, ({} if justfile is None else None), None, None
    for mods, with_stubs, setting in forms if justfile is not None else []:
        write_generated(target, index, probe_text(mods, stubs if with_stubs else (), setting), generated)
        root = just_dump(target, justfile)
        if root is not None:
            break
        # A Justfile that sets allow-duplicate-recipes itself fails each form with the setting, so the error of a form
        # without it names the real problem.
        if setting:
            of_setting = of_setting or just_error(target, justfile)
        else:
            first = first or just_error(target, justfile)
    first = first or of_setting
    if root is None:
        restore()
        if not mine:
            return keep()
        if text is None or not has_block(text):
            say(f'The Justfile does not parse, also with an index with no module, so the installer put the old index '
                f'back. just said: {first}')
        return first
    if kept and not mods:
        say(f'The Justfile does not parse with the modules of the profiles, so the index has no module. Rename the '
            f'recipe, alias or module of the project with that name. just said: {first}')
    own = {name: recipe for name, recipe in (root.get('recipes') or {}).items() if not is_stub(recipe)}
    project = (set(own) | set(root.get('aliases') or {}) | set(root.get('modules') or {})) - set(mods)
    for role, dump in role_recipes.items():
        for name in sorted(names_of(dump) & project):
            # A Justfile that imports the role file itself has the recipe of the role, and that is no clash.
            if (dump.get('recipes') or {}).get(name) != own.get(name):
                restore()
                if not mine:
                    return keep()
                raise StackError(f'The recipe {name} is in {justfile.name} and in the recipe file of a role, '
                                 f'.agents/just/{role}, so just would stop with "Recipe redefined". Rename the recipe '
                                 f'of {justfile.name}, or take the role out with just uninstall. The index stays as '
                                 f'it was.')
    shell = shell_of(root)
    if shell and imported and Path(shell[0] or '').name != 'jlog':
        say(SHELL_NOTE.format(name=justfile.name, line=json.dumps(['jlog'] + shell)))
    # Step 4: the whole index, without the names of the project and of the roles. check can depend only on a recipe
    # that needs no argument.
    taken = project | role_names
    runnable = {name for name, recipe in own.items() if not needs_arguments(recipe)} | {
        name for dump in role_recipes.values() for name, recipe in ((dump or {}).get('recipes') or {}).items()
        if not needs_arguments(recipe)}
    for step in STEPS:
        if step in taken and step not in runnable:
            say(f'{step} in the project is an alias, a module, or a recipe that needs an argument, so check cannot run '
                f'the step {step}, and jlog pass fails. Rename it, or give each argument a default.')
    out['provided_steps'] = provided_of(mods, recipes, role_recipes)
    whole = index_text(mods, recipes, roles, True, taken, shell=shell is None, runnable=runnable)
    if not mine and whole.encode() != old:
        restore()
        return keep()
    write_generated(target, index, whole, generated)
    error = just_error(target, justfile) if justfile is not None else None
    if error:
        restore()
        say(f'The Justfile does not parse with the index, so the installer put the old index back. just said: {error}')
        return error
    if imported and justfile is not None:
        for finding in lint_project(just_dump(target, justfile), mods):
            say(f'The lint of steps: {finding}. Run just install --check after the fix.')
    return None


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
                        help='compare the record in .agents/stack.json with the shared files, and change nothing')
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
    if args.check:
        raw = raw_record(target)
        missing = check_record_programs(target, raw, args.registry, overrides)
        note = justfile_note(target, raw)
        notes, gone = generated_notes(target, raw)
        notes = [text for text in (note, jlog_note(target)) if text] + notes
        code = shared_files.check(target, read_parts(target), notes)
        findings = step_findings(target, raw)
        for finding in findings:
            print(f'The lint of steps: {finding}')
        return 1 if gone or findings or missing else code
    # jlog is the shell of each recipe in .agents/just, so a recipe fails without it.
    if not shutil.which('jlog'):
        raise StackError(f'jlog is not on PATH. Each recipe that just install writes runs through jlog, so install it '
                         f'first: {JLOG_INSTALL}. Nothing was written.')
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
    # The programs of the profiles and tools that stay on. A program without an install must be there before the run
    # writes anything. A program with an install is installed at the end, after the files of the profiles.
    programs = needed_programs(profiles, [name for name in active if name not in leaving], tools)
    works = {}
    missing = missing_expected(target, programs, works)
    if missing and not args.dry_run:
        raise StackError('A program that the profiles need is missing. Nothing was written. Install it, then run just '
                         'install again:\n  ' + '\n  '.join(missing))
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
    # The programs of the roles that are on. Their clones are here only now, and still nothing is written.
    extra = role_programs([p for p, _ in steps], clones, [entry['name'] for entry in programs])
    extra_missing = missing_expected(target, extra, works, earlier=programs)
    if extra_missing and not args.dry_run:
        raise StackError('A program that the roles need is missing. Nothing was written. Install it, then run just '
                         'install again:\n  ' + '\n  '.join(extra_missing))
    programs, missing = programs + extra, missing + extra_missing
    # The step of each role is checked when the role installs too, so that a bad step cannot block its removal later.
    befores = {p['name']: role_before_remove(p, clones[p['name']]) for p in [s[0] for s in steps] + removals
               if 'choice' in p}
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
    # The move from the old log is worked out here, and saved only after the dry pass below found no problem.
    parts, unsure, migrating = move_from_log(target, record, packages, clones, True)
    roles = role_owners([p for p, _ in steps], clones)
    gone_roles = role_owners(removals, clones)
    dropped = dropped_files(packages, chosen, clones)
    # The installer starts a project when it writes the starter Justfile: before, or in this run.
    starting = bool((record or {}).get('started')) or justfile_of(target) is None
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

    def lines_now():
        # The lines of the installer in the Justfile, from the Justfile as it is now.
        return justfile_lines(target, bool((record or {}).get('started')) or state.get('started', False),
                              state.get('clash', (record or {}).get('raw', {}).get('justfile_clash')),
                              state.get('parts', parts))
    state = {}
    for name in active:
        init = profiles[name].get('init')
        if init and any(step[1] == name for step in commands) and (target / init['file']).is_symlink():
            raise StackError(f'{target / init["file"]} is a symlink, so the installer leaves it alone. Make it a file '
                             f'and run just install again. Nothing was written.')
    if args.dry_run:
        for problem in missing:
            print(f'Missing program: {problem}')
        for entry in programs:
            if 'install' in entry and not (works[entry['name']] if entry['name'] in works else program_works(target, entry)):
                print(f'Would install {entry["name"]} for {owner_of(entry)}: {entry["install"]}')
        for package in removals:
            if befores.get(package['name']):
                run_before_remove(target, package, befores[package['name']], True)
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
            print('Would run: ' + ' '.join(command) if command else f'The project already has the packages of {tool}'
                  if entry.get('packages') else f'{tool} needs no packages')
        after = {}
        dry_lines = lines_now()
        justfile = justfile_of(target)
        text = justfile_text(justfile)
        if dry_lines and text is not None and not has_block(text) and not imports_install(text):
            kind, error = import_verdict(target, justfile, text)
            if kind == 'broken':
                dry_lines = {}
                print(f'{justfile.name} does not parse, so the real run would add no import to it. just said: {error}')
            elif kind == 'clash':
                dry_lines = {}
                print(f'{justfile.name} does not parse with the line {IMPORT_LINE}, so the real run would add it only '
                      f'if {justfile.name} still parses with it. just said: {error}')
        sync_files(target, profiles, [n for n in active if n not in leaving], tools, active, True, parts, roles,
                   gone_roles, unsure, migrating, after=after, justfile=dry_lines, dropped=dropped, started=starting)
        write_justfile(target, True, bool((record or {}).get('started')))
        write_claude_md(target, True, after)
        write_recipes(target, profiles, [n for n in active if n not in leaving], tools, True, {}, False)
        if missing:
            print('Dry run: the real run would stop before it writes anything, because a program is missing.')
            return 1
        if any(step[7] for step in commands):
            print('Dry run: the real run would fail at the steps above.')
            return 1
        print('Dry run: ' + ', '.join(p['name'] for p, _ in steps) + f' would be installed in {target}'
              + (', and ' + ', '.join(p['name'] for p in removals) + ' removed' if removals else ''))
        return 0
    # A dry pass over the shared files first: a conflict of two owners or an own file that git ignores stops the run
    # here, before a package command changes the project.
    sync_files(target, profiles, [n for n in active if n not in leaving], tools, active, True, parts, roles,
               gone_roles, unsure, migrating, quiet=True, justfile=lines_now(), dropped=dropped, started=starting)
    parts, unsure, migrating = move_from_log(target, record, packages, clones, False)
    for package, options in steps:
        if run_installer(clones[package['name']], options, target, heads.get(package['name']), False):
            raise StackError(f'The install of {package["name"]} failed. The packages before it are installed.')
    for package in removals:
        if befores.get(package['name']):
            run_before_remove(target, package, befores[package['name']], False)
        options = ['--record', str(record_path(package)), '--remove']
        if run_installer(clones[package['name']], options, target, None, False):
            raise StackError(f'The removal of {package["name"]} failed.')
    record_file = target / RECORD

    # The files that just install writes in .agents/just, with their hashes, and whether it wrote the Justfile. A
    # file is the installer's to change only while its hash is the recorded one.
    generated = dict((record or {}).get('generated', {}))
    state.update(started=bool((record or {}).get('started')), parts=parts,
                 clash=(record or {}).get('raw', {}).get('justfile_clash'))
    state['adding'] = bool(lines_now())
    # The keys that save writes. It keeps each other key of the record, so that it never drops one.
    managed = {'version', 'profiles', 'choices', 'tools', 'variants', 'owned_packages', 'added_packages',
               'pending_tools', 'removing_tools', 'removing_profiles', 'generated', 'started', 'parts',
               'parts_version', 'code', 'justfile_clash', 'steps', 'provided_steps'}

    def save(tool_list, waiting, going=()):
        record_file.parent.mkdir(exist_ok=True)
        try:
            old = json.loads(record_file.read_text()) if record_file.is_file() else {}
        except (OSError, ValueError):
            old = {}
        old = old if isinstance(old, dict) else {}
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
        # The globs of the source files of each profile that stays, for @code of the gate.
        code = {name: profiles[name]['code'] for name in kept_profiles
                if name not in leaving and name in profiles and profiles[name].get('code')}
        if code:
            record['code'] = code
        step_record = {}
        write_recipes(target, profiles, [name for name in kept_profiles if name not in leaving], tool_list, False,
                      generated, state['started'], registry_profiles,
                      [f'roles/{package["name"]}.just' for package, _ in steps], state['adding'], step_record)
        # The steps of just check, and the steps that a profile or a role provides, for jlog pass.
        for key in ('steps', 'provided_steps'):
            if key in step_record or key in old:
                record[key] = step_record.get(key, old.get(key))
        if generated:
            record['generated'] = dict(sorted(generated.items()))
        if state['started']:
            record['started'] = True
        if isinstance(state['clash'], dict):
            record['justfile_clash'] = state['clash']
        record['parts'], record['parts_version'] = state['parts'], 1
        for key, value in old.items():
            if key not in managed:
                record[key] = value
        write_record(target, record)

    # Save the roles before the tools run, so that a failed tool keeps the roles that were installed.
    save(saved_tools, [t for t in tools_add if t not in saved_tools], tools_remove)
    if write_justfile(target, False, state['started']):
        state['started'] = True
        state['adding'] = False
        drop_justfile_parts(parts)
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
                    code = subprocess.run(first, cwd=target, env=project_env()).returncode
                    if code:
                        failed = f'{" ".join(first)} exited with {code}'
                        break
                if failed:
                    pass
                elif command:
                    print(f'{"Adding" if kind == "add" else "Removing"} {tool} for {profile}: {" ".join(command)}',
                          file=sys.stderr)
                    failed = subprocess.run(command, cwd=target, env=project_env()).returncode
                else:
                    print(f'The project already has the packages of {tool} for {profile}' if entry.get('packages')
                          else f'{tool} for {profile} needs no packages', file=sys.stderr)
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
    # notes/plan-shared-files.md says.
    after = {}
    adding = lines_now()
    justfile = justfile_of(target)
    text = justfile_text(justfile)
    before = justfile.read_bytes() if adding and text is not None and not has_block(text) else None
    if before is not None and not any(shared_files.normal(row) == IMPORT_LINE for row in user_rows(text)):
        # A Justfile that does not parse already gets no block: the block would not be the cause.
        error = just_error(target, justfile)
        if error:
            adding = {}
            before = None
            say(f'{justfile.name} does not parse, so the installer adds no import to it. Fix it, then run just '
                f'install again. just said: {error}')
    state['parts'] = sync_files(target, profiles, [name for name in active if name not in leaving], tools, active,
                                False, parts, roles, gone_roles, unsure, migrating, after=after, justfile=adding,
                                dropped=dropped, started=state['started'])
    state['adding'] = False
    justfile = justfile_of(target)
    text = justfile_text(justfile)
    added = justfile.read_bytes() if before is not None and text is not None else None
    # A clash of the record that the Justfile no longer matches is gone: the Justfile changed since.
    if isinstance(state['clash'], dict) and (text is None or state['clash'].get('hash')
                                             != hashlib.sha256(justfile.read_bytes()).hexdigest()):
        state['clash'] = None
    save(tools, [])
    text = justfile_text(justfile)
    error = just_error(target, justfile) if text is not None and has_block(text) else None
    # A Justfile that does not parse without the block either has another fault: the block is not the cause, so it
    # stays.
    other = text_error(target, without_block(text)) if error else None
    lines = len(shared_files.rows_of(text)) - len(shared_files.rows_of(without_block(text))) if error else 0
    if other:
        # Taking the block out would not make the Justfile parse, so the block stays. The error of the real file
        # comes first: a line of yours can need a name of the import, and then the copy names that name.
        say(f'{justfile.name} does not parse, also without the block of the installer, so the block stays. Fix the '
            f'Justfile, then run just install again. just said: {error}'
            + (f' Without the block, just says: {other} (These line numbers do not count the {lines} lines of the '
               f'block.)' if re.sub(r'\d+', 'N', other) != re.sub(r'\d+', 'N', error) else ''))
    elif error:
        # The Justfile does not parse with the import, also with an empty index, so the block goes again. The record
        # keeps the hash of the Justfile without it: the next run tries again only when the Justfile changed.
        untouched = added is not None and justfile.read_bytes() == added
        state['parts'] = sync_files(target, profiles, [name for name in active if name not in leaving], tools,
                                    active, False, state['parts'], roles, gone_roles, (), False, after=after,
                                    quiet=True, justfile={}, dropped=dropped, started=state['started'])
        # The block was new in this run, and nothing else changed the Justfile since, so it gets back its bytes as
        # they were. Else the writer took out the block only.
        if untouched:
            shared_files.write(justfile, before)
        state['clash'] = {'hash': hashlib.sha256(justfile.read_bytes()).hexdigest(), 'error': error}
        say(f'{justfile.name} does not parse with the block of the installer, which imports .agents/install.just, so '
            f'the installer took the block out again. Rename the recipe or variable of the project that just names, '
            f'then run just install again. just said: {error} (The line numbers count the {lines} lines of the block '
            f'at the start of {justfile.name}.)')
        save(tools, [])
    shared_files.finish(target)
    write_claude_md(target, False, after)
    # The files are in place now, so a toolchain file such as rust-toolchain.toml counts for the checks and installs.
    install_programs(target, programs)
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
    for note in generated_notes(target, raw_record(target))[0]:
        say(note)
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
