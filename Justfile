# jnterfaces: every task is a recipe here. `just` lists them.

# Each word after a recipe's name reaches its commands whole, as "$1", "$2" and so on; "$@" is all of them
set positional-arguments

[private]
default:
    @just --list --unsorted

# Python comes from uv, so the machine needs just and uv only
python := "uv run --no-project --quiet --with tomlkit==0.15.2 --with ruamel.yaml==0.19.1 python"
# The tests run with pytest, and pytest-xdist runs them in parallel
test_python := "uv run --no-project --quiet --with tomlkit==0.15.2 --with ruamel.yaml==0.19.1 --with pytest==8.4.2 --with pytest-xdist==3.8.0 python"

# Check the shell scripts, the Python, the JSON and the Justfile templates for syntax, lint the steps of the templates, and check that each link to an optional skill has a fallback
lint:
    for script in .agents/skills/*/scripts/*.sh; do bash -n "$script"; done
    {{python}} -m py_compile scripts/*.py tests/*.py
    {{python}} -m json.tool skill-package.json > /dev/null
    {{python}} -m json.tool stack.json > /dev/null
    for template in .agents/skills/just/assets/templates/*.just .agents/install.just; do just --justfile "$template" --summary > /dev/null; done
    {{python}} scripts/lint_steps.py .agents/skills/just/assets/templates/*.just templates/project.just
    find .agents/skills -type f \( -name '*.md' -o -name '*.sh' -o -name '*.just' -o -name '*.yaml' -o -name '*.txt' \) -exec {{python}} scripts/check_skill_links.py --package . {} +

# Run the tests in parallel, one process for each CPU. Extra arguments go to pytest: just test tests/test_skills.py, or a unittest name such as tests.test_skills.SkillTest. Set JAGT_BIN to run the jagt demo with jagt 0.3.0 or later
test *args:
    #!/usr/bin/env bash
    set -euo pipefail
    words=()
    for word in "$@"; do
        # A unittest name, such as tests.test_skills.SkillTest.test_x, becomes tests/test_skills.py::SkillTest::test_x
        # A path such as tests/test_skills.py stays as it is
        if [[ "$word" =~ ^tests\.(test_[A-Za-z0-9_]+)((\.[A-Za-z0-9_]+)*)$ && "$word" != *.py ]]; then
            rest="${BASH_REMATCH[2]}"
            words+=("tests/${BASH_REMATCH[1]}.py${rest//./::}")
        else
            words+=("$word")
        fi
    done
    exec {{test_python}} -m pytest -q -p no:cacheprovider -c /dev/null --rootdir . -o testpaths=tests -n auto ${words[@]+"${words[@]}"}

# Check skill-package.json with skill-package-maker at the commit that CI uses
package-lint:
    #!/usr/bin/env bash
    set -euo pipefail
    tool_root="$(mktemp -d)"
    trap 'rm -rf -- "$tool_root"' EXIT
    git clone --quiet https://github.com/rahuldave/agent_skill_package_maker.git "$tool_root/package-maker"
    git -C "$tool_root/package-maker" checkout --quiet 6a5938c49ca71c374dc1a234a4d15134c484a741
    uv run --no-project python "$tool_root/package-maker/skills/skill-package-maker/scripts/lint_skill_bundle.py" .

# Check the prose of the Markdown with nossaifd check.py, without front matter: just prose-check ~/Projects/nossaifd/check.py
prose-check checker:
    #!/usr/bin/env bash
    set -uo pipefail
    work="$(mktemp -d)"; trap 'rm -rf -- "$work"' EXIT; status=0
    for page in README.md AGENTS.md docs/*.md .agents/skills/*/SKILL.md .agents/skills/*/references/*.md .agents/skills/*/assets/*.md; do
        copy="$work/$(echo "$page" | tr / _)"
        awk 'NR==1 && /^---$/ {f=1; next} f && /^---$/ {f=0; next} !f' "$page" > "$copy" || status=1
        uv run --no-project --quiet python "$1" "$copy" || status=1
    done
    exit "$status"

# Everything a commit must pass: the lint and the tests
check:
    just lint
    just test

# Run the installer on another project, with roles, tools or options: just install-into ../app --defaults lint
install-into target *args:
    #!/usr/bin/env sh
    target="$1"; shift
    exec uv run --quiet --script scripts/install_stack.py --target "$target" "$@"

# The documentation site, from docs/, with smalldocs.
smalldocs := "uvx --quiet smalldocs@0.5.12"

# Check the site: broken links, and options that smalldocs ignores
docs-check:
    {{smalldocs}} check docs

# Preview the site, and build it again on each change
docs:
    {{smalldocs}} preview docs

# Build the site into docs/_site
docs-build:
    {{smalldocs}} render docs --strict

# Build the site in strict mode, then push it to the branch gh-pages
docs-publish: docs-build
    {{smalldocs}} publish gh-pages docs --no-render
