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

Run the installer from a clone of this repository:

```bash
scripts/install.sh ../my-project --dry-run
scripts/install.sh ../my-project
```

The installer copies the skills to `.agents/skills/` in the target. It records
the source revision and a hash of each file in
`.agents/jnterfaces-install.json`. It never overwrites or removes a file that
you edited after an install. It copies tracked files and untracked files that
Git does not ignore.

To install one commit, add `--source-commit <revision>`. To install one skill,
add `--skill <name>`. The skills link to each other, so a link to a skill that
you left out does not resolve.

You can also install the skills with npx:

```bash
npx skills add rahuldave/jnterfaces -a codex --skill '*' -y
```

The npx route records no source revision in `.agents/`.

The skills use these executables:

- **`just`** runs the recipes.
- **`jagt`** renders and checks packets, from https://github.com/rahuldave/jagt.
  The draft commands need a build that has `jagt draft lint`.
- **`cx`** runs incremental stages, from https://github.com/rahuldave/cx.

## Use with the other skill packages

jnterfaces is one of the packages that combine into one install:

- **agent_gest_git_skills** has the Gest `g*` workflow skills. Its skills link
  to the jnterfaces skills. When jnterfaces is not installed, each link has a
  one-line fallback.
- **jnterfaces** has the skills for the Justfile, jagt and cx.
- **sklanes** has the optional `lanes` skill, which reports requests for human
  attention to a Lanes inbox.

To install the combination, run the stack installer of agent_gest_git_skills.
It installs each package with that package's own installer, so each package
records its own source revision in the target:

```bash
../agent_gest_git_skills/scripts/install_stack.sh ../my-project --with-lanes
```

The stack installer expects the clones of jnterfaces and sklanes next to the
clone of agent_gest_git_skills. The options `--jnterfaces DIR` and
`--sklanes DIR` select other clones.

## Work on this repository

| Command | What it does |
|---|---|
| `just lint` | Checks the syntax of the scripts, the Python and the JSON. |
| `just test` | Runs the tests of the installer and the skills. |
| `just check` | Runs `just lint` and `just test`. Every commit must pass it. |
| `just package-lint` | Checks `skill-package.json` with skill-package-maker. |
| `just prose <check.py>` | Checks the Markdown with the nossaifd writing checker. |

The test of the jagt demo runs only with a `jagt` that has `jagt draft lint`. If
the `jagt` on your PATH is older, set `JAGT_BIN` to a newer binary.

The skill `jagt-protocol` came from the jagt repository, and the `just` and `cx`
references came from agent_gest_git_skills. This repository is now the source
of all three. The jagt repository keeps an installed copy for its own tests.
