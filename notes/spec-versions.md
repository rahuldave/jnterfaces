# Spec: version floors for programs and packages

This spec adds versions to the stack. A program gets a version floor, a
package records its version, and one package can require a version of
another. The installer checks the programs with the PATH that the recipes
get from jlog. A new step of `just check`, `tools-check`, checks the floors
in each clone of a project.

## Terms

| Term | Meaning |
|---|---|
| program | A command on the machine, such as `just` or `gest`. An entry of `programs` of a profile, of `install_extras.programs` of a role, or of `executables` of `skill-package.json` names it. |
| check | The command of a program that shows that the program is there, for example `just --version`. |
| floor | The lowest version of a program that an owner accepts, for example `>=1.50.0`. |
| effective floor | The highest floor that the owners of one program ask for. |
| owner | What asks for a program: jnterfaces, a skill, a profile, a role, or the project. |
| scope | A folder with `.jlog/bin` or `.jlog/environment.json`. jlog 0.2.0 puts its folders on the PATH of each recipe line. |
| release tag | A Git tag of the form `vX.Y.Z`, for example `v0.1.0`. |

## The problems

1. A program has no version. An old `gest` passes `gest --version`, and the
   tasker role then fails later, with a message about something else.
2. A package has no version. The record of a package names a commit only,
   so a person cannot see which release a project has.
3. The installer checks a program with its own PATH. jlog 0.2.0 sets the
   PATH of each recipe line from the scopes. So a tool that only `.jlog/bin`
   gives passes in the recipes, but fails `just install --check`.
4. A check can download. In a project that pins a toolchain, `rustup`
   installs the toolchain during a check.
5. `source_worktree_dirty` is `true` for each clone that holds an agent
   worktree, because the worktrees are untracked files under `.claude/`.

## The design

### A floor for a program

Each program entry gets an optional key `version`:

- `>=X.Y.Z` or `>=X.Y` is a floor. No other form is valid.
- No key means that the program must be there, and its check must pass.

The version of a program is the first `X.Y.Z`, or `X.Y`, anywhere in the
output of its check. The search covers the whole output, because `gest` prints
a banner before its version line. When the output has no version, the
search goes on in the error output. The comparison is by number, part by part, so
`2.101.0` is higher than `2.40`.

A check fails in these cases, and the message names the floor, the version
found, the path of the program and the owner:

- the program is not on the PATH
- the check exits with a code other than 0
- the output does not hold the text of `expect`
- the output holds no version
- the version is below the floor

### The floors of the stack

| Owner | Floors |
|---|---|
| jnterfaces, for each project | jlog >=0.2.0, just >=1.50.0, uv >=0.12.13, git >=2.40, gh >=2.40 |
| the jagt-protocol skill | jagt >=0.3.0 |
| the cx skill | cx >=0.1.0 |
| the tasker role, in agent_gest_git_skills | gest >=0.5.6 |

The floors of jnterfaces are in `executables.required` of its
`skill-package.json`. The floors of the two skills are in
`executables.optional`, and each entry has a new key `skill`. An optional
executable counts only while its skill is on.

### One floor for each program

The installer joins the programs of the profiles and their tools first. The
programs of the project, of the roles, and of the tools tables of the packages
and of the skills that are on come after them, in that order. For one program:

- The effective floor is the highest floor.
- The first entry gives the check. An entry with `install` comes before an
  entry without one.
- Each owner keeps its own floor in the record.

### The record of the floors

`.agents/stack.json` gets two keys:

- **`programs`.** The installer writes it at each run. It holds each program
  without `install`, with its check, its `expect`, its effective floor, and
  the floor of each owner. A clone of the project, as in CI, can check the
  programs from this key alone. A program with `install`, such as the git
  hooks of the tasker role or a Rust toolchain, is the state of one machine.
  A fresh clone has none of it until `just install` runs, so the key leaves
  it out.
- **`project_programs`.** The project writes it, and the installer never
  changes it. It is a list of programs with the keys `name`, `check`,
  `version`, `expect` and `description`. The owner of each one is the
  project.

### The checks run through jlog

When jlog 0.2.0 or later is on the PATH, the installer runs each check and
each install of a program as `jlog run COMMAND`, from the folder of the
command. So the check sees the PATH that a recipe line sees there. When jlog
is older or missing, the installer runs the check with its own PATH, and says
so once.

The other commands of the installer do not change:

- The commands of the tools, such as `uv add`, keep the PATH of the
  installer.
- The installer writes the same paths, and the record holds no path of a
  program.
- The environment of a check has no `VIRTUAL_ENV`, as before. `uv run` of
  the installer sets it for the installer only.

### No check installs or downloads

A check must only read. It must not install, download, or change the
project. The installer sets two variables for each check:

- `RUSTUP_AUTO_INSTALL=0`, so `rustup` installs no toolchain.
- `UV_PYTHON_DOWNLOADS=never`, so `uv` downloads no Python.

The installer cannot stop every download. For example, `uvx` downloads a
package that its cache does not hold. A profile must give such a program an
`install`, and a check that only reads.

### The step tools-check

The installer writes a step `tools-check` into `.agents/just/index.just`. It
runs `.agents/tools_check.py`, which jnterfaces installs as an extra file.
The script reads `programs` and `project_programs` of `.agents/stack.json`,
and runs each check. It needs Python 3.9 or later and no package, and it
installs nothing.

`tools-check` is the first step of `just check`. `release-check` comes next. The tasker role
(agent_gest_git_skills) gives it, and it checks offline that the next release
tag will be valid. Without that role, it logs a skip. The steps are:

```text
tools-check release-check fmt-check lint typecheck test integration coverage-check docstring-check docs-check prose-check diff-check
```

A project whose Justfile has its own `check` must run each step in it, such
as `tools-check` and `release-check`,
because `jlog pass` needs a log line for each step. The lint of steps names a
`check` that does not.

jnterfaces runs its own `tools-check` in its own `just check` and in its CI.
That recipe checks `executables.required` of its `skill-package.json`.

### The version of a package

The record of each package, `.agents/<package>-install.json`, gets the key
`source_version`:

1. the release tag at `source_revision`, the highest when there are several
2. else the output of `git describe --tags` over the release tags, so that a tag of another form, such as a date, cannot meet a floor
3. else `null`

A package can name the versions of other packages that it needs, in a new
top key of `skill-package.json`:

```json
"requires": {"jnterfaces": ">=0.1.0"}
```

Before anything is written, the installer finds the version of each named
package from its clone. The run stops when a package is not in the run,
when its clone has no tag, or when its version is below the floor. The same
comparison as for the programs decides.

### A clean source

`source_worktree_dirty` ignores each untracked file under `.claude/` in the
clone of a package. A tracked file under `.claude/` with a change still
counts.

## The order of the work

1. The comparison and the check of one program, in `.agents/tools_check.py`.
2. The key `version`, the join of the floors, and the key `programs` of the
   record.
3. The checks through `jlog run`, and the variables that stop a download.
4. The step `tools-check`, in the index and in the step lists of the docs.
5. `source_version`, `requires`, and the clean source.
6. The docs: `docs/stack-files.md`, `docs/stack-guide.md`, `README.md` and the
   `just` skill.

## Tests

Each behavior has a test that fails before the change:

- A version in the output after a banner, a version below the floor, an
  output without a version, and a missing program each give the message with
  the floor, the version, the path and the owner.
- Two owners of one program record the highest floor and each owner.
- A reinstall keeps `project_programs`.
- A program that only `.jlog/bin` gives passes the install and `just install
  --check`.
- A check gets no `VIRTUAL_ENV`, and a run through `uv run` works.
- `just install --check` makes no change to the target.
- The index has `tools-check`, and `just check` runs it first.
- `source_version` is the tag, the output of `git describe`, or null.
- `requires` stops a run with a version that is too low.
- An untracked file under `.claude/` of the source does not make it dirty.

## What another package must add

agent_gest_git_skills must add `"version": ">=0.5.6"` to the entry `gest` of
`executables.required` in its `skill-package.json`. It can also add
`"requires": {"jnterfaces": ">=0.2.0"}` once jnterfaces has a release with
this change.

## Limits

- A floor has the form `>=` only. No upper limit and no exact version exist.
- The installer cannot stop a download by `uvx`, `npx` or a similar
  command. The check `node` of the TypeScript profile runs `uvx`, so its
  first check downloads a package.
- A program of a tools table without a check gets no entry in `programs`.
  The installer checks only that such a program is on the PATH.
