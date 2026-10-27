# Working on jnterfaces

This repository is the source of the skills `just`, `jagt-protocol` and `cx`.
Other repositories install copies of the skills.

- Work on a `codex/*` topic branch, and open a reviewed pull request to `main`.
- Run `just check` before every commit.
- Run `just package-lint` after a change to `skill-package.json` or to the
  installer.
- Write Markdown by the nossaifd writing guide, and check it with `just prose`.
- Keep the skills free of paths from one machine. An installed copy has no
  `docs/` folder, so a skill names only files inside `.agents/skills/`.
- A merge publishes the source only. Installed copies change only when someone
  runs the installer again in each repository.
- If a change renames or removes a file that other packages link to, update
  those packages too. agent_gest_git_skills links to
  `.agents/skills/just/references/command_contract.md`, to the jagt wrappers
  and to `.agents/skills/cx/SKILL.md`.
