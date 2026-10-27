#!/usr/bin/env bash
# Install the jnterfaces skills into a target repository: scripts/install.sh TARGET [--dry-run] [--skill NAME] [--source-commit REV]
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
missing=()
for tool in python3 git; do
  command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done
if [ "${#missing[@]}" -gt 0 ]; then
  printf 'Missing executable(s) for the installer: %s\n' "${missing[*]}" >&2
  exit 1
fi
if ! command -v just >/dev/null 2>&1; then
  echo 'Required executable not on PATH: just. The skills describe Justfiles; install just to run them.' >&2
fi
optional=()
if ! command -v jagt >/dev/null 2>&1; then optional+=(jagt); fi
if ! command -v cx >/dev/null 2>&1; then optional+=(cx); fi
if [ "${#optional[@]}" -gt 0 ]; then
  printf 'Optional executable(s) not on PATH: %s\n' "${optional[*]}" >&2
fi
exec python3 "$repo_root/scripts/install_package.py" --source "$repo_root" "$@"
