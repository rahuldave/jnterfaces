# /// script
# requires-python = ">=3.11"
# dependencies = ["ruamel.yaml==0.19.1"]
# ///
"""Add and take out the units of a part in a YAML file, such as the repos of .pre-commit-config.yaml.

docs/plan-snippets.md gives the decisions. A unit is an item of a top-level list, known by one of its keys: the item
of repos whose repo is https://github.com/astral-sh/ruff-pre-commit. Its name is the list, the key and the value:
repos[repo=https://github.com/astral-sh/ruff-pre-commit].

A write with ruamel.yaml moves comments and changes the indent. So ruamel.yaml only finds the rows of each item. The
rows are added and cut as text, with the indent of the other items, and the new text is read again to check it.
"""
import json
import re

from ruamel.yaml import YAML

NAME = re.compile(r'^([A-Za-z0-9_-]+)\[([A-Za-z0-9_-]+)=(.+)\]$')


class EditError(Exception):
    pass


def name(listed, key, value):
    return f'{listed}[{key}={value}]'


def parse_name(text):
    match = NAME.match(text)
    if not match:
        raise EditError(f'{text} is not the name of a YAML unit')
    return match.groups()


def load(text):
    try:
        return YAML(typ='rt').load(text or '')
    except Exception as exc:  # ruamel.yaml raises several kinds of parse error
        raise EditError(f'it does not parse as YAML: {exc}')


def plain(value):
    return json.loads(json.dumps(value, default=str))


def units(text, listed, key):
    """The units of a source file: {name: (data, rows)} for each item of the list, where rows is the text of the item
    with its dash at column 0, without the comments above it."""
    data = load(text)
    found = {}
    if not isinstance(data, dict) or not isinstance(data.get(listed), list):
        return found
    lines = text.splitlines()
    for index, item, rows in items(data[listed], lines):
        if isinstance(item, dict) and isinstance(item.get(key), str):
            found[name(listed, key, item[key])] = (plain(item), rows)
    return found


def items(sequence, lines):
    """Each item of a block list, with its index and its rows at column 0: from the dash to its last row that is not
    blank and not a comment."""
    starts = [sequence.lc.item(index)[0] for index in range(len(sequence))]
    out = []
    for index, start in enumerate(starts):
        dash = lines[start].index('-') if '-' in lines[start] else None
        if dash is None or not lines[start][:dash].strip() == '':
            raise EditError('the list is not a block list with one dash for each item')
        end = start
        limit = starts[index + 1] if index + 1 < len(starts) else len(lines)
        for row in range(start + 1, limit):
            text = lines[row]
            if not text.strip() or text.lstrip().startswith('#'):
                continue
            if len(text) - len(text.lstrip()) <= dash:
                break
            end = row
        rows = [line[dash:] if line.strip() else '' for line in lines[start:end + 1]]
        out.append((index, sequence[index], rows))
    return out


def spans(text, listed, key):
    """{name: (first row, last row, dash column)} of each item of the list in the text."""
    data = load(text)
    if not isinstance(data, dict) or listed not in data:
        return data, {}
    sequence = data[listed]
    if sequence is None:
        return data, {}
    if not isinstance(sequence, list):
        raise EditError(f'{listed} is not a list')
    if sequence and getattr(sequence.fa, '_flow_style', False):
        raise EditError(f'{listed} is a list in flow style, [...]')
    lines = text.splitlines()
    found = {}
    for index, item, rows in items(sequence, lines):
        if isinstance(item, dict) and isinstance(item.get(key), str):
            start = sequence.lc.item(index)[0]
            dash = lines[start].index('-')
            found[name(listed, key, item[key])] = (start, start + len(rows) - 1, dash)
    return data, found


def read(text, names):
    """The data of each named unit that the text has."""
    found = {}
    data = load(text or '')
    for unit in names:
        listed, key, value = parse_name(unit)
        sequence = data.get(listed) if isinstance(data, dict) else None
        for item in sequence if isinstance(sequence, list) else []:
            if isinstance(item, dict) and item.get(key) == value:
                found[unit] = plain(item)
                break
    return found


def plan(text, adds, removes, mark_pattern=None):
    """The new text after the change.

    adds: [(unit name, rows, mark or None)], where rows is the text of the item at column 0. removes: [unit name]. A
    mark is one comment on the row before the item. It goes with the item when mark_pattern matches it.
    """
    text = text or ''
    newline = '\r\n' if text.count('\r\n') > text.count('\n') / 2 else '\n'
    lines = text.splitlines()
    before = load(text)
    expect = plain(before) if before is not None else {}
    listed = {parse_name(unit)[0] for unit, _, _ in adds} | {parse_name(unit)[0] for unit in removes}
    if len(listed) > 1:
        raise EditError('one change edits one list')
    if not listed:
        return text
    listed = listed.pop()
    key = parse_name((adds or [(u, None, None) for u in removes])[0][0])[1]
    _, found = spans(text, listed, key)
    cut = set()
    for unit in removes:
        if unit not in found:
            continue
        first, last, _ = found[unit]
        cut.update(range(first, last + 1))
        if first > 0 and mark_pattern and mark_pattern.match(lines[first - 1].strip().lstrip('#').strip()):
            cut.add(first - 1)
        # One blank row next to the item goes with it: the one before it, else the one after it.
        top = min(index for index in cut if index >= first - 1 and index <= last)
        if top > 0 and not lines[top - 1].strip():
            cut.add(top - 1)
        elif last + 1 < len(lines) and not lines[last + 1].strip():
            cut.add(last + 1)
        _, _, value = parse_name(unit)
        expect[listed] = [item for item in expect.get(listed, []) if not (isinstance(item, dict)
                                                                          and item.get(key) == value)]
    lines = [line for index, line in enumerate(lines) if index not in cut]
    if removes and expect.get(listed) == [] and not adds:
        # The last item went, so the list goes too.
        lines = [line for line in lines if not re.match(rf'^{re.escape(listed)}:\s*$', line)]
        del expect[listed]
        while lines and not lines[-1].strip():
            lines.pop()
    if adds:
        current = load(newline.join(lines))
        if not isinstance(current, dict) or listed not in current or current[listed] in (None, []):
            if isinstance(current, dict) and listed in current:
                # An empty list, as repos: or repos: [], gives its line to the new block list.
                lines = [line for line in lines
                         if not re.match(rf'^{re.escape(listed)}:\s*(\[\s*\])?\s*(#.*)?$', line)]
            if lines and lines[-1].strip():
                lines.append('')
            lines.append(f'{listed}:')
            dash = 2
            insert = len(lines)
        else:
            sequence = current[listed]
            if not isinstance(sequence, list) or not sequence:
                raise EditError(f'{listed} is not a list with items in block style')
            if getattr(sequence.fa, '_flow_style', False):
                raise EditError(f'{listed} is a list in flow style, [...]')
            start = sequence.lc.item(len(sequence) - 1)[0]
            dash = lines[start].index('-')
            insert = start + len(items(sequence, lines)[-1][2])
        block = []
        for unit, rows, mark in adds:
            block.append('')
            if mark:
                block.append(' ' * dash + '# ' + mark)
            block += [(' ' * dash + row) if row else '' for row in rows]
            expect.setdefault(listed, [])
            expect[listed] = (expect[listed] or []) + [plain(load('\n'.join(rows))[0])]
        if insert == len(lines) and lines and lines[-1] == f'{listed}:':
            block = block[1:]
        lines[insert:insert] = block
    new = newline.join(lines) + (newline if lines else '')
    if not new.strip():
        return ''
    got = load(new)
    if (plain(got) if got is not None else {}) != expect:
        raise EditError('the change would alter more than its units')
    return new


def empty(text):
    """True when the text holds no data, or only an empty list."""
    data = load(text or '')
    return data is None or (isinstance(data, dict) and all(value in (None, [], {}) for value in data.values()))

