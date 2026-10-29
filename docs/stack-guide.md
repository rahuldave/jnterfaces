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
| profile | A language setup, for example `python`. It names the manifest, the Justfile template and the tools. |
| tool | A set of packages for the code of the project, for example `lint`, which adds ruff and pre-commit. |
| manifest | The file that lists the dependencies of the project: `pyproject.toml` or `package.json`. |
| record | A JSON file in `.agents/` that lists what the installer wrote, with a hash of each file. |
| mixin | One layer that you add to a project, for example a role or a tool. |

## Where each part lives

The stack has parts in four places:

| Place | What it holds |
|---|---|
| GitHub, `rahuldave/...` | The source of each package: jnterfaces, agent_gest_git_skills, nossaifd and sklanes. |
| The clone folder | One Git clone of each package. The default folder is `~/.local/share/skill-packages`. |
| The project | Copies of the skills in `.agents/skills`, the records, and `.agents/stack.json`. |
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
roles and profiles is in `stack.json` of jnterfaces.

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
3. It copies the skills and other files of each role into the project, and it
   writes one record for each package.
4. It saves the choices in `.agents/stack.json`.
5. If the project has no Justfile, it writes the Justfile template of the
   profile.
6. If a tool of a profile is to be added and the project has no manifest, it
   makes one, with `uv init --bare` or `npm init --yes`.
7. It adds the packages of each tool, with `uv add --group` or `npm install
   --save-dev`, and it copies the config files of the tool.

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

## Build a project in layers

Each layer adds one mixin. Start at layer 0, and add the layers that the
project needs. A layer works with the layers above it, unless the layer says
otherwise.

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

- `.agents/skills/just` and `.agents/skills/nossaifd`, with `check.py`.
- `.agents/install.just`, the records, and `.agents/stack.json`.

**What works.** The install needs `git` and `uv` only. Run the checker on a
page:

```bash
uv run --no-project python .agents/skills/nossaifd/scripts/check.py README.md
```

**What does not work:**

- **A project with a manifest gets tools.** If the project has
  `pyproject.toml` or `package.json`, the installer finds the profile. Then it
  adds the default tools of the profile, and a Justfile if the project has
  none. To install the role alone, pass `--profile ''`. But the project
  saves no profile, so the next `just install` finds the profile again, and
  it adds the tools.
- **No Justfile.** With no profile, the installer writes no Justfile. It
  prints the line to add. So `just install` is not there until you write a
  Justfile.
- **No recipe for the checker.** No template has a recipe such as
  `just prose`. You type the `uv run` command above.
- **No `AGENTS.md`.** Only the `tasker` role writes one. So nothing tells an
  agent to use the nossaifd skill.

### Layer 2: the Justfile

Write a Justfile by hand. These lines are enough:

```just
import? '.agents/install.just'

[private]
default:
    @just --list --unsorted
```

**What works.** `just install`, `just uninstall` and `just install --help`.

**What does not work.** The stack has no profile and no template for a
project that has only prose. Each project writes these lines by hand.

**Order the layers.** If the project will have code soon, skip this layer.
Layer 4 writes the Justfile template of the profile, but only when the
project has no Justfile.

### Layer 3: the task tracker

```bash
just install tasker
```

**What you get:**

- The `g*` skills, which track work with Gest.
- The agent hooks in `.claude/hooks` and `.codex/hooks`, with their entries
  in `.claude/settings.json` and `.codex/hooks.json`.
- A starter `AGENTS.md`, if the project has none.

**What works.** The install. The hooks run without GitButler, and the Git
guard of the hooks lets `git commit` run.

**What does not work:**

- **The hooks tell the agent to use GitButler** with `but`, also when `but`
  is not installed.
- **`just uninstall tasker` keeps `AGENTS.md`.** The file still points to
  `.agents/skills/gtw`, which the removal takes out.
- **The skills need `gest`.** `gest` does not come through `uv`. The installer
  prints a warning when it is missing.
- **The starter `AGENTS.md` has `<replace-me>` fields.** You fill them in.

### Layer 4: a language profile

A profile chooses the manifest, the Justfile template and the tools. Name it
once. The installer saves it.

```bash
just install --profile python
```

Use `--profile typescript` for TypeScript. If the project has a Justfile
already, the installer keeps it. It adds no recipes to it.

**What you get with `python`:**

- `pyproject.toml` from `uv init --bare`, with `default-groups = "all"`, so
  that `uv run` has the tools.
- The Justfile template `python-uv.just`, which you then own.
- The default tools of the profile, `lint` and `test`. The next layers
  describe them.
- The default roles of the profile, `tasker` and `prose`, but only in a
  project with no saved roles. A project keeps the roles that it saved. To
  turn off a role, run `just uninstall`, for example `just uninstall tasker`.

**What you get with `typescript`:**

- `package.json` from `npm init --yes`. `uv` runs npm from the
  `nodejs-wheel` package.
- The Justfile template `typescript-npm.just`.
- The default tools `lint` and `typecheck`.

**What does not work:**

- **A Justfile from layer 2 gets no recipes.** So `just lint`, `just test`
  and `just verify` are missing. Copy them from
  `.agents/skills/just/assets/templates/python-uv.just`.
- **No `.gitignore`.** The installer writes none. The Python folders
  `.venv`, `.local`, `.pytest_cache` and `.ruff_cache` ignore themselves. But
  in a TypeScript project, `git status` shows `.local`, `node_modules` and
  `dist`.
- **The template has recipes for every tool**, also for the tools that are not
  installed. The section on mixins describes the effect.

### Layer 5: lint and format

```bash
just install lint
```

| | Python | TypeScript |
|---|---|---|
| Packages | ruff, pre-commit, in the group `lint` | `@biomejs/biome@2` |
| Config files | `ruff.toml`, `.pre-commit-config.yaml` | `biome.json` |
| Recipes | `just fmt`, `just lint`, `just hook` | `just fmt`, `just lint` |

`ruff.toml` and `biome.json` leave out `.agents`, `.claude` and `.codex`,
because these folders hold copies from other packages.
`.pre-commit-config.yaml` does not leave them out.

**What works.** On an empty Python project, `just fmt --check` and `just lint`
pass. `just hook` installs the pre-commit checks as a Git hook.

**What does not work.** The TypeScript profile has no `just hook`.

### Layer 6: tests

```bash
just install test
```

**Python.** The tool adds pytest and pytest-cov to the group `test`. `just
test` and `just cov` run pytest. With no arguments, a project with no tests
yet passes. A selection that runs no test fails.

**TypeScript.** The tool adds TypeScript and the Node types. `just test`
compiles `src` into `dist`, and it runs the tests with `node --test`.

**What does not work.** On a TypeScript project with no files in `src`,
`just test` fails. The compiler stops with "No inputs were found".

### Layer 7: types

```bash
just install typecheck
```

**Python.** The tool adds ty to the group `lint`. `just typecheck` runs `ty
check`. **TypeScript.** The tool adds TypeScript, and `just typecheck` runs
`tsc --noEmit`.

**What does not work:**

- **The TypeScript check fails with no code.** Like `just test`, `just
  typecheck` fails when `src` has no files. So `just verify` fails on a new
  TypeScript project.
- **ty does not leave out `.agents`.** The stack ships no ty config. On this
  machine, a ty on PATH found two errors in the copy of nossaifd's
  `check.py`. The ty of the project, a newer version, found none.

### Layer 8: one check before a commit

`just verify` runs every check that a commit must pass:

| Profile | `just verify` runs |
|---|---|
| `python` | `fmt --check`, `lint`, `test`, `diff-check` |
| `typescript` | `lint`, `typecheck`, `test`, `diff-check` |

`diff-check` runs `git diff --check`, which finds whitespace errors.

**What works.** On an empty Python project with the default tools, `just
verify` passes. On a TypeScript project with one source file and one test,
`just verify` passes.

**What does not work:**

- **The Python `verify` does not run `typecheck`**, also when the tool is
  installed.
- **cheesecracker has `just check` in place of `verify`.** It runs `fmt
  --check`, `lint` and `test -q`, with no `diff-check`. The command contract
  of the `just` skill names the recipe `verify`, and it lets a project map
  the name.

### Layers that the stack does not have yet

cheesecracker has these parts, and no profile or tool adds them:

| Part | In cheesecracker |
|---|---|
| A locked setup | `just setup` runs `uv sync --locked`. The template runs `uv sync`. |
| A server | `just serve` and `just stop` start and stop uvicorn in the background. |
| System tests | `just test-system` runs tests against the running server. |
| Docker | `just docker-build`, `just docker-serve`, `just docker-stop`, `just docker-shell` and `just docker-test`, with a `.dockerignore`. |
| Async tests | The group `test` has pytest-asyncio and httpx2. |
| Ignore rules | A `.gitignore`. |
| Coverage of one package | `just cov` names the package, `--cov=api`. |
| Test paths | `pyproject.toml` sets `testpaths` and `pythonpath` for pytest. |
| CI | The class repository cheese-app-ci-cd has a GitHub Actions workflow. No profile writes one. |

## How the mixins behave

**Adding a mixin works.** `just install NAME` adds a role, a tool or a
profile, and it saves the choice. A tool that fails runs again at the next
install.

**Removing a mixin works for the packages.** `just uninstall NAME` removes
the files of a role. For a tool, it removes the packages that `just install`
added. It keeps each file that you edited, and each package that the project
had before.

**Removing lint takes out the Git hook.** Before `just uninstall lint`
removes pre-commit, it runs `uv run --group lint pre-commit uninstall`. It runs only when
the project has a `pre-commit` Git hook, and when the removal takes out
pre-commit. If the command fails, the removal stops, and the next `just
install` tries it again.

**Removing a tool breaks the Justfile.** The installer writes the Justfile
once, and it never changes the Justfile again. So the recipes of a removed
tool stay. Tests with `git`, `just` and `uv` as the only programs on PATH
showed these results after `just uninstall lint` on a Python project:

- `just verify` fails, because `just fmt` finds no ruff.
- `ruff.toml` and `.pre-commit-config.yaml` stay, because they are yours.
- `pyproject.toml` keeps an empty group, `lint = []`.

**A profile cannot be removed.** `just uninstall python` fails with "Unknown
name: python".

**A recipe can run a program that is not in the project.** If a tool is not
installed, `uv run ruff` runs a ruff from PATH, if one is there. That ruff can
be another version, with other results.

## What works now

| Mixin | Command | Works now | Does not work |
|---|---|---|---|
| Writing skill | `install_stack.py prose` | The skill and `check.py` | No Justfile, no recipe for the checker, no `AGENTS.md`. A manifest turns on tools |
| Justfile | by hand | `just install` and `just uninstall` | No template for a project with no code |
| Task tracker | `just install tasker` | The skills, the hooks, `AGENTS.md` | Needs `gest`, which `uv` does not give. Removal keeps `AGENTS.md` |
| Python profile | `just install --profile python` | The manifest, the template, the defaults | No `.gitignore`. No recipes for a Justfile that exists |
| TypeScript profile | `just install --profile typescript` | The manifest, the template, the defaults | No `.gitignore`. `verify` fails with no code |
| Lint | `just install lint` | ruff and pre-commit, or biome | No `just hook` for TypeScript |
| Tests | `just install test` | pytest, or `node --test` | TypeScript fails with no code |
| Types | `just install typecheck` | ty, or tsc | Not in the Python `verify`. No ty config |
| Remove a tool | `just uninstall lint` | The packages and the Git hook go | The recipes stay, and they fail |
| Edit a skill | by hand | The edit stays, and the other files install | none |
| Claude Code | any install | The `.claude/skills` links | none |
| Server, Docker, CI | none | none | No mixin |

## The plan: profile repositories

The owner of the stack agreed on this design on 2026-10-29. Nothing in it is built yet.

**Each language gets its own repository**, named `jnprofile-<language>`, for
example `jnprofile-python`, `jnprofile-rust`, `jnprofile-go` and
`jnprofile-pixi`. Pixi is a tool that sets up projects in more than one
language. jnterfaces keeps the installer and the `just` skill, and its
`stack.json` names each profile repository, as it names a package now.

A profile repository holds these parts:

- **A profile file.** It names the files that identify the project, the
  manifest, and the commands that add and remove packages.
- **One folder for each feature.** A feature is one unit of the setup, for
  example `lint`, `fmt` or `test`. The folder holds the recipe file of the
  feature, its config files, and the command that installs it.
- **An `init` feature.** `just install init` installs the toolchain of the
  language. For example, the Rust profile installs `cargo` and `rustc`. The
  machine has `just` and `uv`, so the Python profile needs no toolchain.
- **Skills**, if the language needs them.

**How a feature arrives in a project:**

1. `just install rust` clones the profile repository into the clone folder.
2. `just install init` installs the toolchain.
3. `just install lint` runs the install command of the feature, copies its
   config files, and copies its recipe file into `.agents/just/`.
4. The installer writes `.agents/just/index.just` again, with one `import?`
   line for each installed feature.
5. The Justfile of the project imports `index.just` once, and it does not
   change after that. A removed feature takes its recipes with it.

**A project in more than one language** gets the recipes of each profile, and
the checks add up. `just lint` runs the lint of each installed language. `just
verify` runs every installed check of each language. The plan is to use just
modules, so that `just rust lint` also runs one language alone.

The choices of a project stay in `.agents/stack.json`, for example
`"profiles": ["python", "rust"]`.

## Gaps to close

The profile repositories close the gaps of the recipes:

- **The recipes follow the features.** A removed tool takes its recipes with
  it, and `verify` runs only the checks that are installed.
- **A project with no code has a Justfile** with `just install`, and a later
  profile adds its recipes to it.

These gaps need their own changes. A gap that a change closed says so.

1. **Make the `.claude/skills` links** when the installer copies a skill, and
   take them out on removal. Closed by the installer fixes.
2. **Add a recipe for the writing checker** to the `prose` role.
3. **Write a `.gitignore`** for each profile, when the project has none.
4. **Let the TypeScript checks pass with no code**, as `just test` does for
   Python.
5. **Add features for the parts of cheesecracker**, for example `serve`,
   `docker` and `ci`.
6. **Let an install go on past an edited skill file.** Closed by the
   installer fixes. The installer keeps the edit, names the file, and installs
   the other files.
7. **Ask about the roles only when no role is named** on the command line.
   Closed by the installer fixes.
8. **Keep the choice of no profile.** `--profile ''` works for one install,
   but the next install finds the profile from the files again.
9. **Remove `AGENTS.md` with `tasker`**, or take out its lines about the
   `g*` skills, when the file is not edited.
10. **Let `just uninstall` remove a profile.**
11. **Make the hooks of `tasker` say to use GitButler only when `but` is
    installed.**
12. **Write an `AGENTS.md` line for `prose`**, so that an agent knows to use
    the writing skill.
13. **Ship a ty config** that leaves out `.agents`, `.claude` and `.codex`.
14. **Remove an empty dependency group**, such as `lint = []`, with its tool.
15. **Print the clone folder, and let the installer script read
    `SKILL_PACKAGES_DIR`.** Closed by the installer fixes. The project still
    does not save the folder.
16. **Take out the Git hook when the lint tool goes.** Closed by the installer
    fixes.
