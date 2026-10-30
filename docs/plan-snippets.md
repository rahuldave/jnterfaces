# Plan for the parts that packages add to shared files

Status: plan. Nothing is built. Three adversarial reviews changed this
plan. The third review checked the record against the built code. The
second review ran ruff, ty, tsc, biome, tomlkit and ruamel.yaml to
check how each tool reads an include.

This plan extends `docs/plan-shared-files.md`, which is built. It changes
three decisions of that plan:

- **D1.** `.agents/stack.json` becomes the record of each part. The log
  becomes a history for people, and git does not keep it.
- **D7.** A whole file becomes an own file and an include, or a set of
  units.
- **D12.** A run that stops is settled from `.agents/stack.json`, not from
  the log.

The other decisions of the first plan hold.

## The problem

A package must add its part to a file and take out only its part. Today
only the line files work in this way. In other files, a package writes the
whole file, or it uses a merge of its own with a record of its own:

| What a package adds | Where it goes | How today |
|---|---|---|
| The lines of a profile or a tool | `.gitignore`, `.worktreedisposable` | Blocks, with claims in the log |
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

The records are in three places, so `just install --check` sees only part
of what the installer did.

## Terms

The terms of `docs/plan-shared-files.md` hold here. These terms are new:

- **Own file.** A file that only the installer writes, at a path under
  `.agents/config/` or `.agents/instructions/`. Nobody edits it, so the
  installer can write it again at each update.
- **Shared file.** A file that a person or a second owner can also change,
  such as `ruff.toml` at the top of the project.
- **Include.** A line or a key in a shared file that makes the tool read an
  own file, such as `extend = ".agents/config/python-lint/ruff.toml"`.
- **Unit.** The smallest thing that the installer adds or takes out. A unit
  is a key, such as `tool.uv.default-groups`, an item of a list, or a line
  of a line file.
- **Part.** What one owner adds to one shared file. A part is an include
  with its own file, a set of units, or a block of lines.
- **Starter.** Rows that the installer writes once into a new file, such as
  `Project name: <replace-me>`. They are yours from the start.
- **Record.** The key `parts` in `.agents/stack.json`.

## Decisions

### P1. The record in stack.json is the source of truth

`.agents/stack.json` already holds the profiles, the tools and the roles
that you asked for. It also holds what the installer added for them, such
as `owned_packages`. The new key `parts` holds what the installer added to
each shared file:

```json
"parts_version": 1,
"parts": {
  "ruff.toml": {
    "include": {"line": "extend", "owners": ["python:lint"]},
    "own": {".agents/config/python-lint/ruff.toml": {"hash": "<hash>", "owners": ["python:lint"]}}
  },
  ".gitignore": {
    ".venv/": {"state": "added", "holder": "python", "needs": ["python"]},
    "dist/": {"state": "added", "holder": "python", "needs": ["python", "typescript"]},
    ".local/": {"state": "present", "needs": ["python"]},
    "build/": {"state": "declined", "needs": ["python"]}
  },
  "pyproject.toml": {
    "tool.uv.default-groups": {"state": "added", "holder": "python", "needs": ["python"], "value": "all"}
  }
}
```

Each unit, include and own file has one entry. The entry holds its state,
the owner that holds it, and each owner that needs it. A part that you
deleted, such as a whole `ruff.toml`, has the state `declined`, and the
installer does not write it again.

The files are the output of the record. Each install has a remove:

| Step | What it does |
|---|---|
| Install | Reads the parts of the active owners, writes each file, and records the result |
| Remove | Works out each file again for the owners that stay, as D4 does. A unit that another owner needs passes to it. Each other unit of the old owner goes. |
| `--dry-run` | Works out each file in memory and prints its difference |
| `--check` | Compares the record with the files and names each difference |

A removal never searches a file for what to take out, because the record
says what is there. The loose match of D6 stays only for a line that you
moved out of its block.

**One writer.** `save()` in `install_stack.py` writes `.agents/stack.json`
today, and it builds the record again from scratch at each save. It
changes in two ways:

- It keeps each key that it does not own, so it never drops `parts`.
- It writes through a temporary file and a rename, as
  `shared_files.write` does.

The file pass returns the new `parts`, and `save()` writes them.
`read_record` reads and checks `parts`.

**A part with no record.** A merge of two branches can keep the blocks of
both sides in `.gitignore`, and keep only one side in `.agents/stack.json`.
Some blocks, includes and marked units name a known owner but have no
entry in the record. The installer takes each of them into the record from
the file. The output
names it, and `--check` names it until a run records it.

Reason: `.agents/stack.json` already records the packages of each tool in
this way. One record for all parts is simpler than a log that the installer
must replay.

### P2. A run that stops

Before a run writes a file, it saves `.agents/stack-pending.json`. This
file names each file that the run will change, with the old entries of the
record for that file. git does not keep it, because it describes only this
clone. After the last file and the save of the record, the run deletes it.

The next run reads `.agents/stack-pending.json`. For each named file, it
works out the file from the old entries, the new entries and the file
together. So it can still take out a unit of an owner that the stopped run
removed. A unit that the stopped run did not write is never `declined`.
`--check` names a pending file.

The key `pending` in the record keeps its meaning of today. It names the
tools whose package command has not run yet.

### P3. The log is a history for people

The installer still adds an entry to `.agents/stack-log.jsonl` for each run
that changes a file. Nothing reads the log to decide what to do, except the
one-time step of P15. The `.gitignore` block of the owner `jnterfaces`
holds the line `.agents/stack-log.jsonl`. The installer decides that the
`jnterfaces` owner is active from `parts`, not from the log, so a fresh
clone with no log keeps the line. The line `stack-log.jsonl merge=union` in
`.agents/.gitattributes` goes.

The begin line of a block names the command. In place of the entry of the
log, it names the key of the part in the record:

```gitignore
# begin lines of python:lint, the lint tool of the python profile, from jnprofile-python at c299beb.
# Last written to .gitignore by `just install lint`. The record is parts.".gitignore" in .agents/stack.json.
.ruff_cache/
# end lines of python:lint. `just uninstall lint` removes them.
```

Two worktrees that change `.agents/stack.json` give a git conflict in it.
The same conflict can happen today for the profiles and the tools. A
conflict is visible, so the plan keeps it. The rule of P1 for a part with
no record covers a merge that keeps only one side.

### P4. An include where the format has one

Some tools can read a second file from their config file. For such a file,
the part of an owner is an own file and one include. Your settings in the
shared file win over the settings of the own file. So you change a setting
in the shared file, never in the own file.

| Shared file | Include | Own file | Rules from the review |
|---|---|---|---|
| `justfile` | `import? '.agents/just/…'`, as today | `.agents/just/…` | None |
| `tsconfig.json` | `"extends": ["./.agents/config/typescript/tsconfig.json"]` | One for each source file, not for each tool | The path starts with `./`. The source writes `${configDir}/src` and `${configDir}/dist`, so the paths stay at the top of the project. This needs TypeScript 5.5 or later. |
| `biome.json`, or `biome.jsonc` when it exists | `"extends": ["./.agents/config/typescript-lint/lint.json"]` | `lint.json` | The own file is not named `biome.json`, because biome stops at a second `biome.json`. A `files.includes` of yours replaces the list of the own file. |
| `ruff.toml`, or `[tool.ruff]` in `pyproject.toml` | `extend = ".agents/config/python-lint/ruff.toml"` | One, for one owner | ruff reads a path in the own file from the folder of the own file. So a source may use names such as `.agents`, but not paths such as `legacy/x.py`. |
| `AGENTS.md` | The line "Read and follow `.agents/instructions/tasker.md`." in a block | `.agents/instructions/<role>.md` | See P12 |

`ruff.toml` takes one `extend`, so it has one owner. A second owner of
`ruff.toml` stops the run, and the output names both owners.

`tsconfig.json` and the `extends` of biome are lists, so they take more
than one owner. A later item of the list replaces a whole key of an earlier
item, such as `include`. So two owners with different values of one key
stop the run, as in P8.

An own file goes under `.agents/config/` or `.agents/instructions/`. This
plan adds both folders to the reserved paths of D15. The installer stops if a role ships
a file there with `install_extras.files`.

### P5. Units where the format has no include

If the tool has no include, the part of an owner is a set of units. The
installer reads the file with a parser and writes only the units of the
part.

| Shared file | Parser | Unit |
|---|---|---|
| `ty.toml`, `pyproject.toml` | `tomlkit` | A key, such as `tool.uv.default-groups`. In an array, such as `src.exclude` of ty, each item is a unit. |
| `.pre-commit-config.yaml` | `ruamel.yaml` | An item of `repos`, known by its `repo` |
| `.claude/settings.json`, `.codex/hooks.json` | `json` | A hook in a group of `hooks.<event>`, known by its group keys and its command, as the hook merge does today |
| `.gitignore`, `.gitattributes`, `.worktreedisposable` | None | A line, as in the first plan |

ty has no include. Its `src.exclude` is a unit for each item, so a list of
yours keeps `.agents` out of the check.

**TOML.** `tomlkit` keeps your comments and the order of your keys. A key
path makes the tables on its way when they are missing. When a removal
leaves a table empty, and the record says that the installer made the
table, the table goes too.

**YAML.** A write with `ruamel.yaml` moves comments and changes the indent
of the file. So the installer uses `ruamel.yaml` only to find the rows of
an item. It adds or cuts those rows as text, with the indent of the other
items. Then it reads the file again with `ruamel.yaml` to check it.

**JSON with comments.** A `tsconfig.json` with `//` lines, such as the file
that `tsc --init` writes, is not plain JSON. The installer leaves such a
file alone. The output prints the include, so that you can add it by hand.

### P6. The scripts

The code for lines and for JSON stays in `scripts/shared_files.py`, which
needs only the standard library. TOML and YAML each get a script:

| Script | Packages, pinned to exact versions in a PEP 723 header |
|---|---|
| `scripts/edit_toml.py` | `tomlkit` |
| `scripts/edit_yaml.py` | `ruamel.yaml` |

Each script has the functions `plan`, `add` and `remove` for one file and
one part. `install_stack.py` lists the same packages in its own header and
imports the scripts. `just check` and `just test` run with the same
packages.

Reason: uv is in each project, so a pinned package costs one download, and
then uv keeps it. A dependency group would add the packages to the project
of the user. A PEP 723 header keeps them with the script.

### P7. The lists

jnterfaces keeps one list of the shared files that it knows, in
`scripts/formats.json`. For each file, the list gives its format and its
include, if it has one:

```json
{
  "ruff.toml": {"format": "toml", "include": "extend"},
  "tsconfig.json": {"format": "json", "include": "extends"},
  "ty.toml": {"format": "toml"},
  ".pre-commit-config.yaml": {"format": "yaml"}
}
```

A package cannot add a part to a file that the list does not name. When a
format gets an include, one change to this list moves its owners to the
include.

Each package lists its parts in the same form. A profile and its tools use
the key `parts` in `profile.json`. A role uses `install_extras.parts` in
`skill-package.json`. The value of each target is the source file in the
package:

```json
"parts": {"ruff.toml": "files/ruff.toml", ".pre-commit-config.yaml": "files/pre-commit-config.yaml"}
```

For a file with an include, the source becomes the own file. For a file
without one, the source gives the units. The checks of `profile.json` and
`skill-package.json` reject these sources:

- a target that `formats.json` does not name;
- a source that its parser cannot read;
- a ruff source with a path that has a folder in it;
- a tsconfig source with a path that does not start with `${configDir}`.

### P8. Two owners of one unit

The rule of D4 in the first plan holds for units. The first owner that
needs a unit has it. When that owner goes, the unit passes to the next
owner that needs it, and stays in the file. The order of the owners is
`jnterfaces`, each profile and its tools, then each role.

If two active owners need one unit with different values, the run stops
before it changes a file. The output names the unit and the two owners.

Two tools that ship the same source file, such as the `tsconfig.json` of
`typecheck` and of `test`, share one own file. The record lists both as
owners of it. It goes when the last of them goes.

### P9. Marks in the file

An include or a block of lines in a file with comments goes in a block with
the begin and end lines of P3. For a unit in TOML or YAML, the installer
puts one comment before the unit, with the words of the begin line. When
the unit goes, its comment goes too, but only when the comment has that
exact form.

`biome.json` and the other JSON files get no marks, because JSON has no
comments. The record alone says what the installer added there.

### P10. What you already have, and what they cannot promise

A unit that your part of the file already has counts as `present`. If your
value differs from the value of the owner, the output names the unit and
keeps your value. If you already have an include of your own, such as an
`extend` in `ruff.toml`, the installer stops for that file and names it.

| Case | Update | Removal |
|---|---|---|
| An own file | The installer writes it again | The file goes |
| An include | Stays | Goes |
| A unit with the value of the record | The new value replaces it | Goes |
| A unit with another value | Stays, and is yours now. The output names it. | Stays, and is yours now. The output names it. |
| A unit that you deleted | Declined. It does not come back. | Nothing to do |
| A starter | Stays | Stays |

If you edit a part, the installer may not take all of it out. The output
names what stays, and you take it out by hand.

When the last part of a shared file goes, the installer deletes the file
if nothing else is left in it. An empty `ruff.toml` would still make ruff
skip `[tool.ruff]` in `pyproject.toml`.

### P11. skip_if means no part

The `skip_if` guard of a tool stays as it is. If the guard applies, the
tool adds no part to that file. For example, when `pyproject.toml` has
`[tool.ruff`, the python profile adds no `ruff.toml` and no `extend`.

Reason: an `extend` in your `[tool.ruff]` would change `line-length` and
the rules. Then the formatter would change the whole code base.

### P12. Roles, starters and AGENTS.md

A role is an owner. Its owner key is its role name, such as `tasker`. The
comment names the package that fills the role. If you change the package of
a role, one run takes out the part of the old package. The same run adds
the part of the new package. A unit that you declined stays declined for
the role.

Each role installs through `install_stack.py`, also from the installer
skill of a role. So `install_stack.py` runs one pass over all owners.
The pass runs after the package commands. If a package command fails, the
run stops before the pass. Then the hook scripts of a role can be in place
while its hooks are not in the settings. The output says so, and the next
run adds the hooks.

`install_package.py` stops writing shared files. A role keeps its skills
and hook scripts in `install_extras.files`. These own files stay in the record of the
package, not in `parts`.

A role can name a starter under `install_extras.starter`, as
`{"AGENTS.md": "AGENTS.starter.md"}`. The installer writes it when this run
makes the file. The record holds it as `seeded`. The installer never
changes or takes out a starter.

`AGENTS.template.md` of agent_gest_git_skills splits in two:

- `AGENTS.starter.md` holds the heading and the `<replace-me>` values of
  the project context, which you fill in.
- Each rule moves to the own file `.agents/instructions/tasker.md`. This
  includes the rules on branches and on review that the project context
  holds today.

Codex has no include for `AGENTS.md`, and the installer writes no
`CLAUDE.md`. So the line in `AGENTS.md` is only a pointer. The SessionStart
hook of the role prints the own file, and both Claude and Codex run that
hook. The hook is the step that loads the rules.

### P13. The default groups of uv

The key `tool.uv.default-groups = "all"` in `pyproject.toml` becomes a unit
of each profile that sets `uv_default_groups`. If your file has another
value, the value stays. The output then says loudly that `uv sync` will
not install the groups of the tools.

The packages of a tool stay with `uv add`, `uv remove` and `owned_packages`.
The package manager is already the tool that adds and takes out those
parts.

### P14. Own files must be in git

A fresh clone and CI need the own files. At install, the installer runs
`git check-ignore` on each own file. If git ignores one, the run stops and
names the rule that ignores it.

### P15. Projects and records that exist now

A record with no `parts_version` comes from the installer of today. The
first run with this change moves it to the new record:

- **The claims of the log.** The installer replays the log once, as the
  built code does, and writes the result to `parts`. Each file with an
  unsure claim goes into `.agents/stack-pending.json`, so the run works it
  out again from the file. The run then sets `parts_version` to 1, so the
  replay never runs again.
- **Entries after the move.** An old installer on another branch can add
  entries to the log after the move. If the log has an entry that is newer
  than the move, the run stops. The output says to run the new installer
  on that branch first.
- **The log in git.** A `.gitignore` line does not take a tracked file out
  of git. The output tells you to run `git rm --cached
  .agents/stack-log.jsonl` once.
- **Begin lines.** The run writes the begin line of each block again, so
  that no block names an entry of the log.
- **A whole file in the log, unchanged since the install.** The installer
  writes the own file and an include in place of the whole file.
- **A whole file in the log that you changed.** It stays yours, with no
  include. The record holds no part for it. To have the include, delete the
  file and run `just install` again.
- **A whole file that you deleted.** Its part has the state `declined`, and
  the installer does not write it again.
- **`AGENTS.md` from the old seed, unchanged.** The installer writes the
  starter and the pointer in place of the file.
- **`AGENTS.md` that you changed.** It stays yours, with no pointer. The
  SessionStart hook still prints the own file.
- **`merged_hooks` in a package record.** The installer copies the hooks to
  `parts`. A record from before `merged_hooks` gets the rule of the code
  today. The hooks are those of the source at the recorded revision.
- **`seeds` and `merged_hooks` in a package record.** The run saves `parts`
  first. Then it takes both keys out of the record of the package. So a run
  that stops between the two saves loses no hook. After this step,
  `install_package.py` cannot delete `AGENTS.md` or take out hooks.
- **`default-groups` that the installer wrote.** A value of `"all"` becomes
  a unit of the profile.

### P16. Old and new packages, for one release

For one release, a package ships the old keys next to the new keys. A
profile has `files` next to `parts`. A role has `seed`, `merge_hooks` and
`AGENTS.template.md` next to `parts` and `starter`. The new installer skips
an old key for a path that `parts` names. An old installer reads only the
old keys, so it still installs the hooks and `AGENTS.md`. The release after
drops the old keys.

The old keys help a clone that has not updated jnterfaces yet. They do not
let two installers share one project. An old installer records hooks in
`merged_hooks`, and the new installer records them in `parts`. So each
person on a project moves to the new installer at the same time. The docs
say so.

## The build, in order

1. In jnterfaces, write `scripts/formats.json`, `scripts/edit_toml.py` and
   `scripts/edit_yaml.py`, with their tests.
2. In `scripts/shared_files.py`, move the record from the log to `parts`,
   with `.agents/stack-pending.json` for a run that stops. Write `--check`
   again to read `parts`. Make the log a history, and move the hook merge
   of `install_package.py` into the module. Add `.agents/config/` and
   `.agents/instructions/` to the reserved paths.
3. In `scripts/install_stack.py`, make `save()` keep the keys that it does
   not own and write through a rename. Make `read_record` read `parts`.
4. In `scripts/install_stack.py`, read `parts` and `starter`, add the roles
   as owners, keep `skip_if`, and move the default groups to a unit.
5. Stop the writes of `seed` and `merge_hooks` in `install_package.py`.
6. Add the first-run steps of P15 and the old keys of P16.
7. In jnprofile-python and jnprofile-typescript, add `parts` next to
   `files`. Change the sources for P4. The tsconfig source uses
   `${configDir}`, and the biome source becomes `lint.json`. The ruff source
   has no folder paths. No source says that the file is yours.
8. In agent_gest_git_skills, split `AGENTS.template.md`, add `parts` and
   `starter`, and make the SessionStart hooks print the own file.
9. Write the docs.

The profiles, the role and jnterfaces merge together.

## Tests

- Each script: `plan`, `add`, a second `add` with no change, and `remove`,
  on a new file and on a file with your comments and keys.
- Each include of P4. The tool itself checks the result, with `ruff check`,
  `tsc --showConfig`, `biome check` and `ty check`.
- A YAML removal that keeps your comment on the next item and the indent
  of the file.
- Two owners of one unit, with the pass of P8. Two owners with different
  values. Two tools that share a tsconfig source. A second ruff owner.
- The removal of an owner whose unit another owner needs.
- A unit that you set before the install, with the same value and with
  another value. An `extend` of your own.
- A `tsconfig.json` with comments, and a `biome.jsonc`.
- `skip_if` with `[tool.ruff` in `pyproject.toml`.
- A role that changes its package, with a declined unit.
- A run that stops after the first file, then a second run. A removal
  that stops in the same place.
- A save of the record keeps `parts`.
- A merge that keeps the blocks of both sides and the record of one side.
- An own file that `.gitignore` ignores.
- Each step of P15, from a project made by the installer of today, with a
  profile that changed since the install. A log with an unfinished entry. A
  second run, which does not replay the log. A log entry after the move.
- A fresh clone with no log keeps the log line in `.gitignore`.
- An old installer with a new package, and a new installer with an old
  package.
- An adversarial review of the code, and each fix broken on purpose to see
  its test fail.

## Docs to write

- `docs/stack-guide.md`: the section "Shared files and the log" covers the
  record, includes, units, starters and the table of P10.
- `docs/plan-shared-files.md`: mark D1, D7 and D12 as changed by this plan.
- `README.md` of jnterfaces: `formats.json`, and the `parts` and `starter`
  keys.
- The SKILL.md and README of each profile, and the README of
  agent_gest_git_skills.

## Not in this plan

- **Hook scripts with the same name.** Two roles can each ship
  `.claude/hooks/session-start.sh`. They are own files, so the second write
  fails today. A folder for each package would fix it, but it changes the
  hook commands.
- **A folder for `.worktreedisposable`.** Our prune recipe reads the file,
  so it could read a folder of files, one for each owner. The blocks of
  lines work now, so this waits.
- **Includes for Codex.** If Codex gets an include syntax for `AGENTS.md`,
  one change to `formats.json` uses it.
- **An agentic target for the cases that need judgment.** A few cases need
  judgment, not a rule. One is a part that you edited and that must now go.
  The installer could write an `AGENT_TASK v1` packet for each case, and an
  agent would patch the file with its own tools. With includes and units,
  these cases are rare, so P10 and P15 only name them. If they turn out to
  be common, jagt goes in before the profiles and roles.
