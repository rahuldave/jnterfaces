---
name: docs
description: Use when you write, check, build or publish the docs site of a project that has the docs role of jnterfaces, a smalldocs site in docs/. It gives the recipes docs-check, docs, docs-build and docs-publish, and the rules for the CI workflow that publishes the site.
license: MIT, copyright (c) 2026 Rahul Dave. The full text is in the LICENSE file of https://github.com/rahuldave/jnterfaces
---

# Docs

The docs role gives the project a docs site in `docs/`, for any language.
smalldocs builds the site. `just install docs` turns the role on, and
`just uninstall docs` takes it out.

## What the role writes

- **The config and the first page of the site.** The role writes each of
  the starters `assets/_smalldocs.yml` and `assets/index.md` into the folder
  `docs` of the project, when the folder does not have that file. They are
  yours from then on, and `just uninstall docs` keeps them. A project with
  docs of another tool, such as MkDocs, must move them first.
- **`.github/workflows/docs-publish.yml`**, once. After a push to `main`, it
  runs `just docs-publish`. `just uninstall docs` removes it while you have
  not changed it.
- **A line in `.gitignore`** for the folder of the built site, `_site` in
  the folder `docs`.
- **The recipes** of `.agents/skills/docs/docs.just`, which the index of
  `.agents/just` imports.

## The recipes

| Recipe | What it does |
|---|---|
| `just docs-check` | A step of `just check`. It runs `smalldocs check docs`, then the `api-check` of each language that has one. |
| `just docs` | Builds the site, serves it on localhost, and rebuilds it on each change. |
| `just docs-build` | Builds the site into the folder `_site` of the folder `docs`. |
| `just docs-publish` | Publishes the site to the `gh-pages` branch, only for a public repository. |

## Recipe names

The role defines the recipes `docs`, `docs-check`, `docs-build` and
`docs-publish`. A project Justfile with a recipe of one of these names
cannot turn the role on. Rename the recipe of the project first.

## The publish

`just docs-publish` asks `gh` for the visibility of the remote `origin`,
where smalldocs pushes:

- **On `PUBLIC`,** it runs `smalldocs publish gh-pages docs`.
- **On `PRIVATE` and `INTERNAL`,** it skips, and says so.
- **With the answer `docs-publish=no`** in `.agents/stack.json`, it skips.
- **When `gh` fails** on the machine of a person, it skips, so a failure
  never publishes. In CI, where `CI` is set, it fails, so a missing token
  shows as a failure. The workflow sets `GH_TOKEN: ${{ github.token }}`.

A person must choose the branch `gh-pages` once, under Pages in the
settings of the repository.

Run `just docs-publish` only from the workflow, or when the person asks. It
pushes to GitHub.
