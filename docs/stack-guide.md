# How the stack fits together

This guide describes the skill stack on the `main` branch of jnterfaces. It
says where each part lives, what a machine needs, and how an install works.
Then it builds a project in layers, from a project with no code to a project
with the checks of cheesecracker. Each layer says what works now and what does
not. The results come from installs into empty Git repositories on
2026-10-29. The section "Gaps to close" says which gaps a later change
closed.

## Terms

| Term | Meaning |
|---|---|
| skill | A folder with a `SKILL.md` file. An agent reads it to learn one job. |
| package | A Git repository that ships skills, for example nossaifd. |
| role | The name of a job, for example `prose`. A package does the job, so the package can change and the role name stays. |
| profile | A language setup, for example `python`. A profile package, such as jnprofile-python, ships it. |
| tool | One unit of the setup of a profile, for example `lint`. It brings packages, config files and recipes. |
| manifest | The file that lists the dependencies of the project: `pyproject.toml` or `package.json`. |
| record | A JSON file in `.agents/` that lists what the installer wrote, with a hash of each file. |
| mixin | One layer that you add to a project, for example a role or a tool. |

## Where each part lives

The stack has parts in four places:

| Place | What it holds |
|---|---|
| GitHub, `rahuldave/...` | The source of each package: jnterfaces, agent_gest_git_skills, nossaifd, sklanes, and the profile packages jnprofile-python and jnprofile-typescript. |
| The clone folder | One Git clone of each package. The default folder is `~/.local/share/skill-packages`. |
| The project | Copies of the skills in `.agents/skills`, the recipes in `.agents/just`, the records, and `.agents/stack.json`. |
| The machine | The programs: `git`, `just`, `uv`, and the programs that some roles need. |

The packages and their roles are in this table:

| Role | Package | What it installs | Default |
|---|---|---|---|
| none, always installed | jnterfaces | the `just` skill, the installer file `.agents/install.just` | always |
| `cx` | jnterfaces | the `cx` skill | no |
| `jagt-protocol` | jnterfaces | the `jagt-protocol` skill | no |
| `prose` | nossaifd | the `nossaifd` skill, its guide and `check.py` | yes |
| `tasker` | agent_gest_git_skills | the `g*` skills, the agent hooks, the starter `AGENTS.md` | yes |
| `reporter` | sklanes | the `lanes` skill | no |

Only jnterfaces has an installer, `scripts/install_stack.py`. The other
packages list their files in `skill-package.json`. The list of packages,
roles and profiles is in `stack.json` of jnterfaces. A profile package also
has `profile.json`, which lists its tools.

**The clone folder.** The installer looks for each package in the folder that
`SKILL_PACKAGES_DIR` names. If the variable is not set, it looks in the folder
that holds the jnterfaces clone that runs. If a clone is missing, the installer
clones it from GitHub. Each run prints the folder.

`just install` finds the jnterfaces clone from two variables. `JNTERFACES_DIR`
names the clone. Otherwise, the clone is in `SKILL_PACKAGES_DIR`, by default
`~/.local/share/skill-packages`. The project does not save the folder, so
export the variable in your shell profile, for example `export
SKILL_PACKAGES_DIR=~/Projects` on this machine. If you set none, `just
install` clones new copies into `~/.local/share/skill-packages`.

## What a machine needs

A machine needs `git`, `just` and `uv`. The installer, Python, the Python
tools and Node all come through `uv`. The roles need more programs:

| Role or tool | Program | How the stack handles a missing program |
|---|---|---|
| `tasker` | `gest` | The installer prints a warning and installs the skills. |
| `tasker` hooks | `but` (GitButler) | The hooks check for it, and they work without it. |
| `cx` | `cx` | The skill describes a fallback. |
| `jagt-protocol` | `jagt` | The skill describes a fallback. |
| `reporter` | `node` 22 or later | The package says to run node with `uvx --from 'nodejs-wheel==24.*' node`. |

## How an install works

The installer runs these steps:

1. It reads the roles, profiles and tools from the command line and from
   `.agents/stack.json`. If neither names a profile, it looks at the files of
   the project. For example, `pyproject.toml` turns on the `python` profile.
2. It checks each package. If a check fails, it writes nothing.
3. It copies the skills and other files of each role and profile into the
   project, and it writes one record for each package.
4. It saves the choices in `.agents/stack.json`.
5. If the project has no Justfile, it writes the starter Justfile.
6. If a tool of a profile is to be added and the project has no manifest, it
   makes one, with `uv init --bare` or `npm init --yes`.
7. It adds the packages of each tool, with `uv add --group` or `npm install
   --save-dev`, and it copies the config files of the tool.
8. It writes the recipes of the installed tools in `.agents/just`. After each
   step, it saves the record and writes the recipes again.

A later `just install` with no names installs the same set again, from the
clones as they are. To get new commits of the packages, run `just install
--update`. It pulls the jnterfaces clone and the clone of each package that
the run installs. It skips a clone with no upstream branch, and it prints
"Not updated". A config file that a tool copies, for
example `ruff.toml`, is yours after the first install.

**An edited file stays as you left it.** The installer does not overwrite a
file of a package that you edited. It names the file, and it installs the
other files. The record keeps the hash of the install, so the next run and a
removal still see the edit. To get the new version, undo the edit, or remove
the file, and run `just install` again.

A file that no install wrote, in the place of a file of a package, still stops
the install, and the installer writes nothing.

**The first install.** A new project has no Justfile, so it has no `just
install`. Run the installer from the clone for the first install:

```bash
uv run ~/Projects/jnterfaces/scripts/install_stack.py --target . prose
```

A named role asks no questions, so the command turns on `prose` only. With a
named profile or tool and no named role, a terminal asks about each role.

After that, the project has `.agents/install.just`. A Justfile that has the
line `import? '.agents/install.just'` gets the recipes `just install` and
`just uninstall`.

**The agents.** Codex reads skills from `.agents/skills`. Claude Code reads
skills from `.claude/skills`. So for each installed skill, the installer makes
the link `.claude/skills/NAME`, which points to `../../.agents/skills/NAME`.
When the skill goes, the link goes. The installer leaves alone any other file
or link in `.claude/skills`, and a `.claude` or `.claude/skills` that is a
link or a file.

**A link that you remove.** If you remove a link, no later install makes it
again. If you remove the whole `.claude/skills` folder, the next install
makes all the links again.

**copier.** `.agents/install.just` defines the variable `jn_copier`, which runs
copier 9 through `uvx`. copier makes files from a template, and `copier
update` brings in a later version of the template and keeps your edits. So a
recipe can use `{{jn_copier}} copy TEMPLATE FOLDER` with no install.

## How the recipes arrive

Each tool of a profile has one recipe file, for example
`.agents/just/python/lint.just`. The profile package installs these files.
`just install` then writes two kinds of file in `.agents/just`:

- **`python.just`**, a module for each profile. It imports the recipe files
  of the installed tools only, and its `verify` runs their checks. Its
  recipes run in the folder of the project. `just python lint` runs one.
- **`index.just`**, which `.agents/install.just` imports. It has a `mod` line
  for each profile. In a project that jnterfaces started, it also has a
  combined recipe for each recipe name. So `just lint` runs the `lint` of
  each language. Its `verify` runs the `verify` of each language, then
  `git diff --check`.

**A project that jnterfaces started.** When the project has no Justfile, the
installer writes the starter Justfile, and `.agents/stack.json` records
`"started": true`. The starter Justfile has `set allow-duplicate-recipes`. So
a recipe in the Justfile itself with the name of a recipe of the index, such
as `lint`, takes its place. It does not take the place of the recipe of a
language, so `just python verify` still runs the `lint` of the profile. A
recipe of yours can also depend on a recipe of the index, for example `ci:
verify`.

**A project that jnterfaces did not start**, such as one with its own
Justfile, gets the modules only, for example `just python lint`.

**A name that meets a name of the index.** An alias, a module, or a recipe
in a project without `set allow-duplicate-recipes` can have the name of a
recipe or a module of the index. Then the Justfile does not parse.

The installer checks the Justfile after it writes the index. If it does not
parse, the installer writes the index with the modules only, then with none,
and it names the clash. So `just install` keeps working. Rename the name of
the project to get the recipes back.

**The record.** `.agents/stack.json` holds the hash of each file that `just
install` wrote in `.agents/just`, under `generated`. The installer changes or
removes a file only while its hash is the recorded one. It keeps any other
file, and it names that file. Put your own recipes in the Justfile.

With more than one language, a combined recipe gives the same arguments to
each language. To give arguments to one language, run `just python test x`.

## Build a project in layers

Each layer adds one mixin. Start at layer 0, and add the layers that the
project needs. Each command after layer 1 is a recipe of the project.

### Layer 0: an empty Git repository

```bash
mkdir notes && cd notes && git init
```

The project has no code, no manifest and no Justfile.

### Layer 1: the writing skill

```bash
uv run ~/Projects/jnterfaces/scripts/install_stack.py --target . prose
```

**What you get:**

- `.agents/skills/just` and `.agents/skills/nossaifd`, with `check.py`, and
  their links in `.claude/skills`.
- `.agents/install.just`, the records, and `.agents/stack.json`.
- The starter Justfile, which imports `.agents/install.just`. So `just
  install`, `just uninstall` and `just install --help` work.

Run the checker on the Markdown files that git tracks, or on the pages that
you name. Alone, it leaves out `AGENTS.md` and `CLAUDE.md`, which are
instructions for agents:

```bash
just prose
just prose -v README.md
```

An agent finds the nossaifd skill by its description, through the link in
`.claude/skills` or the copy in `.agents/skills`. So it needs no line in an
`AGENTS.md`.

**What does not work:**

- **A project with a manifest gets a profile.** If the project has
  `pyproject.toml` or `package.json` on the first install, the installer
  finds the profile and adds its default tools. To install the role alone,
  pass `--profile ''` on the first install. The record then keeps no
  profile.

### Layer 2: the task tracker

```bash
just install tasker
```

**What you get:**

- The `g*` skills, which track work with Gest.
- The agent hooks in `.claude/hooks` and `.codex/hooks`, with their entries
  in `.claude/settings.json` and `.codex/hooks.json`.
- A starter `AGENTS.md`, if the project has none. `just uninstall tasker`
  removes it again when nobody changed it.

**Variants.** `just install tasker=gitwt` works on each task in its own Git
worktree, and keeps the main checkout on `main`. The default, `tasker=git`,
works on a branch in the main checkout. The record saves the variant, and
`just tasker-mode` prints it. The `jj` variant is not ready yet.

**The worktree recipes.** In a project that jnterfaces started, the role adds
these recipes:

| Recipe | What it does |
|---|---|
| `just worktree-new NAME [BASE]` | Makes branch NAME in `../<repo>.worktrees/NAME`, sets it up, and runs `just setup` there |
| `just worktree-sync` | Sets up a worktree that another tool made, such as Claude Code |
| `just worktrees` | Lists the worktrees: clean or dirty, merged or not, and the ignored files that a removal deletes |
| `just worktrees-prune` | Removes the clean, merged worktrees that were idle for 2 hours, with no `--force`. A worktree with outputs or other ignored files that setup did not put there stays |

A worktree has only the files that Git tracks. So setup follows two files in
the main checkout. `.worktreeinclude` has gitignore patterns, and each ignored
file that matches is copied, for example `.env`. `.worktreelink` has one path
on each line, and each path becomes a symbolic link to the main checkout, for
example a large `data` folder. Claude Code and Conductor read
`.worktreeinclude`; PostHog uses `.worktreelink`.

Commands in a worktree also make ignored files, such as `.venv` and test
caches. `just worktrees-prune` keeps a worktree with ignored files, so that
it never deletes an output. A third file, `.worktreedisposable`, has gitignore
patterns for the ignored files that a command can make again. Those files do
not keep a worktree. Each profile writes this file for its language when the
project has none. The Python profile names `.venv/` and the caches, and the
TypeScript profile names `node_modules/`.

**What works.** The install, the variant and the worktree recipes. The
GitButler text of the hooks appears only when `but` is installed and GitButler
owns the checkout. The session-start hook reports worktrees that are ready to
prune, and removes nothing. In `gitwt`, a `git commit` in the main checkout
gets a warning.

**What does not work:**

- **The worktree recipes are missing in a project that jnterfaces did not
  start.** There the skills run the script of the `gtw` skill directly.
- **The skills need `gest`.** `gest` does not come through `uv`. The installer
  prints a warning when it is missing.
- **The starter `AGENTS.md` has `<replace-me>` fields.** You fill them in.

### Layer 3: a language profile

```bash
just install --profile python
```

Use `--profile typescript` for TypeScript. Name a profile once. The installer
saves it.

**What you get with `python`:**

- The package jnprofile-python, with its recipe files and its skill
  `python-profile`.
- `pyproject.toml` from `uv init --bare`, with `default-groups = "all"`, so
  that `uv run` has the tools.
- A starter `.gitignore`, if the project has none.
- The default tools `lint` and `test`, which the next layers describe, and the
  recipe `just setup`.

**What you get with `typescript`:**

- The package jnprofile-typescript. `uv` runs npm and node from the
  `nodejs-wheel` package.
- `package.json` from `npm init --yes`, and a starter `.gitignore`.
- The default tools `lint` and `typecheck`.

A profile also turns on its default roles, `tasker` and `prose`, but only in a
project with no saved roles.

### Layer 4: lint and format

```bash
just install lint
```

| | Python | TypeScript |
|---|---|---|
| Packages | ruff, pre-commit, in the group `lint` | `@biomejs/biome@2` |
| Config files | `ruff.toml`, `.pre-commit-config.yaml` | `biome.json` |
| Recipes | `just fmt`, `just lint`, `just hook` | `just fmt`, `just lint` |
| In `just verify` | `fmt --check`, `lint` | `lint` |

`ruff.toml` and `biome.json` leave out `.agents`, `.claude` and `.codex`,
because these folders hold copies from other packages.

**What does not work.** The TypeScript profile has no `just hook`.

### Layer 5: tests

```bash
just install test
```

**Python.** The tool adds pytest and pytest-cov to the group `test`. `just
test` and `just cov` run pytest. With no arguments, a project with no tests
yet passes. A selection that runs no test fails.

**TypeScript.** The tool adds TypeScript and the Node types. `just test`
compiles `src` into `dist`, and it runs the tests with `node --test`. With no
`.ts` file in `src` yet, it passes.

### Layer 6: types

```bash
just install typecheck
```

**Python.** The tool adds ty to the group `lint`, and a `ty.toml` that leaves
out `.agents`, `.claude` and `.codex`. `just typecheck` runs `ty check`.

**TypeScript.** The tool adds TypeScript and `tsconfig.json`. `just
typecheck` runs `tsc --noEmit`. With no `.ts` file in `src` yet, it passes.

### Layer 7: one check before a commit

`just verify` runs the checks of each installed tool of each language, then
`git diff --check`, which finds whitespace errors. On a new project with the
default tools, `just verify` passes, for Python and for TypeScript.

cheesecracker calls this recipe `just check`. The command contract of the
`just` skill names it `verify`, and it lets a project map the name.

### Layers that the stack does not have yet

cheesecracker has these parts, and no profile or tool adds them:

| Part | In cheesecracker |
|---|---|
| A locked setup | `just setup` runs `uv sync --locked`. The profile runs `uv sync`. |
| A server | `just serve` and `just stop` start and stop uvicorn in the background. |
| System tests | `just test-system` runs tests against the running server. |
| Docker | `just docker-build`, `just docker-serve`, `just docker-stop`, `just docker-shell` and `just docker-test`, with a `.dockerignore`. |
| Async tests | The group `test` has pytest-asyncio and httpx2. |
| Coverage of one package | `just cov` names the package, `--cov=api`. |
| Test paths | `pyproject.toml` sets `testpaths` and `pythonpath` for pytest. |
| CI | The class repository cheese-app-ci-cd has a GitHub Actions workflow. No profile writes one. |

## How the mixins behave

**Adding a mixin.** `just install NAME` adds a role, a tool or a profile, and
it saves the choice. A tool that fails runs again at the next install.

**Removing a mixin.** `just uninstall NAME` takes out a role, a tool or a
profile:

- **A role** loses its files. Each file that you edited stays.
- **A tool** loses the packages that `just install` added, and its recipes.
  Each package that the project had before stays. A dependency group that is
  left empty goes too. The config files stay, because they are yours.
- **A profile** loses each of its tools, its recipe files and its package.
  The project keeps `pyproject.toml` or `package.json`. If a step fails, the
  next `just install` goes on with the removal, unless you name the profile
  again. `--profile` with a list that leaves out a saved profile also turns
  that profile off.

**Removing lint takes out the Git hook.** Before `just uninstall lint`
removes pre-commit, it runs `uv run --group lint pre-commit uninstall`. It
runs only when the `pre-commit` Git hook runs the Python of the project. If
the command fails, the removal stops, and the next `just install` tries it
again.

## What works now

| Mixin | Command | Works now | Does not work |
|---|---|---|---|
| Writing skill | `install_stack.py prose` | The skill, the starter Justfile and `just prose` | none |
| Task tracker | `just install tasker` | The skills, the hooks, `AGENTS.md`, the variant, the worktree recipes | Needs `gest`, which `uv` does not give |
| Python profile | `just install --profile python` | The package, the manifest, `.gitignore`, the defaults | none |
| TypeScript profile | `just install --profile typescript` | The package, the manifest, `.gitignore`, the defaults | none |
| Lint | `just install lint` | ruff and pre-commit, or biome | No `just hook` for TypeScript |
| Tests | `just install test` | pytest, or `node --test` | none |
| Types | `just install typecheck` | ty with `ty.toml`, or tsc | none |
| Remove a tool or a profile | `just uninstall NAME` | The packages, the recipes, the Git hook and empty groups go | none |
| Edit a skill | by hand | The edit stays, and the other files install | none |
| Claude Code | any install | The `.claude/skills` links | none |
| Server, Docker, CI | none | none | No mixin |

## Gaps to close

The profile packages closed the gaps of the recipes. The recipes follow the
tools, a project with no code has a Justfile, and `verify` runs only the
checks that are installed. These gaps are open:

1. **Add a profile for a web service**, for example jnprofile-fastapi, with
   the tools `serve`, `docker` and `system-test`. These parts are not for each
   Python project, so they are a profile of their own and not tools of the
   `python` profile. A project names both, with `just install --profile
   python,fastapi`. Two profiles with the same manifest, `pyproject.toml`,
   are not tested yet.
2. **Save the clone folder in the project.**

The installer fixes closed these gaps:

- **`.claude/skills` links** for each installed skill.
- **An edited file** no longer stops an install.
- **A named role** asks no questions.
- **The installer script reads `SKILL_PACKAGES_DIR`**, and each run prints
  the folder.
- **Removing lint** takes out the Git hook.

The role recipes closed these gaps:

- **`just prose`** checks the Markdown of the project.
- **`just uninstall tasker`** removes an `AGENTS.md` that nobody changed.
- **jnprofile-typescript** is on GitHub.
