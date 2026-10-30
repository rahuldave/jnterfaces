"""Tests for scripts/edit_toml.py and scripts/edit_yaml.py: add, add again, and take out the units of a part."""
from pathlib import Path
import re
import sys
import time
import tomllib
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import edit_toml  # noqa: E402
import edit_yaml  # noqa: E402

MARK = re.compile(r'^.+ is a part of .+\. The record is parts\..+$')


def mark(unit):
    return f'{unit} is a part of python:lint, the lint tool. The record is parts."x" in .agents/stack.json.'


class TomlTests(unittest.TestCase):
    def test_names_of_keys_and_items_go_both_ways(self):
        for path, item in ((('tool', 'uv', 'default-groups'), edit_toml.MISSING), (('src', 'exclude'), '.agents'),
                           (('a.b', 'c d'), edit_toml.MISSING), (('x',), 3)):
            self.assertEqual(edit_toml.parse_name(edit_toml.name(path, item)), (path, item))

    def test_the_units_of_a_source_are_its_keys_and_the_items_of_its_plain_arrays(self):
        units = edit_toml.units('a = 1\n[src]\nexclude = [".agents", ".claude"]\nroots = []\n[[t]]\nx = 1\n')
        self.assertEqual(units, {'a': 1, 'src.exclude[".agents"]': '.agents', 'src.exclude[".claude"]': '.claude',
                                 'src.roots': [], 't': [{'x': 1}]})

    def test_a_new_file_gets_the_units_and_loses_them_again(self):
        adds = [('src.exclude[".agents"]', '.agents', None), ('src.exclude[".codex"]', '.codex', None)]
        text, made = edit_toml.plan('', adds, [])
        self.assertEqual(text, '[src]\nexclude = [".agents", ".codex"]\n')
        self.assertEqual(made['src.exclude[".agents"]'], ['src', 'src.exclude'])
        again, _ = edit_toml.plan(text, adds, [])
        self.assertEqual(again, text)
        gone, _ = edit_toml.plan(text, [], [(unit, made[unit]) for unit, _, _ in adds])
        self.assertTrue(edit_toml.empty(gone))
        self.assertEqual(gone, '')

    def test_your_comments_and_keys_stay_and_the_mark_goes_with_the_key(self):
        mine = '# top\n[project]\nname = "app"  # the name\n\n[tool.uv]\npackage = false\n'
        unit = 'tool.uv.default-groups'
        text, made = edit_toml.plan(mine, [(unit, 'all', mark(unit))], [])
        self.assertIn(f'package = false\n# {mark(unit)}\ndefault-groups = "all"\n', text)
        self.assertEqual(made[unit], [])
        self.assertEqual(edit_toml.read(text, [unit]), {unit: 'all'})
        gone, _ = edit_toml.plan(text, [], [(unit, made[unit])])
        self.assertEqual(gone, mine)

    def test_a_key_at_the_top_goes_before_the_first_table_with_its_mark(self):
        text, _ = edit_toml.plan('x = 1\n[a]\nb = 2\n', [('extend', 'p.toml', mark('extend'))], [])
        self.assertEqual(text, f'x = 1\n# {mark("extend")}\nextend = "p.toml"\n\n[a]\nb = 2\n')
        gone, _ = edit_toml.plan(text, [], [('extend', [])])
        self.assertEqual(edit_toml.read(gone, ['extend', 'x']), {'x': 1})
        self.assertNotIn('is a part of', gone)

    def test_tables_that_the_add_made_go_when_they_are_empty(self):
        unit = 'tool.uv.default-groups'
        text, made = edit_toml.plan('[project]\nname = "a"\n', [(unit, 'all', None)], [])
        self.assertEqual(made[unit], ['tool', 'tool.uv'])
        gone, _ = edit_toml.plan(text, [], [(unit, made[unit])])
        self.assertEqual(gone, '[project]\nname = "a"\n')

    def round_trip(self, mine, unit='tool.uv.default-groups'):
        text, made = edit_toml.plan(mine, [(unit, 'all', mark(unit))], [])
        tomllib.loads(text)
        self.assertEqual(edit_toml.read(text, [unit]), {unit: 'all'})
        gone, _ = edit_toml.plan(text, [], [(unit, made[unit])])
        self.assertEqual(gone, mine)
        return text

    def test_a_key_in_an_inline_table_gets_no_mark_and_stays_toml_1_0(self):
        text = self.round_trip('[tool]\nuv = {package = false}\n')
        self.assertEqual(text, '[tool]\nuv = {package = false, default-groups = "all"}\n')
        gone, _ = edit_toml.plan('[tool]\nuv = {default-groups = "all", package = false}\n', [],
                                 [('tool.uv.default-groups', [])])
        self.assertEqual(gone, '[tool]\nuv = {package = false}\n')

    def test_a_key_among_dotted_keys_goes_again(self):
        text = self.round_trip('[tool]\nuv.package = false\n')
        self.assertIn(f'# {mark("tool.uv.default-groups")}\nuv.default-groups = "all"\n', text)
        self.round_trip('tool.uv.dev-dependencies = []\n')

    def test_a_table_that_only_its_subtables_made_gets_the_mark_and_loses_its_header_again(self):
        unit = 'tool.uv.default-groups'
        text = self.round_trip('[project]\nname = "a"\n\n[tool.uv.sources]\nfoo = { workspace = true }\n\n'
                               '[tool.pytest.ini_options]\nx = 1\n')
        self.assertIn(f'[tool.uv]\n# {mark(unit)}\ndefault-groups = "all"\n', text)
        self.assertEqual(text.count('is a part of'), 1)

    def test_a_change_that_tomllib_cannot_read_fails(self):
        # tomlkit reads an inline table over two lines, which TOML 1.0 and the tools of a project do not.
        with self.assertRaises(edit_toml.EditError):
            edit_toml.plan('[tool]\nuv = {package = false,\n  x = 1}\n', [('tool.uv.default-groups', 'all', None)], [])

    def test_tool_tables_split_by_another_table_take_the_key(self):
        # uv add keeps [dependency-groups] where it is, so [tool.*] tables come before and after it.
        text = self.round_trip('[project]\nname = "a"\n\n[tool.black]\nx = 1\n\n[dependency-groups]\nlint = []\n\n'
                               '[tool.mypy]\ny = 2\n')
        self.assertIn(f'[tool.uv]\n# {mark("tool.uv.default-groups")}\ndefault-groups = "all"\n', text)
        self.round_trip('[tool.uv.sources]\nf = 1\n\n[project]\nname = "a"\n\n[tool.uv.index]\nurl = "x"\n')

    def test_an_inline_tool_table_takes_the_key_or_fails_cleanly(self):
        self.round_trip('tool = {black = {line-length = 100}}\n[project]\nname = "app"\n')
        with self.assertRaises(edit_toml.EditError):
            edit_toml.plan('tool = {uv = 1}\n', [('tool.uv.default-groups', 'all', None)], [])

    def test_a_plain_error_of_tomlkit_becomes_an_edit_error(self):
        with mock.patch.object(edit_toml, 'edit', side_effect=ValueError('Inline tables cannot contain a table')):
            with self.assertRaises(edit_toml.EditError):
                edit_toml.plan('', [('a', 1, None)], [])

    def test_a_key_goes_from_an_inline_table_with_its_comma_in_any_place(self):
        for mine, unit, left in (('t = {a=1,b=2,c=3}\n', 't.c', 't = {a=1,b=2}\n'),
                                 ('t = {a=1,b=2,c=3}\n', 't.a', 't = {b=2,c=3}\n'),
                                 ('t = {a=1,b=2,c=3}\n', 't.b', 't = {a=1,c=3}\n'),
                                 ('t = {a=1 ,c={d=1}}\n', 't.c', 't = {a=1}\n'),
                                 ('t = { a = 1, b = 2 }\n', 't.a', 't = { b = 2 }\n'),
                                 ('t = {a = 1, b = 2} # c\n', 't.b', 't = {a = 1} # c\n')):
            self.assertEqual(edit_toml.plan(mine, [], [(unit, [])])[0], left, (mine, unit))

    def test_an_inline_table_with_dotted_keys_is_left_alone(self):
        for mine, unit in (('t = {a.b = 1, c = 2}\n', 't.a.b'), ('t = {c = 2, a.b = 1, a.d = 3}\n', 't.a.d')):
            with self.assertRaises(edit_toml.EditError):
                edit_toml.plan(mine, [], [(unit, [])])

    def test_the_mark_goes_on_the_line_of_the_key_in_any_form(self):
        for mine, unit, before in (('[[arr]]\na.b = 1\n', 'a.b', '\nb = "all"\n'),
                                   ("['tool']\nx = 1\n", 'tool.uv', '\nuv = "all"\n'),
                                   ('[tool]\nd = """\nuv = 1\n"""\n', 'tool.uv', '\nuv = "all"\n'),
                                   ('[ tool . uv ]\nx = 1\n', 'tool.uv.y', '\ny = "all"\n')):
            text = self.round_trip(mine, unit)
            self.assertIn(f'# {mark(unit)}{before}', text, mine)

    def test_a_table_that_uv_filled_later_keeps_its_subtable_when_the_key_goes(self):
        unit = 'tool.uv.default-groups'
        text, made = edit_toml.plan('[project]\nname = "a"\n', [(unit, 'all', mark(unit))], [])
        text += '\n[tool.uv.sources]\nlib = { path = "../lib" }\n'
        gone, _ = edit_toml.plan(text, [], [(unit, made[unit])])
        self.assertEqual(gone, '[project]\nname = "a"\n\n[tool.uv.sources]\nlib = { path = "../lib" }\n')

    def test_two_keys_go_from_one_inline_table_in_one_change(self):
        for units, left in ((('t.a', 't.c'), 't = {b = 2}\n'), (('t.b', 't.c'), 't = {a = 1}\n'),
                            (('t.a', 't.b'), 't = {c = 3}\n')):
            self.assertEqual(edit_toml.plan('t = {a = 1, b = 2, c = 3}\n', [], [(u, []) for u in units])[0], left)

    def test_a_crlf_file_keeps_crlf_on_the_new_lines(self):
        text = self.round_trip('[tool.uv]\r\npackage = false\r\n')
        self.assertEqual(text.count('\r\n'), text.count('\n'))

    def test_a_file_with_no_newline_at_all_takes_an_add(self):
        self.assertEqual(edit_toml.plan('line-length = 100', [('extend', 'x.toml', None)], [])[0],
                         'line-length = 100\nextend = "x.toml"')
        self.assertEqual(edit_toml.plan('a = 1\r\nb = 2', [('extend', 'x.toml', None)], [])[0],
                         'a = 1\r\nb = 2\r\nextend = "x.toml"')
        self.round_trip('[project]\r\nname = "x"\r\n')

    def test_your_comments_stay_when_a_made_table_goes(self):
        unit = 'tool.uv.default-groups'
        text, made = edit_toml.plan('[project]\nname = "x"\n', [(unit, 'all', mark(unit))], [])
        for mine, comment in ((text.replace('"all"\n', '"all"\n# keep me\n'), '# keep me'),
                              (text.replace('[tool.uv]\n', '[tool.uv]  # uv settings\n'), '# uv settings'),
                              (text + '# my own note\n', '# my own note')):
            gone, _ = edit_toml.plan(mine, [], [(unit, made[unit])])
            self.assertEqual(gone, f'[project]\nname = "x"\n{comment}\n')

    def test_a_kept_comment_keeps_the_line_ends_of_the_file(self):
        self.assertEqual(edit_toml.plan('x = 1\n[t]\r\n# c\r\nk = 1\r\n', [], [('t.k', ['t'])])[0], 'x = 1\n# c\n')

    def test_a_line_in_a_string_over_several_lines_is_no_comment(self):
        for quote in ('"""', "'''"):
            mine = f'a = 1\nb = {quote}\n# not a comment\n{quote}\nc = 2\n'
            self.assertEqual(edit_toml.plan(mine, [], [('b', [])])[0], 'a = 1\nc = 2\n')

    def test_quotes_in_comments_and_strings_do_not_hide_a_comment(self):
        for head in ('# use """ here\n', "s = '''he said \"\"\"'''\n", 's = """\nit\'s \'\'\' odd\n"""\n',
                     's = """a\\""""\n', "a = '\"\"\"'\n"):
            mine = head + '[tool.x]\n# my note\na = 1\n'
            self.assertEqual(edit_toml.plan(mine, [], [('tool.x.a', ['tool.x', 'tool'])])[0], head + '# my note\n', head)
        self.assertEqual(edit_toml.comments('x = "a # b" # trailing\n[t] # head\n  # own\n'),
                         ['# trailing', '# head', '# own'])
        # An escaped quote does not end a string, so the line after it is still in the string.
        self.assertEqual(edit_toml.comments('s = """a\\"""\n# inside\n"""\n'), [])

    def test_a_crlf_file_with_a_string_over_several_lines_takes_a_change(self):
        self.assertEqual(edit_toml.plan('a = """x\r\ny"""\r\nb = 1\r\n', [], [('b', [])])[0], 'a = """x\r\ny"""\r\n')

    def test_a_comment_at_the_end_of_a_line_that_goes_stays(self):
        unit = 'tool.uv.default-groups'
        text, made = edit_toml.plan('[project]\nname = "x"\n', [(unit, 'all', mark(unit))], [])
        mine = text.replace('default-groups = "all"\n', 'default-groups = "all"  # why all\n')
        self.assertEqual(edit_toml.plan(mine, [], [(unit, made[unit])])[0], '[project]\nname = "x"\n# why all\n')

    def test_a_file_with_many_tables_is_quick(self):
        mine = ''.join(f'[tool.x{index}]\na = 1\nb = 2\n' for index in range(400))
        started = time.monotonic()
        self.round_trip(mine)
        self.assertLess(time.monotonic() - started, 3)

    def test_a_header_that_a_removal_leaves_empty_goes(self):
        mine = '[project]\nname = "a"\n\n[tool.uv.sources]\nf = 1\n'
        first, made_a = edit_toml.plan(mine, [('tool.uv.a', 1, mark('tool.uv.a'))], [])
        second, made_b = edit_toml.plan(first, [('tool.uv.b', 2, mark('tool.uv.b'))], [])
        gone, _ = edit_toml.plan(second, [], [('tool.uv.a', made_a['tool.uv.a'])])
        gone, _ = edit_toml.plan(gone, [], [('tool.uv.b', made_b['tool.uv.b'])])
        self.assertEqual(gone, mine)
        split = '[tool.uv]\ndefault-groups = "all"\n\n[project]\nname = "a"\n\n[tool.uv.sources]\nf = 1\n'
        gone, _ = edit_toml.plan(split, [], [('tool.uv.default-groups', [])])
        self.assertEqual(gone, '[project]\nname = "a"\n\n[tool.uv.sources]\nf = 1\n')
        # A table of yours keeps its header, because its going would change the data.
        self.assertEqual(edit_toml.plan('[a]\n[b]\nx = 1\n', [], [('b.x', [])])[0], '[a]\n[b]\n')

    def test_a_file_with_no_final_newline_keeps_it_that_way(self):
        self.round_trip('[project]\nname = "a"')

    def test_a_key_that_is_not_a_table_on_the_way_fails(self):
        with self.assertRaises(edit_toml.EditError):
            edit_toml.plan('tool = 1\n', [('tool.uv.x', 1, None)], [])

    def test_text_that_does_not_parse_fails(self):
        with self.assertRaises(edit_toml.EditError):
            edit_toml.read('[a\n', ['a'])


class YamlTests(unittest.TestCase):
    SOURCE = ('# top\nrepos:\n  # checks of any file\n  - repo: https://a\n    rev: v1\n    hooks:\n      - id: x\n\n'
              '  - repo: https://b\n    rev: v2\n    hooks:\n      - id: y\n')

    def adds(self):
        return [(unit, rows, mark(unit)) for unit, (_, rows) in edit_yaml.units(self.SOURCE, 'repos', 'repo').items()]

    def test_the_units_of_a_source_are_its_items_without_their_comments(self):
        units = edit_yaml.units(self.SOURCE, 'repos', 'repo')
        self.assertEqual(list(units), ['repos[repo=https://a]', 'repos[repo=https://b]'])
        self.assertEqual(units['repos[repo=https://a]'][1], ['- repo: https://a', '  rev: v1', '  hooks:', '    - id: x'])

    def test_a_new_file_gets_the_items_and_goes_when_they_go(self):
        text = edit_yaml.plan('', self.adds(), [])
        self.assertTrue(text.startswith('repos:\n  # repos[repo=https://a] is a part of'), text)
        self.assertEqual(edit_yaml.plan(text, [], [unit for unit, _, _ in self.adds()], MARK), '')

    def test_your_items_keep_their_comments_and_indent(self):
        mine = ('repos:\n    # mine\n    -   repo: local\n        hooks: []\n\n    -   repo: https://c   # trailing\n'
                '        rev: v3\n        hooks: [{id: z}]\n')
        text = edit_yaml.plan(mine, self.adds(), [])
        self.assertTrue(text.startswith(mine))
        self.assertIn('\n    - repo: https://a\n      rev: v1\n', text)
        self.assertEqual(edit_yaml.read(text, ['repos[repo=https://b]'])['repos[repo=https://b]']['rev'], 'v2')
        self.assertEqual(edit_yaml.plan(text, [], [unit for unit, _, _ in self.adds()], MARK), mine)

    def test_one_item_goes_and_the_others_stay(self):
        text = edit_yaml.plan('', self.adds(), [])
        left = edit_yaml.plan(text, [], ['repos[repo=https://a]'], MARK)
        self.assertNotIn('https://a', left)
        self.assertEqual(list(edit_yaml.read(left, ['repos[repo=https://b]'])), ['repos[repo=https://b]'])

    def test_a_list_in_flow_style_fails(self):
        with self.assertRaises(edit_yaml.EditError):
            edit_yaml.plan('repos: [{repo: x}]\n', self.adds(), [])

    def test_an_empty_list_gets_the_items(self):
        for mine in ('repos:\n', 'repos: []\n', 'default_stages: [pre-commit]\nrepos:\n'):
            text = edit_yaml.plan(mine, self.adds(), [])
            self.assertEqual(list(edit_yaml.read(text, ['repos[repo=https://a]'])), ['repos[repo=https://a]'], mine)
            self.assertEqual(text.count('repos:'), 1)

    def test_a_file_without_the_list_gets_it_at_the_end(self):
        text = edit_yaml.plan('default_stages: [pre-commit]\n', self.adds()[:1], [])
        self.assertTrue(text.startswith('default_stages: [pre-commit]\n\nrepos:\n'), text)


if __name__ == '__main__':
    unittest.main()
