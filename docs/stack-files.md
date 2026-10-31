---
title: The files of the stack
---

This page tells you each file that the stack reads or writes, what each key
in it means, and how the files work together. [How the stack fits
together](stack-guide.md) tells you what an install does. This page tells you
the files behind it.

The examples come from an install into an empty Python project on
2026-10-31, with the command `just install --profile python --defaults`.

## Terms

| Term | Meaning |
|---|---|
| package | A Git repository that ships skills, for example nossaifd. |
| clone | The local Git clone of a package. The installer reads each package from its clone. |
| role | The name of a job, for example `tasker`. One package does the job. |
| profile | A language setup, for example `python`. A profile package, such as jnprofile-python, ships it. |
| tool | One unit of a profile, for example `lint`. It brings packages, recipes and parts. |
| owner | A thing that adds to the files of a project: jnterfaces, a profile, a tool, or a role. A tool has the key `profile:tool`, for example `python:lint`. |
| shared file | A file that more than one owner, and you, can add to, for example `.gitignore` or `pyproject.toml`. |
| part | What one owner adds to one shared file. |
| unit | The smallest piece of a part: one line, one key, one list item or one hook. |
| record | A JSON file in `.agents/` that says what the installer wrote. |
| own file | A file that the installer writes whole under `.agents/`, for example `.agents/config/python/ruff.toml` or `.agents/instructions/tasker.md`. A shared file includes it or points to it. |

## One record for each project

A project has one record of the stack, `.agents/stack.json`. A profile or a
role does not bring a record of its own. Each run of the installer reads what
the packages declare, and writes all of it into this one file.

Two other names can confuse you:

- **`stack.json` at the top of jnterfaces** is the catalog. It lists the
  packages, roles and profiles. It is not in your project.
- **`.agents/<package>-install.json`** is one file for each package. It keeps
  the hashes of the skill files that the package copied in. It does not hold
  the shared files.

## The files at a glance

The packages declare. The installer reads the declarations, then writes the
project and its records.

| File | Where | Who writes it | What it holds |
|---|---|---|---|
| `stack.json` | jnterfaces | the author of jnterfaces | The catalog: each package, role and profile. |
| `scripts/formats.json` | jnterfaces | the author of jnterfaces | The format of each shared file that a part can go to. |
| `skill-package.json` | each package | the author of the package | The skills and the extras of the package. |
| `profile.json` | each profile package | the author of the profile | The tools of the profile, and the parts of each tool. |
| `.agents/stack.json` | the project | the installer | The record of the stack: your choices and each part. |
| `.agents/<package>-install.json` | the project | the installer | The record of one package: its revision and the hash of each of its files. |
| `.agents/stack-pending.json` | the project | the installer | The state of a run that has not finished. It is gone after a run that works. |
| `.agents/stack-log.jsonl` | the project | the installer | A history of the runs. Nothing reads it to decide what to do. |
| `.agents/just/index.just` and `.agents/just/<profile>.just` | the project | the installer | The modules that import the recipes of the installed tools. `generated` in the record keeps their hashes. |
| `.agents/just/<profile>/*.just` and `.agents/just/roles/<package>.just` | the project | the packages, through `install_extras.files` | The recipe files of each tool and role. The package record keeps their hashes. |
| `.agents/config/<profile>/...` | the project | the installer | The own files of the tools. |
| `.agents/instructions/<role>.md` | the project | the installer | The own file of a role. A line in `AGENTS.md` points to it. |
| `.agents/install.just` | the project | the installer | The recipes `just install` and `just uninstall`. |
| `.agents/skills/` and `.claude/skills/` | the project | the installer | The skills, and a link to each one for Claude Code. |
| `.claude/hooks/` and `.codex/hooks/` | the project | the tasker role, through `install_extras.files` | The hook scripts. The hook entries go in `.claude/settings.json` and `.codex/hooks.json` as parts. |
| `Justfile` | the project | the installer once, then you | The starter Justfile, when the project has none. It is yours from then on. |
| `.gitignore`, `.worktreedisposable`, `AGENTS.md` | the project | the installer and you | Line files. Each owner adds a block of lines. |

## How one run uses the files

1. `just install` runs `scripts/install_stack.py` from the jnterfaces clone.
2. The installer reads the catalog `stack.json`. It finds each role, profile
   and alias there.
3. It reads `.agents/stack.json` of the project for your saved choices. Names
   on the command line change them.
4. For each package that the run needs, it reads `skill-package.json` from
   the clone. For each profile, it also reads `profile.json`.
5. It makes one owner from each profile, tool and role, with its lines, parts
   and units. `scripts/formats.json` tells it how to read and write each
   shared file. It does a dry pass over the shared files. A conflict of two
   owners stops the run here, before anything changes.
6. It copies the skills and the extra files of each package. It writes
   `.agents/<package>-install.json` for each one.
7. It saves your choices in `.agents/stack.json`, and writes the modules in
   `.agents/just`. It does this again each time that it saves the record.
8. If the project has no Justfile, it writes the starter Justfile. If a tool
   needs the manifest and the project has none, it runs `init` of the
   profile.
9. It runs the package commands of the tools, such as `uv add --group lint
   ruff`.
10. It saves `.agents/stack-pending.json`, then it writes the shared files
    and the own files, and adds an entry to `.agents/stack-log.jsonl`.
11. It saves the new parts in `.agents/stack.json`, and deletes
    `.agents/stack-pending.json`.

A removal, such as `just uninstall lint`, runs the same steps. The owner is
not active, so its parts go. The installer reads what to take out from the
record, never from the files.

## In the packages

### The catalog: `stack.json` of jnterfaces

The catalog has four keys:

| Key | What it holds |
|---|---|
| `version` | The version of the format. It is `2` now. |
| `packages` | A list with one entry for each package. jnterfaces is first. |
| `aliases` | Other names for a role, for example `"gest": "tasker"`. |
| `profiles` | One entry for each profile, keyed by its name. |

An entry of `packages` has these keys:

| Key | What it means |
|---|---|
| `name` | The name of the package and of its clone folder. |
| `repository` | The URL to clone when the clone is missing. |
| `about` | One line for the questions and the help. |
| `choice` | The role that the package does, for example `tasker`. jnterfaces has none, because it is always installed. |
| `default` | `true` when a new project turns the role on, unless the profile says otherwise. |
| `needs` | The packages that the role needs. |
| `record` | The path of the package record, when it is not `.agents/<package>-install.json`. |
| `variants` and `default_variant` | The forms of a role, for example `git` or `gitwt` for `tasker`. A variant with `"ready": false` cannot be chosen yet. |
| `required_skills` and `optional_skills` | For jnterfaces only: the `just` skill is always installed. `cx` and `jagt-protocol` are roles of their own. |

An entry of `profiles` has `package`, `repository`, `about`, and `detect`.
`detect` lists the files that turn the profile on in a project with no saved
profile, for example `pyproject.toml` or `uv.lock`.

### A package: `skill-package.json`

Each package has one `skill-package.json`. The installer reads these keys:

| Key | What it means |
|---|---|
| `repository` | The owner, name and URL of the package. |
| `skills` | Each skill, with its name and its folder. |
| `install_extras.files` | Files and folders outside `.agents/skills` that the installer copies and records like a skill, for example `.agents/just/roles/nossaifd.just`. |
| `install_extras.parts` | The parts of a role: `{shared file: source file}`. For example, tasker adds a line to `AGENTS.md` and hooks to `.claude/settings.json`. |
| `install_extras.starter` | A file that the role writes once, when the project has none, for example the first `AGENTS.md`. It is yours from then on. |
| `install_extras.merge_hooks` and `install_extras.seed` | The same hooks and starter for an installer from before the parts. A newer installer leaves these files to the parts. |
| `executables` | The programs that the package needs, with a command that checks each one. |

### A profile: `profile.json`

A profile package also has `profile.json`. Its top keys are these:

| Key | What it means |
|---|---|
| `version` | The version of the format. It is `1` now. |
| `name`, `about`, `detect` | As in the catalog. |
| `manifest` | The file that lists the dependencies: `pyproject.toml` or `package.json`. |
| `init` | The manifest file and the command that makes it when it is missing: `{"file": "pyproject.toml", "command": ["uv", "init", "--bare", "--no-workspace"]}`. |
| `add` and `remove` | The commands that add or remove the packages of a tool. `{group}` takes the group of the tool. |
| `choices` | The default roles of the profile, for example `tasker` and `prose`. |
| `uv_default_groups` | For Python: the value of `tool.uv.default-groups` in `pyproject.toml`. |
| `base` | What the profile adds with no tool: its recipe file, and its `lines`, `parts` and `files`, as for a tool. |
| `tools` | Each tool, keyed by its name. |
| `default_tools` | The tools that a new project gets, for example `lint` and `test`. |

A tool has these keys:

| Key | What it means |
|---|---|
| `packages` | The packages that the tool adds, for example `ruff` and `pre-commit`. |
| `group` | For Python: the dependency group of the packages. |
| `recipes` | The recipe file of the tool, for example `lint.just`. |
| `verify` | The recipes that `just verify` runs for the tool. |
| `lines` | Lines for line files: `{".gitignore": [".ruff_cache/"]}`. |
| `parts` | Parts for unit files: `{"ruff.toml": "files/ruff.toml"}`. |
| `files` | Whole files, for an installer from before the parts. A file that is also in `parts` goes as a part. |
| `skip_if` | A guard. A new part is left out when a file has a text, for example no `ruff.toml` part when `pyproject.toml` has `[tool.ruff`. A part that the record already holds stays. |
| `before_remove` | A command that runs before the packages go, for example `pre-commit uninstall`. |

### The formats: `scripts/formats.json`

This file names each shared file that a part can go to. A line file, such as
`.gitignore`, needs no entry. Each entry has these keys:

| Key | What it means |
|---|---|
| `format` | `lines`, `toml`, `json`, `yaml` or `hooks`. |
| `include` | How the part reaches its own file. `extend` is the `extend` key of `ruff.toml`. `extends` is the `extends` list of `tsconfig.json` and `biome.json`. `pointer` is a line in `AGENTS.md` that names a file. |
| `instead` | Another name of the same file. A part of `biome.json` goes to `biome.jsonc` when only that file exists. |
| `create` | `false` when the installer must not make the file. `pyproject.toml` comes from `uv init` only. |
| `list` and `key` | For YAML: the list that holds the units, and the key that names each item. `.pre-commit-config.yaml` has `repos`, named by `repo`. |
| `comments` | For a line file: `html` when the comments are HTML comments, as in `AGENTS.md`. |

## In the project

### The record: `.agents/stack.json`

The record has these keys:

| Key | What it holds | Example |
|---|---|---|
| `version` | The version of the format. | `2` |
| `profiles` | The installed profiles. | `["python"]` |
| `choices` | Each role, on or off. | `{"cx": false, "jagt-protocol": false, "prose": true, "reporter": false, "tasker": true}` |
| `tools` | The installed tools. | `["python:lint", "python:test"]` |
| `variants` | The variant of each role that has variants. | `{"tasker": "git"}` |
| `owned_packages` | The packages that the installer added, keyed by their place in the manifest. A removal takes out only these. For Python the place is `profile:group`. For TypeScript it is the profile, because npm has no groups. | `{"python:lint": ["pre-commit", "ruff"]}` |
| `generated` | The hash of `index.just` and of each `<profile>.just` that the installer wrote. | |
| `started` | `true` when the installer wrote the starter Justfile. | `true` |
| `parts` | Each unit of each shared file. See below. | |
| `parts_version` | The version of the format of `parts`. | `1` |
| `pending_tools`, `removing_tools`, `removing_profiles` | Work that a run that failed did not finish. The next run finishes it. | |
| `parts_log_end` | The id of the last entry of the old log that moved into `parts`, for a project of an older installer. | |

The installer keeps any other key that it finds in the record. One old key,
`added_packages`, moves into `owned_packages` and goes.

**`parts`** is keyed by the shared file, then by the unit. For example:

```json
"parts": {
  ".gitignore": {
    ".venv": {"state": "added", "needs": ["python"], "holder": "python"}
  },
  "pyproject.toml": {
    "tool.uv.default-groups": {"state": "added", "needs": ["python"], "holder": "python",
                               "value": "all", "made": ["tool", "tool.uv"]}
  },
  "ruff.toml": {
    "extend": {"state": "added", "needs": ["python:lint"], "holder": "python:lint",
               "value": ".agents/config/python/ruff.toml"}
  },
  ".agents/config/python/ruff.toml": {
    "(own file)": {"state": "added", "holder": "python:lint", "needs": ["python:lint"], "hash": "7ca1..."}
  }
}
```

The name of a unit depends on the format of the file:

| Format | Unit name | Example |
|---|---|---|
| lines | The line, without a slash at the end. | `.venv` |
| TOML | The dotted key. | `tool.uv.default-groups` |
| TOML include | The include key. | `extend` |
| JSON include | The include list with its value. | `extends["./.agents/config/typescript/tsconfig.json"]` |
| YAML | The list, and the key of the item. | `repos[repo=https://github.com/astral-sh/ruff-pre-commit]` |
| hooks | The event, the group, and the command. | `hooks.PreToolUse {"matcher": "Bash"} $CLAUDE_PROJECT_DIR/.claude/hooks/raw-git-write-guard.sh` |

An entry has these fields:

| Field | What it means |
|---|---|
| `state` | One of the states below. |
| `needs` | Each owner that needs the unit. |
| `holder` | The owner that holds the unit now. When it goes, the next owner in `needs` holds it. |
| `value` | The value that the installer wrote, for a unit file. |
| `made` | What the installer made for the unit, for example the tables `tool` and `tool.uv`. They go with the last unit inside them. |
| `file` | For a file with two names: the file that holds the unit. A part of `biome.json` has `biome.json` or `biome.jsonc`. |
| `hash` | For an own file or a whole file: the hash of what the installer wrote. |
| `earlier` | Only after a run that stopped: other values that the file can hold for the unit. |
| `unsure` | Only after a run that stopped and a later run that could not read the file. `just install --check` fails until a run reads the file. |

The states are these:

| State | Meaning |
|---|---|
| `added` | The installer added the unit. It goes when no owner needs it. |
| `present` | The unit was in the file before its owner came. It is yours, and stays. |
| `declined` | You deleted the unit. The installer does not add it again. |
| `kept` | The whole file is yours, with no part. You changed a whole config file of an older installer, or you made again a file that you had deleted. |
| `seeded` | A starter, such as the first `AGENTS.md`. It is yours from the start. |

Some keys of `parts` are not units. Each one starts with a bracket:

| Key | What it means |
|---|---|
| `(own file)` | The file is an own file of an owner. |
| `(whole file)` | The file is a whole file of a package from before the parts. |
| `(file)` | The whole file is yours. The installer adds no part to it. |
| `(starter)` | A role wrote the file from its starter. |
| `(seed)` | An older installer wrote the file from its seed. |

### Which file holds a part

A part has one name in the record, the name in `profile.json`, for example
`biome.json`. The file that holds it can have another name. Biome reads
`biome.json`, else `biome.jsonc`, and it uses `biome.json` when both are
there.

- Each entry of the part saves the file that holds it, in `file`. The
  installer accepts only `biome.json` or `biome.jsonc` there.
- When only `biome.jsonc` is there, the part goes to `biome.jsonc`.
- When you add a `biome.json` later, the next run moves the part. It takes
  the units out of `biome.jsonc` and adds them to `biome.json`. A unit that
  you deleted stays out.
- `just install --check` names a part that the next run will move.
- A record of an older installer has no `file`. The installer then looks for
  the units in `biome.json` first, then in `biome.jsonc`.

### The package records: `.agents/<package>-install.json`

The installer writes one record for each package, for example
`.agents/jnterfaces-install.json`. The tasker role uses
`.agents/gest-git-install.json`, the name in the catalog. A profile package
gets one too, for example `.agents/jnprofile-python-install.json`. The main
keys are these:

| Key | What it holds |
|---|---|
| `schema_version` | The version of the format. |
| `package`, `source_repository` | The package and the repository of its clone. |
| `source_revision` | The commit of the clone that the run installed. |
| `source_worktree_dirty` | `true` when the clone had changes that are not committed. |
| `source_requested_commit` | The commit that the run asked for, if any. |
| `skills`, `skill_folders` | The installed skills. |
| `extras` | The extra files and folders of `install_extras.files`. |
| `managed_files` | The hash of each file that the package copied in. A file whose hash is not the recorded hash is yours, and the installer does not overwrite it. |
| `claude_links` | The links in `.claude/skills` that the installer made. |
| `merged_hooks`, `created_settings`, `seeds` | For an installer from before the parts. |

### A run that stops: `.agents/stack-pending.json`

Before a run writes a shared file, it saves this file:

```json
{"files": {"biome.json": {"old": {...}, "new": {...}}}}
```

For each file, `old` is the record before the run and `new` is the record
that the run is writing. `"starter": true` marks a file that the run writes
from a starter. The key is the name of the record, also when the
file is `biome.jsonc`.

When a run stops, the next run reads this file. So it
can still take out a part that the stopped run added. A run that works
deletes the file. Git ignores it.

### The history: `.agents/stack-log.jsonl`

Each run that changes a file adds one line:

```json
{"kind": "history", "id": "2026-10-31T14:04:22Z-47bb",
 "command": "just install --profile python --defaults",
 "changes": {"ruff.toml": "units", ".gitignore": "lines", "...": "..."},
 "sources": {"jnterfaces": "b4c8b65...", "jnprofile-python": "3a2a1e0..."}}
```

`changes` gives the kind of change of each file. The kinds are `lines`,
`units`, `whole` and `own`.
`sources` gives the commit of each package. Nothing reads this file to decide
what to do. Git ignores it.

## What you can edit

| File | Can you edit it? |
|---|---|
| `Justfile` | Yes. It is yours. Put the recipes of the project in it. |
| A shared file, such as `.gitignore` or `ruff.toml` | Yes. A unit that you change or delete is yours, and the installer keeps your change. |
| `.agents/config/...` and `.agents/instructions/...` | No. The installer writes them again. Put your settings in the shared file, for example `ruff.toml` or `AGENTS.md`. |
| `.agents/just/...` | No. A file that you change is yours, and the installer stops changing it. Put your recipes in the Justfile. |
| `.agents/skills/...` | Only if you want to keep the change. The installer then leaves the file alone and names it. |
| `.agents/stack.json` | Only to resolve a Git conflict. Run `just install --check` after. |
| `.agents/*-install.json`, `.agents/stack-pending.json`, `.agents/stack-log.jsonl` | No. |

## Check the files

`just install --check` compares `parts` in `.agents/stack.json` with the
shared files, and changes nothing. It prints each difference, for example:

- a unit that is gone, or that you changed;
- a block that lacks its begin or end comment;
- a part that the next run will move;
- a run that stopped.

 It fails in these cases:

- a block lacks its begin or end comment;
- a run stopped, or `.agents/stack-pending.json` is not a file that the
  installer wrote;
- an entry is `unsure`, because a later run could not read its file.

Run `just install` again to finish a run that stopped.

`just install --dry-run` prints each file that a run would change, and writes
nothing.
