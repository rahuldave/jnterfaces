#!/usr/bin/env python3
"""Check that the skills of a package stay composable: a link to a skill that may be missing has a fallback.

A link names a skill folder, for example `.agents/skills/cx/SKILL.md` or `../cx/SKILL.md`. These links need a
fallback in their paragraph or list item: a sentence that says the skill, or its package, is not installed or is
missing, such as "If the cx skill is not installed, ...":

- a link to a skill of another package
- a link to an optional skill of the same package, from a file outside that skill

A link to a skill of the package itself, or of a package given with --resolve, must name a path that exists.

    check_skill_links.py --package DIR [--resolve DIR ...] FILE ...
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
NAME = r'([A-Za-z0-9_-]+)'
REST = r'(/[A-Za-z0-9_./-]*)?'
# A link from this target's root, as `.agents/skills/cx/...`, `./.agents/skills/cx` or `$root/.claude/skills/cx`.
# A path under the home folder, such as `~/.agents/skills/tool`, is a global install, not a link into the target.
ROOTED = re.compile(r'(?<![A-Za-z0-9_~-])(?<!~/)(?:\./)?\.(?:agents|claude)/skills/' + NAME + REST)
BARE = re.compile(r'(?<![A-Za-z0-9_./~-])skills/' + NAME + REST)
# A link between skills, as `../cx/SKILL.md` or `../../cx/references/x.md`.
RELATIVE = re.compile(r'(?<![A-Za-z0-9_./-])(?:\.\./)+' + NAME + r'(/[A-Za-z0-9_./-]*)')
NEGATIVE = r'(?:\s*\([^)]*\))?\s+(?:is|are)\s+(?:not installed|missing|absent)\b'
ITEM = re.compile(r'^\s*(?:[-*+]|\d+\.)\s')


def skills_of(package_dir):
    """Return the package name and its skill folders, from skill-package.json."""
    manifest = json.loads((package_dir / 'skill-package.json').read_text())
    folders = {Path(skill['path']).name for skill in manifest.get('skills', [])}
    return manifest['repository']['name'], folders


def read_registry(registry):
    """Return the optional skills of each package and the package of each listed skill."""
    optional, owner = {}, {}
    for entry in json.loads(registry.read_text())['packages']:
        choices = {skill['choice'] for skill in entry.get('optional_skills', [])}
        optional[entry['name']] = choices
        for skill in [*entry.get('required_skills', []), *choices]:
            owner[skill] = entry['name']
    return optional, owner


def optional_skills(registry, package):
    return read_registry(registry)[0].get(package, set())


def own_skill(path, package_dir):
    """The skill folder that holds this file, or None for a file outside the skills."""
    try:
        parts = Path(path).resolve().relative_to(package_dir.resolve()).parts
    except ValueError:
        return None
    return parts[2] if parts[:2] == ('.agents', 'skills') and len(parts) > 3 else None


def blocks(text):
    """Split a page into paragraphs, and each list item into its own block."""
    out, block = [], []
    for line in text.split('\n'):
        if not line.strip() or ITEM.match(line):
            if block:
                out.append('\n'.join(block))
            block = [line] if line.strip() else []
        else:
            block.append(line)
    if block:
        out.append('\n'.join(block))
    return out


def has_fallback(block, skill, packages):
    """True when a sentence of the block says what to do when this skill, or its package, is not installed."""
    flat = ' '.join(block.split())
    # A dot inside inline code does not end a sentence.
    flat = re.sub(r'`[^`]*`', lambda m: m.group(0).replace('.', '_'), flat)
    names = [skill.replace('_', ' '), skill, *packages]
    subject = '|'.join([*(re.escape(name) + r'(?: skill| package)?' for name in names), r'that skill'])
    # The skill, its package or "that skill" must be the subject of "is not installed", "is missing" or "is absent".
    pattern = re.compile(r'(?<![A-Za-z0-9_-])(?:' + subject + r')(?![A-Za-z0-9_-])' + NEGATIVE, re.I)
    return bool(pattern.search(flat))


def relative_links(path, package_dir, block, packages):
    """Return the `../X/...` links of a file in a skill that land in another skill folder X of the target."""
    found = []
    skills_root = (package_dir / '.agents/skills').resolve()
    for match in RELATIVE.finditer(block):
        landed = Path(os.path.normpath(Path(path).resolve().parent / match.group(0)))
        try:
            parts = landed.relative_to(skills_root).parts
        except ValueError:
            # A file outside the skills: the link is about the folder of the file. Count it for a known skill only.
            if match.group(1) in packages:
                found.append((match.group(1), match.group(2)))
            continue
        # A link inside the skill of the file itself is the business of that skill's own tests.
        if parts and parts[0] != own_skill(path, package_dir):
            rest = '/' + '/'.join(parts[1:]) if len(parts) > 1 else ''
            found.append((parts[0], rest))
    return found


def problems(path, text, package_dir, optional=(), resolve=(), owner=None):
    package, own = skills_of(package_dir)
    owner = dict(owner or {})
    roots = {folder: package_dir for folder in own}
    packages = {folder: package for folder in own}
    for other in resolve:
        name, folders = skills_of(other)
        for folder in folders:
            roots.setdefault(folder, other)
            packages.setdefault(folder, name)
    for skill, name in owner.items():
        packages.setdefault(skill, name)
    home = own_skill(path, package_dir)
    found = []
    for block in blocks(text):
        links = [(m.group(1), m.group(2)) for m in ROOTED.finditer(block)]
        links += [(m.group(1), m.group(2)) for m in BARE.finditer(block)]
        links += relative_links(path, package_dir, block, packages)
        for skill, rest in links:
            rest = (rest or '').rstrip('.')
            link = f'.agents/skills/{skill}{rest}'
            may_be_missing = skill not in own or (skill in optional and skill != home)
            # A package name covers a link only to a skill that is always part of that package.
            covers = [packages[skill]] if skill in packages and skill not in (optional if skill in own else set()) else []
            if may_be_missing and not has_fallback(block, skill, covers):
                found.append(f'{path}: the link {link} names a skill that may not be installed, and its '
                             f'paragraph has no fallback such as "If the {skill} skill is not installed, ..."')
            if skill in roots:
                parts = Path(link).parts
                if '..' in parts or not (roots[skill] / link).exists():
                    found.append(f'{path}: the link {link} names a path that the {skill} skill does not have')
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('files', nargs='+', type=Path)
    parser.add_argument('--package', type=Path, required=True, help='the package that ships the files')
    parser.add_argument('--resolve', type=Path, action='append', default=[],
                        help='a clone of another package; links into its skills must name paths it has')
    parser.add_argument('--registry', type=Path, default=ROOT / 'stack.json')
    args = parser.parse_args(argv)
    for directory in [args.package, *args.resolve]:
        if not (directory / 'skill-package.json').is_file():
            print(f'No skill package at {directory}', file=sys.stderr)
            return 1
    package, _ = skills_of(args.package)
    optional, owner = read_registry(args.registry)
    found = [line for path in args.files
             for line in problems(path, path.read_text(), args.package, optional.get(package, set()), args.resolve, owner)]
    for line in found:
        print(line, file=sys.stderr)
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())
