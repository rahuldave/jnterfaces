# The recipes of the docs role of jnterfaces. The index of .agents/just imports this file while the role is on.
# The site is in docs/, and smalldocs builds it. The version is pinned, so each machine builds the same site.
jn_smalldocs := "uvx --quiet smalldocs@0.5.13"

# Check the site: broken links, and options that smalldocs ignores. Then the api-check of each language that has one
docs-check:
    {{jn_smalldocs}} check docs
    recipes="$(just --justfile {{quote(justfile())}} --summary)" && if printf '%s\n' $recipes | grep -qx api-check; then just --justfile {{quote(justfile())}} api-check; fi
    jlog step docs-check

# Build the site, serve it on localhost, and rebuild it on each change
docs:
    {{jn_smalldocs}} preview docs

# Build the site into docs/_site
docs-build:
    {{jn_smalldocs}} render docs --strict

# Publish the site to the gh-pages branch, only for a public repository: the CI workflow of the role runs it
docs-publish:
    uv run --no-project --quiet python {{quote(source_directory() / "docs_publish.py")}} {{quote(jn_smalldocs)}}
