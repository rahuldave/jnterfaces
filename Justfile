# jnterfaces: every task is a recipe here. `just` lists them.

# Each word after a recipe's name reaches its commands whole, as "$1", "$2" and so on; "$@" is all of them
set positional-arguments

[private]
default:
    @just --list --unsorted

# Check the shell scripts, the Python and the JSON for syntax
lint:
    for script in scripts/*.sh .agents/skills/*/scripts/*.sh; do bash -n "$script"; done
    python3 -m py_compile scripts/install_package.py tests/*.py
    python3 -m json.tool skill-package.json > /dev/null

# Run the tests. Extra arguments go to unittest: just test tests.test_skills. Set JAGT_BIN to run the jagt demo with a jagt that has draft lint
test *args:
    python3 -m unittest discover -s tests -q "$@"

# Check skill-package.json with skill-package-maker at the commit that CI uses
package-lint:
    #!/usr/bin/env bash
    set -euo pipefail
    tool_root="$(mktemp -d)"
    trap 'rm -rf -- "$tool_root"' EXIT
    git clone --quiet https://github.com/rahuldave/agent_skill_package_maker.git "$tool_root/package-maker"
    git -C "$tool_root/package-maker" checkout --quiet 6a5938c49ca71c374dc1a234a4d15134c484a741
    uv run --no-project python "$tool_root/package-maker/skills/skill-package-maker/scripts/lint_skill_bundle.py" .

# Check the prose of the Markdown with nossaifd check.py, without front matter: just prose ~/Projects/nossaifd/check.py
prose checker:
    #!/usr/bin/env bash
    set -uo pipefail
    work="$(mktemp -d)"; trap 'rm -rf -- "$work"' EXIT; status=0
    for page in README.md AGENTS.md .agents/skills/*/SKILL.md .agents/skills/*/references/*.md; do
        copy="$work/$(echo "$page" | tr / _)"
        awk 'NR==1 && /^---$/ {f=1; next} f && /^---$/ {f=0; next} !f' "$page" > "$copy" || status=1
        python3 "$1" "$copy" || status=1
    done
    exit "$status"

# Everything a commit must pass: the lint and the tests
check:
    just lint
    just test

# Install the skills into a target repository. Extra arguments go to the installer: just install ../app --dry-run
install target *args:
    scripts/install.sh "$@"
