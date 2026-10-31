---
title: Start a Python or TypeScript project
---

This page sets up a new project with every part that the stack gives. The
parts are the agent skills, the task tracker, the writing checker, format,
lint, tests, coverage, types, a Git hook and one full check. Each step and each
example was run on 2026-10-31 in an empty folder.

[How the stack fits together](stack-guide.md) tells how an install works.
[The files of the stack](stack-files.md) tells what each file holds.

## Terms

| Term | Meaning |
|---|---|
| recipe | A command in a Justfile. `just NAME` runs the recipe NAME. |
| role | A job of the agent skills, for example `tasker`. One package does the job. |
| profile | A language setup: `python` or `typescript`. |
| tool | One part of a profile, such as `lint`. It adds packages, config files and recipes. |
| clone | The local Git clone of a package. The installer copies the skills from it. |
| the record | `.agents/stack.json` in the project. It saves the profiles, roles and tools. |

## What the machine needs

| Program | For | How to get it |
|---|---|---|
| `git` | Each project and each package clone | The system package manager |
| `just` | Every recipe | https://just.systems |
| `uv` | The installer, Python, and Node for TypeScript | https://docs.astral.sh/uv |
| `gest` | The skills of the `tasker` role | Install it on its own. It does not come through `uv`. The installer warns when it is missing. |

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

Use `--profile typescript` for TypeScript, or `--profile python,typescript`
for both. At a terminal, the installer asks about each role. Add
`--defaults` to take the defaults with no questions:

| Role | Default | What it gives |
|---|---|---|
| `tasker` | yes | The `g*` skills, which track work with Gest, the agent hooks, a starter `AGENTS.md` and the worktree recipes |
| `prose` | yes | The nossaifd writing guide, its checker, and `just prose` |
| `cx` | no | The `cx` skill: run the file stages of a recipe only when they are out of date. It needs the `cx` program. |
| `jagt-protocol` | no | The `jagt-protocol` skill: the packets of agentic recipes. It needs the `jagt` program. |
| `reporter` | no | The sklanes skill: report requests for human attention to a Lanes inbox |

The `just` skill of jnterfaces is always installed. Name a role to add it
later, for example `just install cx`.

The installer then:

1. makes `pyproject.toml` with `uv init --bare`, or `package.json` with `npm
   init --yes`, when the project has none;
2. adds the default tools of the profile, with `uv add --group` or `npm
   install --save-dev`;
3. writes the starter `Justfile` and the starter `AGENTS.md`. Both are yours
   from then on;
4. saves the choices in `.agents/stack.json`.

Run `just` alone to list the recipes. Run `just verify`. A project with no
code yet passes.

## A Python project

### The tools

| Tool | Default | Packages | Files | Recipes |
|---|---|---|---|---|
| `lint` | yes | ruff, pre-commit, in the group `lint` | `ruff.toml`, `.pre-commit-config.yaml` | `fmt`, `lint`, `hook` |
| `test` | yes | pytest, pytest-cov, in the group `test` | `[tool.pytest.ini_options]` in `pyproject.toml` | `test`, `cov` |
| `typecheck` | no | ty, in the group `lint` | `ty.toml` | `typecheck` |

Add the type check:

```bash
just install typecheck
```

`pyproject.toml` gets `default-groups = "all"` under `[tool.uv]`, so that
`uv run` has every tool. The project uses the Python that `uv` picks; `uv
init` writes it as `requires-python`.

### The recipes

| Recipe | What it runs | Notes |
|---|---|---|
| `just setup` | `uv sync` | Installs Python and each package into `.venv`. Run it in a new clone. |
| `just fmt` | `ruff format` | Changes the files. `just fmt --check` only checks. |
| `just lint` | `ruff check` | `just lint --fix` fixes what it can. |
| `just hook` | `pre-commit install` | Run it once in each clone. |
| `just test` | `pytest` | Extra words go to pytest: `just test tests/test_app.py`. With no tests yet, it passes. A selection that runs no test fails. |
| `just cov` | `pytest --cov --cov-report=term-missing` | Shows the lines that no test ran. |
| `just typecheck` | `ty check` | |
| `just verify` | `fmt --check`, `lint`, `test`, `typecheck`, then `git diff --check` | Runs the checks of the installed tools only. It stops at the first failure. |

Each recipe also has a form for one language, such as `just python lint`.
That form matters in a project with two profiles.

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

### The Git hook

`just hook` writes `.git/hooks/pre-commit`. Before each commit, it runs the
checks of `.pre-commit-config.yaml` on the staged files:

- trailing whitespace and the end of each file;
- the syntax of YAML and JSON files;
- files larger than 1000 KB;
- merge conflict markers and private keys;
- `ruff check` and `ruff format`.

A check that changes a file stops the commit. Add the change and commit again.
`uv run pre-commit run --all-files` runs the checks on every file.

### Change a setting

- **ruff.** `ruff.toml` reads the settings of the profile with `extend`. Add
  your settings below that line. They win.
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
| `test` | no | `typescript@7`, `@types/node@24` | `tsconfig.json` | `build`, `test` |

Add the tests:

```bash
just install test
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
| `just test` | `tsc`, then `node --test 'dist/**/*.test.js'` | Extra words go to `node --test`. With no `.ts` file in `src` yet, it passes. |
| `just verify` | `lint`, `typecheck`, `test`, then `git diff --check` | Runs the checks of the installed tools only. |

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
`extends`. Add your keys beside `extends`. They win. For example, the profile
sets a line width of 120 for Biome.

## The task tracker

The `tasker` role works on a branch in the main checkout. To work on each task
in its own Git worktree, and keep the main checkout on `main`:

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

`just prose` checks each Markdown file that Git tracks against the writing
guide. It leaves out `AGENTS.md`, `CLAUDE.md` and the folders of the
installer. Name pages to check only them, and add `-v` to see the soft notes:

```bash
just prose -v README.md
```

## What to commit

Commit these files:

- `.agents/`, `.claude/` and `.codex/`, which hold the skills, the records and
  the hooks;
- `AGENTS.md` and the `Justfile`;
- `pyproject.toml` and `uv.lock`, or `package.json` and `package-lock.json`;
- the config files: `ruff.toml`, `.pre-commit-config.yaml`, `ty.toml`,
  `biome.json`, `tsconfig.json`;
- `.gitignore` and `.worktreedisposable`.

`.gitignore` already leaves out what a command makes again, such as `.venv/`,
`node_modules/`, `dist/`, the caches and the logs of the installer.

A person who clones the project runs:

```bash
just setup
just hook
```

`just hook` is for Python only.

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

- **CI.** No profile writes a GitHub Actions workflow. A workflow can run
  `just setup` and `just verify`.
- **A locked setup.** `just setup` runs `uv sync`, not `uv sync --locked`.
- **`testpaths`.** pytest searches the whole project for tests. Set
  `testpaths = ["tests"]` under `[tool.pytest.ini_options]` to search one
  folder.
- **A Git hook for TypeScript.** Run `just verify` before each commit.
- **Coverage for TypeScript.** There is no `just cov`.
- **Async tests, a server, and Docker.** No tool adds pytest-asyncio, a
  `just serve` recipe or a `Dockerfile`.
- **A `biome.jsonc` with comments.** The installer leaves a file with
  comments alone, and it names the `extends` entry to add. Add it yourself.
