#!/usr/bin/env python3
"""Copy the skills of this package into a target repository and record the source revision.

The skills come from skill-package.json. The record is .agents/<package>-install.json in the target.
A file that the user edited after an install is never overwritten or removed.
"""
import argparse
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
    stop = (target / '.agents/skills').resolve()
    parent = path.parent
    while parent.resolve() != stop and parent.resolve().is_relative_to(stop):
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', type=Path)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--source-commit')
    parser.add_argument('--skill', action='append', default=[], help='install only this skill; repeatable')
    parser.add_argument('--exact', action='store_true',
                        help='with --skill: the selected skills are the whole install; remove the other skills of this package')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.exact and not args.skill:
        fail('--exact needs at least one --skill')
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
    provenance = Path('.agents') / f'{package}-install.json'
    reject_symlinks(target, provenance)
    old = read_json(target / provenance)
    old_hashes = string_map(old.get('managed_files', {}))
    old_skills = old.get('skills', [])
    old_skills = [name for name in old_skills if isinstance(name, str)] if isinstance(old_skills, list) else []
    known = other_records(target, provenance)
    keep = []
    planned, aliases = {}, {}
    for skill, folder in paths.items():
        files = tree.files(folder)
        if folder / 'SKILL.md' not in files:
            fail(f'Missing SKILL.md in {folder}')
        for relative, mode in files.items():
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
                    aliases[relative] = alias
                    accepted.add(old_hashes[alias.as_posix()])
                if current not in accepted:
                    fail(f'Existing file differs from source and from every recorded install: {destination}')
            planned[relative] = (data, mode)
    # A file of an earlier install that this source no longer ships is removed when nobody changed it.
    # Only skills of this package, now or in its earlier record, are in scope; in a partial install, only the selected ones.
    owned = all_skills | set(old_skills)
    scope = {path.as_posix() + '/' for path in paths.values()}
    renamed = {alias.as_posix() for alias in aliases.values()}
    # With --exact, a skill that is turned off but has an edited file stays whole, so no skill is left half removed.
    selected_folders = {path.parts[2] for path in paths.values()}
    held = set()
    if args.exact:
        for name, recorded in old_hashes.items():
            relative = Path(name)
            if len(relative.parts) < 4 or relative.parts[:2] != ('.agents', 'skills') or '..' in relative.parts:
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
        if relative.is_absolute() or '..' in relative.parts or relative.parts[:2] != ('.agents', 'skills'):
            fail(f'Recorded managed file is outside .agents/skills: {name}')
        if len(relative.parts) < 4 or relative.parts[2] not in owned or relative.parts[2] in held:
            continue
        if relative in planned or name in renamed:
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
               if (partial and not any(name.startswith(prefix) for prefix in scope))
               or (len(Path(name).parts) > 3 and Path(name).parts[2] in held)}
    managed.update({relative.as_posix(): digest(data) for relative, (data, _) in planned.items()})
    metadata = {
        'schema_version': 1,
        'package': package,
        'source_repository': remote,
        'source_revision': revision,
        'source_worktree_dirty': dirty if not args.source_commit else False,
        'source_requested_commit': args.source_commit,
        'skills': sorted(set(old_skills) | set(paths)) if partial else sorted(set(paths) | (set(old_skills) & held)),
        'managed_files': dict(sorted(managed.items())),
    }
    for name, reason in keep:
        print(f'Kept {name}: {reason}.', file=sys.stderr)
    if args.dry_run:
        print(f'Would install {len(planned)} files and remove {len(prune)} old files of {package} in {target} from {revision}')
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
    atomic_write(target / provenance, (json.dumps(metadata, indent=2) + '\n').encode())
    print(f'Installed {package} in {target} from {revision} (dirty source: {metadata["source_worktree_dirty"]})')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as exc:
        print(f'Install failed: {exc}', file=sys.stderr)
        sys.exit(1)
