---
title: Start a Python, TypeScript or Rust project
---

This page tells how to set up a new project with every part that the stack gives. The
parts are the agent skills, the task tracker, the writing checker, format,
lint, tests, coverage, types, a Git hook and one full check. Each step and each
example was run on 2026-10-31 in an empty folder. The Rust steps and examples
were run on 2026-11-15.

[How the stack fits together](stack-guide.md) tells how an install works.
[The files of the stack](stack-files.md) tells what each file holds.

## Terms

| Term | Meaning |
|---|---|
| recipe | A command in a Justfile. `just NAME` runs the recipe NAME. |
| role | A job of the agent skills, for example `tasker`. One package does the job. |
| profile | A language setup: `python`, `typescript` or `rust`. |
| tool | One part of a profile, such as `lint`. It adds packages, config files and recipes. |
| clone | The local Git clone of a package. The installer copies the skills from it. |
| the record | `.agents/stack.json` in the project. It saves the profiles, roles and tools. |

## What the machine needs

| Program | For | How to get it |
|---|---|---|
| `git` 2.40 or later | Each project and each package clone | The system package manager |
| `gh` 2.40 or later | The pull requests and the checks of CI | https://cli.github.com |
| `just` 1.50.0 or later | Every recipe | https://just.systems |
| `uv` 0.12.13 or later | The installer, Python, and Node for TypeScript | https://docs.astral.sh/uv |
| `jlog` v0.2.0 or later | The shell of each recipe that `just install` writes. It logs each line in the run log. | Run the install line of jlog under this table. It needs no Rust. The installer stops when jlog is missing. |
| `gest` 0.6.0 or later | The skills of the `tasker` role | Run the install line of gest under this table. It does not come through `uv`. The installer stops when it is missing. |

The install line of jlog downloads the release binary, checks its SHA-256,
and puts jlog in `~/.local/bin`. Put that folder on PATH.

```bash
curl -fsSL https://raw.githubusercontent.com/rahuldave/jlog/main/scripts/install.sh | sh -s -- --tag v0.3.0
```

To build jlog from source instead, clone https://github.com/rahuldave/jlog,
run `git checkout v0.3.0` in the clone, then run `just install` there. That
build needs Rust, from https://rustup.rs/.

The install line of gest works the same way. It installs the fork of
rahuldave, whose `gest --version` prints `(rahuldave)`.

```bash
curl -fsSL https://raw.githubusercontent.com/rahuldave/gest/v0.6.0/scripts/install.sh | sh -s -- --tag v0.6.0
```

To build gest from source instead, clone https://github.com/rahuldave/gest,
run `git checkout v0.6.0` in the clone, then run
`cargo install --locked --path .` there.

A profile can need more programs. The rust profile needs `rustup`, from
https://rustup.rs/. The installer checks each program of the profile. When
`rustup` is missing, it stops before it writes anything, and it says where to
get `rustup`. The installer installs the other programs of the profile
itself. For example, it installs the toolchain, which brings `cargo`, with
`rustup toolchain install`, and `cargo-llvm-cov` with `cargo +stable
install`. It never removes a program.

TypeScript needs no Node on the machine. `uv` runs `node` and `npm` from the
`nodejs-wheel` package, version 24.

## Step 1: clone jnterfaces once

The installer reads each package from a clone in one folder. The default
folder is `~/.local/share/skill-packages`. To use another folder, export
`SKILL_PACKAGES_DIR` in your shell profile, for example:

```bash
export SKILL_PACKAGES_DIR=~/Projects
```

Set it before the first install. `just install` in a project finds the clones
in that folder only.

```bash
git clone https://github.com/rahuldave/jnterfaces "${SKILL_PACKAGES_DIR:-$HOME/.local/share/skill-packages}/jnterfaces"
```

The installer clones each other package that it needs into the same folder.

## Step 2: make the project and install

```bash
mkdir app && cd app && git init
uv run "${SKILL_PACKAGES_DIR:-$HOME/.local/share/skill-packages}/jnterfaces/scripts/install_stack.py" --profile python
```

Use `--profile typescript` for TypeScript, `--profile rust` for Rust, or
`--profile python,typescript` for two languages. At a terminal, the installer asks about each role. Add
`--defaults` to take the defaults with no questions:

| Role | Default | What it gives |
|---|---|---|
| `tasker` | yes | The `g*` skills, which track work with Gest, the agent hooks, a starter `AGENTS.md` and the worktree recipes |
| `prose` | yes | The nossaifd writing guide, its checker, `just prose-check`, the declaude skill, from the declauding skill by Oskar Austegard, and the [writing flow](https://rahuldave.github.io/nossaifd/): an instruction, two hooks and a GitHub workflow |
| `cx` | no | The `cx` skill: run the file stages of a recipe only when they are out of date. It needs the `cx` program. |
| `jagt-protocol` | no | The `jagt-protocol` skill: the packets of agentic recipes. It needs the `jagt` program. |
| `docs` | no | A docs site in `docs/` with smalldocs: `docs-check` in `just check`, and `docs-publish` with a CI workflow that publishes a public repository |
| `reporter` | no | The sklanes skill: report requests for human attention to a Lanes inbox |

The `just` skill of jnterfaces is always installed. Name a role to add it
later, for example `just install cx`.

The installer then does these steps:

1. If the project has no manifest, it makes one. The manifest is
   `pyproject.toml`, from `uv init --bare`, `package.json`, from `npm init
   --yes`, or `Cargo.toml`, from `cargo init --vcs none`.
2. It adds the default tools of the profile, with `uv add --group` or `npm
   install --save-dev`. The Rust tools add no crate.
3. It writes the starter `Justfile`, the starter `AGENTS.md`, and a
   `CLAUDE.md` that imports `AGENTS.md`. With the `prose` role and without the
   `tasker` role, it also writes the workflow
   `.github/workflows/nossaifd-prose.yml`. With the `tasker` role, it writes
   `.github/workflows/check.yml`, which runs `just ci` on each pull request.
   Each of these files is yours from then on.
4. It writes two empty lists, `.agents/generated.txt` and
   `.agents/coverage-exclude.txt`. You or an agent add paths to them later.
5. It saves the choices in `.agents/stack.json`.

Run `just` alone to list the recipes. Then run `just verify`, which passes in a
project with no code yet. A Rust project always has code, so first read
[A Rust project](#a-rust-project).

`just check` runs the steps of the commit gates, in this order:

```text
tools-check release-check fmt-check lint typecheck test integration coverage-check docstring-check docs-check prose-check diff-check
```

A step fails when the change breaks a rule. The `prose` role gives
`prose-check`, and the tasker role (agent_gest_git_skills) gives
`release-check`. The installer gives `tools-check` and `diff-check`. A step that nothing gives
logs a skip. Each profile gives the steps of its installed tools, and the
`test-paths` recipe:

| Profile | Steps | `test-paths` |
|---|---|---|
| `python` | `lint`, `test`, `typecheck` | yes |
| `typescript` | `fmt-check`, `lint`, `typecheck`, `test`, `coverage-check`, `docstring-check` | yes |
| `rust` | `fmt-check`, `lint`, `typecheck`, `test`, `coverage-check`, `docstring-check` | yes |

`test-paths` runs only the tests of some test files. The `test-parent` recipe
of the `tasker` role runs it with the added and changed test files, on the
parent commit. The agent of `test-review` reads the result.

## A Python project

### The tools

| Tool | Default | Packages | Files | Recipes |
|---|---|---|---|---|
| `lint` | yes | ruff, in the group `lint` | `ruff.toml` | `fmt`, `lint` |
| `test` | yes | pytest, pytest-cov, in the group `test` | `[tool.pytest.ini_options]` in `pyproject.toml` | `test`, `cov` |
| `typecheck` | no | ty, in the group `lint` | `ty.toml` | `typecheck` |

Add the type check:

```bash
just install typecheck
```

`pyproject.toml` gets `default-groups = "all"` under `[tool.uv]`, so that
`uv run` has every tool. The project uses the Python that `uv` picks, and `uv
init` writes that version as `requires-python`.

### The recipes

| Recipe | What it runs | Notes |
|---|---|---|
| `just setup` | `uv sync` | Installs Python and each package into `.venv`. Run it in a new clone. |
| `just fmt` | `ruff format` | Changes the files. `just fmt --check` only checks. |
| `just lint` | `ruff check` | `just lint --fix` fixes what it can. |
| `just test` | `pytest` | Extra words go to pytest: `just test tests/test_app.py`. With no tests yet, it passes. A selection that runs no test fails. |
| `just cov` | `pytest --cov --cov-report=term-missing` | Shows the lines that no test ran. |
| `just test-paths FILE...` | `pytest` | Runs only the test files that you name. With no file, it runs nothing and exits with 5. |
| `just typecheck` | `ty check` | |
| `just verify` | `fmt --check`, `lint`, `test`, `typecheck`, then `git diff --check` | Runs the checks of the installed tools only. It stops at the first failure. |

Each recipe also has a form for one language, such as `just python lint`. In
a project with two profiles, use that form to run the recipe of one language
only.

### The code and the tests

Put modules at the top of the project, such as `app.py`, or put a package
folder there. Put the tests in `tests/`, in files named `test_NAME.py`:

```python
# app.py
def add(a: int, b: int) -> int:
    return a + b
```

```python
# tests/test_app.py
from app import add


def test_add():
    assert add(1, 2) == 3
```

pytest finds `app` because the `test` tool adds `"."` to `pythonpath`. Then:

```bash
just fmt
just verify
just cov
```

### The Git hooks

The Python profile installs no Git hook. The `tasker` role installs the
pre-commit framework and its hooks, for each language. Before each commit,
they run the commit gate and the checks of `.pre-commit-config.yaml` that
change no file. `just install` installs the hooks in each clone.

### Change a setting

- **ruff.** `ruff.toml` reads the settings of the profile with `extend`. Add
  your settings below that line. Your settings win over the settings of the
  profile.
- **ty.** Add your settings to `ty.toml`.
- **pytest.** Add your settings under `[tool.pytest.ini_options]` in
  `pyproject.toml`, beside `pythonpath`. If the project already has a pytest
  config, such as `pytest.ini` or `[tool.pytest]`, the `test` tool does not
  add `pythonpath`. Add `pythonpath = ["."]` to that config yourself.

## A TypeScript project

### The tools

| Tool | Default | Packages | Files | Recipes |
|---|---|---|---|---|
| `lint` | yes | `@biomejs/biome@2` | `biome.json` | `fmt`, `lint` |
| `typecheck` | yes | `typescript@7`, `@types/node@24` | `tsconfig.json` | `typecheck` |
| `test` | yes | `typescript@7`, `@types/node@24`, `c8@12` | `tsconfig.json` | `build`, `test`, `coverage-check`, `cov` |
| `docstring` | yes | none in `package.json` | none | `docstring-check` |
| `api` | yes | none | none | `api-check`, `api-prose-check` |
| `integration` | no | `typescript@7`, `@types/node@24`, `c8@12` | `tsconfig.json` | `integration` |

The install turns on the default tools. Add the integration tests, which run from `tests/integration`:

```bash
just install integration
```

The packages go into `devDependencies` of `package.json`. The npm cache is in
`.local/npm-cache`, which Git ignores.

### The recipes

| Recipe | What it runs | Notes |
|---|---|---|
| `just setup` | `npm install` | Installs each package of `package.json` into `node_modules`. It can update `package-lock.json`. Run it in a new clone. |
| `just fmt` | `biome format --write` | Changes the files. |
| `just lint` | `biome check` | Checks the lint rules, the format and the order of imports. `just lint --write` fixes what it can. |
| `just typecheck` | `tsc --noEmit` | With no `.ts` file in `src` yet, it passes. |
| `just build` | `tsc` | Compiles `src` into `dist`, with source maps. |
| `just test` | `tsc`, then `node --test` under c8 | Extra words go to `node --test`. With no `.ts` file in `src` yet, it passes. |
| `just cov` | `tsc`, then the tests under c8 | Shows the lines that the tests ran. |
| `just verify` | The steps of the installed tools, then `git diff --check` | Runs the checks of the installed tools only, for example `lint`, `typecheck`, `test` and `docstring-check`. |

### The code and the tests

Put the code and the tests in `src/`. Name each test file `NAME.test.ts`.
`tsconfig.json` compiles all of `src`, so the build writes the tests to
`dist` too. Import the code with the `.js` name that the build writes:

```typescript
// src/app.ts
export function add(a: number, b: number): number {
  return a + b;
}
```

```typescript
// src/app.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";
import { add } from "./app.js";

test("add", () => {
  assert.equal(add(1, 2), 3);
});
```

The tests use `node:test` and `node:assert`, which come with Node. Then:

```bash
just fmt
just verify
```

### Change a setting

`biome.json` and `tsconfig.json` read the settings of the profile with
`extends`. Add your keys beside `extends`. Your keys win over the settings of
the profile. For example, the profile
sets a line width of 120 for Biome.

## A Rust project

### The tools

| Tool | Default | Programs | Recipes |
|---|---|---|---|
| `lint` | yes | rustfmt, clippy | `fmt`, `fmt-check`, `lint` |
| `typecheck` | yes | cargo, from the toolchain | `typecheck` |
| `test` | yes | stable, llvm-tools-preview, cargo-llvm-cov | `test`, `coverage-check`, `test-paths` |
| `docstring` | yes | clippy | `docstring-check` |
| `toml` | no | stable, taplo, cargo-sort | `toml-fmt`, `toml-check` |

Add the format of the TOML files:

```bash
just install toml
```

No tool adds a crate to `Cargo.toml` or writes a config file. The installer
installs the programs of each tool, with `rustup component add` or `cargo
+stable install --locked`. The skill `rust-profile` tells an agent which tool
gives which recipe. The rules in `.agents/targets/rust/` add to the agentic
targets `review`, `test-review`, `docs` and `prose`.

### The recipes

| Recipe | What it runs | Notes |
|---|---|---|
| `just setup` | `cargo fetch` | Downloads each dependency, and makes or updates `Cargo.lock`. Run it in a new clone. |
| `just build` | `cargo build --workspace` | Extra words go to cargo: `just build --release`. |
| `just fmt` | `cargo fmt --all` | Changes the files. |
| `just fmt-check` | `cargo fmt --all --check` | Changes no file. |
| `just lint` | `cargo clippy` | Each warning fails. |
| `just typecheck` | `cargo check` | Checks the code and the tests. |
| `just test` | `cargo llvm-cov test` | Writes the coverage to `target/llvm-cov/lcov.info`. `just test parse` runs the tests whose names hold `parse`. |
| `just coverage-check` | `diff-cover` | Fails on each changed line of code that no test runs. On the first commit, each line of code must run. |
| `just docstring-check` | `cargo clippy` with the lints of missing docs | Fails on each item with no doc comment, public or private. |
| `just test-paths PATH...` | `cargo test` | Runs the tests of some files in `tests/`, without coverage. |
| `just toml-fmt` | `cargo sort`, then `taplo fmt` | Changes the files. |
| `just toml-check` | `cargo sort --check`, then `taplo fmt --check` | `just verify` runs it, and `just check` does not. |
| `just verify` | `fmt-check`, `lint`, `typecheck`, `test`, `coverage-check`, `docstring-check`, then `git diff --check` | Runs the checks of the installed tools only. |

The steps run cargo with `--locked`, so a step never makes or changes
`Cargo.lock`. Run `just setup` first, and commit `Cargo.lock`.

### The code and the tests

`cargo init` writes a `src/main.rs` with no test. On the first commit,
`coverage-check` fails on each line of it. So write a test in `tests/` that
runs the binary, from `env!("CARGO_BIN_EXE_NAME")`, where `NAME` is the name of
the binary. For a library, run `cargo init --lib --vcs none` before the
install. `cargo init` names the package after
the folder. For a folder name that is not a valid crate name, such as a name
that starts with a digit, add `--name NAME`.

Put a unit test in a `#[cfg(test)] mod tests` in the file of its code. Put
an integration test in `tests/`. Write a doc comment for each item. The
example is a library, so it starts from `cargo init --lib --vcs none` before
the install, and has no `src/main.rs`:

```rust
// src/lib.rs
//! The arithmetic of the app.

/// Returns the sum of `a` and `b`.
pub fn add(a: i64, b: i64) -> i64 {
    a + b
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn adds() {
        assert_eq!(add(1, 2), 3);
    }
}
```

```rust
// tests/add.rs
//! The tests of the public API.

#[test]
fn adds_negative_numbers() {
    assert_eq!(app::add(-1, -2), -3);
}
```

The integration test names the crate `app`, the name of the folder. Then:

```bash
just setup
just fmt
just verify
```

### Lines that no test can run

Stable Rust has no comment that leaves one line out of the coverage. So the
steps read two lists:

- **`.agents/coverage-exclude.txt`** lists the files that `coverage-check`
  leaves out.
- **`.agents/generated.txt`** lists the generated files. `coverage-check`
  and `docstring-check` leave them out.

The steps read both lists from the parent commit. So a new entry counts only
after it is in a commit of its own. Write the reason for each entry in a
comment line, which starts with `#`, above it.

### Change a setting

The profile writes no config file. cargo reads your own `rustfmt.toml`,
`clippy.toml` and the table `[lints]` of `Cargo.toml`. The profile keeps the
toolchain that your `rust-toolchain.toml` pins.
[The tools of jnprofile-rust](https://github.com/rahuldave/jnprofile-rust/blob/main/docs/tools.md)
gives each recipe and each rule of the steps.

## The task tracker

The `tasker` role works on a branch in the main checkout. A Git worktree is a
second checkout of the same repository, in another folder. To work on each
task in its own worktree, and keep the main checkout on `main`, run:

```bash
just install tasker=gitwt
just tasker-mode
```

These recipes manage the worktrees:

| Recipe | What it does |
|---|---|
| `just worktree-new NAME [BASE]` | Makes branch NAME in `../<repo>.worktrees/NAME`, sets it up, and runs `just setup` there |
| `just worktree-sync` | Sets up a worktree that another tool made |
| `just worktrees` | Lists the worktrees: clean or dirty, merged or not |
| `just worktrees-prune` | Removes the clean, merged worktrees that were idle for 2 hours |

Fill in the `<replace-me>` fields of `AGENTS.md`. They name the project, the
source folder, the docs and the branches.

## The writing checker

`just prose-check` checks each Markdown page that changed since the last
commit against the writing guide. It leaves out `AGENTS.md`, `CLAUDE.md` and
the folders of the installer. Name pages to check only them, and add `-v` to
see the soft notes:

```bash
just prose-check -v README.md
```

It is the step `prose-check` of `just check`.

## What to commit

Commit these files:

- `.agents/`, `.claude/` and `.codex/`, which hold the skills, the records and
  the hooks
- `AGENTS.md`, `CLAUDE.md` and the `Justfile`
- `.github/workflows/nossaifd-prose.yml`, if the `prose` role is on and the
  `tasker` role is off
- `.github/workflows/check.yml`, if the `tasker` role is on
- `.agents/generated.txt` and `.agents/coverage-exclude.txt`
- `pyproject.toml` and `uv.lock`, `package.json` and `package-lock.json`, or
  `Cargo.toml` and `Cargo.lock`
- the config files `ruff.toml`, `.pre-commit-config.yaml` of the `tasker`
  role, `ty.toml`,
  `biome.json` and `tsconfig.json`
- `.gitignore` and `.worktreedisposable`

`.gitignore` already leaves out what a command makes again, such as `.venv/`,
`node_modules/`, `dist/`, `target/`, the caches and the logs of the installer.

A person who clones the project runs these commands. `just install` also
installs the Git hooks of the `tasker` role:

```bash
just setup
just install
```

## Keep it up to date

| Command | What it does |
|---|---|
| `just install` | Installs again what the record saves |
| `just install --update` | Gets the new commits of each package clone first |
| `just install --check` | Compares the record with the files, and changes nothing |
| `just install --dry-run` | Checks everything, and writes nothing |
| `just uninstall typecheck` | Takes out a tool: its packages, its files and its recipes |
| `just uninstall reporter` | Takes out a role |

The installer never overwrites or removes a file that you edited. It names
the file and installs the rest.

## What the stack does not give

Add these parts yourself:

- **CI.** No profile writes a GitHub Actions workflow. Without the `tasker`
  role, the `prose` role writes only one that checks the prose of a pull
  request. The `check.yml` of the `tasker` role runs no step of your languages
  until you add the step to the list `ci` of `.agents/stack.json`. Then add
  the setup that the step needs, as its comments say.
- **A locked setup.** `just setup` runs `uv sync`, not `uv sync --locked`.
- **`testpaths`.** pytest searches the whole project for tests. Set
  `testpaths = ["tests"]` under `[tool.pytest.ini_options]` to search one
  folder.
- **A Git hook for TypeScript.** Run `just verify` before each commit.
- **Async tests, a server, and Docker.** No tool adds pytest-asyncio, a
  `just serve` recipe or a `Dockerfile`.
- **A `biome.jsonc` with comments.** The installer leaves a file with
  comments alone, and it names the `extends` entry to add. Add it yourself.
