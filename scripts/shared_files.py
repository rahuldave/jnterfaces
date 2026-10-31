"""The parts that the owners of a project add to its shared files, and the record of each part in .agents/stack.json.

notes/plan-shared-files.md and notes/plan-snippets.md give the decisions. An owner is jnterfaces, a profile such as
python, a tool of a profile such as python:lint, or a role such as tasker. Each owner adds its part to a shared file
and takes out only its part:

- A line file, such as .gitignore, holds one block of lines for each owner, at the start of the file.
- A file with an include, such as ruff.toml, gets one include, such as extend = "...", and the installer writes the
  settings of the owner in an own file under .agents/config/.
- A file without an include, such as ty.toml, gets the units of the owner: keys, items of a list, or hooks.

The key parts in .agents/stack.json records each unit: its state, the owner that holds it and each owner that needs
it. A removal reads the record, never the log. .agents/stack-log.jsonl is a history for people.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import time

import edit_toml
import edit_yaml

LOG = Path('.agents/stack-log.jsonl')
PENDING = Path('.agents/stack-pending.json')
RECORD = '.agents/stack.json'
FORMATS = json.loads((Path(__file__).resolve().parent / 'formats.json').read_text())
# The history and the file of a run that stopped describe one clone, so git does not keep them.
JNTERFACES_LINES = {'.gitignore': [LOG.as_posix(), PENDING.as_posix()]}
# The files and folders of the installer itself, which no owner may name.
RESERVED = (LOG.as_posix(), RECORD, PENDING.as_posix())
RESERVED_FOLDERS = ('.agents/just/', '.agents/config/', '.agents/instructions/')
OWNER = r'[a-z][a-z0-9_-]*(?::[a-z][a-z0-9_-]*)?'
STATES = ('added', 'present', 'declined', 'released')
UNIT_STATES = ('added', 'present', 'declined', 'kept', 'seeded')
# The keys of the record that are not units of the file.
WHOLE, FILE, STARTER, SEED = '(whole file)', '(file)', '(starter)', '(seed)'
OWN = '(own file)'
EXTENDS_STRING = '(extends string)'
MARK = re.compile(r'^.+ is a part of .+\. The record is parts\..+$')


class SharedError(Exception):
    pass


class Owner:
    """One owner, in the order of the run: jnterfaces, then each profile followed by its tools, then each role."""

    def __init__(self, key, about, package, commit, removal, lines=None, files=None, parts=None, units=None,
                 starter=None, skipped=None):
        self.key, self.about, self.package, self.commit, self.removal = key, about, package, commit, removal
        # lines: {path: [line, ...]}. files: {path: (source file, skip_if guard or None)}, the whole files of a
        # package from before the parts. parts: {shared file: source file}. units: {shared file: {unit: value}}, the
        # units that the installer adds without a source file. starter: {path: source file}. skipped: {shared file:
        # skip_if guard}.
        self.lines, self.files, self.parts = lines or {}, files or {}, parts or {}
        self.units, self.starter, self.skipped = units or {}, starter or {}, skipped or {}


class Style:
    """The comments of the blocks in a line file: # in .gitignore, <!-- --> in Markdown."""

    def __init__(self, open_, close):
        self.open, self.close = open_, close
        o, c = re.escape(open_), re.escape(close)
        self.begin = re.compile(rf'^{o}begin lines of ({OWNER}),')
        self.written = re.compile(rf'^{o}Last written to ')
        self.end = re.compile(rf'^{o}end lines of ({OWNER})\.')
        # The whole begin and end comments, as the installer writes them. A comment that lost its partner is dropped
        # only when it has this form, so that a comment of yours that starts in the same way stays.
        self.own_begin = re.compile(rf'^{o}begin lines of ({OWNER}), .+, from \S+ at \S+\.{c}$')
        self.own_end = re.compile(rf'^{o}end lines of ({OWNER})\. (`just uninstall \S+` removes them|They stay while .+)\.{c}$')

    def is_rule(self, line):
        """True for a line that the file reads: not blank and not a comment."""
        stripped = line.strip()
        return bool(stripped) and not stripped.startswith(self.open.strip())

    def comment(self, text):
        return f'{self.open}{text}{self.close}'


HASH = Style('# ', '')
HTML = Style('<!-- ', ' -->')
BEGIN, WRITTEN, END = HASH.begin, HASH.written, HASH.end


def style_of(path):
    return HTML if FORMATS.get(path, {}).get('comments') == 'html' else HASH


def is_rule(line):
    return HASH.is_rule(line)


def normal(line):
    """The form of a line for the loose match: no spaces or carriage return at the ends, and no / at the end."""
    line = line.strip()
    return line.rstrip('/') or line


def wide(line):
    """A wider form, for a possible match only: also no / or **/ at the start."""
    line = normal(line)
    while line.startswith(('/', '**/')):
        line = line[1:] if line.startswith('/') else line[3:]
    return line


def rows_of(text):
    """The rows of a text, each with its own line ending. Only a newline ends a row."""
    return re.findall(r'[^\n]*\n|[^\n]+$', text)


def bare(row):
    """A row without its line ending."""
    return row[:-2] if row.endswith('\r\n') else row[:-1] if row.endswith('\n') else row


def digest(data):
    return hashlib.sha256(data).hexdigest()


def plain(path):
    """True for a relative path inside the project: not absolute, with no empty, . or .. part, and not in .git."""
    if not isinstance(path, str) or not path or path.startswith('/') or '\\' in path or '\x00' in path:
        return False
    return all(part not in ('', '.', '..') and part.casefold() != '.git' for part in path.split('/'))


def reserved(path):
    """True for a file of the installer. The letter case does not count, because macOS and Windows ignore it."""
    folded = path.casefold()
    return folded in RESERVED or folded.startswith(RESERVED_FOLDERS)


def linked(target, path):
    """The first part of the path, the file or a folder on the way, that is a symlink, or None."""
    current = target
    for part in Path(path).parts:
        current = current / part
        if current.is_symlink():
            return current.relative_to(target).as_posix()
    return None


def one_line(text):
    return ' '.join(str(text).split())


def write(file, data):
    """Write a file through a file next to it and a rename, so that a run that stops leaves the old file whole."""
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_name(f'.{file.name}.{secrets.token_hex(4)}.tmp')
    temporary.write_bytes(data)
    if file.exists():
        shutil.copymode(file, temporary)
    os.replace(temporary, file)


def read_text(target, path):
    file = target / path
    return file.read_bytes().decode('utf-8', errors='surrogateescape') if file.exists() else None


def own_path(owner, path, source):
    """The own file of a part: .agents/instructions/ROLE.md for the instructions of a role, else
    .agents/config/OWNER/SOURCE. Two tools of one profile that ship one source file share one own file."""
    if FORMATS[path].get('include') == 'pointer':
        return f'.agents/instructions/{owner.key.replace(":", "-")}.md'
    return f'.agents/config/{owner.key.partition(":")[0]}/{source.name}'


def include_value(path, own):
    include = FORMATS[path]['include']
    if include == 'extends':
        return './' + own
    if include == 'pointer':
        return f'Read and follow `{own}`.'
    return own


def include_unit(path, own):
    include = FORMATS[path]['include']
    if include == 'extends':
        return f'extends[{json.dumps(include_value(path, own))}]'
    return include


def target_of(target, path):
    """The file that a part of path goes to: biome.jsonc instead of biome.json when it exists."""
    instead = FORMATS.get(path, {}).get('instead')
    return instead if instead and (target / instead).exists() and not (target / path).exists() else path


def held_in(target, path, units):
    """The file that holds the units of path: the file that the record names, else path. Only path and its instead
    file, such as biome.jsonc, can be named. A record of an older installer names no file, so the units that it added
    are looked for in the instead file, when path does not hold them."""
    instead = FORMATS.get(path, {}).get('instead')
    named = [entry['file'] for unit, entry in units.items() if not unit.startswith('(') and entry.get('file')]
    if named:
        return named[0] if named[0] in (path, instead) else path
    added = [unit for unit, entry in units.items() if not unit.startswith('(') and entry['state'] == 'added']
    if not instead or not added or not (target / instead).exists() or linked(target, instead):
        return path

    def holds(name):
        try:
            current = read_units(FORMATS[path]['format'], read_text(target, name), added, FORMATS[path])
        except SharedError:
            return False
        return any(unit in current for unit in added)
    if (target / path).exists() and not linked(target, path) and holds(path):
        return path
    return instead if holds(instead) else path


# The log of the installer from before the record, which the first run replays once.

def strings(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def entry_ok(entry):
    """True when an entry has the shape that replay reads."""
    if not isinstance(entry, dict) or not isinstance(entry.get('id'), str):
        return False
    lines, files = entry.get('lines', {}), entry.get('files', {})
    return (isinstance(lines, dict) and isinstance(files, dict)
            and all(plain(path) for path in list(lines) + list(files))
            and all(re.fullmatch(OWNER, owner) for owners in lines.values() if isinstance(owners, dict)
                    for owner in owners)
            and all(isinstance(owners, dict) and all(
                isinstance(change, dict) and all(state in STATES and strings(rows) for state, rows in change.items())
                for change in owners.values()) for owners in lines.values())
            and all(isinstance(change, dict) and strings(change.get('owners', []))
                    and isinstance(change.get('hash', ''), (str, type(None))) for change in files.values()))


def read_log(target):
    """The entries of the log of the old installer, in the order of the file. An entry of the history that this
    installer writes has no status, so it is left out."""
    found = linked(target, LOG)
    if found:
        raise SharedError(f'{found} is a symlink; make it a folder or a file')
    path = target / LOG
    if not path.exists():
        return []
    entries = []
    for number, text in enumerate(path.read_text().splitlines(), 1):
        if not text.strip():
            continue
        try:
            entry = json.loads(text)
        except ValueError as exc:
            raise SharedError(f'{LOG} line {number} is not JSON: {exc}')
        if isinstance(entry, dict) and 'status' not in entry and entry.get('kind') == 'history':
            continue
        if not entry_ok(entry):
            raise SharedError(f'{LOG} line {number} is not an entry of the log')
        entries.append(entry)
    return entries


def replay(entries):
    """The claims of the old log: lines {path: {owner: {normal line: state}}}, files {path: claim}, and the paths
    with an unsure claim. A claim is unsure while the entry that made it has not finished."""
    finished = {entry['id'] for entry in entries if entry.get('status') == 'finished'}
    lines, files, unsure = {}, {}, set()
    for entry in entries:
        if entry.get('status') == 'finished':
            continue
        sure = entry['id'] in finished
        for path, owners in entry.get('lines', {}).items():
            for owner, change in owners.items():
                claims = lines.setdefault(path, {}).setdefault(owner, {})
                for state in STATES:
                    for line in change.get(state, []):
                        key = normal(line)
                        if state == 'released':
                            claims.pop(key, None)
                        else:
                            claims[key] = state
            if not sure:
                unsure.add(path)
        for path, change in entry.get('files', {}).items():
            if change.get('owners') or change.get('declined'):
                files[path] = {'owners': list(change.get('owners', [])), 'hash': change.get('hash'),
                               'declined': bool(change.get('declined'))}
            else:
                files.pop(path, None)
            if not sure:
                unsure.add(path)
    return lines, files, unsure


def from_log(target, package_records):
    """The record of the parts from the old log and from the records of the role packages: the parts, the paths
    with an unsure claim, and the id of the last entry of the log. package_records is {role: record}."""
    entries = read_log(target)
    lines, files, unsure = replay(entries)
    parts = {}
    for path, owners in lines.items():
        parts[path] = entries_of_claims(owners)
    for path, claim in files.items():
        parts.setdefault(path, {})[WHOLE] = {
            'state': 'declined' if claim['declined'] else 'added', 'needs': claim['owners'], 'hash': claim['hash']}
    for role, record in package_records.items():
        seeds = record.get('seeds') if isinstance(record.get('seeds'), dict) else {}
        for path, value in seeds.items():
            if plain(path) and isinstance(value, str):
                parts.setdefault(path, {})[SEED] = {'state': 'seeded', 'holder': role, 'needs': [role], 'hash': value}
        merged = record.get('merged_hooks') if isinstance(record.get('merged_hooks'), dict) else {}
        for path, ours in merged.items():
            if not plain(path) or not isinstance(ours, dict):
                continue
            for unit, value in hook_units(ours).items():
                parts.setdefault(path, {})[unit] = {'state': 'added', 'holder': role, 'needs': [role], 'value': value}
    last = entries[-1]['id'] if entries else None
    return parts, unsure, last


def entries_of_claims(owners):
    """The units of a line file from the claims of each owner {owner: {normal line: state}}. At most one owner
    added a line; the other owners that need it have it as present."""
    units = {}
    for owner, claims in owners.items():
        for key, state in claims.items():
            if state == 'released':
                continue
            entry = units.setdefault(key, {'state': None, 'needs': []})
            entry['needs'].append(owner)
            if state == 'added':
                entry['state'], entry['holder'] = 'added', owner
            elif entry['state'] is None or (entry['state'] == 'declined' and state == 'present'):
                entry['state'] = state
    return units


def claims_of_entries(units):
    """The claims of each owner {owner: {normal line: state}} from the units of a line file."""
    owners = {}
    for key, entry in units.items():
        if key.startswith('('):
            continue
        for owner in entry.get('needs', []):
            state = entry['state']
            if state == 'added' and entry.get('holder') != owner:
                state = 'present'
            if state in STATES:
                owners.setdefault(owner, {})[key] = state
    return owners


def history(target, command, sources, changes):
    """Add an entry to the history of the runs. Nothing reads it to decide what to do."""
    found = linked(target, LOG)
    if found:
        return
    path = target / LOG
    path.parent.mkdir(exist_ok=True)
    entry = {'kind': 'history', 'id': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()) + '-' + secrets.token_hex(2),
             'command': one_line(command), 'sources': sources, 'changes': changes}
    with path.open('a') as handle:
        handle.write(json.dumps(entry, sort_keys=True) + '\n')


# Line files.

def parse(text, owners, style=HASH):
    """Split a line file into its blocks {owner: [row, ...]}, the rows of the user in order, and the owners whose
    block lacks its begin or end comment. Only the comments of the owners in owners mark a block, so a comment of
    yours that looks like one stays yours. The rows of a broken block are the user's, and its comments go."""
    rows = rows_of(text)
    blocks, user, broken = {}, [], []

    def mark(pattern, row):
        match = pattern.match(bare(row))
        return match.group(1) if match and match.group(1) in owners else None
    index = 0
    while index < len(rows):
        owner = mark(style.begin, rows[index])
        if owner:
            close = None
            for later in range(index + 1, len(rows)):
                if mark(style.begin, rows[later]) or mark(style.end, rows[later]):
                    close = later if mark(style.end, rows[later]) == owner else None
                    break
            first = index + 1
            if first < len(rows) and style.written.match(bare(rows[first])):
                first += 1
            if close is None:
                if style.own_begin.match(bare(rows[index])):
                    broken.append(owner)
                    index = first
                else:
                    user.append(rows[index])
                    index += 1
                continue
            blocks.setdefault(owner, []).extend(rows[first:close])
            index = close + 1
            continue
        if mark(style.end, rows[index]) and style.own_end.match(bare(rows[index])):
            broken.append(mark(style.end, rows[index]))
        else:
            user.append(rows[index])
        index += 1
    return blocks, user, broken


def header(owner, path, command, style=HASH):
    return [style.comment(f'begin lines of {owner.key}, {owner.about}, from {owner.package} at {owner.commit[:7]}.'),
            style.comment(f'Last written to {path} by `{one_line(command)}`. The record is parts."{path}" in '
                          f'{RECORD}.')]


def footer(owner, style=HASH):
    return style.comment(f'end lines of {owner.key}. {owner.removal}')


def plan_line_file(path, text, active, known, claims, unsure, style=HASH):
    """Work out one line file for the active owners, in order, with the claims of the record for this file. known
    holds every owner of the run, also the ones that go, so that their own comments go with them.

    Returns the blocks [(owner, lines, same)], the rows of the user, the new claims {owner: {normal line: state}},
    the notes, and whether a row of the user went. same is True when a block keeps its lines."""
    keys = {owner.key for owner in known} | set(claims)
    blocks, user, _ = parse(text or '', keys, style)
    comments = {(owner.key, line.strip()) for owner in known for line in owner.lines.get(path, [])
                if not style.is_rule(line)}
    notes = []
    claimed = {key for owner_claims in claims.values() for key in owner_claims}
    # A block of a known owner with lines that the record does not know, after a merge that kept the blocks of both
    # sides and the record of one side: the installer takes them into the record from the file.
    for owner_key, rows in blocks.items():
        lines_of = {normal(line) for owner in known if owner.key == owner_key
                    for line in owner.lines.get(path, []) if style.is_rule(line)}
        for row in rows:
            key = normal(bare(row))
            if style.is_rule(bare(row)) and key in lines_of and key not in claimed:
                claims.setdefault(owner_key, {})[key] = 'added'
                claimed.add(key)
                notes.append(f'{path}: {key} is in the block of {owner_key} and not in the record, so the installer '
                             f'takes it into the record.')
    # D4 step 5: a row in a block that the owner of the block did not add, a line or a comment, is the user's. It
    # moves to the start of the user part before the owners look for their lines, so that an owner counts it as present.
    moved_out = []
    for owner_key in list(blocks):
        kept = []
        for row in blocks[owner_key]:
            line = bare(row)
            if not line.strip():
                continue
            mine = claims.get(owner_key, {}).get(normal(line)) == 'added'
            if style.is_rule(line) and mine or not style.is_rule(line) and (owner_key, line.strip()) in comments:
                kept.append(row)
            else:
                moved_out.append(row)
        blocks[owner_key] = kept
    user = moved_out + user
    user_rules = {}
    for row in user:
        if style.is_rule(bare(row)):
            user_rules.setdefault(normal(bare(row)), []).append(row)
    in_blocks = {normal(bare(row)) for body in blocks.values() for row in body if style.is_rule(bare(row))}
    anywhere = in_blocks | set(user_rules)

    def sure(owner_key, key):
        return (path, owner_key, key) not in unsure
    # D4 step 3: a line that the record knows, from a run that finished, and that the file lacks, was deleted by you.
    deleted = {key for owner_key, owner_claims in claims.items() for key in owner_claims
               if key not in anywhere and sure(owner_key, key)}
    homed, wanted, new_claims = {}, set(), {}
    for owner in active:
        mine = claims.get(owner.key, {})
        for line in owner.lines.get(path, []):
            if not style.is_rule(line):
                continue
            key = normal(line)
            wanted.add(key)
            before = mine.get(key)
            if key in user_rules:
                # D5: a line that the owner added and that a person moved out stays added. A copy that a person
                # wrote while the block still holds the line is theirs.
                moved = before == 'added' and sure(owner.key, key) and key not in in_blocks
                state = 'added' if moved else 'present'
            elif key in homed:
                state = 'present'
            elif key in deleted:
                state = 'declined'
                if before != 'declined':
                    notes.append(f'{path}: {line} is not in the file, so {owner.key} records it as declined and does '
                                 f'not add it again. To have it back, add the line after the blocks.')
            else:
                homed[key] = (owner.key, line)
                state = 'added'
            new_claims.setdefault(owner.key, {})[key] = state
    # D5: a line that an owner added, that no active owner needs, and that is outside the blocks.
    removed = set()
    for owner_key, owner_claims in claims.items():
        for key, state in owner_claims.items():
            if state != 'added' or key in wanted or key in in_blocks or not sure(owner_key, key):
                continue
            copies = user_rules.get(key, [])
            if len(copies) == 1:
                removed.add(key)
            elif copies:
                notes.append(f'{path}: {key} of {owner_key} is in the file {len(copies)} times, so it stays. Remove '
                             f'it by hand if no one needs it.')
            else:
                maybe = [bare(row).strip() for row in user if style.is_rule(bare(row))
                         and wide(bare(row)) == wide(key)]
                if maybe:
                    notes.append(f'{path}: {owner_key} added {key}, and the file has {maybe[0]}, which may be the '
                                 f'same line. It stays; remove it by hand if no one needs it.')
    out = []
    for owner in active:
        body = [line for line in owner.lines.get(path, []) if style.is_rule(line)
                and homed.get(normal(line), (None,))[0] == owner.key]
        if not body:
            continue
        body = [line for line in owner.lines.get(path, []) if not style.is_rule(line)] + body
        old = blocks.get(owner.key)
        same = old is not None and [bare(r).strip() for r in old if bare(r).strip()] == [line.strip() for line in body]
        out.append((owner, body, same))
    rest = []
    took = False
    for row in user:
        if style.is_rule(bare(row)) and normal(bare(row)) in removed:
            removed.discard(normal(bare(row)))
            took = True
            continue
        rest.append(row)
    return out, rest, new_claims, notes, took


def render(path, out, rest, old_text, command, owners, took, style=HASH):
    """The text of a line file from its blocks and the rows of the user. A run that changes no block and no row of the
    user returns the old text as it is. A block whose lines did not change keeps its old comments. Each row of the
    user keeps its own line ending, and the new rows take the ending that most rows of the file have."""
    old_blocks, old_user, broken = parse(old_text or '', owners, style)
    if (old_text is not None and not broken and rest == old_user and [o.key for o, _, _ in out] == list(old_blocks)
            and all(same for _, _, same in out)):
        return old_text
    old_rows = rows_of(old_text or '')
    crlf = sum(row.endswith('\r\n') for row in old_rows)
    lf = sum(row.endswith('\n') for row in old_rows) - crlf
    newline = '\r\n' if crlf > lf else '\n'
    rows = []
    for owner, body, same in out:
        start = next((index for index, row in enumerate(old_rows)
                      if style.begin.match(bare(row)) and style.begin.match(bare(row)).group(1) == owner.key), None)
        written = start is not None and start + 1 < len(old_rows) and style.written.match(bare(old_rows[start + 1]))
        # A block keeps its comments while its lines stay, unless they name an entry of the old log.
        if same and written and 'stack-log.jsonl' not in old_rows[start + 1]:
            rows += [bare(row) + newline for row in old_rows[start:start + 2]]
        else:
            rows += [line + newline for line in header(owner, path, command, style)]
        rows += [line + newline for line in body] + [footer(owner, style) + newline]
    if rows:
        if rest and bare(rest[0]).strip():
            rows.append(newline)
    elif old_blocks or took:
        # The last block or line of an owner went: drop the blank rows that separated it from the rows of the user.
        while rest and not bare(rest[0]).strip():
            rest = rest[1:]
    rows += rest
    if rows and not rows[-1].endswith('\n'):
        rows[-1] += newline
    if not any(bare(row).strip() for row in rows) and (old_text is None or old_blocks or took):
        return None
    return ''.join(rows)


# Whole files, from the packages that ship the old key files.

def plan_whole_file(target, path, active, claim, sure):
    """Work out one whole file. Returns (action, owners, source, notes): action is write, own, remove, release,
    decline or None."""
    needers, source, notes = [], None, []
    file = target / path
    for owner in active:
        if path not in owner.files:
            continue
        origin, guard = owner.files[path]
        # A skip_if rule stops a new file only. An owner of a file that is there keeps it.
        if (guard and not linked(target, guard[0]) and (target / guard[0]).is_file()
                and guard[1] in (target / guard[0]).read_text(errors='replace')):
            if not (claim and file.exists()):
                notes.append(f'Left out {path}: {guard[0]} already has {guard[1]}')
                continue
        needers.append(owner.key)
        source = source or origin
    if claim and claim.get('declined'):
        # You deleted the file. It stays out while it is gone; a file that you put back is yours.
        return ('release' if file.exists() or not needers else None), [], None, notes
    if needers:
        if not file.exists():
            if claim and sure:
                notes.append(f'{path} is gone, so the installer does not write it again')
                return 'decline', [], None, notes
            return 'write', needers, source, notes
        if claim and (claim['owners'] != needers or not sure):
            return 'own', needers, None, notes
        return None, needers, None, notes
    if not claim:
        return None, [], None, notes
    if not file.exists():
        return 'release', [], None, notes
    if digest(file.read_bytes()) == claim.get('hash'):
        return 'remove', [], None, notes
    notes.append(f'{path} stays: you edited it, and its owners {", ".join(claim["owners"])} are gone')
    return 'release', [], None, notes


# Files of units: TOML, YAML and JSON.

def hook_units(settings):
    """The units of a hooks file: one for each hook, named by its event, the keys of its group and its command."""
    found = {}
    hooks = settings.get('hooks') if isinstance(settings, dict) else None
    for event, groups in (hooks.items() if isinstance(hooks, dict) else []):
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict) or not isinstance(group.get('hooks'), list):
                continue
            selector = {key: value for key, value in group.items() if key != 'hooks'}
            for hook in group['hooks']:
                if isinstance(hook, dict) and isinstance(hook.get('command'), str):
                    unit = f'hooks.{event} {json.dumps(selector, sort_keys=True)} {hook["command"]}'
                    found[unit] = {'event': event, 'group': selector, 'hook': hook}
    return found


def json_load(text):
    try:
        data = json.loads(text) if text and text.strip() else {}
    except ValueError as exc:
        raise SharedError(f'it is not plain JSON ({exc})')
    if not isinstance(data, dict):
        raise SharedError('it is not a JSON object')
    return data


def json_dump(data, old):
    """JSON text with the indent of the old text, two spaces when there is none."""
    indent = 2
    for line in (old or '').splitlines()[1:]:
        if line.strip():
            indent = (len(line) - len(line.lstrip())) or 2
            break
    return json.dumps(data, indent=indent, ensure_ascii=False) + '\n'


def read_units(kind, text, names, spec):
    """The value of each named unit that the text has."""
    if text is None:
        return {}
    if kind == 'toml':
        try:
            return edit_toml.read(text, names)
        except edit_toml.EditError as exc:
            raise SharedError(str(exc))
    if kind == 'yaml':
        try:
            return edit_yaml.read(text, names)
        except edit_yaml.EditError as exc:
            raise SharedError(str(exc))
    data = json_load(text)
    if kind == 'hooks':
        found = hook_units(data)
        return {unit: found[unit] for unit in names if unit in found}
    found = {}
    listed = data.get('extends')
    listed = [listed] if isinstance(listed, str) else listed if isinstance(listed, list) else []
    for unit in names:
        item = json.loads(unit[len('extends['):-1]) if unit.startswith('extends[') else None
        if item is not None and item in listed:
            found[unit] = item
    return found


def apply_units(kind, text, adds, removes, spec):
    """The new text after the change, and what each added unit made. adds: [(unit, value, mark, rows)]. removes:
    [(unit, entry)]."""
    if kind == 'toml':
        try:
            return edit_toml.plan(text or '', [(u, v, m) for u, v, m, _ in adds],
                                  [(u, e.get('made', [])) for u, e in removes])
        except edit_toml.EditError as exc:
            raise SharedError(str(exc))
    if kind == 'yaml':
        try:
            return edit_yaml.plan(text or '', [(u, rows, m) for u, _, m, rows in adds], [u for u, _ in removes],
                                  MARK), {}
        except edit_yaml.EditError as exc:
            raise SharedError(str(exc))
    data = json_load(text)
    made = {}
    if kind == 'hooks':
        hooks = data.get('hooks', {})
        if not isinstance(hooks, dict):
            raise SharedError('its hooks key is not an object')
        for unit, entry in removes:
            value = entry['value']
            groups = hooks.get(value['event'])
            if not isinstance(groups, list):
                continue
            for group in groups:
                if (isinstance(group, dict) and isinstance(group.get('hooks'), list)
                        and {k: v for k, v in group.items() if k != 'hooks'} == value['group']):
                    group['hooks'] = [hook for hook in group['hooks']
                                      if not (isinstance(hook, dict) and hook.get('command') == value['hook']['command'])]
            groups[:] = [group for group in groups if not (isinstance(group, dict) and group.get('hooks') == [])]
            if not groups:
                del hooks[value['event']]
        for unit, value, _, _ in adds:
            groups = hooks.setdefault(value['event'], [])
            if not isinstance(groups, list):
                raise SharedError(f'the hooks of {value["event"]} are not a list')
            group = next((group for group in groups if isinstance(group, dict) and isinstance(group.get('hooks'), list)
                          and {k: v for k, v in group.items() if k != 'hooks'} == value['group']), None)
            if group is None:
                group = dict(value['group'], hooks=[])
                groups.append(group)
            group['hooks'].append(value['hook'])
        if hooks:
            data['hooks'] = hooks
        else:
            data.pop('hooks', None)
        return json_dump(data, text) if data or text else '', made
    listed = data.get('extends')
    was_string = isinstance(listed, str)
    listed = [listed] if was_string else list(listed) if isinstance(listed, list) else None
    if listed is None and 'extends' in data:
        raise SharedError('its extends key is not a list or a string')
    gone = {json.loads(unit[len('extends['):-1]) for unit, _ in removes}
    if listed is not None:
        listed = [item for item in listed if item not in gone]
    new = [value for _, value, _, _ in adds]
    if new:
        if listed is None:
            made = {unit: ['extends'] for unit, _, _, _ in adds}
        elif was_string:
            # Your extends was one string. It becomes a list, and a string again when the part goes.
            made = {unit: [EXTENDS_STRING] for unit, _, _, _ in adds}
        # The parts come first, so that a later extends of yours wins over them.
        listed = new + [item for item in (listed or []) if item not in new]
    if listed and not new and len(listed) == 1 and any(
            EXTENDS_STRING in entry.get('made', []) for _, entry in removes):
        data['extends'] = listed[0]
    elif listed:
        data['extends'] = listed
    elif listed is not None and any('extends' in entry.get('made', []) for _, entry in removes):
        del data['extends']
    elif listed is not None:
        data['extends'] = []
    return (json_dump(data, text) if data else ''), made


def empty_units(kind, text):
    """True when nothing is left in the text: no data and no comment."""
    if text is None or not text.strip():
        return True
    if kind in ('toml', 'yaml'):
        rows = [row.strip() for row in text.splitlines() if row.strip()]
        if any(not row.startswith('#') for row in rows):
            return False
        return not rows
    try:
        return json.loads(text) == {}
    except ValueError:
        return False


def mark_of(owner, unit, path):
    return (f'{unit} is a part of {owner.key}, {owner.about}, from {owner.package} at {owner.commit[:7]}. The record '
            f'is parts."{path}" in {RECORD}.')


def desired_units(target, path, active, notes):
    """The units that the active owners need in path, in order: {unit: {value, needs, owner, rows, own, source}}.
    Two owners that need one unit with two values stop the run."""
    spec = FORMATS[path]
    want = {}
    for owner in active:
        found = {}
        if path in owner.parts and path not in owner.skipped:
            source = owner.parts[path]
            text = source.read_bytes().decode('utf-8', errors='surrogateescape')
            if spec.get('include'):
                own = own_path(owner, path, source)
                found[include_unit(path, own)] = {'value': include_value(path, own), 'own': own, 'data': source.read_bytes()}
            elif spec['format'] == 'toml':
                try:
                    found = {unit: {'value': value} for unit, value in edit_toml.units(text).items()}
                except edit_toml.EditError as exc:
                    raise SharedError(f'The source {source} of {owner.key} for {path}: {exc}')
            elif spec['format'] == 'yaml':
                try:
                    found = {unit: {'value': value, 'rows': rows} for unit, (value, rows)
                             in edit_yaml.units(text, spec['list'], spec['key']).items()}
                except edit_yaml.EditError as exc:
                    raise SharedError(f'The source {source} of {owner.key} for {path}: {exc}')
            elif spec['format'] == 'hooks':
                try:
                    found = {unit: {'value': value} for unit, value in hook_units(json.loads(text)).items()}
                except ValueError as exc:
                    raise SharedError(f'The source {source} of {owner.key} for {path} is not JSON: {exc}')
        elif path in owner.skipped:
            guard = owner.skipped[path]
            notes.append(f'Left out the part of {owner.key} in {path}: {guard[0]} already has {guard[1]}')
        for unit, value in owner.units.get(path, {}).items():
            found[unit] = {'value': value}
        for unit, item in found.items():
            if unit in want:
                if want[unit]['value'] != item['value'] or want[unit].get('data') != item.get('data'):
                    raise SharedError(f'{path}: {want[unit]["owner"]} and {owner.key} need {unit} with two values. '
                                      f'Turn one of them off.')
                want[unit]['needs'].append(owner.key)
            else:
                want[unit] = dict(item, needs=[owner.key], owner=owner.key, about=owner)
    return want


def plan_unit_file(path, kind, text, want, old, unsure, migrating, notes):
    """Work out one file of units. Returns the new text, the new entries, and the own files that the includes need
    {own path: (data, needs)}."""
    names = list(dict.fromkeys(list(want) + [unit for unit in old if not unit.startswith('(')]))
    current = read_units(kind, text, names, FORMATS[path])
    adds, removes, entries, owns, adopted_made = [], [], {}, {}, {}

    def ours(unit, entry):
        """The file holds the value that the installer wrote: the value of the record, or a value of a run that
        stopped before. 'earlier' holds those values, and only while no run has worked out the file since: a file
        that a run could not read keeps its entries as they were."""
        return unit in current and (current[unit] == entry.get('value') or current[unit] in entry.get('earlier', []))

    def as_written(unit, entry):
        return dict(entry, value=current[unit]) if unit in current else entry
    for unit, item in want.items():
        entry = old.get(unit)
        value = item['value']
        here = unit in current
        state = None
        if entry and entry['state'] == 'added':
            if ours(unit, entry):
                state = 'added'
                if value != current[unit]:
                    removes.append((unit, as_written(unit, entry)))
                    adds.append(unit)
            elif here:
                state = 'present'
                notes.append(f'{path}: you changed {unit}, so it is yours now and stays as you set it.')
            elif unit in unsure:
                state = 'added'
                adds.append(unit)
            else:
                state = 'declined'
                notes.append(f'{path}: {unit} of {item["owner"]} is gone, so the installer records it as declined and '
                             f'does not add it again.')
        elif entry and entry['state'] in ('present', 'declined'):
            state = 'present' if here else 'declined'
        elif here:
            marked = re.search(rf'^\s*# {re.escape(unit)} is a part of {re.escape(item["owner"])},', text or '', re.M)
            state = 'added' if (marked or (migrating and adopt(path, unit, text))) and current[unit] == value else 'present'
            if state == 'added' and kind == 'toml':
                # The tables on the way go with the unit when they are empty, as when the installer made them.
                path_of = edit_toml.parse_name(unit)[0]
                adopted_made[unit] = [edit_toml.key_text(path_of[:depth]) for depth in range(1, len(path_of))]
            if current[unit] != value:
                loud = 'Note: ' if unit == 'tool.uv.default-groups' else ''
                notes.append(f'{loud}{path}: {unit} is already set to {json.dumps(current[unit])}, which is yours and '
                             f'stays. {item["owner"]} would set {json.dumps(value)}.'
                             + (' uv sync will not install the groups of the tools.' if loud else '')
                             + (' The installer does not write the own file of the include.' if 'own' in item else ''))
        else:
            state = 'added'
            adds.append(unit)
        new = {'state': state, 'needs': list(item['needs'])}
        if state == 'added':
            new['holder'] = item['needs'][0]
            new['value'] = value
            if entry and entry.get('made') and unit not in adds:
                new['made'] = entry['made']
            if adopted_made.get(unit):
                new['made'] = adopted_made[unit]
        entries[unit] = new
        if 'own' in item and (state == 'added' or (state == 'present' and current.get(unit) == value)):
            owns[item['own']] = (item['data'], item['needs'])
    for unit, entry in old.items():
        if unit in want or unit.startswith('('):
            continue
        if entry['state'] == 'added':
            if ours(unit, entry):
                removes.append((unit, as_written(unit, entry)))
            elif unit in current:
                notes.append(f'{path}: you changed {unit}, so it stays when its owners go. Remove it by hand if no one '
                             f'needs it.')
    if not adds and not removes:
        return text, entries, owns
    add_rows = [(unit, want[unit]['value'], mark_of(want[unit]['about'], unit, path) if kind in ('toml', 'yaml')
                 and not unit.startswith('extends[') else None, want[unit].get('rows')) for unit in adds]
    new_text, made = apply_units(kind, text, add_rows, removes, FORMATS[path])
    for unit, things in made.items():
        if things:
            entries[unit]['made'] = things
    # What a unit that goes made, such as a table or the extends key, passes to each unit that stays inside it.
    # Then the last unit to go takes it out. The same holds for the note that your extends was one string.
    gone = {unit for unit, _ in removes}
    for _, entry in removes:
        for thing in entry.get('made', []):
            for unit, kept in entries.items():
                if unit in gone or unit.startswith('(') or kept.get('state') != 'added' \
                        or thing in kept.get('made', []) or not inside(kind, thing, unit):
                    continue
                kept['made'] = kept.get('made', []) + [thing]
    return new_text, entries, owns


def inside(kind, thing, unit):
    """True when the thing that an add made holds the unit: a table above a TOML key, or the extends key of JSON."""
    if kind == 'toml':
        table = edit_toml.parse_name(thing)[0]
        return edit_toml.parse_name(unit)[0][:len(table)] == table
    return thing in ('extends', EXTENDS_STRING) and unit.startswith('extends[')


def adopt(path, unit, text):
    """The first run after the old installer: a key that the old installer wrote, with its own comment."""
    return (path == 'pyproject.toml' and unit == 'tool.uv.default-groups'
            and '# uv sync and uv run install these dependency groups, the groups of the tools of just install' in
            (text or ''))


# A run.

def read_pending(target):
    """The files that a run that stopped was changing: {path: {'old': entries, 'new': entries}}."""
    path = target / PENDING
    if linked(target, PENDING) or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        files = data['files']
        if not isinstance(files, dict):
            raise ValueError
        # A part of a file with two names saves the name of the file that holds it.
        for change in files.values():
            if not isinstance(change, dict) or not all(isinstance(change.get(side) or {}, dict)
                                                       for side in ('old', 'new')):
                raise ValueError
            for side in ('old', 'new'):
                for entry in (change.get(side) or {}).values():
                    if isinstance(entry, dict) and not isinstance(entry.get('file', ''), str):
                        raise ValueError
        return files
    except (OSError, ValueError, KeyError, TypeError):
        raise SharedError(f'{PENDING} is not a file that the installer wrote. Delete it, then run just install --check.')


def merged_entries(old, new):
    """The entries of a file that a stopped run was changing: each unit as the new entries have it, else as the old
    ones have it. When the two give a unit two values, the file may hold either, so the entry keeps the old values
    in 'earlier'."""
    merged = {unit: dict(entry) for unit, entry in (old or {}).items()}
    for unit, entry in (new or {}).items():
        before = merged.get(unit, {})
        merged[unit] = dict(entry)
        values = [value for value in before.get('earlier', []) + ([before['value']] if 'value' in before else [])
                  + entry.get('earlier', []) if value != entry.get('value')]
        if values and before.get('state') == 'added':
            merged[unit]['earlier'] = [value for index, value in enumerate(values) if value not in values[:index]]
    return merged


def ignored(target, paths):
    """The paths that git ignores, each with the rule that ignores it. An empty answer when the target is no Git
    repository."""
    if not paths:
        return {}
    run = subprocess.run(['git', '-C', str(target), 'check-ignore', '-v', '--no-index', '--', *paths],
                         capture_output=True, text=True)
    found = {}
    for line in run.stdout.splitlines():
        rule, _, name = line.partition('\t')
        found[name] = rule
    return found


def sync(target, active, known, command, sources, parts, unsure_paths=(), dry_run=False, migrating=False):
    """Bring the shared files of the target to the active owners. parts is the record of the parts in
    .agents/stack.json. Return the notes, the new parts and the files that changed. Unless dry_run, write the files,
    with .agents/stack-pending.json while they change. The caller saves the parts, then calls finish."""
    pending = {}
    # A pending file of an older installer may name the file that a part went to, such as biome.jsonc.
    for path, change in read_pending(target).items():
        path = next((name for name, spec in FORMATS.items() if spec.get('instead') == path), path)
        held = pending.setdefault(path, {})
        held['old'] = merged_entries(held.get('old'), change.get('old'))
        held['new'] = merged_entries(held.get('new'), change.get('new'))
        if change.get('starter'):
            held['starter'] = True
    parts = {path: {unit: dict(entry) for unit, entry in units.items()} for path, units in (parts or {}).items()}
    unsure_files = set(unsure_paths)
    # A run that stopped while it moved a part, such as from biome.jsonc to biome.json, may have left the part in the
    # file that the old record names. The new record names the other file, so this run looks in both.
    left_in = {}
    for path, change in pending.items():
        if plain(path):
            parts[path] = merged_entries(change.get('old'), change.get('new'))
            unsure_files.add(path)
            left_in[path] = {entry.get('file') for entry in (change.get('old') or {}).values()
                             if isinstance(entry, dict) and isinstance(entry.get('file'), str)}
    # A file that a run could not read while it was unsure keeps 'unsure' in its entries, so it stays unsure until a
    # run works it out. The flag moves to unsure_files here, and kept() puts it back on the entries that a run keeps
    # as they were.
    for path, units in parts.items():
        for entry in units.values():
            if entry.pop('unsure', False):
                unsure_files.add(path)
    # The record before this run, for the old record of each file in .agents/stack-pending.json.
    recorded = {path: {unit: dict(entry) for unit, entry in units.items()} for path, units in parts.items()}
    # The include of AGENTS.md is a line in a block, which points to the own file.
    for owner in known:
        for path, source in owner.parts.items():
            if FORMATS.get(path, {}).get('include') == 'pointer':
                owner.lines[path] = [include_value(path, own_path(owner, path, source))]
    texts, writes = {}, {}
    # Each file that this run decides from the file alone: a whole file of the old installer that a part replaces,
    # and a starter of a new file.
    replaced, messages = set(), []
    for path in list(parts):
        units = parts[path]
        takers = [owner for owner in active if path in owner.parts or path in owner.starter]
        if not takers:
            continue
        old_whole = units.get(WHOLE) or units.get(SEED)
        if old_whole is not None and not linked(target, path):
            file = target / path
            if old_whole['state'] == 'declined' or not file.exists():
                parts[path] = {FILE: {'state': 'declined', 'needs': old_whole.get('needs', [])}}
            elif digest(file.read_bytes()) == old_whole.get('hash'):
                parts[path] = {}
                texts[path] = None
                writes[path] = None
                replaced.add(path)
                messages.append(f'{path}: the installer writes its part in place of the whole file that it wrote '
                                f'before.')
            else:
                parts[path] = {FILE: {'state': 'kept', 'needs': old_whole.get('needs', [])}}
                messages.append(f'{path}: you changed the file that the installer wrote, so it stays yours, with no '
                                f'part. To have the part, delete the file and run just install again.')
    line_paths = list(dict.fromkeys([p for owner in active for p in owner.lines] + [
        path for path, units in parts.items() if FORMATS.get(path, {}).get('format', 'lines') == 'lines'
        and WHOLE not in units and OWN not in units and any(not unit.startswith('(') for unit in units)]))
    unit_paths = list(dict.fromkeys(
        [p for owner in active for p in list(owner.parts) + list(owner.units) + list(owner.skipped)
         if FORMATS[p]['format'] != 'lines'] + [
            path for path, units in parts.items() if path in FORMATS and FORMATS[path]['format'] != 'lines'
            and any(not unit.startswith('(') for unit in units)]))
    whole = list(dict.fromkeys([p for owner in active for p in owner.files] + [
        path for path, units in parts.items() if WHOLE in units]))
    for path in line_paths + unit_paths + whole:
        if not plain(path):
            raise SharedError(f'{path} is not a path inside the project')
        if reserved(path):
            raise SharedError(f'{path} belongs to the installer, so no owner may name it')
    new_parts, changes = {}, {}

    def kept(path, units):
        """The entries of a file that this run leaves alone, as they were. A file that is yours alone, (file), has
        nothing to work out, so only the other entries stay unsure."""
        if path not in unsure_files:
            return units
        return {unit: entry if unit == FILE else dict(entry, unsure=True) for unit, entry in units.items()}
    # The file that each part goes to, when it is not the path of the record: biome.jsonc for biome.json.
    record_path = {}

    def text_of(path):
        if path not in texts:
            texts[path] = read_text(target, path)
        return texts[path]

    def put(path, text):
        texts[path] = text
        writes[path] = text

    # A kept file that you deleted is no longer yours alone: the parts go to it again, after a starter.
    for path in list(parts):
        units = parts[path]
        held = units.get(FILE)
        if held and held['state'] == 'kept' and not (target / path).exists():
            del units[FILE]
        elif held and held['state'] == 'declined' and (target / path).exists():
            units[FILE] = dict(held, state='kept')
    # The record of a starter names each owner that is on since it wrote the starter. An owner that went drops out,
    # so that a new install of it writes the starter again.
    keys = {owner.key for owner in active}
    for units in parts.values():
        if STARTER in units:
            units[STARTER] = dict(units[STARTER], needs=[key for key in units[STARTER].get('needs', []) if key in keys])
    starters, seeded = {}, set()
    for owner in active:
        for path, source in owner.starter.items():
            if not plain(path) or reserved(path):
                raise SharedError(f'{path} is not a path that a starter may name')
            starters.setdefault(path, []).append((owner, source))
    for path, takers in starters.items():
        # A starter is written once. While any owner with a starter for the file stays on, a file that you delete
        # stays deleted. Each such owner joins the record, so the file waits until all of them went.
        held = parts.get(path, {})
        if linked(target, path) or held.get(FILE):
            continue
        keys = [owner.key for owner, _ in takers]
        # A run that stopped while it wrote a starter marks the file in .agents/stack-pending.json. If the file is
        # missing, the starter was never written, so this run writes it.
        stopped = pending.get(path, {}).get('starter') and text_of(path) is None
        if held.get(STARTER, {}).get('needs') and not stopped:
            held[STARTER]['needs'] = list(dict.fromkeys(held[STARTER]['needs'] + keys))
            continue
        if text_of(path) is None:
            owner, source = takers[0]
            put(path, source.read_bytes().decode('utf-8', errors='surrogateescape'))
            parts.setdefault(path, {})[STARTER] = {'state': 'seeded', 'holder': owner.key, 'needs': keys}
            seeded.add(path)
            messages.append(f'{"Would write" if dry_run else "Wrote"} {path} from the starter of {owner.key}; it '
                            f'is yours now.')
    own_files, keep_own = {}, set()

    def keep_includes(path, units):
        """A file that the installer leaves alone keeps its parts, so each own file that a part includes stays: the
        own file of an active owner, and of an owner that went while the record still holds its part."""
        holding = {key for unit, entry in units.items() if not unit.startswith('(') and entry.get('state') == 'added'
                   for key in entry.get('needs', [])}
        keep_own.update(own_path(owner, path, owner.parts[path]) for owner in known
                        if path in owner.parts and (owner in active or owner.key in holding))
        # An owner that is gone from the catalog is not in known, so the record names its own file: the include of
        # a kept unit holds the path of the own file.
        includes = ' '.join(unit + ' ' + json.dumps(entry.get('value')) for unit, entry in units.items()
                            if not unit.startswith('(') and entry.get('state') == 'added')
        keep_own.update(own for own, own_units in parts.items() if OWN in own_units and re.search(
            r'(?<![\w./-])(?:\./)?' + re.escape(own) + r'(?![\w.-])', includes))
    # The own file of a pointer is written for each active owner, also when the pointer is not in AGENTS.md: a hook
    # of the role prints the own file.
    for owner in active:
        for path, source in owner.parts.items():
            if FORMATS[path].get('include') == 'pointer':
                own_files[own_path(owner, path, source)] = (source.read_bytes(), [owner.key])
    for path in line_paths:
        style = style_of(path)
        found = linked(target, path)
        units = parts.get(path, {})
        if found or FILE in units:
            if found:
                messages.append(f'{path}: {found} is a symlink, so the installer leaves {path} alone')
                keep_includes(path, units)
            # A file that is yours alone, (file), was read; only a symlink is a file that this run could not read.
            new_parts[path] = kept(path, units) if found else units
            continue
        old = text_of(path)
        # git skips a byte-order mark only at the very start, so the mark stays there, before the blocks.
        mark = '﻿' if old and old.startswith('﻿') else ''
        old = old[len(mark):] if old is not None else None
        claims = claims_of_entries(units)
        unsure = {(path, owner, key) for owner, owner_claims in claims.items() for key in owner_claims} \
            if path in unsure_files else set()
        out, rest, new_claims, notes, took = plan_line_file(path, old, active, known, claims, unsure, style)
        messages += notes
        owners = {owner.key for owner in known} | set(claims)
        text = render(path, out, rest, old, command, owners, took, style)
        if text != old:
            put(path, mark + text if text is not None else None)
            report(messages, path, old, text, dry_run, style)
            changes[path] = 'lines'
        entries = entries_of_claims(new_claims)
        for key in (STARTER,):
            if key in units:
                entries[key] = units[key]
        if entries:
            new_parts[path] = entries
    for path in unit_paths:
        spec = FORMATS[path]
        file_path = target_of(target, path)
        if file_path != path:
            record_path[file_path] = path
        found = linked(target, file_path)
        units = parts.get(path, {})
        if found or FILE in units:
            if found:
                messages.append(f'{path}: {found} is a symlink, so the installer leaves {path} alone')
                # The part stays in the file, so the own file that its include reads stays too.
                keep_includes(path, units)
            new_parts[path] = kept(path, units) if found else units
            continue
        notes = []
        want = desired_units(target, path, active, notes)
        old = {unit: entry for unit, entry in units.items() if not unit.startswith('(')}
        unsure = set(old) if path in unsure_files else set()
        # The record names the file that holds the units. When a file that comes before it is there now, such as a
        # biome.json next to biome.jsonc, the tool reads that file only, so the units move to it. A unit that you
        # deleted stays declined.
        held = held_in(target, path, units)
        gone = {unit: entry for unit, entry in old.items() if entry['state'] == 'added'}

        def take_out(source):
            """Take the units that the installer added out of source, a file that the tool no longer reads."""
            record_path[source] = path
            source_text = text_of(source)
            try:
                moved, _, _ = plan_unit_file(path, spec['format'], source_text, {}, gone, unsure, False, notes)
            except SharedError as exc:
                messages.append(f'{source}: the installer cannot take {", ".join(gone)} out of it: {exc}. '
                                f'Remove it by hand.')
                return
            if moved != source_text:
                put(source, None if empty_units(spec['format'], moved) else moved)
                report_units(messages, source, source_text, writes.get(source), dry_run)
                changes[source] = 'units'
        # The files to take the units out of: the file that the record names, and a file that a stopped run named.
        sources = [source for source in [held] + sorted(left_in.get(path, set()) - {held})
                   if source != file_path and source in (path, spec.get('instead')) and (target / source).exists()]
        link = next((source for source in sources if linked(target, source)), None)
        if link:
            messages.append(f'{path}: {linked(target, link)} is a symlink, so the installer leaves {path} alone')
            keep_includes(path, units)
            # The record names the file that still holds the part, so that the next run leaves it alone too.
            new_parts[path] = {unit: entry if unit.startswith('(') else dict(entry, file=link)
                               for unit, entry in kept(path, units).items()}
            continue
        if held in sources:
            if gone:
                take_out(held)
                messages.append(f'{path}: the tool reads {file_path} before {held}, so the parts move from {held} '
                                f'to {file_path}.')
            old = {unit: entry for unit, entry in old.items() if entry['state'] == 'declined'}
        for source in sources:
            if source != held and gone:
                take_out(source)
        text = text_of(file_path)
        try:
            if text is None and not spec.get('create', True):
                raise SharedError(f'there is no {path} yet')
            new_text, entries, owns = plan_unit_file(path, spec['format'], text, want, old, unsure,
                                                     migrating or path in replaced, notes)
        except SharedError as exc:
            include = [item['value'] for item in want.values() if 'own' in item]
            # The output names the include to add by hand, so the own file that it reads must be there.
            own_files.update({item['own']: (item['data'], item['needs']) for item in want.values() if 'own' in item})
            messages.append(f'{file_path}: the installer leaves the file alone: {exc}.'
                            + (f' Add the include yourself: {spec["include"]} {include[0]}' if include else ''))
            # A file that is not there holds no value of a stopped run, so its units are not unsure.
            new_parts[path] = kept(path, units) if text is not None else units
            continue
        messages += notes
        # The record names the file that holds the units of a file with two names, such as biome.json.
        if spec.get('instead'):
            for unit, entry in entries.items():
                entry['file'] = file_path
        for key in (STARTER,):
            if key in units:
                entries[key] = units[key]
        if new_text != text:
            if not empty_units(spec['format'], new_text):
                put(file_path, new_text)
            elif text is not None:
                put(file_path, None)
            report_units(messages, file_path, text, writes.get(file_path), dry_run)
            changes[file_path] = 'units'
        if entries:
            new_parts[path] = entries
        own_files.update(owns)
    for path in whole:
        found = linked(target, path)
        units = parts.get(path, {})
        if found:
            if any(path in owner.files for owner in active) or WHOLE in units:
                messages.append(f'{path}: {found} is a symlink, so the installer leaves {path} alone')
            if units:
                new_parts[path] = kept(path, units)
            continue
        old = units.get(WHOLE)
        claim = {'owners': old.get('needs', []), 'hash': old.get('hash'), 'declined': old['state'] == 'declined'} \
            if old else None
        action, owners, source, notes = plan_whole_file(target, path, active, claim, path not in unsure_files)
        messages += notes
        if action == 'write':
            data = source.read_bytes()
            writes[path] = data
            new_parts[path] = {WHOLE: {'state': 'added', 'needs': owners, 'hash': digest(data)}}
            messages.append(f'{"Would write" if dry_run else "Wrote"} {path} for {", ".join(owners)}')
            changes[path] = 'whole'
        elif action == 'own' or (action is None and owners and old):
            new_parts[path] = {WHOLE: {'state': 'added', 'needs': owners, 'hash': old['hash']}}
        elif action == 'remove':
            writes[path] = None
            messages.append(f'{"Would remove" if dry_run else "Removed"} {path}: no owner needs it, and nobody '
                            f'changed it')
            changes[path] = 'whole'
        elif action == 'decline':
            new_parts[path] = {WHOLE: {'state': 'declined', 'needs': []}}
        elif action is None and old and old['state'] == 'declined' and not (target / path).exists():
            new_parts[path] = units
    # The own files: each one that an include needs is written again; each one that no include needs goes.
    old_own = [path for path, units in parts.items() if OWN in units]
    # A dry run checks this too, so that a run stops before its package commands change the project.
    blocked = ignored(target, list(own_files))
    if blocked:
        raise SharedError('git ignores the own file ' + ', '.join(f'{own} (by {rule})' for own, rule in blocked.items())
                          + '. A fresh clone needs it, so change that rule and run just install again.')
    for own, (data, needs) in own_files.items():
        if linked(target, own):
            raise SharedError(f'{own}: a folder on its way is a symlink, so the installer cannot write it')
        new_parts[own] = {OWN: {'state': 'added', 'holder': needs[0], 'needs': needs, 'hash': digest(data)}}
        file = target / own
        if not file.exists() or file.read_bytes() != data:
            writes[own] = data
            messages.append(f'{"Would write" if dry_run else "Wrote"} {own} for {", ".join(needs)}')
            changes[own] = 'own'
    for own in old_own:
        if own in keep_own and own not in own_files:
            new_parts[own] = parts[own]
        if own in own_files or own in keep_own or linked(target, own):
            continue
        if (target / own).exists():
            writes[own] = None
            messages.append(f'{"Would remove" if dry_run else "Removed"} {own}: no include needs it')
            changes[own] = 'own'
    for path, units in parts.items():
        for key in (FILE, STARTER, SEED):
            if path not in new_parts and key in units:
                new_parts[path] = {key: units[key]}
    if dry_run:
        return messages, new_parts, changes
    if writes or pending:
        # Each file of a run that stopped before stays in the pending file with its first old record, also when
        # this run writes nothing to it, so that two runs that stop in a row lose no record. Until this run writes
        # a file, its new record also keeps the new entries of the run before, which the next run checks against
        # the file. Once this run wrote the file, its new record is the one of this run. The pending file names
        # the path of the record, biome.json also when the part goes to biome.jsonc. Only a starter that this run
        # writes is marked: a starter that a run before wrote is in the file now.
        # A file that this run read and leaves as it is holds what this run's new record says.
        paths = list(dict.fromkeys(list(pending) + [record_path.get(path, path) for path in writes]))
        to_write = {record_path.get(path, path) for path in writes}
        read = set(line_paths) | set(unit_paths) | set(whole)
        files = {path: dict({'old': pending[path].get('old', {}) if path in pending else recorded.get(path, {}),
                             'new': merged_entries(pending[path].get('new'), new_parts.get(path))
                             if path in pending and path in to_write
                             else new_parts.get(path, {}) if path in read or path not in pending
                             else pending[path].get('new', {})},
                            **({'starter': True} if path in seeded else {}))
                 for path in paths}
        save_pending(target, files)
        for path, data in writes.items():
            file = target / path
            if data is None:
                file.unlink(missing_ok=True)
                remove_empty_folders(target, file)
            else:
                write(file, data if isinstance(data, bytes) else data.encode('utf-8', errors='surrogateescape'))
            entry = files[record_path.get(path, path)]
            if entry['new'] != new_parts.get(record_path.get(path, path), {}):
                entry['new'] = new_parts.get(record_path.get(path, path), {})
                save_pending(target, files)
        if changes:
            history(target, command, sources, changes)
    return messages, new_parts, changes


def remove_empty_folders(target, file):
    """Take out the folders of an own file that are now empty, up to .agents/config or .agents/instructions."""
    folder = file.parent
    while folder != target and folder.relative_to(target).as_posix() not in ('.agents/config', '.agents/instructions',
                                                                              '.agents'):
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent
    for stop in ('.agents/config', '.agents/instructions'):
        try:
            (target / stop).rmdir()
        except OSError:
            pass


def save_pending(target, files):
    path = target / PENDING
    path.parent.mkdir(exist_ok=True)
    write(path, (json.dumps({'files': files}, indent=2, sort_keys=True) + '\n').encode())


def finish(target):
    """After the record of the parts is saved, the run is whole, so the file of a run that stopped goes."""
    if not linked(target, PENDING):
        (target / PENDING).unlink(missing_ok=True)


def report(messages, path, old, new, dry_run, style=HASH):
    before = {normal(bare(row)) for row in rows_of(old or '') if style.is_rule(bare(row))}
    after = {normal(bare(row)) for row in rows_of(new or '') if style.is_rule(bare(row))}
    parts = []
    if after - before:
        parts.append('adds ' + ', '.join(sorted(after - before)))
    if before - after:
        parts.append('removes ' + ', '.join(sorted(before - after)))
    if not parts:
        parts.append('moves lines between blocks')
    messages.append(f'{verb(old, new, dry_run)} {path}: {"; ".join(parts)}')


def report_units(messages, path, old, new, dry_run):
    messages.append(f'{verb(old, new, dry_run)} {path}')


def verb(old, new, dry_run):
    if old is None:
        return 'Would write' if dry_run else 'Wrote'
    if new is None:
        return 'Would remove' if dry_run else 'Removed'
    return 'Would change' if dry_run else 'Changed'


def check(target, parts):
    """Compare the record of the parts with the files. Print each difference. Return 1 for a broken block, a run that
    stopped or a file that cannot be read, else 0."""
    failed = False
    try:
        pending = read_pending(target)
    except SharedError as exc:
        print(f'just install --check: {exc}')
        return 1
    if pending:
        print(f'A run stopped while it changed {", ".join(sorted(pending))}. Run just install again.')
        failed = True
    unread = sorted(path for path, units in (parts or {}).items() if any(entry.get('unsure') for entry in units.values()))
    if unread:
        print(f'A run stopped while it changed {", ".join(unread)}, and a later run could not read the file. Fix the '
              f'file, then run just install again.')
        failed = True
    if parts is None:
        print(f'just install --check: {RECORD} has no record of the parts yet. The next just install makes it.')
        return 1 if failed else 0
    for path, units in sorted(parts.items()):
        found = linked(target, path)
        if found:
            print(f'{path}: {found} is a symlink, so the installer leaves {path} alone.')
            continue
        file = target / path
        if OWN in units or WHOLE in units:
            entry = units.get(OWN) or units[WHOLE]
            if entry['state'] != 'added':
                continue
            if not file.exists():
                print(f'{path} of {", ".join(entry["needs"])} is gone. The next just install writes it again.'
                      if OWN in units else f'{path} of {", ".join(entry["needs"])} is gone.')
            elif digest(file.read_bytes()) != entry.get('hash'):
                print(f'{path} of {", ".join(entry["needs"])} is edited. '
                      + ('The next just install writes it again.' if OWN in units else 'It stays when its owners go.'))
            continue
        if FILE in units:
            continue
        spec = FORMATS.get(path, {'format': 'lines'})
        if spec['format'] == 'lines':
            if not file.exists():
                if any(entry.get('state') == 'added' for unit, entry in units.items() if not unit.startswith('(')):
                    print(f'{path} is gone. The next just install records its lines as declined.')
                continue
            style = style_of(path)
            text = file.read_bytes().decode('utf-8', errors='surrogateescape')
            claims = claims_of_entries(units)
            blocks, user, broken = parse(text[1:] if text.startswith('﻿') else text,
                                         set(claims) | {'jnterfaces'}, style)
            for owner in broken:
                print(f'{path}: the block of {owner} lacks its begin or end comment.')
                failed = True
            here = {normal(bare(row)) for row in user if style.is_rule(bare(row))} | {
                normal(bare(row)) for body in blocks.values() for row in body if style.is_rule(bare(row))}
            for owner, owner_claims in claims.items():
                for key, state in owner_claims.items():
                    if state == 'added' and key not in here:
                        print(f'{path}: {key} of {owner} is gone. The next just install records it as declined.')
                for row in blocks.get(owner, []):
                    if style.is_rule(bare(row)) and owner_claims.get(normal(bare(row))) != 'added':
                        print(f'{path}: {bare(row).strip()} is inside the block of {owner}, but {owner} did not add '
                              f'it. The next just install moves it to your part.')
            for owner in blocks:
                if owner not in claims:
                    print(f'{path}: the record has no lines of {owner}, which has a block. The next just install '
                          f'takes the lines that {owner} needs into the record.')
            continue
        file_path = held_in(target, path, units)
        if not (target / file_path).exists():
            file_path = target_of(target, path)
        found = linked(target, file_path) or linked(target, target_of(target, path))
        if found:
            print(f'{path}: {found} is a symlink, so the installer leaves {path} alone.')
            continue
        if file_path != target_of(target, path):
            print(f'{path}: the tool reads {target_of(target, path)} before {file_path}, which holds the parts. The '
                  f'next just install moves them.')
        if read_text(target, file_path) is None and not spec.get('create', True):
            print(f'{file_path}: the file is not there. If you make it again, the next just install records each unit '
                  f'that it lacks as declined.')
            continue
        try:
            current = read_units(spec['format'], read_text(target, file_path),
                                 [unit for unit in units if not unit.startswith('(')], spec)
        except SharedError as exc:
            print(f'{file_path}: cannot read it: {exc}.')
            continue
        for unit, entry in units.items():
            if unit.startswith('(') or entry['state'] != 'added':
                continue
            if unit not in current:
                print(f'{file_path}: {unit} of {entry.get("holder")} is gone. The next just install records it as '
                      f'declined.')
            elif current[unit] != entry.get('value'):
                print(f'{file_path}: {unit} of {entry.get("holder")} is changed. It is yours now.')
    if not failed:
        print(f'just install --check: the record in {RECORD} and the files agree, apart from the notes above')
    return 1 if failed else 0
