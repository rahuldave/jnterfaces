# A Python project with uv. `just` lists the recipes. `just install lint` adds a tool; `just install` adds the rest.
import? '.agents/install.just'

# Each word after a recipe's name reaches its commands whole, as "$1", "$2" and so on; "$@" is all of them
set positional-arguments

export UV_CACHE_DIR := ".local/uv-cache"

[private]
default:
    @just --list --unsorted

# Install Python and every dependency into .venv, as the lockfile says
setup:
    uv sync

# Install the git hook: the checks of .pre-commit-config.yaml before every commit. Once per clone
hook:
    uv run pre-commit install

# Format the code. Extra arguments go to ruff: just fmt --check
fmt *args:
    uv run ruff format "$@"

# Check the code against the rules in ruff.toml. Extra arguments go to ruff: just lint --fix
lint *args:
    uv run ruff check "$@"

# Check the types. Needs just install typecheck
typecheck *args:
    uv run ty check "$@"

# Run the tests. Extra arguments go to pytest: just test tests/unit. A project with no tests yet passes
test *args:
    uv run pytest "$@" || test $? -eq 5

# Run the tests, and show which lines they ran
cov *args:
    uv run pytest --cov --cov-report=term-missing "$@"

# Find whitespace errors in the changes
diff-check:
    git diff --check

# Everything a commit must pass: the format, the lint, the tests and the diff check
verify:
    just fmt --check
    just lint
    just test -q
    just diff-check
