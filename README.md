# jnterfaces

jnterfaces is a package of agent skills for the command interface of a project.
The interface is the Justfile, the packets that agentic Just targets emit, and
the incremental file stages of `cx`. An agent skill is a folder with a
`SKILL.md` file that Codex, Claude and other agents read.

| Skill | Use it when |
|---|---|
| `just` | You write, change or run a Justfile, or map workflow concepts to recipes. |
| `jagt-protocol` | A Just target emits an `AGENT_TASK v1`, `AGENT_RESULT v1` or `AGENT_TASK_DRAFT v1` packet. |
| `cx` | A recipe has expensive stages that read files and write files. |

## Install into a repository

jnterfaces is the base of every project. Its stack installer installs the
`just` skill, and it asks which other skills and packages to add:

```bash
scripts/install_stack.sh ../my-project
```

The stack installer asks about each choice in the table. Press Enter to keep
the default:

| Choice | What it installs | Default |
|---|---|---|
| `cx` | the cx skill of jnterfaces | no |
| `jagt-protocol` | the jagt-protocol skill of jnterfaces | no |
| `gest` | the Gest `g*` skills of agent_gest_git_skills | yes |
| `nossaifd` | the writing guide and its checker, from nossaifd | yes |
| `sklanes` | the `lanes` skill, which reports requests for human attention | no |

The installer saves your choices in `.agents/stack.json` in the target. A later
run without options installs the same choices again, with no questions. So
`scripts/install_stack.sh ../my-project` also updates a project.

Use options to choose without questions, for example from an agent or CI:

- **`--with cx,sklanes`** and **`--without gest`** turn choices on and off.
- **`--defaults`** takes the defaults, and **`--all`** takes every choice.
- **`--ask`** asks again, even when the target has saved choices.
- **`--clone NAME=DIR`** uses a clone in another folder. By default, each
  package is a folder next to this clone, for example `../nossaifd`.
- **`--clean`** installs the last commit of each clone, without uncommitted
  changes. **`--dry-run`** checks every package and writes nothing.

The installer runs the own installer of each package, so each package records
its own source revision in the target. First it does a dry run of every
package. If one dry run fails, it writes nothing.

If you turn off a skill of jnterfaces, the installer removes it, but it keeps
each file that you edited. If you turn off a package, the installer leaves the
package installed and says so. Remove its skills yourself.

The list of packages and their defaults is in `stack.json`.

### Install only the skills of jnterfaces

To install the skills of this package and no other package, run its own
installer:

```bash
scripts/install.sh ../my-project --skill just --skill cx --exact
```

The installer copies the skills to `.agents/skills/` in the target. It records
the source revision and a hash of each file in
`.agents/jnterfaces-install.json`. It never overwrites or removes a file that
you edited after an install. It copies tracked files and untracked files that
Git does not ignore.

Each `--skill` adds one skill. With `--exact`, the installer also removes the
skills that you left out. Without `--skill`, it installs all three skills. To
install one commit, add `--source-commit <revision>`.

You can also install the skills with npx:

```bash
npx skills add rahuldave/jnterfaces -a codex --skill just -y
```

The npx route records no source revision in `.agents/`.

The skills use these executables:

- **`just`** runs the recipes.
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
| `just install-stack <target>` | Runs the stack installer. |
| `just check` | Runs `just lint` and `just test`. Every commit must pass it. |
| `just package-lint` | Checks `skill-package.json` with skill-package-maker. |
| `just prose <check.py>` | Checks the Markdown with the nossaifd writing checker. |

The test of the jagt demo runs only with a `jagt` that has `jagt draft lint`. If
the `jagt` on your PATH is older, set `JAGT_BIN` to a newer binary.

The skill `jagt-protocol` came from the jagt repository, and the `just` and `cx`
references came from agent_gest_git_skills. This repository is now the source
of all three. The stack installer came from agent_gest_git_skills too. Its
`scripts/install_stack.sh` now passes its arguments to the one here.

## License

MIT. See [LICENSE](LICENSE).
