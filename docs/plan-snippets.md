# Plan for snippets in every shared file

Status: plan. Nothing is built. An adversarial review of the first draft
changed each decision and added S13 to S15. This plan extends
`docs/plan-shared-files.md`, which gave line files, such as `.gitignore`,
blocks and a log. This plan gives the same blocks and log to the other
files that a package changes.

## The problem

A package must add its part to a file and take out only its part. Today
only the line files work in this way. In other files, a package writes the
whole file, or it uses a merge of its own with a record of its own:

| What a package adds | Where it goes | How today |
|---|---|---|
| The lines of a profile or a tool | `.gitignore`, `.worktreedisposable` | Blocks and the log. Built. |
| The settings of a tool | `ruff.toml`, `ty.toml`, `biome.json`, `tsconfig.json` | The whole file, with owners and a hash in the log |
| The checks before a commit | `.pre-commit-config.yaml` | The whole file, with an owner and a hash in the log |
| The agent instructions of a role | `AGENTS.md` | The whole file, once, as a seed. Never changed again. |
| The hooks of a role | `.claude/settings.json`, `.codex/hooks.json` | A merge of hooks, recorded in `.agents/<package>-install.json` |
| The default groups of uv | `pyproject.toml` | A change by hand-written code, with no record |

A whole file causes two problems:

- When you change the file, the package can no longer change its own
  settings in it. A newer profile cannot add a setting.
- Two packages cannot each add a part to the same file. For example, a
  second role cannot add its instructions to `AGENTS.md`.

Three different records also mean that `just install --check` sees only
part of what the installer did.

## Terms

The terms of `docs/plan-shared-files.md` hold here. These terms are new:

- **Snippet.** The part of a file that one owner adds. It is one or more
  blocks, or, in a JSON file, a set of keys.
- **Anchor.** The line that a block goes after, such as `[lint]` in a TOML
  file or `repos:` in a YAML file. A block with no anchor goes at the place
  that its file kind sets.
- **Own file.** A file that only one package writes, at a path that the
  package names, such as `.agents/skills/gtw/SKILL.md`. Nobody else adds a
  part to it.
- **Shared file.** Each file that is not an own file. A person or a second
  package can add a part to it.
- **Starter.** Rows that the installer writes once into your part of a new
  file, such as `Project name: <replace-me>`. They are yours from the start.
- **Edited block.** A block whose rows differ from the rows that the log
  records for it.

## Decisions

### S1. A shared file gets snippets, never a whole file

A package adds a snippet to a shared file. The installer adds the snippet
on install and takes it out on uninstall. A dry run prints the snippet that
each change adds or takes out. If the file does not exist, the snippet
makes it.

When the last snippet goes, the installer deletes the file if nothing else
is left in it. Only blocks, and anchor lines that the log says the
installer added, count as nothing else. A file with a row of yours stays.

Reason: an empty `ruff.toml` still makes ruff skip `[tool.ruff]` in
`pyproject.toml`, and an empty `.pre-commit-config.yaml` is not valid. So a
file that holds only what the installer made must go.

An own file stays whole. The own files are the skills, the hook scripts,
the role recipes in `.agents/just/roles/`, and the generated recipe index in
`.agents/just/`. No one adds a part to them, so a snippet gives nothing.

### S2. Each file kind has one snippet form

| File kind | Files | Unit of a snippet | Begin and end lines | Default place |
|---|---|---|---|---|
| Lines | `.gitignore`, `.worktreedisposable`, `.agents/.gitattributes` | Each row, as now | `# begin …` and `# end …` | Start of the file |
| TOML | `ruff.toml`, `ty.toml`, `pyproject.toml` | A block for each table | `# begin …` and `# end …` | After the anchor. The block of top-level keys goes at the start. |
| YAML | `.pre-commit-config.yaml` | A block for each top-level key | `# begin …` and `# end …`, with the indent of the items | After the anchor |
| Markdown | `AGENTS.md` | One block | `<!-- begin … -->` and `<!-- end … -->` | End of the file |
| JSON | `biome.json`, `tsconfig.json`, `.claude/settings.json`, `.codex/hooks.json` | Each key | None. JSON has no comments. | Merged into the object |

Each block keeps the begin and end lines of D2 in the first plan. They name
the owner, the package and its commit, the file, the command, and the entry
of the log. Only the comment marks change with the file kind.

The unit is what the installer adds, finds and takes out as one thing. A
row of a TOML, YAML or Markdown block has a meaning only in its place. So
the installer never moves one row of such a block. It works on the whole
block.

Reason: the blocks and the log work for each kind that has comments. JSON
has none, so its keys must be the record, as the hook merge does today.

### S3. A package ships an ordinary file, and the installer cuts it into snippets

A package ships the file as a person would write it, such as a normal
`ruff.toml`, with its comments. The installer cuts it into blocks:

- **TOML.** The rows before the first table form a block with no anchor.
  Each table, such as `[lint]`, forms a block whose anchor is the table
  line. A package source may not have an array of tables, such as
  `[[tool]]`, or a dotted key that names a table of the source.
- **YAML.** Each top-level key with indented rows, such as `repos:`, forms a
  block whose anchor is the key line. The rows `key: value` at the top level
  form a block with no anchor. A package source may not use a value on the
  anchor line, such as `repos: []`.
- **Markdown.** The whole file is one block.
- **JSON.** Each key path with a value that is not an object, such as
  `compilerOptions.strict`, is one key of the snippet. An array is one
  value, except at the paths of S8.

A comment row goes with the rows after it. So the comments of the package
stay next to its settings in your file. A package source must not say "this
file is yours" in a comment, because the comment lands in the block of the
package. The sources of today change for this.

The checks of `profile.json` and `skill-package.json` reject a source that
breaks these rules.

### S4. Finding the anchor

The installer finds a TOML table by reading the file with `tomllib` and by
comparing the table lines after it takes out spaces and quotes. So
`[ lint ]`, `["lint"]` and `[lint] # mine` all match `[lint]`.

If the file lacks the table, the installer adds the anchor line, then the
block. The log records that the installer added the line. If you defined
the table in another form, such as `lint.select = [...]` at the top level
or `lint = {...}`, a new `[lint]` line would break the file. Then the
installer writes no block for that table, and names the table and the
owner. The other blocks of the file go in as usual.

For YAML, the installer reads the indent of the items under the anchor and
writes the block with that indent. If the anchor line has a value, such as
`repos: []`, the installer writes no block there and names the anchor.

Reason: a text match on `[lint]` misses the forms that TOML allows, and a
fixed indent breaks a list at column 0.

### S5. A key or an item that you already have wins

In a TOML or JSON file, the installer reads the keys of your part. A key of
a snippet that your part already sets counts as `present`, and the block
leaves it out. If your value differs from the value of the snippet, the
output names the key and keeps your value. A block that is empty after this
step does not go in, and its anchor does not go in.

In a YAML block, a list item that your part already has counts as
`present`. The installer compares the items by their first row, such as
`- repo: https://github.com/astral-sh/ruff-pre-commit`.

A Markdown block has no keys. If your part already holds the same text,
the installer cannot tell, and the block can repeat your text.

Reason: TOML allows each key once, so a second copy breaks the file.

### S6. The installer checks a file before it writes it

The installer parses the new text of a TOML or JSON file before it writes
it. It writes only when the result has each key of each snippet with its
value, and each key of your part with your value. Otherwise it writes
nothing to that file, and names the file and the key.

For a YAML file, the installer runs `pre-commit validate-config` through
`uv run` when the project has `pre-commit`. Without it, the installer does
not check YAML, and `pre-commit` checks the file when it runs. Markdown has
no check.

A JSON file with comments or a trailing comma, such as the `tsconfig.json`
that `tsc --init` writes, does not parse as JSON. The installer leaves it
alone. The output prints the keys that the snippet would add, so that you
can add them by hand.

The installer writes JSON with an indent of two spaces. An array of plain
values stays on one line when it fits in 120 characters, so the file keeps
the form of the package source.

### S7. Two owners of one key or one block

The rule of D4 in the first plan holds for keys and blocks too. The first
owner that needs a key or a block has it. When that owner goes, the key or
the block passes to the next owner that needs it, and stays in the file.
The order of the owners is the order of D4. It is `jnterfaces`, each
profile and its tools, then each role.

For example, `typescript:typecheck` and `typescript:test` both need
`tsconfig.json`. `just uninstall typecheck` passes each key to `test`, so
the file does not change.

If two active owners need one key with different values, the run stops
before it changes a file. The output names the key and the two owners. Two
owners can give the same value. Two TOML or YAML blocks under one anchor
are two blocks, one after the other, in the order of the owners.

### S8. Arrays that merge by item

A JSON array is one value, except at these paths, where each item is one
unit:

| File | Path | An item is known by |
|---|---|---|
| `.claude/settings.json`, `.codex/hooks.json` | `hooks.<event>` | Its group keys, then each hook by its command, as the hook merge does today |

The rules of the hook merge stay. Its record moves from `merged_hooks` in
`.agents/<package>-install.json` to the log. A new path needs a change to
this table, so two owners cannot add items to other arrays, such as
`files.includes` in `biome.json`.

### S9. A role is an owner

A role package declares snippets under `install_extras.snippets` in its
`skill-package.json`. A profile and a tool declare them under `snippets` in
`profile.json`. In both, the key is the target path and the value is the
source file in the package:

```json
"snippets": {"ruff.toml": "files/ruff.toml", ".pre-commit-config.yaml": "files/pre-commit-config.yaml"}
```

The `skip_if` guard of a tool stays. It stops a snippet from making a new
file, as it stops a whole file today. For example, the python profile makes
no `ruff.toml` when `pyproject.toml` has `[tool.ruff`.

The owner key of a role is its role name, such as `tasker`. The comment
names the package that fills the role. If you change the package of a role,
one run takes out the block of the old package. The same run adds the block
of the new package. A line or a block that you declined stays declined for
the role, whatever package fills it. The end line names `just uninstall
tasker`.

Each role installs through `install_stack.py`, also from the installer
skill of a role. So `install_stack.py` runs one pass over all owners. One
run writes one pair of entries in the log. `install_package.py` stops
writing shared files. It keeps the own files of a package and its record of
them.

### S10. Starters go in your part

A role can name starter rows under `install_extras.starter`, as
`{"AGENTS.md": "AGENTS.starter.md"}`. The installer writes them into your
part when this run makes the file. Your part of a Markdown file is at the
start, before the blocks. The log records the starter as `seeded`. The
installer never changes or takes out a starter.

`AGENTS.template.md` of agent_gest_git_skills splits in two:

- `AGENTS.starter.md`: the heading and the `Project Context` section with
  its `<replace-me>` values, which you fill in.
- `AGENTS.gest.md`: the Gest rules, which the role keeps up to date in its
  block.

### S11. The default groups of uv become a snippet

The setting `default-groups = "all"` under `[tool.uv]` in `pyproject.toml`
becomes a TOML snippet of each profile that sets `uv_default_groups`. The
value stays `"all"`, so it does not change when a tool changes. The check
of S6 replaces the check of the code today.

The packages of a tool stay with `uv add` and `uv remove`, and with
`owned_packages` in `.agents/stack.json`. The package manager is already the
tool that adds and takes out those parts.

### S12. Removal, update and what they cannot promise

The installer finds the snippet of an owner by its begin and end lines, or,
for JSON, by the keys in the log.

| Case | Update to a newer package | Removal |
|---|---|---|
| Lines, any case | As in the first plan | As in the first plan |
| A TOML, YAML or Markdown block, not edited | The new block replaces it | The block goes, and an anchor line that the installer added and that has nothing under it |
| An edited block | Stays as it is. The output names it and prints the new block. | Stays, and is yours now. The output names it. |
| A block that lost a begin or end line | Stays. The output names the file. | Stays. The output names the file. |
| A block that you deleted | Declined. It does not come back. | Nothing to do |
| A JSON key with the value of the log | The new value replaces it | The key goes |
| A JSON key with another value | Stays, and is yours now | Stays, and is yours now |
| A JSON key that you deleted | Declined. It does not come back. | Nothing to do |
| A starter | Stays | Stays |

If you edit a snippet, the installer does not take it out. The output names
each part that stays, and you take it out by hand. The installer cannot
promise more for a file that you can change.

### S13. The log and the check

The log keeps the form of D1 in the first plan. A new key, `snippets`,
holds an item for each file, owner and block or key:

- A block holds its anchor, its state and the hash of its rows. The hash
  decides whether the block is edited.
- A JSON key holds its path, its state and its value.
- An anchor line that the installer added holds the state `added`.

The states are those of the first plan, `added`, `present`, `declined` and
`released`. A starter has the state `seeded`. The unsure claims of D12 hold for
blocks and keys too. An unsure claim never declines and never removes.

`just install --check` reports each block and key that differs from the
log, for all file kinds.

### S14. Projects and records that exist now

The first run with this change does the following:

- **A whole file in the log.** The installer reads the package source at
  the commit that the log names in `sources`. It cuts that source into
  snippets with S3, and records each block or key that the file still has
  as `added`. Then the run applies the package of today as an ordinary
  update, with S12. So a file that you changed keeps your changes, and a
  newer profile still updates the parts that you did not change.
- **`AGENTS.md` from the old seed, unchanged.** The installer writes the
  starter as your part and the rules as the block.
- **`AGENTS.md` that you changed.** It stays yours. The installer adds no
  block and records the rules as `present`. To have the block, take out the
  old Gest rules and run `just install` again.
- **`merged_hooks` in a package record.** The installer copies the hooks to
  the log as `added`. A record from before `merged_hooks` gets the rule of
  the code today. The hooks are those of the source at the recorded
  revision.
- **`seeds` and `merged_hooks` in a package record.** The same run takes
  both keys out of the record. So `install_package.py` can no longer delete
  `AGENTS.md` or take out hooks on its own.

### S15. Old and new packages, for one release

For one release, a package can ship the old keys next to the new keys. A
profile has `files` next to `snippets`. A role has `seed` and `merge_hooks`
next to `snippets` and `starter`. The new installer
reads the new keys and skips the old keys for a path that the new keys
name. An old installer reads only the old keys, so it still installs the
hooks and `AGENTS.md`. The release after drops the old keys.

## A simpler design that this plan does not take

The review proposed a smaller change. `ruff.toml`, `ty.toml`,
`.pre-commit-config.yaml`, `biome.json` and `tsconfig.json` each have one
package as owner. They could stay whole files with a three-way update. The
installer writes the new source when the file is unchanged since the last
write. Otherwise it prints the difference.

Snippets would then be needed
only for `AGENTS.md`, the hook settings, `pyproject.toml` and the line
files. That drops the cut of TOML and YAML into blocks, S4 and the YAML
check.

This plan keeps snippets for those files, because you asked for snippets in
each shared file. With a snippet, a newer profile can still change its part
of a file that you edited. With a whole file, it cannot.

## The build, in order

1. In jnterfaces, add the file kinds to `scripts/shared_files.py`. Each kind
   needs the cut into blocks, anchors, comment marks and the check of S6.
   JSON also needs keys and the arrays of S8.
2. Move the hook merge of `scripts/install_package.py` into the module.
   Stop the writes of `seed` and `merge_hooks` in `install_package.py`.
3. In `scripts/install_stack.py`, read `snippets` and `starter`, add the
   roles as owners, keep `skip_if`, and move the default groups to a
   snippet.
4. Add the first-run steps of S14 and the old keys of S15.
5. In jnprofile-python and jnprofile-typescript, add `snippets` next to
   `files`, and take the "this file is yours" comments out of the sources.
6. In agent_gest_git_skills, split `AGENTS.template.md`, and add `starter`
   and `snippets` next to `seed` and `merge_hooks`.
7. Write the tests and the docs.

The profiles, the role and jnterfaces merge together.

## Tests

- Each file kind: install, a second install with no change, a newer
  package, uninstall, and a dry run that prints the snippet.
- Two owners in one TOML table, one YAML list and one JSON file, with the
  pass of S7 when the first owner goes. Two owners with different values.
- A key that you set before the install, with the same value and with
  another value.
- Each anchor case of S4. The cases are the spaced and quoted forms, a
  dotted or inline table of yours, a YAML list at column 0, and `repos: []`.
- An edited block, on update and on removal, for TOML, YAML and Markdown.
- `pre-commit autoupdate` changing a `rev:` row in the block.
- A file that holds only installer-made rows after the last owner goes.
- A `tsconfig.json` with comments.
- `skip_if` with `[tool.ruff` in `pyproject.toml`.
- A role that changes its package, with a declined line.
- Each step of S14, from a project made by the installer of today, with a
  profile that changed since the install.
- An old installer with a new package, and a new installer with an old
  package.
- An adversarial review of the code, and each fix broken on purpose to see
  its test fail.

## Docs to write

- `docs/stack-guide.md`: the section "Shared files and the log" covers each
  file kind, the starter and the table of S12.
- `README.md` of jnterfaces: the `snippets` and `starter` keys.
- The SKILL.md and README of each profile, and the README of
  agent_gest_git_skills.

## Not in this plan

- **Hook scripts with the same name.** Two roles can each ship
  `.claude/hooks/session-start.sh`. They are own files, so the second write
  fails today. A folder for each package would fix it, but it changes the
  hook commands.
- **A YAML or Markdown parser.** The checks of S5 and S6 are weaker for
  these kinds. A parser needs a package outside the standard library.
- **Items of other JSON arrays.** Only the paths of S8 merge by item.
