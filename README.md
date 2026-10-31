# jnterfaces

jnterfaces is a package of agent skills for the command interface of a project.
The interface is the Justfile, the packets that agentic Just targets emit, and
the incremental file stages of `cx`. An agent skill is a folder with a
`SKILL.md` file that Codex, Claude and other agents read.

The documentation site is https://rahuldave.github.io/jnterfaces/. It is built from `docs/`.
`just docs` previews it, and `just docs-publish` publishes it to the branch `gh-pages`.

| Skill | Use it when |
|---|---|
| `just` | You write, change or run a Justfile, or map workflow concepts to recipes. |
| `jagt-protocol` | A Just target emits an `AGENT_TASK v1`, `AGENT_RESULT v1` or `AGENT_TASK_DRAFT v1` packet. |
| `cx` | A recipe has expensive stages that read files and write files. |

## Install into a repository

jnterfaces is the base of every project. Besides Git, a project needs two
programs only, `just` and `uv`. One command installs everything else:

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

On a terminal, the installer asks which roles to install, and it saves the
answers in `.agents/stack.json`. A named role asks nothing, and named roles
alone turn on only those roles. Without a terminal, a named profile takes the
default roles. A later `just install` installs the same set again, with
no questions. So `just install` also updates a project.

### Roles

A role names a job, and a package does that job. So the package behind a role
can change and the name stays. The installer asks about each role. Press
Enter to keep the default:

| Role | What it installs | Package | Default |
|---|---|---|---|
| `cx` | the cx skill | jnterfaces | no |
| `jagt-protocol` | the jagt-protocol skill | jnterfaces | no |
| `tasker` | the Gest `g*` skills, which track work | agent_gest_git_skills | yes |
| `prose` | the writing guide and its checker | nossaifd | yes |
| `reporter` | the `lanes` skill, which reports requests for human attention | sklanes | no |

The old names `gest`, `nossaifd` and `sklanes` still work.

**Variants.** A role can have variants, such as `just install tasker=gitwt`.
The record saves the variant, so a later `just install` keeps it:

| Variant of `tasker` | How the work goes |
|---|---|
| `git`, the default | on a branch in the main checkout |
| `gitwt` | each task in its own Git worktree, and the main checkout stays on `main` |
| `jj` | with jj; not ready yet, so the installer refuses it |

The `g*` skills and the hooks of agent_gest_git_skills read the variant with
`just tasker-mode`. In a project that jnterfaces started, the role also adds
the worktree recipes `just worktree-new NAME`, `just worktree-sync`,
`just worktrees` and `just worktrees-prune`.

**The recipes of a role.** A role package can ship a recipe file,
`.agents/just/roles/<package>.just`. In a project that jnterfaces started, the
index imports it. For example, `prose` gives `just prose`, which checks the
Markdown of the project. Another project gets the line to add to its Justfile.

**Starter files.** A role can write a starter file, such as the `AGENTS.md` of
`tasker`. The record keeps its hash. `just uninstall` removes the file when
nobody changed it, and keeps it, with a note, when you edited it.

### Tools and profiles

A tool is one unit of the setup of the project, such as `lint`. It brings its
packages, its config files and its recipes. A profile, such as `python`, is a
language. It sets the default roles and the tools. Each profile is a package
of its own, and `stack.json` names it:

| Profile | Package | Tools | Default tools | The command that adds a tool |
|---|---|---|---|---|
| `python` | [jnprofile-python](https://github.com/rahuldave/jnprofile-python) | `lint`, `test`, `typecheck` | `lint`, `test` | `uv add --group ...` |
| `typescript` | [jnprofile-typescript](https://github.com/rahuldave/jnprofile-typescript) | `lint`, `test`, `typecheck` | `lint`, `typecheck` | npm, which uvx runs from `nodejs-wheel` |

On the first install, the installer finds the profile from the files of the
project, such as `pyproject.toml`, or you name it with `--profile`. After that,
the saved record keeps the profiles, also when it has none.

**The recipes follow the tools.** The profile package has one recipe file for
each tool. `just install` writes a module for each profile in `.agents/just`,
which imports the recipe files of the installed tools only. It also writes
`.agents/just/index.just`, which `.agents/install.just` imports. In a project
that jnterfaces started, with its starter Justfile:

- **`just python lint`** runs the `lint` of one language.
- **`just lint`** runs the `lint` of each language of the project.
- **`just verify`** runs every installed check of each language, then `git
  diff --check`.

The starter Justfile has `set allow-duplicate-recipes`. So a recipe of the
same name in the Justfile, such as `lint`, takes the place of the combined
one. A project with a Justfile of its own gets the modules only, such as
`just python lint`. `.agents/stack.json` records the hash of each
file that `just install` writes in `.agents/just`, and the installer changes
only a file whose hash is the recorded one. With more than one language, a
combined recipe gives the same arguments to each language.

**The other files.** A profile, a tool or a role adds a part to each shared
file that it needs, and takes out only its part. A part is one of three kinds:

- a block of lines, as in `.gitignore`;
- an include that reads an own file, as the `extend` of `ruff.toml`;
- units, such as the items of `.pre-commit-config.yaml`.

`scripts/formats.json` lists the shared files and the format of each. A
package names its parts with the key `parts`. A profile and its tools have it
in `profile.json`, and a role has it in `install_extras.parts` of
`skill-package.json`. A role names a starter file, written once, with
`install_extras.starter`.

The key `parts` in `.agents/stack.json` records each part. `just uninstall`
reads it to take the parts out again. The "Shared files and the record"
section of `docs/stack-guide.md` tells how. If the project has no Justfile,
the installer writes a starter Justfile. Otherwise it names the line to add to
your Justfile.

If the project has no `pyproject.toml` or `package.json`, the installer makes
one with `uv init --bare` or `npm init --yes`. For Python, the profile also
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
  You may remove a package by hand and add it back with no install between.
  Then it still counts as added. To keep it, take it out of
  `owned_packages` in `.agents/stack.json`.
- **`--profile python,typescript`** sets the profiles. A saved profile that
  the list leaves out is turned off.
- **`--defaults`** takes the defaults, and **`--all`** takes every role.
- **`--ask`** asks again, even when the project has saved choices.
- **`--update`** runs `git pull --ff-only` in each clone first.
- **`--clone NAME=DIR`** uses one clone in another folder.
- **`--clean`** installs the last commit of each clone, without uncommitted
  changes. **`--dry-run`** checks everything and writes nothing in the
  project.
- **`--check`** compares the record in `.agents/stack.json` with the shared
  files, and changes nothing.

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
link `.claude/skills/NAME` for Claude Code. First it
checks every package. If one check fails, it writes nothing.

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
  The draft commands need a build that has `jagt draft lint`.
- **`cx`** runs incremental stages, from https://github.com/rahuldave/cx.

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
| `just test` | Runs the tests of the installers, the link checker and the skills. |
| `just install-into <target> [names]` | Runs the installer on another project. |
| `just check` | Runs `just lint` and `just test`. Every commit must pass it. |
| `just package-lint` | Checks `skill-package.json` with skill-package-maker. |
| `just prose <check.py>` | Checks the Markdown with the nossaifd writing checker. |

The test of the jagt demo runs only with a `jagt` that has `jagt draft lint`. If
the `jagt` on your PATH is older, set `JAGT_BIN` to a newer binary.

The skill `jagt-protocol` came from the jagt repository, and the `just` and `cx`
references came from agent_gest_git_skills. This repository is now the source
of all three, and of the one installer.

## License

MIT. See [LICENSE](https://github.com/rahuldave/jnterfaces/blob/main/LICENSE).
