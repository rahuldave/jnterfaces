# Working on jnterfaces

This repository is the source of the skills `just`, `jagt-protocol` and `cx`.
Other repositories install copies of the skills.

- Work on a `codex/*` topic branch, and open a reviewed pull request to `main`.
- Run `just check` before every commit.
- `just check` runs `just package-lint`, which clones skill-package-maker from
  GitHub, so `just check` needs the network.
- `just check` builds the docs site with `just docs-check`, the step that the
  docs role puts in `just check` of each project. A broken page or link
  fails the step. The step builds the site in a temporary folder, so it
  changes nothing in `docs/` and in `gh-pages`. The step needs `jlog`.
- Write Markdown by the nossaifd writing guide, and check it with `just prose-check`.
- Keep the skills free of paths from one machine. An installed copy has no
  `docs/` folder, so a skill names only files inside `.agents/skills/`.
- A merge publishes the source only. Installed copies change only when someone
  runs `just install` again in each repository.
- If a change renames or removes a file that other packages link to, update
  those packages too. agent_gest_git_skills links to
  `.agents/skills/just/references/command_contract.md`, to the jagt wrappers
  and to `.agents/skills/cx/SKILL.md`.
- Keep the skills composable. `just` is the only required skill. A link to
  `cx`, to `jagt-protocol` or to a skill of another package needs a fallback in
  its paragraph. `just lint` checks this rule.
- `stack.json` lists the packages, their roles, the old names and the
  profiles. When you add a package, a role or a profile, or change a default,
  change `stack.json` and the tables in `README.md` together.
- Only this repository has an installer. A project may assume `just`, `uv` and `jlog`
  only. Run Python with `uv run`, not `python3`, and get every other tool
  through `uv` or `uvx`.
