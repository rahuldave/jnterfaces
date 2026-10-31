# /// script
# requires-python = ">=3.11"
# dependencies = ["tomlkit==0.15.2"]
# ///
"""Add and take out the units of a part in a TOML file, such as ty.toml or pyproject.toml.

notes/plan-snippets.md gives the decisions. A unit is a key, such as tool.uv.default-groups, or one item of an array of
plain values, such as ".agents" in src.exclude. The name of a key unit is its path. The name of an item unit is the
path of its array, then the item in JSON: src.exclude[".agents"]. tomlkit keeps your comments and the order of your
keys. Each change is checked: the data of the new text must be the data of the old text with only the units changed.
"""
import copy
import json
import re
import tomllib

import tomlkit

BARE = re.compile(r'[A-Za-z0-9_-]+')
MISSING = object()


class EditError(Exception):
    pass


def key_text(path):
    return '.'.join(part if BARE.fullmatch(part) else json.dumps(part) for part in path)


def name(path, item=MISSING):
    """The name of a unit: the path of a key, and for an item the item in JSON after it."""
    return key_text(path) + ('' if item is MISSING else f'[{json.dumps(item)}]')


def parse_name(text):
    """The path and the item of a unit name. The item is MISSING for a key unit."""
    path, index, item = [], 0, MISSING
    while index < len(text):
        if text[index] == '"':
            part, end = json.JSONDecoder().raw_decode(text, index)
            path.append(part)
            index = end
        else:
            match = BARE.match(text, index)
            if not match:
                raise EditError(f'{text} is not the name of a TOML unit')
            path.append(match.group())
            index = match.end()
        if index == len(text):
            break
        if text[index] == '.':
            index += 1
        elif text[index] == '[' and text.endswith(']'):
            item = json.loads(text[index + 1:-1])
            break
        else:
            raise EditError(f'{text} is not the name of a TOML unit')
    return tuple(path), item


def plain(value):
    return value.unwrap() if hasattr(value, 'unwrap') else value


def scalar(value):
    return isinstance(value, (str, int, float, bool))


def load(text):
    try:
        return tomlkit.parse(text)
    except Exception as exc:  # tomlkit raises several kinds of parse error
        raise EditError(f'it does not parse as TOML: {exc}')


def lf(data):
    """The data with each CRLF in a string made LF."""
    if isinstance(data, dict):
        return {key: lf(value) for key, value in data.items()}
    if isinstance(data, list):
        return [lf(value) for value in data]
    return data.replace('\r\n', '\n') if isinstance(data, str) else data


def strict(text):
    """The data of the text as tomllib reads it. tomlkit reads some text that TOML 1.0 does not allow, and the tools
    of a project read pyproject.toml with tomllib."""
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise EditError(f'the change would not parse as TOML 1.0: {exc}')


def units(text):
    """The units of a source file, in order: each key that holds no table, and one unit for each item of an array of
    plain values."""
    found = {}

    def walk(table, path):
        for key, value in table.items():
            value = plain(value)
            here = path + (key,)
            if isinstance(value, dict):
                walk(value, here)
            elif isinstance(value, list) and value and all(scalar(item) for item in value):
                for item in value:
                    found[name(here, item)] = item
            else:
                found[name(here)] = value
    walk(plain(load(text)), ())
    return found


def lookup(data, path):
    for part in path:
        if not isinstance(data, dict) or part not in data:
            return MISSING
        data = data[part]
    return data


def read(text, names):
    """The value of each named unit that the text has. An item unit that is there has its item as value."""
    data = plain(load(text or ''))
    found = {}
    for unit in names:
        path, item = parse_name(unit)
        value = lookup(data, path)
        if item is MISSING:
            if value is not MISSING:
                found[unit] = value
        elif isinstance(value, list) and item in value:
            found[unit] = item
        elif value is not MISSING and not isinstance(value, list):
            # The key holds one value of yours, such as pythonpath = "src", so it has no list to add the item to.
            found[unit] = value
    return found


def put(table, key, value):
    """Add a key to a table. The proxy of a split table has no add, so the key goes in by key there."""
    if hasattr(table, 'add'):
        table.add(key, value)
    else:
        table[key] = value


def is_table(value):
    """True for a table that keys can go in: a table, an inline table, the document, or the proxy that tomlkit gives
    for a table split by other tables, as [tool.a], [project], [tool.b]."""
    return hasattr(value, '__delitem__') and hasattr(value, 'items') and not isinstance(value, list)


def container(document, path, made):
    """The table at path, made where it is missing. Each table that this makes goes into made."""
    table = document
    for depth, part in enumerate(path):
        if part not in table:
            if isinstance(table, tomlkit.items.InlineTable):
                new = tomlkit.inline_table()
            else:
                new = tomlkit.table(is_super_table=depth + 1 < len(path))
            # The proxy of a split table has no append, so the new table goes in by key.
            table[part] = new
            made.append(key_text(path[:depth + 1]))
        table = table[part]
        if not is_table(table):
            raise EditError(f'{key_text(path[:depth + 1])} is not a table')
    return table


def check_inline(document, path):
    """Stop at an inline table with dotted keys on the way to path, as {a.b = 1}, because tomlkit edits it wrongly."""
    table = document
    for part in path:
        if not is_table(table) or part not in table:
            return
        table = table[part]
        if isinstance(table, tomlkit.items.InlineTable) and any(
                name is not None and name.is_dotted() for name, _ in table.value.body):
            raise EditError(f'{key_text(path)} is in an inline table with dotted keys, which the installer does not '
                            f'edit')


def drop_inline(table, key):
    """Take a key out of an inline table with the comma next to it, so the rest keeps its text: the whitespace and
    comma after the key before it, or else the ones before the key after it."""
    body = table.value.body
    keys = [index for index, (name, _) in enumerate(body) if name is not None]
    index = next((i for i in keys if not body[i][0].is_dotted() and body[i][0].key == key), None)
    if index is None:
        raise EditError(f'{key} is a dotted key in an inline table, which the installer does not edit')
    before = [i for i in keys if i < index]
    after = [i for i in keys if i > index]
    gap = range(before[-1] + 1, index) if before else range(index + 1, after[0]) if after else range(0)
    del table[key]
    body[:] = [entry for i, entry in enumerate(body)
               if i not in gap and not (entry[0] is None and isinstance(entry[1], tomlkit.items.Null))]


def find_table(document, path):
    """The table at path, or None when it is missing or is not a table."""
    table = document
    for part in path:
        if not hasattr(table, 'get') or part not in table:
            return None
        table = table[part]
    # A table of dotted keys, such as tool.uv.x = 1, is a proxy with no remove, so del takes out its keys.
    return table if is_table(table) else None


def expected_after(data, adds, removes):
    """The data that the text must hold after the change."""
    data = copy.deepcopy(data)
    for unit, made in removes:
        path, item = parse_name(unit)
        parent = lookup(data, path[:-1])
        if not isinstance(parent, dict) or path[-1] not in parent:
            continue
        if item is MISSING:
            del parent[path[-1]]
        elif isinstance(parent[path[-1]], list):
            parent[path[-1]] = [value for value in parent[path[-1]] if value != item]
    drop_made(data, [key for _, made in removes for key in made])
    for unit, value, _ in adds:
        path, item = parse_name(unit)
        parent = data
        for part in path[:-1]:
            parent = parent.setdefault(part, {})
        if item is MISSING:
            parent[path[-1]] = value
        else:
            parent.setdefault(path[-1], [])
            if item not in parent[path[-1]]:
                parent[path[-1]].append(item)
    return data


def drop_made(data, made):
    """Take out each key or table in made that is now empty, the deepest first."""
    for text in sorted(made, key=lambda key: -len(parse_name(key)[0])):
        path = parse_name(text)[0]
        parent = lookup(data, path[:-1])
        if isinstance(parent, dict) and parent.get(path[-1]) in ({}, []):
            del parent[path[-1]]


def mark_line(mark):
    return f'# {mark}'


def plan(text, adds, removes):
    """The new text after the change. tomlkit raises plain errors for some layouts, such as a table in an inline table,
    and each one becomes an EditError, so the installer leaves the file alone with a message."""
    try:
        return edit(text, adds, removes)
    except EditError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
        raise EditError(f'tomlkit cannot make the change: {exc}')


def edit(text, adds, removes):
    """The new text after the change, and the keys and tables that each added unit made.

    adds: [(unit name, value, mark or None)]. removes: [(unit name, made)], where made lists the keys and tables that
    the add of the unit made; each one that is empty after the removal goes too. A mark is the text of one comment,
    which goes on the line before a key unit, and goes with it.
    """
    document = load(text or '')
    before = plain(document)
    for unit in [unit for unit, _ in removes] + [unit for unit, _, _ in adds]:
        check_inline(document, parse_name(unit)[0][:-1])
    for unit, made in removes:
        path, item = parse_name(unit)
        parent = find_table(document, path[:-1])
        if parent is None or path[-1] not in parent:
            continue
        if item is MISSING:
            if isinstance(parent, tomlkit.items.InlineTable):
                drop_inline(parent, path[-1])
                # drop_inline cuts the body of the table, so tomlkit must read the text again before the next edit.
                document = load(document.as_string())
            else:
                del parent[path[-1]]
        else:
            array = parent[path[-1]]
            if not isinstance(plain(array), list):
                continue
            for index in reversed([i for i, value in enumerate(array) if value == item]):
                del array[index]
    removed_text = document.as_string()
    for unit, made in removes:
        removed_text = drop_mark(removed_text, unit)
    document = load(removed_text)
    for unit, made in removes:
        for key in sorted(made, key=lambda key: -len(parse_name(key)[0])):
            path = parse_name(key)[0]
            parent = find_table(document, path[:-1])
            if parent is None or path[-1] not in parent:
                continue
            # A table that still holds tables, as [tool.uv] with [tool.uv.sources], stays. If its header is left
            # with nothing under it, drop_empty_headers takes the header out.
            if plain(parent[path[-1]]) in ({}, []):
                del parent[path[-1]]
    made_by = {}
    marks = []
    for unit, value, mark in adds:
        path, item = parse_name(unit)
        made = []
        existing = find_table(document, path[:-1])
        if isinstance(existing, tomlkit.items.Table) and existing.is_super_table():
            made.append(key_text(path[:-1]))
        parent = container(document, path[:-1], made)
        if item is MISSING:
            put(parent, path[-1], value)
            if mark:
                marks.append((path, mark))
        else:
            if path[-1] not in parent:
                put(parent, path[-1], tomlkit.array())
                made.append(key_text(path))
            if item not in plain(parent[path[-1]]):
                parent[path[-1]].append(item)
        made_by[unit] = made
    new = document.as_string()
    # A file with CRLF on each line keeps it on the lines that tomlkit adds. The ends are set on LF text first.
    crlf = bool(text) and '\n' in text and text.count('\r\n') == text.count('\n')
    if crlf:
        new = new.replace('\r\n', '\n')
    if removes and new.strip():
        new = new.rstrip('\n') + '\n'
    if text and not text.endswith('\n') and new.endswith('\n'):
        new = new[:-1]
    if crlf:
        new = new.replace('\n', '\r\n')
    for path, mark in marks:
        new = mark_key(new, path, mark)
    if removes:
        new = drop_empty_headers(text or '', new)
        new = keep_comments(text or '', new, [unit for unit, _ in removes])
    want = expected_after(before, adds, removes)
    got = plain(load(new))
    # tomllib reads a CRLF in a string over several lines as LF, and tomlkit keeps it, so both compare on LF.
    if got != want or lf(strict(new)) != lf(want):
        raise EditError('the change would alter more than its units')
    return new, made_by


HEADER = re.compile(r'^\s*\[(?!\[)\s*(.+?)\s*\]\s*(#.*)?$')


def comments(text):
    """The comments of the text, also a comment at the end of a line of a key. A scan of the text skips each string,
    so a # or quotes in a string count for nothing."""
    found, index, size, quote = [], 0, len(text), None
    while index < size:
        char = text[index]
        if quote is None:
            if char == '#':
                end = text.find('\n', index)
                end = size if end < 0 else end
                found.append(text[index:end].rstrip('\r').strip())
                index = end
                continue
            for mark in ('"""', "'''", '"', "'"):
                if text.startswith(mark, index):
                    quote = mark
                    index += len(mark)
                    break
            else:
                index += 1
            continue
        if char == '\\' and quote in ('"', '"""'):
            index += 2
            continue
        if text.startswith(quote, index):
            index += len(quote)
            # A string over several lines can end in up to two more quotes of its kind.
            while len(quote) == 3 and index < size and text[index] == quote[0]:
                index += 1
            quote = None
            continue
        if char == '\n' and len(quote) == 1:
            quote = None
        index += 1
    return found


def keep_comments(old, new, units):
    """Your comments stay when a removal takes out a table that the installer made and that you wrote a comment in.
    Each comment that the change lost, apart from the marks of the units that go, goes at the end of the file."""
    marks = [re.compile(rf'^# {re.escape(unit)} is a part of .+\. The record is parts\..+$') for unit in units]
    left = comments(new)
    lost = []
    for comment in comments(old):
        if comment in left:
            left.remove(comment)
        elif not any(mark.match(comment) for mark in marks):
            lost.append(comment)
    if not lost:
        return new
    ending = '\r\n' if '\n' in old and old.count('\r\n') == old.count('\n') else '\n'
    if new and not new.endswith('\n'):
        new += ending
    return new + ending.join(lost) + ending
ARRAY = object()


def key_path(text):
    """The path of a TOML key in any form, such as tool.uv, "tool".uv or 'tool' . uv, or None for no key."""
    try:
        data = tomllib.loads(f'{text.strip()} = 0')
    except tomllib.TOMLDecodeError:
        return None
    path = []
    while isinstance(data, dict) and len(data) == 1:
        (key, data), = data.items()
        path.append(key)
    return tuple(path) if data == 0 else None


def header_path(line):
    """The path of a [table] header line, ARRAY for an [[array]] header, or None for a line that is neither."""
    if line.lstrip().startswith('[['):
        return ARRAY
    match = HEADER.match(line)
    return key_path(match.group(1)) if match else None


def empty_headers(lines):
    """The index of each [table] header line with no key and no comment after it, before the next header. One pass
    from the end: each line knows whether the next line that is not blank is a header."""
    found = []
    next_is_header = True
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if not line.strip():
            continue
        header = header_path(line)
        if header is not None and header is not ARRAY and next_is_header:
            found.append(index)
        next_is_header = header is not None
    return sorted(found)


def drop_empty_headers(old, new):
    """Take out each header that the change left with nothing under it, with one blank line next to it, when that
    leaves the data as it is: a [tool.uv] whose keys went, while [tool.uv.sources] still makes the table."""
    old_lines = old.splitlines()
    before = {old_lines[index].strip() for index in empty_headers(old_lines)}
    lines = new.splitlines(keepends=True)
    data = plain(load(new))
    for index in reversed(empty_headers([line.rstrip('\r\n') for line in lines])):
        if lines[index].strip() in before:
            continue
        cut = {index}
        if index + 1 < len(lines) and not lines[index + 1].strip():
            cut.add(index + 1)
        elif index > 0 and not lines[index - 1].strip():
            cut.add(index - 1)
        trial = ''.join(line for number, line in enumerate(lines) if number not in cut)
        try:
            same = plain(load(trial)) == data
        except EditError:
            same = False
        if same:
            lines = trial.splitlines(keepends=True)
    return ''.join(lines)


def mark_key(text, path, mark):
    """Put the mark on the line before the key at path: a key under its own [table], or a dotted key under a table
    above it. A key inside an inline table gets no mark, because TOML 1.0 has no comments there. A line that only
    looks like the key, as in a string over several lines, is skipped, because a mark there would change the data."""
    lines = text.splitlines(keepends=True)
    data = plain(load(text))
    table = ()
    for index, line in enumerate(lines):
        header = header_path(line)
        if header is not None:
            table = None if header is ARRAY else header
            continue
        if table is None or '=' not in line or tuple(path[:len(table)]) != table or len(table) >= len(path):
            continue
        if key_path(line.split('=', 1)[0]) == tuple(path[len(table):]):
            ending = '\r\n' if line.endswith('\r\n') else '\n'
            trial = lines[:index] + [mark_line(mark) + ending] + lines[index:]
            try:
                same = plain(load(''.join(trial))) == data
            except EditError:
                same = False
            if same:
                return ''.join(trial)
    return text


def drop_mark(text, unit):
    """Take out the mark of a unit: a comment line that names the unit in the form that the installer writes."""
    pattern = re.compile(rf'^# {re.escape(unit)} is a part of .+\. The record is parts\..+$')
    lines = text.splitlines(keepends=True)
    return ''.join(line for line in lines if not pattern.match(line.rstrip('\r\n')))


def empty(text):
    """True when the text holds no key and no table."""
    return not plain(load(text or ''))
