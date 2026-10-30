"""The line files and the whole files that the owners of a project share, with the log of each change.

docs/plan-shared-files.md gives the decisions, D1 to D14. An owner is jnterfaces, a profile such as python, or a tool of
a profile such as python:lint. A line file, such as .gitignore, holds one block of lines for each owner, at the start of
the file, and the lines of the user after the blocks. A whole file, such as ruff.toml, has a list of owners and the
hash of what the installer wrote. .agents/stack-log.jsonl records each change, and the log decides which lines and
files an owner may remove.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import time

LOG = Path('.agents/stack-log.jsonl')
ATTRIBUTES = '.agents/.gitattributes'
# The log only grows, so a union merge of two branches keeps the entries of both.
JNTERFACES_LINES = {ATTRIBUTES: ['stack-log.jsonl merge=union']}
# The files of the installer itself, which no owner may name.
RESERVED = ('.agents/stack-log.jsonl', '.agents/stack.json')
RESERVED_FOLDERS = ('.agents/just/',)
OWNER = r'[a-z][a-z0-9_-]*(?::[a-z][a-z0-9_-]*)?'
BEGIN = re.compile(rf'^# begin lines of ({OWNER}),')
WRITTEN = re.compile(r'^# Last written to ')
END = re.compile(rf'^# end lines of ({OWNER})\.')
# The whole begin and end comments, as the installer writes them. A comment that lost its partner is dropped only
# when it has this form, so that a comment of yours that starts in the same way stays.
OWN_BEGIN = re.compile(rf'^# begin lines of ({OWNER}), .+, from \S+ at \S+\.$')
OWN_END = re.compile(rf'^# end lines of ({OWNER})\. (`just uninstall \S+` removes them|They stay while .+)\.$')
STATES = ('added', 'present', 'declined', 'released')


class SharedError(Exception):
    pass


class Owner:
    """One owner, in the order of the run: jnterfaces, then each profile followed by its tools."""

    def __init__(self, key, about, package, commit, removal, lines=None, files=None):
        self.key, self.about, self.package, self.commit, self.removal = key, about, package, commit, removal
        # lines: {path: [line, ...]}. files: {path: (source file, skip_if guard or None)}.
        self.lines, self.files = lines or {}, files or {}


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


def is_rule(line):
    """True for a line that git reads as a pattern: not blank and not a comment."""
    return bool(line.strip()) and not line.lstrip().startswith('#')


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


# The log.

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
    """The entries of the log, in the order of the file. A missing log has no entries."""
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
        if not entry_ok(entry):
            raise SharedError(f'{LOG} line {number} is not an entry of the log')
        entries.append(entry)
    return entries


def replay(entries):
    """The claims of the log: lines {path: {owner: {normal line: state}}}, files {path: claim}, the unsure claims
    and the entries that started and did not finish.

    A claim is unsure while the entry that made it has not finished: the run may have stopped before it wrote the
    file. An unsure claim never declines a line or a file."""
    finished = {entry['id'] for entry in entries if entry.get('status') == 'finished'}
    lines, files, unsure, started = {}, {}, set(), {}
    for entry in entries:
        if entry.get('status') == 'finished':
            continue
        sure = entry['id'] in finished
        if not sure:
            started[entry['id']] = entry.get('command', '')
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
                        (unsure.discard if sure else unsure.add)((path, owner, key))
        for path, change in entry.get('files', {}).items():
            if change.get('owners') or change.get('declined'):
                files[path] = {'owners': list(change.get('owners', [])), 'hash': change.get('hash'),
                               'declined': bool(change.get('declined'))}
            else:
                files.pop(path, None)
            (unsure.discard if sure else unsure.add)((path, None, None))
    return lines, files, unsure, started


def new_id():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()) + '-' + secrets.token_hex(2)


def append(target, entry):
    found = linked(target, LOG)
    if found:
        raise SharedError(f'{found} is a symlink; make it a folder or a file')
    path = target / LOG
    path.parent.mkdir(exist_ok=True)
    with path.open('a') as handle:
        handle.write(json.dumps(entry, sort_keys=True) + '\n')


def write(file, data):
    """Write a file through a file next to it and a rename, so that a run that stops leaves the old file whole."""
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_name(f'.{file.name}.{secrets.token_hex(4)}.tmp')
    temporary.write_bytes(data)
    if file.exists():
        shutil.copymode(file, temporary)
    os.replace(temporary, file)


# Line files.

def parse(text, owners):
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
        owner = mark(BEGIN, rows[index])
        if owner:
            close = None
            for later in range(index + 1, len(rows)):
                if mark(BEGIN, rows[later]) or mark(END, rows[later]):
                    close = later if mark(END, rows[later]) == owner else None
                    break
            first = index + 1
            if first < len(rows) and WRITTEN.match(bare(rows[first])):
                first += 1
            if close is None:
                if OWN_BEGIN.match(bare(rows[index])):
                    broken.append(owner)
                    index = first
                else:
                    user.append(rows[index])
                    index += 1
                continue
            blocks.setdefault(owner, []).extend(rows[first:close])
            index = close + 1
            continue
        if mark(END, rows[index]) and OWN_END.match(bare(rows[index])):
            broken.append(mark(END, rows[index]))
        else:
            user.append(rows[index])
        index += 1
    return blocks, user, broken


def one_line(text):
    return ' '.join(str(text).split())


def header(owner, path, command, entry_id):
    return [f'# begin lines of {owner.key}, {owner.about}, from {owner.package} at {owner.commit[:7]}.',
            f'# Last written to {path} by `{one_line(command)}`, entry {entry_id} of {LOG.as_posix()}.']


def footer(owner):
    return f'# end lines of {owner.key}. {owner.removal}'


def plan_line_file(path, text, active, known, claims, unsure):
    """Work out one line file for the active owners, in order, with the claims of the log for this file. known holds
    every owner of the run, also the ones that go, so that their own comments go with them.

    Returns the blocks [(owner, lines, same)], the rows of the user, the changes of the claims {owner: {state:
    [line]}}, the notes, and whether a row of the user went. same is True when a block keeps its lines."""
    keys = {owner.key for owner in known} | set(claims)
    blocks, user, _ = parse(text or '', keys)
    comments = {(owner.key, line.strip()) for owner in known for line in owner.lines.get(path, [])
                if not is_rule(line)}
    # D4 step 5: a row in a block that the owner of the block did not add, a line or a comment, is the user's. It moves to the start of the
    # user part before the owners look for their lines, so that an owner counts it as present.
    moved_out = []
    for owner_key in list(blocks):
        kept = []
        for row in blocks[owner_key]:
            line = bare(row)
            if not line.strip():
                continue
            mine = claims.get(owner_key, {}).get(normal(line)) == 'added'
            if is_rule(line) and mine or not is_rule(line) and (owner_key, line.strip()) in comments:
                kept.append(row)
            else:
                moved_out.append(row)
        blocks[owner_key] = kept
    user = moved_out + user
    user_rules = {}
    for row in user:
        if is_rule(bare(row)):
            user_rules.setdefault(normal(bare(row)), []).append(row)
    in_blocks = {normal(bare(row)) for body in blocks.values() for row in body if is_rule(bare(row))}
    anywhere = in_blocks | set(user_rules)

    def sure(owner_key, key):
        return (path, owner_key, key) not in unsure
    # D4 step 3: a line that the log knows, from a run that finished, and that the file lacks, was deleted by you.
    deleted = {key for owner_key, owner_claims in claims.items() for key in owner_claims
               if key not in anywhere and sure(owner_key, key)}
    homed, wanted, new_claims, notes = {}, set(), {}, []
    for owner in active:
        mine = claims.get(owner.key, {})
        for line in owner.lines.get(path, []):
            if not is_rule(line):
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
            new_claims.setdefault(owner.key, {})[key] = (state, line)
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
                maybe = [bare(row).strip() for row in user if is_rule(bare(row)) and wide(bare(row)) == wide(key)]
                if maybe:
                    notes.append(f'{path}: {owner_key} added {key}, and the file has {maybe[0]}, which may be the '
                                 f'same line. It stays; remove it by hand if no one needs it.')
    states = {}
    for owner_key, owner_claims in new_claims.items():
        before = claims.get(owner_key, {})
        for key, (state, line) in owner_claims.items():
            if before.get(key) != state or not sure(owner_key, key):
                states.setdefault(owner_key, {}).setdefault(state, []).append(line)
    for owner_key, owner_claims in claims.items():
        for key in owner_claims:
            if key not in new_claims.get(owner_key, {}):
                states.setdefault(owner_key, {}).setdefault('released', []).append(key)
    out = []
    for owner in active:
        body = [line for line in owner.lines.get(path, []) if is_rule(line)
                and homed.get(normal(line), (None,))[0] == owner.key]
        if not body:
            continue
        body = [line for line in owner.lines.get(path, []) if not is_rule(line)] + body
        old = blocks.get(owner.key)
        same = old is not None and [bare(r).strip() for r in old if bare(r).strip()] == [line.strip() for line in body]
        out.append((owner, body, same))
    rest = []
    took = False
    for row in user:
        if is_rule(bare(row)) and normal(bare(row)) in removed:
            removed.discard(normal(bare(row)))
            took = True
            continue
        rest.append(row)
    return out, rest, states, notes, took


def render(path, out, rest, old_text, command, entry_id, owners, took):
    """The text of a line file from its blocks and the rows of the user. A run that changes no block and no row of the
    user returns the old text as it is. A block whose lines did not change keeps its old comments. Each row of the
    user keeps its own line ending, and the new rows take the ending that most rows of the file have."""
    old_blocks, old_user, broken = parse(old_text or '', owners)
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
                      if BEGIN.match(bare(row)) and BEGIN.match(bare(row)).group(1) == owner.key), None)
        if same and start is not None and start + 1 < len(old_rows) and WRITTEN.match(bare(old_rows[start + 1])):
            rows += [bare(row) + newline for row in old_rows[start:start + 2]]
        else:
            rows += [line + newline for line in header(owner, path, command, entry_id)]
        rows += [line + newline for line in body] + [footer(owner) + newline]
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


# Whole files.

def plan_whole_file(target, path, active, claim, unsure):
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
    sure = (path, None, None) not in unsure
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


# A run.

def sync(target, active, known, command, sources, dry_run=False):
    """Bring the line files and the whole files of the target to the active owners. Return the notes, and write an
    entry of the log for each change, unless dry_run."""
    entries = read_log(target)
    claims, file_claims, unsure, stopped = replay(entries)
    paths = list(dict.fromkeys([p for owner in active for p in owner.lines] + list(claims)))
    whole = list(dict.fromkeys([p for owner in active for p in owner.files] + list(file_claims)))
    for path in paths + whole:
        if not plain(path):
            raise SharedError(f'{path} is not a path inside the project')
        if reserved(path):
            raise SharedError(f'{path} belongs to the installer, so no profile may name it')
    clash = set(paths) & ({p for owner in known for p in owner.files} | set(file_claims))
    if clash:
        raise SharedError(f'{", ".join(sorted(clash))} is both a line file and a whole file of the owners')
    entry_id = new_id()
    writes, entry_lines, entry_files, messages, skipped = {}, {}, {}, [], set()
    for path in paths:
        found = linked(target, path)
        if found:
            messages.append(f'{path}: {found} is a symlink, so the installer leaves {path} alone')
            skipped.add(path)
            continue
        file = target / path
        old = file.read_bytes().decode('utf-8', errors='surrogateescape') if file.exists() else None
        # git skips a byte-order mark only at the very start, so the mark stays there, before the blocks.
        mark = '\ufeff' if old and old.startswith('\ufeff') else ''
        old = old[len(mark):] if old is not None else None
        out, rest, states, notes, took = plan_line_file(path, old, active, known, claims.get(path, {}), unsure)
        messages += notes
        owners = {owner.key for owner in known} | set(claims.get(path, {}))
        text = render(path, out, rest, old, command, entry_id, owners, took)
        if text != old:
            writes[path] = mark + text if text is not None else None
            report(messages, path, old, text, dry_run)
        if states:
            entry_lines[path] = states
    for path in whole:
        found = linked(target, path)
        if found:
            if any(path in owner.files for owner in active) or path in file_claims:
                messages.append(f'{path}: {found} is a symlink, so the installer leaves {path} alone')
            skipped.add(path)
            continue
        action, owners, source, notes = plan_whole_file(target, path, active, file_claims.get(path), unsure)
        messages += notes
        if action == 'write':
            data = source.read_bytes()
            writes[path] = data
            entry_files[path] = {'owners': owners, 'hash': digest(data)}
            messages.append(f'{"Would write" if dry_run else "Wrote"} {path} for {", ".join(owners)}')
        elif action == 'own':
            entry_files[path] = {'owners': owners, 'hash': file_claims[path]['hash']}
        elif action == 'remove':
            writes[path] = None
            entry_files[path] = {'removed': True}
            messages.append(f'{"Would remove" if dry_run else "Removed"} {path}: no owner needs it, and nobody '
                            f'changed it')
        elif action == 'decline':
            entry_files[path] = {'declined': True}
        elif action == 'release':
            entry_files[path] = {'released': True}
    if dry_run or not (writes or entry_lines or entry_files or stopped):
        return messages
    if not entries:
        append(target, {'id': new_id(), 'status': 'finished', 'command': 'baseline',
                        'note': 'The lines and the files before this entry are yours. No removal takes them out.'})
    append(target, {'id': entry_id, 'status': 'started', 'command': one_line(command), 'sources': sources,
                    'lines': entry_lines, 'files': entry_files})
    for path, data in writes.items():
        file = target / path
        if data is None:
            file.unlink(missing_ok=True)
        else:
            write(file, data if isinstance(data, bytes) else data.encode('utf-8', errors='surrogateescape'))
    append(target, {'id': entry_id, 'status': 'finished'})
    # This run recorded each unsure claim again, so the entries that stopped are settled. An entry with a path that
    # this run left alone stays unsettled, so that its claims stay unsure.
    touched = {entry['id']: set(entry.get('lines', {})) | set(entry.get('files', {})) for entry in entries}
    for old in stopped:
        if not touched.get(old, set()) & skipped:
            append(target, {'id': old, 'status': 'finished', 'note': f'Settled by entry {entry_id}.'})
    return messages


def report(messages, path, old, new, dry_run):
    before = {normal(bare(row)) for row in rows_of(old or '') if is_rule(bare(row))}
    after = {normal(bare(row)) for row in rows_of(new or '') if is_rule(bare(row))}
    parts = []
    if after - before:
        parts.append('adds ' + ', '.join(sorted(after - before)))
    if before - after:
        parts.append('removes ' + ', '.join(sorted(before - after)))
    if not parts:
        parts.append('moves lines between blocks')
    if old is None:
        verb = 'Would write' if dry_run else 'Wrote'
    elif new is None:
        verb = 'Would remove' if dry_run else 'Removed'
    else:
        verb = 'Would change' if dry_run else 'Changed'
    messages.append(f'{verb} {path}: {"; ".join(parts)}')


def check(target):
    """Compare the log with the files. Print each difference. Return 1 for a broken block, an unfinished entry or a
    log that cannot be read, else 0."""
    try:
        entries = read_log(target)
    except SharedError as exc:
        print(f'just install --check: {exc}')
        return 1
    if not entries:
        print(f'just install --check: no {LOG.as_posix()}, so there is nothing to compare')
        return 0
    claims, file_claims, _, started = replay(entries)
    failed = False
    for entry_id, command in started.items():
        print(f'Entry {entry_id} ({command}) started and did not finish. Run just install again.')
        failed = True
    for path, owners in claims.items():
        found = linked(target, path)
        if found:
            print(f'{path}: {found} is a symlink, so the installer leaves {path} alone.')
            continue
        file = target / path
        if not file.exists():
            if any(state == 'added' for c in owners.values() for state in c.values()):
                print(f'{path} is gone. The next just install records its lines as declined.')
            continue
        text = file.read_bytes().decode('utf-8', errors='surrogateescape')
        blocks, user, broken = parse(text[1:] if text.startswith('\ufeff') else text, set(owners) | {'jnterfaces'})
        for owner in broken:
            print(f'{path}: the block of {owner} lacks its begin or end comment.')
            failed = True
        here = {normal(bare(row)) for row in user if is_rule(bare(row))} | {
            normal(bare(row)) for body in blocks.values() for row in body if is_rule(bare(row))}
        for owner, owner_claims in owners.items():
            for key, state in owner_claims.items():
                if state == 'added' and key not in here:
                    print(f'{path}: {key} of {owner} is gone. The next just install records it as declined.')
            for row in blocks.get(owner, []):
                if is_rule(bare(row)) and owner_claims.get(normal(bare(row))) != 'added':
                    print(f'{path}: {bare(row).strip()} is inside the block of {owner}, but {owner} did not add it. '
                          f'The next just install moves it to your part.')
        for owner in blocks:
            if owner not in owners:
                print(f'{path}: the log has no lines of {owner}, which has a block. The next just install moves its '
                      f'lines to your part.')
    for path, claim in file_claims.items():
        if claim.get('declined'):
            continue
        file = target / path
        if linked(target, path):
            print(f'{path} is a symlink, or is in a folder that is one.')
        elif not file.exists():
            print(f'{path} of {", ".join(claim["owners"])} is gone.')
        elif digest(file.read_bytes()) != claim.get('hash'):
            print(f'{path} of {", ".join(claim["owners"])} is edited. It stays when its owners go.')
    if not failed:
        print('just install --check: the log and the files agree, apart from the notes above')
    return 1 if failed else 0
