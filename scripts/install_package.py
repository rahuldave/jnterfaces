#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Copy the skills of one package, and its extras, into a target repository and record the source revision.

The skills come from skill-package.json. The record is .agents/<package>-install.json in the target, or --record.
The key install_extras of skill-package.json names the extras:

- files: files or folders outside .agents/skills that the installer manages like the files of a skill
- merge_hooks: JSON settings files whose "hooks" entries are merged into the file of the same name in the target
- seed: {"target file": "source file"} pairs that are written only when the target file is absent
- parts and starter: the parts that the role adds to shared files, and its starter files. install_stack.py writes
  them and records them in .agents/stack.json, so this installer leaves alone each file that they name, also in
  merge_hooks and seed, which a package keeps for one release for the installers from before the parts

A file that the user edited after an install is never overwritten or removed. --remove takes the package out.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def fail(message):
    raise ValueError(message)


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args], text=True, stderr=subprocess.PIPE).strip()


def git_bytes(source, *args, data=None):
    run = subprocess.run(['git', '-C', str(source), *args], input=data, capture_output=True)
    if run.returncode:
        fail(f'git {" ".join(args[:2])} failed: {run.stderr.decode(errors="replace").strip()}')
    return run.stdout


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        fail(f'Invalid JSON in {path}: {exc}')
    if not isinstance(value, dict):
        fail(f'Expected a JSON object in {path}')
    return value


def string_map(value):
    """Keep the entries of a recorded managed_files map whose path and hash are strings."""
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str) and isinstance(item, str)}


def source_revision(source, requested):
    try:
        top = Path(git(source, 'rev-parse', '--show-toplevel')).resolve()
        if top != source.resolve():
            fail(f'Source must be a Git repository root: {source}')
        revision = git(source, 'rev-parse', '--verify', f'{requested or "HEAD"}^{{commit}}')
        remote = subprocess.run(['git', '-C', str(source), 'config', '--get', 'remote.origin.url'],
                                capture_output=True, text=True).stdout.strip() or None
        dirty = bool(git(source, 'status', '--porcelain', '--untracked-files=all'))
        return revision, remote, dirty
    except subprocess.CalledProcessError as exc:
        fail(f'Cannot resolve source Git revision: {exc.stderr.strip()}')


def old_revision(source, old):
    """The source revision that an old record names, when this clone has that commit, else None."""
    revision = old.get('source_revision') if isinstance(old, dict) else None
    if not isinstance(revision, str) or not revision:
        return None
    run = subprocess.run(['git', '-C', str(source), 'cat-file', '-e', f'{revision}^{{commit}}'], capture_output=True)
    return revision if run.returncode == 0 else None


def shipped_at(source, revision, relative):
    """True when the source had this path at the revision. With no revision, False.

    This is how an old record, which lists no skills, tells a skill of the package from a skill of the user. Only
    the exact old revision decides. A guess from the history or from the clone now could take a skill of the user,
    so a skill that the revision does not show stays in place.
    """
    if not revision:
        return False
    run = subprocess.run(['git', '-C', str(source), 'ls-tree', '--name-only', revision, '--', relative.as_posix()],
                         capture_output=True, text=True)
    return run.returncode == 0 and bool(run.stdout.strip())


def json_at(source, revision, relative):
    """The JSON object of a source file at a revision, or {} when the file was not there."""
    run = subprocess.run(['git', '-C', str(source), 'cat-file', 'blob', f'{revision}:{relative.as_posix()}'],
                         capture_output=True)
    if run.returncode:
        return {}
    try:
        value = json.loads(run.stdout)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def merged_then(source, revision):
    """The hook settings files that the manifest of the old revision merged, as paths. Bad entries are left out."""
    extras = json_at(source, revision, Path('skill-package.json')).get('install_extras', {}) if revision else {}
    files = extras.get('merge_hooks', []) if isinstance(extras, dict) else []
    found = set()
    for item in files if isinstance(files, list) else []:
        try:
            found.add(clean_relative(item, 'A merge_hooks file of the old revision'))
        except ValueError:
            continue
    return found


def reject_symlinks(root, relative):
    """Reject a link in any part of a managed path, including a dangling link."""
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            fail(f'Symlinked managed path is not allowed: {current}')


class Tree:
    """The files of the source: the working tree, or one commit read straight from the Git objects."""

    def __init__(self, source, revision=None):
        self.source, self.revision = source, revision

    def read(self, relative):
        if self.revision:
            return git_bytes(self.source, 'cat-file', 'blob', f'{self.revision}:{relative.as_posix()}')
        reject_symlinks(self.source, relative)
        return (self.source / relative).read_bytes()

    def files(self, folder):
        """Map each file under folder to its mode, 0o755 or 0o644. Ignored and untracked-ignored files are left out."""
        spec = folder.as_posix() + '/'
        if self.revision:
            out = git_bytes(self.source, 'ls-tree', '-r', '-z', self.revision, '--', spec)
            found = {}
            for entry in filter(None, out.split(b'\0')):
                meta, name = entry.split(b'\t', 1)
                mode, kind = meta.split()[:2]
                path = Path(name.decode())
                if kind != b'blob' or mode == b'120000':
                    fail(f'Only regular files can be installed: {path}')
                found[path] = 0o755 if mode == b'100755' else 0o644
            return found
        out = git_bytes(self.source, 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', spec)
        found = {}
        for name in sorted(set(filter(None, out.decode().split('\0')))):
            path = Path(name)
            if not (self.source / path).exists() and not (self.source / path).is_symlink():
                continue  # deleted in the working tree
            reject_symlinks(self.source, path)
            if not (self.source / path).is_file():
                fail(f'Only regular files can be installed: {path}')
            found[path] = 0o755 if os.access(self.source / path, os.X_OK) else 0o644
        return found


def tree_entries(tree, path):
    """The files of an extra, a file or a folder of the source, each with its mode."""
    if tree.revision:
        out = git_bytes(tree.source, 'ls-tree', '-z', tree.revision, '--', path.as_posix())
        meta = out.split(b'\t', 1)[0].split() if out else []
        if meta[1:2] == [b'blob']:
            if meta[0] == b'120000':
                fail(f'Only regular files can be installed: {path}')
            return {path: 0o755 if meta[0] == b'100755' else 0o644}
    else:
        reject_symlinks(tree.source, path)
        if (tree.source / path).is_file():
            return {path: 0o755 if os.access(tree.source / path, os.X_OK) else 0o644}
    found = tree.files(path)
    if not found:
        fail(f'install_extras names a path that the source does not have: {path}')
    return found


def package_skills(tree, selected):
    """Return the package name, the paths of the selected skills and the names of all its skills."""
    manifest = json.loads(tree.read(Path('skill-package.json')))
    repository = manifest.get('repository') if isinstance(manifest, dict) else None
    name = repository.get('name') if isinstance(repository, dict) else None
    skills = manifest.get('skills') if isinstance(manifest, dict) else None
    if not isinstance(name, str) or not name or not isinstance(skills, list) or not skills:
        fail('skill-package.json needs repository.name and a list of skills')
    paths = {}
    for skill in skills:
        if not isinstance(skill, dict) or not isinstance(skill.get('name'), str) or not isinstance(skill.get('path'), str):
            fail('Each skill in skill-package.json needs a name and a path')
        path = Path(skill['path'])
        if path.is_absolute() or '..' in path.parts or path.parts[:2] != ('.agents', 'skills') or len(path.parts) != 3:
            fail(f'Skill path must be .agents/skills/<name>: {path}')
        paths[skill['name']] = path
    folders = {path.parts[2] for path in paths.values()}
    if selected:
        unknown = sorted(set(selected) - set(paths))
        if unknown:
            fail(f'Unknown skill(s) for {name}: {", ".join(unknown)}')
        return name, {key: value for key, value in paths.items() if key in selected}, folders
    return name, paths, folders


def clean_relative(value, what):
    """A relative path inside the repository, outside .git and outside .agents/skills."""
    if not isinstance(value, str) or not value.strip('/'):
        fail(f'{what} must be a relative path: {value!r}')
    path = Path(value.strip('/'))
    # Compare without case: on a case-insensitive disk .GIT is .git.
    folded = tuple(part.casefold() for part in path.parts)
    if (not folded or path.is_absolute() or '..' in path.parts or '.' in value.strip('/').split('/')
            or folded[0] == '.git' or folded[:2] == ('.agents', 'skills')):
        fail(f'{what} must be a relative path outside .git and .agents/skills: {value}')
    return path


def package_extras(tree):
    """Return the extras of skill-package.json: managed files or folders, hook settings to merge, and seed files."""
    manifest = json.loads(tree.read(Path('skill-package.json')))
    extras = manifest.get('install_extras', {}) if isinstance(manifest, dict) else {}
    if not isinstance(extras, dict):
        fail('install_extras in skill-package.json must be an object')
    files, hooks, seed = extras.get('files', []), extras.get('merge_hooks', []), extras.get('seed', {})
    if not isinstance(files, list) or not isinstance(hooks, list) or not isinstance(seed, dict):
        fail('install_extras needs a list of files, a list of merge_hooks and a seed object')
    files = [clean_relative(item, 'An install_extras file') for item in files]
    taken = taken_paths(extras)
    hooks = [path for path in (clean_relative(item, 'A merge_hooks file') for item in hooks) if path.as_posix() not in taken]
    seed = {clean_relative(key, 'A seed target'): clean_relative(value, 'A seed source') for key, value in seed.items()
            if key not in taken}
    return files, hooks, seed


def taken_paths(extras):
    """The files that the parts and the starter of a role name. install_stack.py writes them, not this installer."""
    parts, starter = extras.get('parts', {}), extras.get('starter', {})
    if not isinstance(parts, dict) or not isinstance(starter, dict):
        fail('install_extras.parts and install_extras.starter must be objects')
    return {clean_relative(path, 'A part or a starter').as_posix() for path in list(parts) + list(starter)}


def taken_of(tree):
    manifest = json.loads(tree.read(Path('skill-package.json')))
    extras = manifest.get('install_extras', {}) if isinstance(manifest, dict) else {}
    return taken_paths(extras) if isinstance(extras, dict) else set()


def under(name, folders):
    """True when the path name is one of the folders or files, or inside one of them."""
    return any(name == folder or name.startswith(folder + '/') for folder in folders)


def selector(group):
    return {key: value for key, value in group.items() if key != 'hooks'}


def hook_list(config, event, group):
    """The hooks of the group of config that has the same keys as group, or []."""
    hooks = config.get('hooks') if isinstance(config, dict) else None
    groups = hooks.get(event, []) if isinstance(hooks, dict) else []
    return [hook for other in groups if isinstance(other, dict) and selector(other) == selector(group)
            for hook in other.get('hooks', []) if isinstance(other.get('hooks'), list)]


def merge_config(existing, incoming, path, owned=None):
    """Add the hook commands of incoming to existing. A group matches on every key but its hooks.

    Return the merged settings and the hooks that this package owns in them: the ones it added now, and the ones
    that the record says it added before. A hook that the user had already is the user's, and stays the user's.
    """
    result = copy.deepcopy(existing)
    if not isinstance(incoming.get('hooks'), dict) or not isinstance(result.get('hooks', {}), dict):
        fail(f'Invalid hooks object in {path}')
    hooks = result.setdefault('hooks', {})
    ours = {'hooks': {}}
    for event, groups in incoming['hooks'].items():
        new_event = event not in hooks
        current = hooks.setdefault(event, [])
        if not isinstance(current, list) or not isinstance(groups, list):
            fail(f'Invalid hook groups for {event} in {path}')
        shipped_selectors = [selector(group) for group in groups if isinstance(group, dict)]
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get('hooks'), list):
                fail(f'Invalid hook group for {event} in {path}')
            match = None
            for candidate in current:
                if not isinstance(candidate, dict) or not isinstance(candidate.get('hooks'), list):
                    fail(f'Invalid existing hook group for {event} in {path}')
                if selector(candidate) == selector(group):
                    match = candidate
                    break
            new_group = match is None
            if new_group:
                match = dict(selector(group), hooks=[])
                current.append(match)
            before = hook_list(owned or {}, event, group)
            mine = []
            for hook in group['hooks']:
                if not isinstance(hook, dict) or not isinstance(hook.get('command'), str):
                    fail(f'Invalid hook command for {event} in {path}')
                same = [item for item in match['hooks'] if isinstance(item, dict) and item.get('command') == hook['command']]
                elsewhere = [other for other in current if other is not match and isinstance(other, dict)
                             and selector(other) not in shipped_selectors
                             and any(isinstance(item, dict) and item.get('command') == hook['command']
                                     for item in other.get('hooks', []))]
                if not same and elsewhere:
                    print(f'The hook {hook["command"]!r} is in another group of {event} in {path}, so the installer '
                          f'leaves it there and adds no second copy.', file=sys.stderr)
                elif not same:
                    match['hooks'].append(hook)
                    mine.append(hook)
                elif hook in same:
                    if hook in before:
                        mine.append(hook)
                elif any(old.get('command') == hook['command'] for old in before if isinstance(old, dict)):
                    print(f'Kept your edited hook {hook["command"]!r} in {path}; it is yours now.', file=sys.stderr)
                else:
                    fail(f'Conflicting hook command {hook["command"]!r} in {path}; resolve it by hand')
            if mine:
                ours['hooks'].setdefault(event, []).append(dict(selector(group), hooks=mine))
            # A group or an event that this merge made, and then left empty, goes. One of the user's stays.
            if new_group and not match['hooks']:
                current.remove(match)
        if new_event and not current:
            del hooks[event]
    if not hooks and 'hooks' not in existing:
        del result['hooks']
    return result, ours


def unmerge_config(existing, old, keep):
    """Take out of existing each hook of old that keep does not have. A group or event that this empties goes too."""
    result = copy.deepcopy(existing)
    hooks = result.get('hooks')
    old_hooks = old.get('hooks') if isinstance(old, dict) else None
    keep_hooks = keep.get('hooks') if isinstance(keep, dict) and isinstance(keep.get('hooks'), dict) else {}
    if not isinstance(hooks, dict) or not isinstance(old_hooks, dict):
        return result
    for event, groups in old_hooks.items():
        current = hooks.get(event)
        if not isinstance(current, list) or not isinstance(groups, list):
            continue
        emptied = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get('hooks'), list):
                continue
            kept = [hook for other in keep_hooks.get(event, []) if isinstance(other, dict)
                    and selector(other) == selector(group) for hook in other.get('hooks', [])]
            for candidate in current:
                if isinstance(candidate, dict) and isinstance(candidate.get('hooks'), list) \
                        and selector(candidate) == selector(group) and candidate['hooks']:
                    candidate['hooks'] = [hook for hook in candidate['hooks'] if hook not in group['hooks'] or hook in kept]
                    if not candidate['hooks']:
                        emptied.append(candidate)
        current[:] = [candidate for candidate in current if not any(candidate is gone for gone in emptied)]
        if not current:
            del hooks[event]
    if not hooks:
        del result['hooks']
    return result


def other_records(target, own):
    """Hashes that other package installs recorded, by path. They mark a file as a known shipped copy."""
    known = {}
    agents = target / '.agents'
    if not agents.is_dir() or agents.is_symlink():
        return known
    for record in sorted(agents.glob('*-install.json')):
        if record.name == own.name or record.is_symlink():
            continue
        try:
            managed = string_map(json.loads(record.read_text()).get('managed_files'))
        except (OSError, json.JSONDecodeError, AttributeError):
            continue
        for path, value in managed.items():
            known.setdefault(path, set()).add(value)
    return known


def check_parents(target, relative):
    """Fail in planning when a part of the path in the target exists and is not a directory."""
    current = target
    for part in relative.parts[:-1]:
        current = current / part
        if current.exists() and not current.is_dir():
            fail(f'A file is in the way of a skill folder: {current}')


def atomic_write(path, data, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def other_case(path):
    """The name of an existing entry that differs from path only in case, on a case-insensitive disk."""
    if not path.parent.is_dir():
        return None
    for entry in os.listdir(path.parent):
        if entry != path.name and entry.casefold() == path.name.casefold():
            return path.parent / entry
    return None


def renamed_from(target, relative, old_hashes):
    """The old name of a case-only rename that is the same file on this disk, or None."""
    destination = target / relative
    alias = other_case(destination)
    if alias is None or not destination.exists():
        return None
    name = alias.relative_to(target).as_posix()
    return Path(name) if name in old_hashes and os.path.samefile(alias, destination) else None


def remove_empty_parents(target, path):
    """Remove the folders that a removal left empty, up to .agents/skills for a skill file, else up to the target."""
    inside = path.resolve().is_relative_to((target / '.agents/skills').resolve())
    stop = (target / '.agents/skills').resolve() if inside else target.resolve()
    parent = path.parent
    while parent.resolve() != stop and parent.resolve().is_relative_to(stop):
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent


def skill_folders(names):
    """The skill folders of a list of recorded paths."""
    return {Path(name).parts[2] for name in names if scope_of(name, []) == 'skill'}


def recorded_folders(old):
    """The skill folders that a record names as the package's: its skill_folders, its links and its skill names. A
    skill name can differ from its folder, so the folders are recorded too."""
    folders = old.get('skill_folders') if isinstance(old, dict) else None
    names = old.get('skills') if isinstance(old, dict) else None
    found = {name for name in (folders if isinstance(folders, list) else []) + (names if isinstance(names, list) else [])
             if isinstance(name, str)}
    return found | recorded_links(old)


def recorded_seeds(old):
    """The starter files of a record, with the hash of what the install wrote: plain paths inside the target only."""
    seeds = old.get('seeds') if isinstance(old, dict) else None
    found = {}
    for name, value in (seeds.items() if isinstance(seeds, dict) else []):
        if isinstance(name, str) and isinstance(value, str) and clean_relative(name, '').as_posix() == name:
            found[name] = value
    return found


def recorded_links(old):
    """The skill folders in the claude_links of a record: plain folder names only."""
    links = old.get('claude_links') if isinstance(old, dict) else None
    return {name for name in links if isinstance(name, str) and name not in ('', '.', '..') and '/' not in name
            and '\\' not in name} if isinstance(links, list) else set()


def link_skills(target, folders, present, old_links, create=True, dry_run=False):
    """Make .claude/skills/NAME a link to .agents/skills/NAME for each present skill folder, so that Claude Code reads
    it, and take out such a link when its skill is gone. Return the links that the package now has.

    A link that an earlier install made and the user then removed stays removed, while .claude/skills is there. Any
    other file or link in
    .claude/skills is left alone, and so is a .claude or .claude/skills that is a link or a file.
    """
    claude, folder = target / '.claude', target / '.claude/skills'
    for path in (claude, folder):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            return set(old_links) & set(present)
    if not folder.is_dir():
        # With no .claude/skills folder, no link was removed by hand, so every link is made again.
        old_links = set()
    links, made, gone = set(), [], []
    for name in sorted(folders):
        link, want = folder / name, f'../../.agents/skills/{name}'
        ours = link.is_symlink() and os.readlink(link) == want
        if name in present:
            if ours or name in old_links:
                # A recorded link that is gone was removed by the user. Its name stays in the record, so that no
                # later install makes it again.
                links.add(name)
            elif create and not link.exists() and not link.is_symlink():
                links.add(name)
                made.append(name)
                if not dry_run:
                    folder.mkdir(parents=True, exist_ok=True)
                    os.symlink(want, link)
        elif ours:
            gone.append(name)
            if not dry_run:
                link.unlink()
                remove_empty_parents(target, link)
    if made:
        print(f'{"Would link" if dry_run else "Linked"} .claude/skills to {", ".join(made)}, so that Claude Code '
              f'reads {"it" if len(made) == 1 else "them"}')
    if gone:
        print(f'{"Would remove" if dry_run else "Removed"} the .claude/skills link of {", ".join(gone)}')
    return links


def scope_of(name, extras):
    """'skill' for a file in a skill folder, 'extra' for a file of an extra, else None."""
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts:
        return None
    if relative.parts[:2] == ('.agents', 'skills'):
        return 'skill' if len(relative.parts) >= 4 else None
    return 'extra' if under(name, extras) else None


def read_record(target, provenance):
    """The earlier record, its managed files and its skills, with every malformed part left out."""
    reject_symlinks(target, provenance)
    old = read_json(target / provenance)
    old_hashes = string_map(old.get('managed_files', {}))
    old_skills = old.get('skills', [])
    old_skills = [name for name in old_skills if isinstance(name, str)] if isinstance(old_skills, list) else []
    old_extras = old.get('extras', [])
    old_extras = [name for name in old_extras if isinstance(name, str)] if isinstance(old_extras, list) else []
    old_hooks = old.get('merged_hooks', {})
    old_hooks = {k: v for k, v in old_hooks.items() if isinstance(v, dict)} if isinstance(old_hooks, dict) else {}
    created = old.get('created_settings', [])
    created = [name for name in created if isinstance(name, str)] if isinstance(created, list) else []
    # A recorded path names a file in the target: never one outside it, in .git or in the skills.
    for name in [*old_extras, *old_hooks, *created]:
        clean_relative(name, f'A path in {provenance}')
        if clean_relative(name, '').as_posix() != name:
            fail(f'A path in {provenance} is not in its plain form: {name}')
    return old, old_hashes, old_skills, old_extras, old_hooks, created


def hook_settings(tree, target, hooks, old_hooks, created, removing=False, legacy=False, legacy_files=(),
                  revision=None):
    """Plan the hook settings: {path: (new bytes or None to delete, the hooks that the package owns, created)}.

    A settings file that the package created goes again when it ends up empty. Any other file stays.
    """
    then = merged_then(tree.source, revision) if legacy and revision else set()
    then = {path for path in then if path.as_posix() not in taken_of(tree)}
    planned = {}
    for relative in sorted(set(hooks) | {Path(name) for name in old_hooks} | then, key=str):
        reject_symlinks(target, relative)
        incoming = {}
        if relative in hooks and not removing:
            incoming = json.loads(tree.read(relative))
            if not isinstance(incoming, dict):
                fail(f'Expected a JSON object in the source {relative}')
        existed = (target / relative).exists()
        existing = read_json(target / relative)
        old = old_hooks.get(relative.as_posix())
        if old is None and legacy and revision and (relative in hooks or relative in then):
            # A record from before merged_hooks was recorded. It merged the settings of its own source revision.
            old = json_at(tree.source, revision, relative)
        elif old is None and legacy and relative in hooks:
            # The clone has no commit of the old revision, so take each hook of the file now whose command the
            # source ships, in a group with the same keys, even if the source has changed it since.
            source = json.loads(tree.read(relative))
            old = shipped_commands(existing, source, relative)
            unclaimed(existing, old, legacy_files, relative)
        merged, ours = unmerge_config(existing, old or {}, incoming), {}
        if incoming:
            merged, ours = merge_config(merged, incoming, target / relative, old)
        made = relative.as_posix() in created or (not existed and bool(incoming))
        if merged == existing:
            data = (target / relative).read_bytes() if existed else None
        elif not merged and existed and (made or (legacy and removing)):
            # An old record does not say who made the file. Once it holds nothing, it goes.
            data = None
        else:
            data = (json.dumps(merged, indent=2, ensure_ascii=False) + '\n').encode()
        if data is not None or existed:
            planned[relative] = (data, ours, made)
    return planned


def shipped_commands(existing, source, path=''):
    """The hooks of existing that have the command of a hook of source, in a group with the same keys.

    An old record cannot tell a user's edit of a shipped hook from a change in the source, so each claimed hook that
    differs from the source is named.
    """
    found = {'hooks': {}}
    source_hooks = source.get('hooks') if isinstance(source, dict) else None
    for event, groups in (source_hooks.items() if isinstance(source_hooks, dict) else []):
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict) or not isinstance(group.get('hooks'), list):
                continue
            commands = {hook.get('command') for hook in group['hooks'] if isinstance(hook, dict)}
            mine = [hook for hook in hook_list(existing, event, group)
                    if isinstance(hook, dict) and hook.get('command') in commands]
            for hook in mine:
                if hook not in group['hooks']:
                    print(f'The hook {hook.get("command")!r} in {path} differs from the one that the package ships '
                          f'now. The installer takes it as an old copy and replaces it. If you edited it, edit it '
                          f'again.', file=sys.stderr)
            if mine:
                found['hooks'].setdefault(event, []).append(dict(selector(group), hooks=mine))
    return found


def unclaimed(existing, claimed, managed, path):
    """Name each hook that runs a file of the old install but has no command that the package ships now."""
    hooks = existing.get('hooks') if isinstance(existing, dict) else None
    for event, groups in (hooks.items() if isinstance(hooks, dict) else []):
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict) or not isinstance(group.get('hooks'), list):
                continue
            taken = hook_list(claimed, event, group)
            for hook in group['hooks']:
                command = hook.get('command') if isinstance(hook, dict) else None
                if isinstance(command, str) and hook not in taken and any(name in command for name in managed):
                    print(f'The hook {command!r} in {path} runs a file of the old install, but the package no '
                          f'longer ships that command. Check it, and remove it if it is an old copy.',
                          file=sys.stderr)


def write_hooks(target, planned):
    for relative, (data, _, _) in planned.items():
        destination = target / relative
        if data is None:
            if destination.exists():
                destination.unlink()
                remove_empty_parents(target, destination)
        elif not destination.exists() or destination.read_bytes() != data:
            atomic_write(destination, data)


def commands_left(planned):
    """The hook commands that stay in the planned settings files."""
    found = []
    for data, _, _ in planned.values():
        settings = json.loads(data) if data else {}
        for groups in (settings.get('hooks') or {}).values() if isinstance(settings, dict) else []:
            for group in groups if isinstance(groups, list) else []:
                for hook in group.get('hooks', []) if isinstance(group, dict) else []:
                    if isinstance(hook, dict) and isinstance(hook.get('command'), str):
                        found.append(hook['command'])
    return found


def remove(args, target, tree, package, provenance, hooks):
    """Take the package out of the target. An edited file stays, and the record then lists only the kept files."""
    old, old_hashes, old_skills, old_extras, old_hooks, created = read_record(target, provenance)
    old_hooks = {name: value for name, value in old_hooks.items() if name not in taken_of(tree)}
    if not (target / provenance).exists():
        print(f'{package} is not installed in {target}; nothing to remove')
        return
    known = other_records(target, provenance)
    revision_then = old_revision(tree.source, old)
    planned = hook_settings(tree, target, hooks, old_hooks, created, removing=True,
                            legacy=bool(old) and 'merged_hooks' not in old, legacy_files=list(old_hashes),
                            revision=revision_then)
    # An old record has no list of skills. A recorded skill folder that the source never shipped is the user's. An
    # older partial install also wrote a list of skills that leaves out such a folder, which it still recorded: a
    # folder that is in no list, not in the source now and not in the source then is the user's too.
    foreign = {folder for folder in skill_folders(old_hashes)
               if ('skills' not in old or folder not in recorded_folders(old)) and folder not in package_skills(tree, [])[2]
               and not shipped_at(tree.source, revision_then, Path('.agents/skills') / folder)} if old else set()
    left = commands_left(planned)
    gone, kept, shared = [], {}, set()
    for name, recorded in old_hashes.items():
        if scope_of(name, old_extras + [path.as_posix() for path in package_extras(tree)[0]]) is None:
            fail(f'Recorded managed file is outside .agents/skills and outside the extras: {name}')
        relative = Path(name)
        reject_symlinks(target, relative)
        destination = target / relative
        if not destination.is_file():
            continue
        if name in known:
            shared.add(name)
            print(f'Kept {name}: another package records it.', file=sys.stderr)
        elif scope_of(name, []) == 'skill' and relative.parts[2] in foreign:
            print(f'Left {name}: the package never shipped that skill, so it is not the package\'s to remove.',
                  file=sys.stderr)
        elif any(name in command for command in left):
            kept[name] = recorded
            print(f'Kept {name}: a hook that stays in the settings runs it.', file=sys.stderr)
        elif digest(destination.read_bytes()) == recorded:
            gone.append(relative)
        else:
            kept[name] = recorded
            print(f'Kept {name}: edited after install; remove it yourself when you no longer want it.', file=sys.stderr)
    # A skill stays when its SKILL.md stays: edited, run by a hook, or recorded by another package.
    staying = {Path(name).parts[2] for name in [*kept, *shared] if scope_of(name, []) == 'skill'
               and Path(name).name == 'SKILL.md'}
    old_links = recorded_links(old)
    package_folders = (skill_folders(old_hashes) | old_links) - foreign
    # A starter file goes when it is still as the install wrote it, or as the source ships it now. An edited one stays.
    seed_gone, seed_kept = [], []
    # A record from before the seeds were recorded cannot say who wrote the file, so a file equal to what the source
    # ships counts as the install's. A newer record names its starter files.
    shipped_seeds = {} if 'seeds' in old else {
        relative.as_posix(): digest(tree.read(origin)) for relative, origin in package_extras(tree)[2].items()}
    for name in sorted((set(recorded_seeds(old)) | set(shipped_seeds)) - taken_of(tree)):
        reject_symlinks(target, Path(name))
        path = target / name
        if not path.is_file():
            continue
        if digest(path.read_bytes()) in {recorded_seeds(old).get(name), shipped_seeds.get(name)}:
            seed_gone.append(Path(name))
        else:
            seed_kept.append(name)
            print(f'Kept {name}: you edited this starter file of {package}, so it stays. It may still name the skills '
                  f'of {package}; edit it, or remove it yourself.', file=sys.stderr)
    if args.dry_run:
        link_skills(target, package_folders, staying, old_links, create=False, dry_run=True)
        print(f'Would remove {len(gone) + len(seed_gone)} files of {package} from {target} and keep '
              f'{len(kept) + len(seed_kept)} edited files')
        return
    for relative in gone + seed_gone:
        (target / relative).unlink()
        remove_empty_parents(target, target / relative)
    write_hooks(target, planned)
    links = link_skills(target, package_folders, staying, old_links, create=False)
    if kept:
        old['managed_files'] = dict(sorted(kept.items()))
        old['skills'] = sorted({Path(name).parts[2] for name in kept if scope_of(name, []) == 'skill'} & set(old_skills))
        old['merged_hooks'] = {}
        old['created_settings'] = []
        old['seeds'] = {}
        old['claude_links'] = sorted(links)
        old['removed'] = True
        atomic_write(target / provenance, (json.dumps(old, indent=2) + '\n').encode())
    else:
        (target / provenance).unlink()
    print(f'Removed {package} from {target}: {len(gone) + len(seed_gone)} files removed, '
          f'{len(kept) + len(seed_kept)} files kept')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('target', type=Path)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--source-commit')
    parser.add_argument('--record', type=Path, help='the record in the target; default .agents/<package>-install.json')
    parser.add_argument('--skill', action='append', default=[], help='install only this skill; repeatable')
    parser.add_argument('--exact', action='store_true',
                        help='with --skill: the selected skills are the whole install; remove the other skills of this package')
    parser.add_argument('--remove', action='store_true',
                        help='remove the files and hook settings that the record lists; keep an edited file')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.exact and not args.skill:
        fail('--exact needs at least one --skill')
    if args.remove and args.skill:
        fail('--remove takes the whole package out, so it does not go with --skill')
    # Partial: --skill without --exact leaves the other skills of this package as they are.
    partial = bool(args.skill) and not args.exact
    if args.target.is_symlink() or args.source.is_symlink():
        fail('Source and target repository roots must not be symlinks')
    target = args.target.resolve()
    source = args.source.resolve()
    if not target.is_dir():
        fail(f'Target does not exist: {target}')
    if target == source:
        fail('Target must not be the source repository')
    revision, remote, dirty = source_revision(source, args.source_commit)
    tree = Tree(source, revision if args.source_commit else None)
    package, paths, all_skills = package_skills(tree, args.skill)
    extras, hooks, seed = package_extras(tree)
    provenance = Path('.agents') / f'{package}-install.json' if args.record is None else args.record
    if provenance.is_absolute() or '..' in provenance.parts or provenance.parts[:1] != ('.agents',):
        fail(f'--record must be a path inside .agents: {provenance}')
    if args.remove:
        return remove(args, target, tree, package, provenance, hooks)
    old, old_hashes, old_skills, old_extras, old_hooks, created = read_record(target, provenance)
    old_hooks = {name: value for name, value in old_hooks.items() if name not in taken_of(tree)}
    extra_names = [path.as_posix() for path in extras]
    for name in old_hashes:
        if scope_of(name, extra_names + old_extras) is None:
            fail(f'Recorded managed file is outside .agents/skills and outside the extras: {name}')
    known = other_records(target, provenance)
    keep = []
    planned, aliases, edited = {}, {}, {}
    shipped = {}
    for skill, folder in paths.items():
        files = tree.files(folder)
        if folder / 'SKILL.md' not in files:
            fail(f'Missing SKILL.md in {folder}')
        shipped.update(files)
    for extra in extras:
        shipped.update(tree_entries(tree, extra))
    for relative, mode in shipped.items():
        reject_symlinks(target, relative)
        check_parents(target, relative)
        destination = target / relative
        data = tree.read(relative)
        if destination.exists():
            if destination.is_dir():
                fail(f'Destination is a directory: {destination}')
            current = digest(destination.read_bytes())
            accepted = {digest(data), old_hashes.get(relative.as_posix())} | known.get(relative.as_posix(), set())
            alias = renamed_from(target, relative, old_hashes)
            if alias is not None:
                accepted.add(old_hashes[alias.as_posix()])
            if current not in accepted:
                # On a case-insensitive disk, the old name of a case-only rename is this same file.
                was = relative.as_posix() if relative.as_posix() in old_hashes else alias and alias.as_posix()
                if not was:
                    fail(f'Existing file differs from source and from every recorded install: {destination}')
                # A file of this package that the user edited stays as it is, and the rest of the install goes on.
                # The record keeps the old hash under the name on disk, so that a later run and a removal still see
                # the edit, and an undone edit later takes the new name.
                edited[was] = old_hashes[was]
                keep.append((relative.as_posix(), 'edited after install, so the new version from the source is not '
                                                  'written; undo the edit to get it'))
                continue
            if alias is not None:
                aliases[relative] = alias
        planned[relative] = (data, mode)
    seeds = {}
    for relative, origin in seed.items():
        reject_symlinks(target, relative)
        if not (target / relative).exists():
            seeds[relative] = tree.read(origin)
    # The starter files that an install wrote, with the hash of what it wrote, so that a removal can take out one that
    # nobody changed. A starter file that is gone leaves the record.
    seed_hashes = {name: value for name, value in recorded_seeds(old).items() if (target / name).is_file()
                   and name not in taken_of(tree)}
    seed_hashes.update({relative.as_posix(): digest(data) for relative, data in seeds.items()})
    legacy = bool(old) and 'merged_hooks' not in old
    revision_then = old_revision(tree.source, old) if legacy else None
    settings = hook_settings(tree, target, hooks, old_hooks, created, legacy=legacy, legacy_files=list(old_hashes),
                             revision=revision_then)
    # A file of an earlier install that this source no longer ships is removed when nobody changed it.
    # In scope: skills of this package, now or in its earlier record (in a partial install, only the selected ones),
    # and the extras, which every install ships.
    owned = all_skills | set(old_skills) | recorded_folders(old)
    if old and 'skills' not in old:
        # An old record has no list of skills. A recorded skill folder is this package's when the old revision has
        # it. Any other folder stays, so a skill of the user is never taken.
        for folder in {Path(name).parts[2] for name in old_hashes if scope_of(name, []) == 'skill'} - owned:
            if shipped_at(source, revision_then, Path('.agents/skills') / folder):
                owned.add(folder)
    scope = {path.as_posix() + '/' for path in paths.values()}
    renamed = {alias.as_posix() for alias in aliases.values()}
    # With --exact, a skill that is turned off but has an edited file stays whole, so no skill is left half removed.
    selected_folders = {path.parts[2] for path in paths.values()}
    held = set()
    if args.exact:
        for name, recorded in old_hashes.items():
            relative = Path(name)
            if scope_of(name, []) != 'skill':
                continue
            folder = relative.parts[2]
            if folder in owned and folder not in selected_folders and name not in known:
                destination = target / relative
                if destination.is_file() and not destination.is_symlink() and digest(destination.read_bytes()) != recorded:
                    held.add(folder)
                    keep.append((name, f'edited after install, so the {folder} skill stays installed; '
                                       f'undo the edit or remove .agents/skills/{folder} yourself'))
    prune = []
    for name, recorded in old_hashes.items():
        relative = Path(name)
        if relative in planned or name in renamed or name in edited:
            continue
        if scope_of(name, extra_names + old_extras) == 'skill':
            if relative.parts[2] not in owned or relative.parts[2] in held:
                continue
            if partial and not any(name.startswith(prefix) for prefix in scope):
                continue
        reject_symlinks(target, relative)
        destination = target / relative
        if not destination.is_file():
            continue
        if name in known:
            keep.append((name, 'another package records it'))
        elif digest(destination.read_bytes()) == recorded:
            prune.append(relative)
        else:
            keep.append((name, 'edited after install'))
    managed = {name: value for name, value in old_hashes.items()
               if scope_of(name, extra_names + old_extras) == 'skill' and Path(name).parts[2] in owned and (
                   (partial and not any(name.startswith(prefix) for prefix in scope)) or Path(name).parts[2] in held)}
    managed.update({relative.as_posix(): digest(data) for relative, (data, _) in planned.items()})
    managed.update(edited)
    # A skill folder that is not the package's, from an old record, leaves the record, so that no removal takes it.
    # An old record has no list of skills, so a partial install keeps the skill folders of the package that it leaves.
    legacy_kept = skill_folders(managed) & owned if old and 'skills' not in old else set()
    metadata = {
        'schema_version': 1,
        'package': package,
        'source_repository': remote,
        'source_revision': revision,
        'source_worktree_dirty': dirty if not args.source_commit else False,
        'source_requested_commit': args.source_commit,
        'skills': (sorted(set(old_skills) | set(paths) | legacy_kept) if partial
                   else sorted(set(paths) | (set(old_skills) & held))),
        'skill_folders': sorted(skill_folders(managed)),
        'extras': sorted(extra_names),
        'merged_hooks': {relative.as_posix(): ours for relative, (_, ours, _) in settings.items() if ours.get('hooks')},
        'created_settings': sorted(relative.as_posix() for relative, (_, _, made) in settings.items() if made),
        'seeds': dict(sorted(seed_hashes.items())),
        'managed_files': dict(sorted(managed.items())),
    }
    for name, reason in keep:
        print(f'Kept {name}: {reason}.', file=sys.stderr)
    # A skill gets a link when its SKILL.md is, or will be, on disk. link_folders holds only the package's skills.
    present = {Path(name).parts[2] for name in managed if scope_of(name, []) == 'skill' and Path(name).name == 'SKILL.md'
               and (Path(name) in planned or (target / name).is_file())}
    # A SKILL.md that stays on disk, edited or recorded by another package, keeps its link too.
    present |= {Path(name).parts[2] for name, _ in keep if scope_of(name, []) == 'skill'
                and Path(name).name == 'SKILL.md' and (target / name).is_file()}
    old_links = recorded_links(old)
    # A link that the package made stays in scope, so that it goes when its skill folder goes.
    link_folders = ((skill_folders(managed) | skill_folders(old_hashes)) & owned) | old_links
    if args.dry_run:
        link_skills(target, link_folders, present, old_links, dry_run=True)
        print(f'Would install {len(planned)} files, remove {len(prune)} old files and write {len(seeds)} starter '
              f'files of {package} in {target} from {revision}')
        return
    for relative, (data, mode) in planned.items():
        destination = target / relative
        # On a case-insensitive disk the old name of a case-only rename is the same file. It makes way for the new name.
        if relative in aliases:
            (target / aliases[relative]).unlink()
        if not destination.exists() or destination.read_bytes() != data or destination.stat().st_mode & 0o777 != mode:
            atomic_write(destination, data, mode)
    for relative in prune:
        (target / relative).unlink()
        remove_empty_parents(target, target / relative)
    for relative, data in seeds.items():
        atomic_write(target / relative, data)
        print(f'Wrote {relative} from {seed[relative]}; it is yours now. Later installs leave it alone, and a removal '
              f'of {package} takes it out only while nobody has changed it.')
    write_hooks(target, settings)
    metadata['claude_links'] = sorted(link_skills(target, link_folders, present, old_links))
    atomic_write(target / provenance, (json.dumps(metadata, indent=2) + '\n').encode())
    print(f'Installed {package} in {target} from {revision} (dirty source: {metadata["source_worktree_dirty"]})')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as exc:
        print(f'Install failed: {exc}', file=sys.stderr)
        sys.exit(1)
