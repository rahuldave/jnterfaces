#!/usr/bin/env python3
"""Publish the docs site to the gh-pages branch, only for a public repository.

Usage: docs_publish.py SMALLDOCS. SMALLDOCS is the command that runs smalldocs, for example
"uvx --quiet smalldocs@0.5.13". The script asks gh for the visibility of the remote origin, where smalldocs pushes.
It publishes only on PUBLIC.
On PRIVATE and INTERNAL, and with the answer docs-publish=no in .agents/stack.json, it skips. An error of gh skips on
the machine of a person, so a failure never publishes. In CI, where CI is set, an error of gh fails, so a missing
token shows as a failure.
"""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


def in_ci():
    """True when CI is set and is not 0 or false."""
    return os.environ.get('CI', '').strip().lower() not in ('', '0', 'false')


def answer():
    """The answer of the setup question docs-publish, from .agents/stack.json. yes when there is none."""
    try:
        value = json.loads(Path('.agents/stack.json').read_text()).get('answers', {}).get('docs-publish')
    except (OSError, ValueError, AttributeError):
        return 'yes'
    return value if value in ('yes', 'no') else 'yes'


def visibility():
    """The visibility of the repository from gh, and the error of gh, one of them None."""
    try:
        # smalldocs pushes to origin, so gh asks about the push URL of origin. With no argument, gh may pick a remote such as upstream.
        origin = subprocess.run(['git', 'remote', 'get-url', '--push', 'origin'], capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=60)
        if origin.returncode or not origin.stdout.strip():
            return None, 'the repository has no remote origin'
        run = subprocess.run(['gh', 'repo', 'view', origin.stdout.strip(), '--json', 'visibility', '--jq', '.visibility'],
                             capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if run.returncode:
        return None, (run.stderr.strip() or f'gh exited with {run.returncode}')[-2000:]
    if not run.stdout.strip():
        return None, 'gh gave no visibility'
    return run.stdout.strip(), None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    if answer() == 'no':
        print('docs-publish: skipped, because the answer docs-publish=no is in .agents/stack.json')
        return 0
    seen, error = visibility()
    if error is not None:
        if in_ci():
            print(f'docs-publish: gh cannot read the visibility of the repository, so the site is not published. In '
                  f'CI, the workflow must set GH_TOKEN: ${{{{ github.token }}}}. gh said: {error}', file=sys.stderr)
            return 1
        print(f'docs-publish: skipped, because gh cannot read the visibility of the repository. gh said: {error}')
        return 0
    if seen != 'PUBLIC':
        print(f'docs-publish: skipped, because the repository is {seen or "of no known visibility"}, not PUBLIC')
        return 0
    return subprocess.run(shlex.split(argv[0]) + ['publish', 'gh-pages', 'docs']).returncode


if __name__ == '__main__':
    sys.exit(main())
