# jnterfaces

jnterfaces is a package of agent skills for the command interface of a project.
An agent skill is a folder with a `SKILL.md` file that Codex, Claude and other
agents read. The interface has three parts:

- **The Justfile.** Its recipes are the commands of the project.
- **The packets of `jagt`.** A packet is a block of text in a fixed format. A
  Just target emits one to hand a task to an agent, or to return a result.
- **The file stages of `cx`.** A stage is a recipe line that runs again only
  when its inputs, its outputs or its command change.

The documentation site is https://rahuldave.github.io/jnterfaces/. It is built from `docs/`.
`just docs` previews it, and `just docs-publish` publishes it to the branch `gh-pages`.

| Skill | Use it when |
|---|---|
| `just` | You write, change or run a Justfile, or map workflow concepts to recipes. |
| `jagt-protocol` | A Just target emits an `AGENT_TASK v1`, `AGENT_RESULT v1` or `AGENT_TASK_DRAFT v1` packet. |
| `cx` | A recipe has expensive stages that read files and write files. |

## Install into a repository

jnterfaces is the base of every project. Besides Git, a project needs three
programs only, `just`, `uv` and `jlog`. A profile can need more programs,
such as `rustup` for Rust, and it names them in the key `programs` of its
`profile.json`. One command installs everything else:

```bash
just install
```

`just install` comes from the file `.agents/install.just`, which jnterfaces
installs in the project. The Justfile of the project imports that file. To
start a new project, clone jnterfaces once and run its installer:

```bash
git clone https://github.com/rahuldave/jnterfaces ~/.local/share/skill-packages/jnterfaces
uv run ~/.local/share/skill-packages/jnterfaces/scripts/install_stack.py --profile python
```

[Start a new project](#start-a-new-project) gives each step, from an empty
folder to a project whose checks pass.
For a project that already has code or a Justfile,
[Bring an existing project in](https://rahuldave.github.io/jnterfaces/existing-project.html)
gives each step, and tells how to fix a clash of recipe names.

On the first install at a terminal, the installer asks which roles to
install. It saves the answers in `.agents/stack.json`. If you name only roles, the
installer asks nothing and turns on only the named roles. If you also name a
profile or a tool, it turns on the default roles too. Without a terminal, a
named profile takes the default roles.

A later `just install` installs the same set again, so `just install` also
updates a project. At a terminal, it asks only about a role that is newer than
the saved set.

### Roles

A role names a job, and a package does that job. So the package behind a role
can change and the name stays. The installer asks about each role. Press
Enter to keep the default:

| Role | What it installs | Package | Default |
|---|---|---|---|
| `cx` | the cx skill | jnterfaces | no |
| `jagt-protocol` | the jagt-protocol skill | jnterfaces | no |
| `docs` | the docs skill, a smalldocs site in `docs/`, `docs-check` in `just check`, and `docs-publish` with its CI workflow. [The docs role](#the-docs-role) tells more. | jnterfaces | no |
| `tasker` | the Gest `g*` skills, which track work | agent_gest_git_skills | yes |
| `prose` | the writing guide, its checker, the declaude skill, from the declauding skill by Oskar Austegard, and the [writing flow](https://rahuldave.github.io/nossaifd/): an instruction, two hooks and a GitHub workflow | nossaifd | yes |
| `reporter` | the `lanes` skill, which reports requests for human attention | sklanes | no |

The old names `gest`, `nossaifd` and `sklanes` still work for `tasker`, `prose`
and `reporter`.

**Variants.** A role can have variants, such as `just install tasker=gitwt`.
The record saves the variant, so a later `just install` keeps it:

| Variant of `tasker` | How the work goes |
|---|---|
| `git`, the default | on a branch in the main checkout |
| `gitwt` | each task in its own Git worktree, and the main checkout stays on `main` |
| `jj` | with jj. It is not ready yet, so the installer refuses it. |

The `g*` skills and the hooks of agent_gest_git_skills read the variant with
`just tasker-mode`. In a project that jnterfaces started, the role also adds
the worktree recipes `just worktree-new NAME`, `just worktree-sync`,
`just worktrees` and `just worktrees-prune`.

**The recipes of a role.** A role package can ship a recipe file,
`.agents/just/roles/<package>.just`. In a project that jnterfaces started, the
index imports it. For example, `prose` gives `just prose-check`, which checks
the Markdown of the project. For another project, the installer names the line to
add to its Justfile.

**The docs role.** `just install docs` gives the project a docs site, for
any language:

- **It writes once** `docs/_smalldocs.yml`, `docs/index.md` and the workflow
  `.github/workflows/docs-publish.yml`. It adds `docs/_site/` to
  `.gitignore`.
- **The theme is Atelier**, with a light mode and a dark mode. The role
  writes the theme into `docs/_extensions/atelier/`, and the config sets
  `theme: atelier`. It adds two lines to `.gitattributes`, so that
  `git diff --check` accepts the line ends of the theme files and of their
  copy in the docs skill.
- **`just docs-check`** is a step of `just check`. It runs
  `smalldocs check docs`, then the `api-check` of each language that has one.
  `just docs` previews the site, and `just docs-build` builds it.
- **`just docs-publish`** publishes the site to the `gh-pages` branch, only
  when `gh repo view` says that the repository is public. It skips a private
  repository, and the answer `docs-publish=no`. When `gh` fails, it skips on
  your machine, and it fails in CI, so a missing token shows.
- **The workflow** runs `just docs-publish` after each push to `main`, with
  `GH_TOKEN` set. Once, you choose the branch `gh-pages` under Pages in the
  settings of the repository.
- **`just uninstall docs`** takes out the line and the recipes. Your pages
  stay. The workflow goes too, unless you changed it.

**Starter files.** A role can write a starter file, such as the `AGENTS.md` of
`tasker`, once, when the project has none. The starter is yours from then on.
`just uninstall` takes out only the lines that the role added to it, and the
file stays.

### Start a new project

1. Install `git`, `just`, `uv` and `jlog`. jlog logs each line of the
   recipes that `just install` writes. To install the release binary of
   jlog, run this line. It puts jlog in `~/.local/bin`, so put that folder
   on PATH.

   ```bash
   curl -fsSL https://raw.githubusercontent.com/rahuldave/jlog/v0.3.0/scripts/install.sh | sh -s -- --tag v0.3.0
   ```

   To build jlog from source instead, clone
   https://github.com/rahuldave/jlog, run `git checkout v0.3.0` in the
   clone, then run `just install`.
2. Choose the folder of the package clones. The default is
   `~/.local/share/skill-packages`. To use another folder, export
   `SKILL_PACKAGES_DIR` in your shell profile first, for example `export
   SKILL_PACKAGES_DIR=~/Projects`. `just install` in a project finds the
   clones in that folder only. Without the variable, it uses the default
   folder, also when you ran the first install from another clone.
3. Clone jnterfaces into that folder:

   ```bash
   git clone https://github.com/rahuldave/jnterfaces "${SKILL_PACKAGES_DIR:-$HOME/.local/share/skill-packages}/jnterfaces"
   ```

4. Make the project, and run the installer from the clone:

   ```bash
   mkdir app && cd app && git init
   uv run "${SKILL_PACKAGES_DIR:-$HOME/.local/share/skill-packages}/jnterfaces/scripts/install_stack.py" --profile python
   ```

   Use `--profile typescript` for TypeScript, or `--profile rust` for Rust. The installer clones the other
   packages that it needs, writes the starter Justfile, and adds the tools.
5. Run `just verify`. A project with no code yet passes. A Rust project
   always has code, because `cargo init` writes `src/main.rs`. Its steps run
   with `--locked`, so first make `Cargo.lock` with `just setup`. The
   steps also check the docs, so add a `//!` doc line at the top of
   `src/main.rs`. On the first commit, `coverage-check` fails on each line
   that no test runs, and no test runs the `main` of `cargo init`. So write a
   test in `tests/` that runs the binary, from `env!("CARGO_BIN_EXE_NAME")`.
   For a library, run `cargo init --lib --vcs none` before the install.
   `cargo init` names the package after the folder, so a folder
   name that is not a valid crate name fails. Examples are a name that starts with
   a digit, a name with a `.` or a space, a Rust keyword such as `fn`, and `test`.
   For such a folder, run `cargo init --vcs none --name NAME` first.
6. Put the code in the place that the profile expects:

   | Profile | Code | Tests |
   |---|---|---|
   | `python` | Modules at the top of the project, such as `app.py`, or a package folder | `tests/test_NAME.py`. pytest finds the modules at the top, because the `test` tool sets `pythonpath`. |
   | `typescript` | `src/`, for example `src/app.ts` | `src/NAME.test.ts`, which imports the code as `./app.js`. |
   | `rust` | `src/`, as `cargo init` makes it | Unit tests in the module, and the files in `tests/` |

7. Run `just fmt` to format the code, then `just verify` again.
8. Commit the project, with `.agents/`, `.claude/`, `.codex/`, `AGENTS.md`,
   `CLAUDE.md`, `.github/workflows/`, the `Justfile` and the config files. `.gitignore` already leaves out what a
   command makes again, such as `.venv/` or `node_modules/`.

Later, `just install --update` gets the new commits of each package, and
`just install --check` compares the record with the files.

[Start a Python, TypeScript or Rust project](https://rahuldave.github.io/jnterfaces/new-project.html)
is the guide to a new project. It gives each tool, recipe, hook and config file, with examples.
[Bring an existing project in](https://rahuldave.github.io/jnterfaces/existing-project.html)
is the guide to a project that already has code or a Justfile.

### Tools and profiles

A tool is one unit of the setup of the project, such as `lint`. It brings its
packages, its config files and its recipes. A profile, such as `python`, is a
language. It sets the default roles and the tools. Each profile is a package
of its own, and `stack.json` names it:

| Profile | Package | Tools | Default tools | The command that adds a tool |
|---|---|---|---|---|
| `python` | [jnprofile-python](https://github.com/rahuldave/jnprofile-python) | `lint`, `integration`, `test`, `docstring`, `api`, `typecheck` | `lint`, `test`, `docstring`, `api` | `uv add --group ...` |
| `typescript` | [jnprofile-typescript](https://github.com/rahuldave/jnprofile-typescript) | `lint`, `typecheck`, `test`, `docstring` | all | npm, which uvx runs from `nodejs-wheel` |
| `rust` | [jnprofile-rust](https://github.com/rahuldave/jnprofile-rust) | `lint`, `typecheck`, `test`, `docstring`, `toml` | all but `toml` | none: each tool uses programs, such as `cargo-llvm-cov` and `taplo`, which the key `programs` names |

On the first install, the installer finds the profile from the files of the
project, such as `pyproject.toml`, or you name it with `--profile`. A project
with both `pyproject.toml` and `Cargo.toml`, such as a Python package with a
Rust extension, gets both profiles. After that,
the installer uses the profiles of the saved record, also when the record has
no profile.

**The recipes follow the tools.** The profile package has one recipe file for
each tool. `just install` writes a module for each profile in `.agents/just`,
which imports the recipe files of the installed tools only. It also writes
`.agents/just/index.just`, which `.agents/install.just` imports.

**Commit the files in `.agents/just`.** `.agents/install.just` imports the
index with `import?`, so just gives no error when the index is missing. A
clone without the index does not have the recipes of the profiles and roles.
So `just install` names each file there that git does not track, and
`just install --check` fails when one of them is gone.

In a project that jnterfaces started, with its starter Justfile:

- **`just python lint`** runs the `lint` of one language.
- **`just lint`** runs the `lint` of each language of the project.
- **`just verify`** runs every installed check of each language, then `git
  diff --check`.
- **`just check`** runs the steps of every project, from `fmt-check` to
  `diff-check`, and ends with `jlog pass`. A step that no profile, role or
  recipe of the Justfile has is a no-op step that logs the skip. So in a new
  project with no language, `just check` passes.
- **`just ci`** is what CI checks. It runs `ci-gate`, then the steps in the
  list `ci` of `.agents/stack.json`. A role with a commit gate gives
  `ci-gate`, which checks the trailer of the gate on each commit. `ci-gate` is
  a step of `just check` too, and each step in the list must be a step of
  `just check`. So CI checks only a part of `just check`. The list is empty
  at first.

**The steps of just check.** `just check` runs these steps, in this order:

```text
tools-check release-check ci-gate fmt-check lint typecheck test integration coverage-check docstring-check docs-check prose-check diff-check
```

The `prose` role gives `prose-check`, the tasker role (agent_gest_git_skills) gives `release-check` and `ci-gate`, and the installer gives `tools-check` and `diff-check`. An older tasker role whose `ci-gate` does not end with `jlog step ci-gate` stays out of `just check`, and the installer says to update it. `tools-check` checks that each program of `.agents/stack.json` is there, at its version floor.
Each profile gives the steps of its installed tools:

| Profile | Steps | `test-paths` |
|---|---|---|
| `python` | `lint`, `test`, `typecheck` | yes |
| `typescript` | `fmt-check`, `lint`, `typecheck`, `test`, `coverage-check`, `docstring-check` | yes |
| `rust` | `fmt-check`, `lint`, `typecheck`, `test`, `coverage-check`, `docstring-check` | yes |

`coverage-check` fails on each changed line of code that no test runs, and
`docstring-check` fails on each item with no doc comment. `test-paths` is a
recipe, not a step. It runs only the tests of some test files. The
`test-parent` recipe of the `tasker` role runs it with the added and changed
test files, on the parent commit.

A recipe of the Justfile with the name of a combined recipe, such as `lint`,
takes the place of the combined recipe. The index leaves that name out. A project with a
Justfile of its own gets the same index. The installer adds a block of two
lines to that Justfile. They are `import? '.agents/install.just'`, and the
line of the run ID, `export JLOG_RUN := env("JLOG_RUN", uuid())`.

`.agents/stack.json` records the hash of each file that `just install`
writes in `.agents/just`, and the installer changes only a file whose hash is
the recorded one. With more than one language, a
combined recipe gives the same arguments to each language.

**The source files.** A profile can name the globs of its source files with
the key `code` of `profile.json`, for example `**/*.rs`. `just install` copies
them into the key `code` of `.agents/stack.json`, for each profile. The commit
gate of agent_gest_git_skills reads them as `@code`.

**The other files.** A shared file, such as `.gitignore`, is a file that you
and the installer both add to. A profile, a tool or a role
adds a part to each shared file that it needs, and takes out only its part. A part is one
of three kinds:

- a block of lines, as in `.gitignore`
- an include, as the `extend` of `ruff.toml`, which reads an own file, a file
  that the installer writes whole under `.agents/`
- units, such as the items of `.pre-commit-config.yaml`

`scripts/formats.json` lists the shared files and the format of each. A
package names its parts with the key `parts`. A profile and its tools have it
in `profile.json`, and a role has it in `install_extras.parts` of
`skill-package.json`. A role names a starter file, written once, with
`install_extras.starter`.

The key `parts` in `.agents/stack.json` records each part. `just uninstall`
reads it to take the parts out again.
[Shared files and the record](https://rahuldave.github.io/jnterfaces/stack-guide.html#shared-files-and-the-record),
in `docs/stack-guide.md`, tells how. If the project has no Justfile,
the installer writes a starter Justfile. Otherwise it names the line to add to
your Justfile.

If the project has no `pyproject.toml`, `package.json` or `Cargo.toml`, the
installer makes one with `uv init --bare`, `npm init --yes` or
`cargo init --vcs none`. For Python, the profile also
adds `default-groups = "all"` to `pyproject.toml` as a part, so that `uv run`
has the tools.

If a tool fails, a later `just install` tries it again. When the last package
of a dependency group goes, the installer takes out the empty group.

### Names and options

- **`just install lint tasker`** turns on a tool and a role.
- **`just install tasker=gitwt`** turns on a role with a variant.
- **`just uninstall python`** takes out a profile with its tools, its recipes
  and its package. The project keeps `pyproject.toml`.
- **`just uninstall reporter lint`** turns them off. It removes the files of
  the role and the packages that `just install` added for the tool. It keeps
  each file that you edited, and each package that the project had before.
  If you remove a package by hand and add it back with no install between,
  it still counts as added. To keep it, take it out of
  `owned_packages` in `.agents/stack.json`.
- **`--profile python,typescript`** sets the profiles. A saved profile that
  the list leaves out is turned off.
- **`--defaults`** takes the defaults, and **`--all`** takes every role.
- **`--answer KEY=VALUE`** answers a setup question and saves the answer.
  The next section lists the questions.
- **`--ask`** asks again, even when the project has saved choices.
- **`--update`** first fast-forwards each package clone that the run uses.
  It does not change a clone with uncommitted changes, or a clone on another
  branch. [The stack guide](https://rahuldave.github.io/jnterfaces/stack-guide.html) gives the rules.
- **`--clone NAME=DIR`** uses one clone in another folder.
- **`--clean`** installs the last commit of each clone, without uncommitted
  changes. **`--dry-run`** checks everything and writes nothing in the
  project.
- **`--check`** compares the record in `.agents/stack.json` with the shared
  files, and changes nothing.

### Setup questions

Some choices of a project belong to you, not to the stack. The installer asks
each question that has no saved answer:

| Key | Question | Answers | Default |
|---|---|---|---|
| `issues` | Which Gest tasks become GitHub issues? | `top`, `development`, `none` | `top` |
| `docs-publish` | Publish the docs site of a public repository after a merge to `main`? Only while the `docs` role is on. | `yes`, `no` | `yes` |
| `integration` | Run the integration tests in `just check`, or only in `just verify` before a PR? | `check`, `verify` | `check` |
| `tasker` | One branch in the main checkout, or a worktree for each task? Only while the `tasker` role is on. | `git`, `gitwt` | `git` |
| `review-model` | Which model does the adversarial review? | `strongest`, or a model name | `strongest` |

- **At a terminal,** the installer asks each open question. Type the answer,
  its number, or nothing for the default.
- **With no terminal,** for example under an agent, the installer prints a
  `SETUP_QUESTIONS v1` packet with the open questions, and stops. It writes
  nothing. The agent asks you each question, then runs `just install` again
  with `--answer KEY=VALUE` for each answer.
- **With `--defaults`, or in CI,** the installer takes the default of each
  open question and asks nothing. CI counts when the variable `CI` is set and
  is not `0` or `false`.
- **A run that only removes,** such as `just uninstall reporter`, asks
  nothing. A dry run takes the defaults and names the open questions.
- **The answers** are in the key `answers` of `.agents/stack.json`. A
  question with an answer is not asked again. A later version of the stack
  can add questions, and then only the new ones are asked.
- **The answer `integration=verify`** takes `integration` out of the steps of
  `just check`. The recipe stays, and `just verify` runs it once. The
  `verify` of the profile runs it when its tools list `integration`. Else
  the root `verify` has a line of its own for it.
- **The answer of `tasker`** is the variant of the role, as in
  `just install tasker=gitwt`.

### Where the packages are

Each package is a Git clone in one folder. The default folder is
`~/.local/share/skill-packages`. Set `SKILL_PACKAGES_DIR` to use another
folder, for example the folder of your own clones. If a clone is missing, the
installer clones it from GitHub. `JNTERFACES_DIR` names one jnterfaces clone.

Only jnterfaces has an installer. The other packages list their skills, and
their other files, in `skill-package.json`. The installer copies them from the
clone, and it writes one record for each package, such as
`.agents/nossaifd-install.json`. A record holds the source revision and a hash
of each file.

The installer never overwrites or removes a file that you edited. It names
the file and installs the rest. For each installed skill, it also makes the
link `.claude/skills/NAME` for Claude Code. Before it writes, it checks every
package. If one check fails, it writes nothing.

A record from an older installer lists no skills and no hooks. The installer
then reads the files of the old source revision in the clone. So it removes
the old copies of skills and hooks that it shipped, and it keeps your edits.

`skill-package.json` names the other files of a package in `install_extras`:

- **`files`** are files or folders outside `.agents/skills`, such as hook
  scripts. The installer manages them like the files of a skill.
- **`merge_hooks`** are JSON settings files. The installer adds their `hooks`
  entries to the file of the same name in the project, and it takes them out
  again on removal.
- **`seed`** maps a file of the project to a starter file. The installer
  writes it only when the project does not have it.

The list of packages, roles, aliases and profiles is in `stack.json`.
[The stack guide](https://rahuldave.github.io/jnterfaces/stack-guide.html) builds a project in layers, from a
project with no code to a project with lint and tests. It says what works now
and what does not.
[The files of the stack](https://rahuldave.github.io/jnterfaces/stack-files.html)
gives each file that the installer reads or writes, each of its keys, and how
the files work together.

### The environment lab

A recipe runs with `sh`, not with your shell. A git hook, a GUI program and
CI do not read `.zshrc`. So a recipe must not need a setting from it.

- **`just env-lab`** runs `just check` with a clean environment, which keeps
  only `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `TMPDIR`, `LANG`, `LC_ALL`
  and `TERM`. `just env-lab test lint` runs those recipes. A recipe with
  arguments goes in quotes, as in `just env-lab "test -k name"`.
- **PATH stays.** The lab keeps the `PATH` of your shell, so a `PATH` that
  `.zshrc` sets is not tested. A GUI program may have a shorter `PATH`.
- **A recipe that fails there** runs again in your shell. When it passes in
  your shell, the lab names it. It also lists the variables of your shell
  that the recipe may need. They are the variables that the recipe, the
  recipes that it depends on, and the assignments of its module read. That list is a hint. It can
  miss a variable behind a dependency in another module (`m::x`), an alias,
  or an assignment without `export`. It never prints a value. It says to put the setting in the
  Justfile with `set export`, or in an ignored `.env` file with
  `set dotenv-load`.
- **The lab runs a failed recipe twice,** so use it on recipes that can run
  again.

### When the Justfile does not parse

`just install` is a recipe, so it cannot run when just cannot read the
Justfile. This occurs, for example, when you remove a recipe that a recipe of
the index uses, and just says ``Recipe `check` has unknown dependency `lint` ``.
Run the installer itself in the folder of the project, the folder with
`.agents/install.just`. It writes the index again for the recipes that the
Justfile has now. In another folder, the command stops and writes nothing:

```bash
jn_dir="${JNTERFACES_DIR:-${SKILL_PACKAGES_DIR:-$HOME/.local/share/skill-packages}/jnterfaces}"
if [ ! -f .agents/install.just ]; then
    echo "Run this in the folder of the project, which has .agents/install.just" >&2; false
elif [ -f "$jn_dir/scripts/install_stack.py" ] || git clone --quiet https://github.com/rahuldave/jnterfaces "$jn_dir"; then
    uv run --script "$jn_dir/scripts/install_stack.py" --target .
fi
```

Then run `just install --check`.
[When a later edit breaks the Justfile](https://rahuldave.github.io/jnterfaces/stack-guide.html#how-the-recipes-arrive),
in `docs/stack-guide.md`, gives each cause.

### copier

`.agents/install.just` defines the variable `jn_copier`, which runs copier 9
through `uvx`. copier makes files from a template, and `copier update` brings
in a later version of the template. Any recipe of the project can use it, for
example `{{jn_copier}} copy TEMPLATE FOLDER`.

### npx

You can also install the skills with npx:

```bash
npx skills add rahuldave/jnterfaces -a codex --skill just -y
```

The npx route installs no tools and records no source revision in `.agents/`.

The skills use these executables:

- **`just`** runs the recipes, and **`uv`** runs the installer and the tools.
- **`jagt`** renders and checks packets, from https://github.com/rahuldave/jagt.
  The skill needs jagt 0.3.0 or later, which gives each packet a nonce.
  Install the release binary with
  `curl -fsSL https://raw.githubusercontent.com/rahuldave/jagt/v0.6.4/scripts/install.sh | sh -s -- --tag v0.6.4`.
  To build it from source instead, run
  `cargo install --locked --git https://github.com/rahuldave/jagt --tag v0.6.4 jagt`.
- **`cx`** runs incremental stages, from https://github.com/rahuldave/cx.
  Install the release binary with
  `curl -fsSL https://raw.githubusercontent.com/rahuldave/cx/v0.2.3/scripts/install.sh | sh -s -- --tag v0.2.3`.
  To build it from source instead, run
  `cargo install --locked --git https://github.com/rahuldave/cx --tag v0.2.3 cx`.

## How the packages stay apart

Each package works alone, and each skill works without the optional skills:

- **`just` is the only required skill.** `cx` and `jagt-protocol` are
  optional.
- **Each fact has one source.** The packet rules are in jagt-protocol, and the
  cx rules are in cx. The just skill points to them.
- **A link to a skill that may be missing has a fallback** in the same
  paragraph, for example "If the cx skill is not installed, ...".
  `scripts/check_skill_links.py` checks this rule, and `just lint` runs it.
  The other packages run it in their CI too.

## Work on this repository

| Command | What it does |
|---|---|
| `just lint` | Checks the syntax of the scripts, the Python and the JSON, and the links between skills. |
| `just test` | Runs the tests of the installers, the link checker and the skills, in parallel with pytest. `just test tests/test_skills.py` runs one module. |
| `just install-into <target> [names]` | Runs the installer on another project. |
| `just check` | Runs `just tools-check`, `just release-check`, `just lint`, `just package-lint` and `just test`. Every commit must pass it. CI runs no recipe outside it. `just package-lint` clones from GitHub, so it needs the network. |
| `just release-check` | Prints the next tag. It needs no network. This repository has no manifest with a version, so the tag comes from the commits since the last tag. |
| `just release` | Tags HEAD with the next release `vX.Y.Z`, pushes the tag and checks it. `just release --dry-run` prints the tag and changes nothing. |
| `just package-lint` | Checks `skill-package.json` with skill-package-maker. |
| `just prose-check <check.py>` | Checks the Markdown with the nossaifd writing checker. |

On each push to `main`, the job `release` of `.github/workflows/verify.yml`
runs `just release` after the job `verify` passes. Only that job can write to
the repository, to push the tag. So nobody tags by hand. The script is
`scripts/release_tag.py`, a copy of the script of the gpa skill of
agent_gest_git_skills. The section "Release tags" of its
`docs/integration_delivery_workflow.md` gives the rules.

The test of the jagt demo runs only with jagt 0.3.0 or later. If the `jagt`
on your PATH is older, set `JAGT_BIN` to a newer binary.

The skill `jagt-protocol` came from the jagt repository, and the `just` and `cx`
references came from agent_gest_git_skills. This repository is now the source
of all three, and of the one installer.

## License

MIT. See [LICENSE](https://github.com/rahuldave/jnterfaces/blob/main/LICENSE).
