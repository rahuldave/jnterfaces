---
title: Home
---

The home page of the docs site. Write the pages in
this folder, and add each page to `navbar` in `_smalldocs.yml`.

- `just docs` builds the site and serves it on localhost.
- `just docs-check` checks the links. `just check` runs it.
- After a merge to `main`, the workflow `docs-publish.yml` publishes the
  site of a public repository to the `gh-pages` branch.
