---
title: Bring an existing project in
---

This page tells how to add the stack to a project that already has code, a `Justfile` or an
`AGENTS.md`. Each step was run on 2026-11-19 with a new package cache. For a project that starts in an
empty folder, read [Start a Python, TypeScript or Rust project](new-project.md).

The machine needs the same programs as for a new project. [What the machine
needs](new-project.md#what-the-machine-needs) lists them.

## Terms

| Term | Meaning |
|---|---|
| recipe | A command in a Justfile. `just NAME` runs the recipe NAME. |
| shared recipe | A recipe that the installer writes, in `.agents/install.just` or in `.agents/just/`. |
| the block | The lines that the installer adds at the top of your `Justfile`. |
| step | One check of `just check`, such as `lint` or `test`. |

## Step 1: clone jnterfaces once

The project has no `just install` yet, so the first install runs from a clone of jnterfaces. Clone it
into the folder of package clones:

```bash
git clone https://github.com/rahuldave/jnterfaces "${SKILL_PACKAGES_DIR:-$HOME/.local/share/skill-packages}/jnterfaces"
```

The installer clones each other package that it needs into the same folder.

## Step 2: run the installer

In the folder of the project, run the installer from the clone:

```bash
uv run "${SKILL_PACKAGES_DIR:-$HOME/.local/share/skill-packages}/jnterfaces/scripts/install_stack.py" --profile python --target .
```

Use `--profile typescript` or `--profile rust` for another language.

At a terminal, the installer first asks which roles to install, then it asks the setup questions. With
no terminal, for example when an agent runs it, the installer takes the default roles, `tasker` and
`prose`. Then it prints the setup questions, writes nothing and stops:

| Key | Question | Answers |
|---|---|---|
| `issues` | Which Gest tasks become GitHub issues? | `top` (the default), `development` or `none` |
| `integration` | Run the integration tests in `just check`, or only in `just verify`? | `check` (the default) or `verify` |
| `tasker` | One branch in the main checkout, or a worktree for each task? Only with the `tasker` role. | `git` (the default) or `gitwt` |
| `review-model` | Which model does the adversarial review? | `strongest` (the default), or any model name |
| `docs-publish` | Publish the docs site of a public repository after a merge to main? Only with the `docs` role. | `yes` (the default) or `no` |

The line `then:` of the questions gives the arguments to add, one `--answer KEY=VALUE` for each key. Add
them to the `uv run` command, for example:

```bash
uv run "${SKILL_PACKAGES_DIR:-$HOME/.local/share/skill-packages}/jnterfaces/scripts/install_stack.py" --profile python --target . \
  --answer issues=none --answer integration=check --answer tasker=git --answer review-model=strongest
```

`--defaults` takes the default roles and the default answer of each question.

## Step 3: read the block in your Justfile

Your `Justfile` stays yours. The installer adds one block at its top:

```just
# begin lines of jnterfaces, the installer, from jnterfaces at 361dcc3.
# Last written to Justfile by `install_stack.py --profile python --target .`. The record is parts."Justfile" in .agents/stack.json.
import? '.agents/install.just'
export JLOG_RUN := env("JLOG_RUN", uuid())
# end lines of jnterfaces. They stay while the project keeps .agents/stack.json.
```

The `import?` line reads the shared recipes. The `JLOG_RUN` line gives one run ID to each `just` call
inside one `just check`, so that the run log of jlog keeps them together. The commit and the command in
the comments change with each run.

The installer leaves out a line that your `Justfile` already has. It adds no block to a `Justfile` that
starts with a `#!` line or that is a symlink. [How the stack fits together](stack-guide.md) lists each
case.

## Step 4: fix a clash of recipe names

Some names of the shared recipes clash with a recipe of yours. Others let your recipe take their place.

**The names that clash.** `.agents/install.just` has the recipes `install`, `uninstall` and `env-lab`,
and the variable `jn_copier`. When your `Justfile` has one of these names, just stops with this error:

```text
error: Recipe `install` first defined on line 7 is redefined on line 16
```

Then the installer takes its block out again, and it tells you to rename your recipe. Your own `install`
recipe stays, so `just install` still runs your old recipe and not the installer.

To fix it, rename your recipe, for example to `setup`. Then run the `uv run` command of step 2 again.
The block stays this time. The name of a profile, such as `python`, also clashes. So does a recipe of a role
that is on, such as `review`, `worktree-new` or `ci-gate` of the `tasker` role, or `prose-check` of the
`prose` role. For these, the installer stops, writes nothing in `.agents/just`, and names the clash.

**The names that you can keep.** `.agents/just/index.just` leaves out each name that your `Justfile`
has, so your recipe takes its place. These names are the recipes of the profiles, such as `setup`,
`fmt`, `lint` and `test`, the steps of `just check` that no role gives, and `verify`, `check` and `ci`. For example, your
`setup` then takes the place of the `setup` of the profile, which runs `uv sync`.

A recipe of yours with the name of a step must end with the line `jlog step NAME`, for example
`jlog step test`. Without that line, `jlog pass` fails at the end of `just check`. A `check` of yours must end with the
line `jlog pass`.

## Step 5: map your names in AGENTS.md

When the project already has an `AGENTS.md`, the installer adds only its own blocks to it. Keep the
names of your recipes. Write the mapping from each concept to your recipe in `AGENTS.md`, for example
"full local check: `just check`" or "install the packages: `just setup`". The `just` skill tells an agent
to use the names that `AGENTS.md` maps.

## Step 6: check and commit

The first commit goes through the commit gate of the `tasker` role. The gate needs a record of each
agentic target that the change needs, and a pass of `just check` for the tree that you commit. It checks
the whole worktree, with the files that are not staged too. Do the steps in this order:

1. Stage the files, and run `just check`. Fix each step that fails.
2. Run `just review`, and each other agentic target that the change needs. They and their fixes can
   change the files.
3. Stage all the changes, and run `just check` again.
4. Commit.

```bash
git add --all
just check
```

When the gate stops, its message names what is missing.
[What to commit](new-project.md#what-to-commit) lists the files.
