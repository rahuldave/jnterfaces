#!/usr/bin/env bash
# Install jnterfaces and the packages you choose into TARGET: scripts/install_stack.sh TARGET [--with A,B] [--without A,B] [--all] [--defaults] [--ask] [--clone NAME=DIR] [--clean] [--dry-run]
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v python3 >/dev/null 2>&1 || { echo 'Missing executable for the stack installer: python3' >&2; exit 1; }
exec python3 "$repo_root/scripts/install_stack.py" "$@"
