"""The pins of the workflows: verify.yml of jnterfaces and docs-publish.yml, the template of the docs role.

Each action is pinned by the full SHA of a commit, with its version in a note. One step of each job, "Install the pinned
tools", downloads each tool at an exact version and checks it against a SHA-256 in the file. Each tool has one version
and one SHA-256 in all the workflows, and the version of jlog, jagt and smalldocs is the one of the recipes, the
installer, the manifest and the docs. The scan of the missing pins is a copy of the scan of the CI starters of
agent_gest_git_skills, scripts/test_ci_starters.py, and the tests there compare the pins of both repositories.
JN_TEST_NETWORK=1 also checks each pin against its download, and each SHA of an action against its tag.
"""
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import urllib.request
from pathlib import Path

from ruamel.yaml import YAML

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import install_stack  # noqa: E402

VERIFY = REPO / '.github/workflows/verify.yml'
DOCS_PUBLISH = REPO / '.agents/skills/docs/assets/docs-publish.yml'
# The tools that each job installs, and no other.
JOB_TOOLS = {
    (VERIFY, 'verify'): {'just', 'uv', 'jlog', 'jagt', 'smalldocs'},
    (VERIFY, 'release'): {'just', 'uv'},
    (VERIFY, 'docs'): {'just', 'uv', 'smalldocs'},
    (DOCS_PUBLISH, 'publish'): {'just', 'uv', 'jlog', 'smalldocs'},
}
ACTIONS = {'actions/checkout'}
INSTALL_TOOLS = 'Install the pinned tools'
TARGETS = ('x86_64-unknown-linux-musl', 'aarch64-unknown-linux-musl')
# The one curl of each step, in the function fetch, which checks the SHA-256 of each download.
FETCH_CURL = 'curl -fsSL --retry 3 -o "$work/$1" "$2"'

# --- The scan of the missing pins. Keep it the same as in scripts/test_ci_starters.py of agent_gest_git_skills. ---
# uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
# The key may be quoted, and the step may be a flow mapping: {uses: a/b@SHA} # v1.2.3.
USES = re.compile(r"""(?:^|[\s{,\-])["']?uses["']?\s*:\s*["']?([^\s"'{}\[\],]+)["']?\s*[}\],]*\s*(.*)$""")
SHA_PIN = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
# An action of the repository itself, or an image pinned by its digest, needs no SHA of a commit and no note.
LOCAL_OR_IMAGE = re.compile(r"^(?:\./\S+|docker://\S+@sha256:[0-9a-f]{64})$")
VERSION_NOTE = re.compile(r"^\s*# v[0-9]+\.[0-9]+\.[0-9]+\s*$")
EXACT = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
# The runner image of a job, by an exact version, such as ubuntu-26.04. ubuntu-latest moves to a new image.
RUNNER = re.compile(r"^ubuntu-[0-9]{2}\.[0-9]{2}$")
# A download of a release of just or uv. The version after download/ must be exact.
RELEASE = re.compile(r"github\.com/(casey/just|astral-sh/uv)/releases/(?:download/([^/\s\"']+)|latest)")
SHELLS = r"(?:(?:ba|z|da|k)?sh|python3?|perl|ruby)"
# A shell that reads its script from stdin: a shell, or python, perl or ruby with no script or code of its own.
STDIN_SHELL = (r"(?:(?:ba|z|da|k)?sh\b|(?:python3?|perl|ruby)(?:\s+-(?![cmeE]\b)[A-Za-z]+)*"
               r"(?:\s+-(?=\s|$)|\s*(?:$|[;&|)])))")
# A shell at any stage after a pipe, also by its path, through env or sudo, as after tee or gunzip.
PIPE_SHELL = (r"\|\s*(?:[^|]*\|\s*)*(?:sudo\s+(?:-\S+\s+)*)?(?:env\s+(?:-\S+\s+|\w+=\S*\s+)*)?(?:/\S*/)?"
              + STDIN_SHELL)
# A script that a download pipes into a shell, that a shell runs from $(...) or <(...), and an install of just or uv
# with pip, pipx, cargo or brew. No pin checks any of them.
PIPED = re.compile(r"\b(?:curl|wget)\b[^|]*" + PIPE_SHELL + r"|"
                   r"\b(?:" + SHELLS + r"\s+-c|eval)\s+[\"']?(?:\$\(|`)\s*(?:curl|wget)\b|"
                   r"<\(\s*(?:curl|wget)\b|"
                   r"\b(?:pipx|pip3?|uv\s+pip|uv\s+tool|cargo)\s+(?:b?install)\s+(?:-\S+\s+)*(?:rust-just|uv|just)(?![\w.-])|"
                   r"\b(?:apt-get|apt|snap)\s+install\b.*\b(?:just|uv)\b")
# An install with brew, and a download with gh, which no pin checks.
BREW = re.compile(r"\bbrew\s+(?:install|reinstall|upgrade|bundle)\b|\bgh\s+release\s+download\b")
DOWNLOAD = re.compile(r"\b(curl|wget)\b([^|;&\n]*)")
# A run of a file by a shell, source, exec, ., or by its path.
RUN_BY = r"(?:(?:sudo\s+)?(?:" + SHELLS + r"|source|exec|\.)\s+(?:-\S+\s+)*(?:<\s*)?[\"']?[^\s\"']*|\./[^\s\"']*|chmod\s+\S+\s+[\"']?[^\s\"']*)"


def downloaded_files(args: str, tool: str) -> list[str]:
    """The name of each file that a curl or wget with the words ARGS writes, without its folder."""
    try:
        words = shlex.split(args, comments=False)
    except ValueError:
        words = args.split()
    names, urls, saved = [], [w for w in words if re.match(r"^[a-z]+://", w)], False
    for index, word in enumerate(words):
        value = None
        if word in ("-o", "--output", "--output-document") or (tool == "wget" and word == "-O"):
            value = words[index + 1] if index + 1 < len(words) else None
        elif word.startswith(("--output=", "--output-document=")):
            value = word.split("=", 1)[1]
        elif tool == "curl" and re.match(r"^-[A-Za-z]*o$", word):
            value = words[index + 1] if index + 1 < len(words) else None
        elif tool == "curl" and re.match(r"^-[A-Za-z]*o.+", word):
            value = word[word.index("o") + 1:]
        elif tool == "wget" and re.match(r"^-[A-Za-z]*O.+", word):
            value = word[word.index("O") + 1:]
        elif re.fullmatch(r"[12]?>>?", word):
            value = words[index + 1] if index + 1 < len(words) else None
        elif re.match(r"^[12]?>", word):
            value = re.sub(r"^[12]?>+", "", word)
        elif tool == "wget" and re.match(r"^-[A-Za-z]*O$", word):
            value = words[index + 1] if index + 1 < len(words) else None
        elif tool == "curl" and (word in ("-O", "--remote-name") or re.match(r"^-[A-Za-z]*O[A-Za-z]*$", word)):
            names += [url.rstrip("/").rsplit("/", 1)[-1] for url in urls]
            saved = True
        if value:
            saved = True
            if value != "-":
                names.append(value.rstrip("/").rsplit("/", 1)[-1])
    if tool == "wget" and not saved:
        names += [url.rstrip("/").rsplit("/", 1)[-1] for url in urls]
    return [name for name in names if name]


def pin_findings(text: str) -> list[str]:
    """The missing pins of the workflow TEXT: an action, also in a note, not pinned by the full SHA of a commit with
    its version in a note after it; a setup of just or uv with no exact version; a download of a release of just or
    uv with no exact version; a script that a download pipes into a shell, or that a shell runs from $(...) or <(...);
    a file that curl or wget writes and a later line runs; and an install with brew. No pin checks these."""
    findings = []
    data = YAML(typ="safe").load(text) or {}
    steps = [each for job in (data.get("jobs") or {}).values() for each in job.get("steps") or []]
    findings += lines_findings(text.splitlines(), "line")
    # The run of each step as the shell gets it, so a quoted YAML string with \\n cannot hide a line.
    for index, each in enumerate(steps, 1):
        findings += lines_findings(str(each.get("run") or "").splitlines(), f"step {index}, line")
    # Each uses of a step or of a job as YAML reads it, so no spelling of the key can hide an action.
    jobs = [job for job in (data.get("jobs") or {}).values() if isinstance(job, dict)]
    for name, job in (data.get("jobs") or {}).items():
        if isinstance(job, dict) and "uses" not in job and not RUNNER.match(str(job.get("runs-on", ""))):
            findings.append(f"job {name}: runs-on {job.get('runs-on')!r} is not an exact image such as ubuntu-26.04")
    for each in steps + jobs:
        uses = str(each.get("uses") or "")
        if uses and not (SHA_PIN.match(uses) or LOCAL_OR_IMAGE.match(uses)):
            findings.append(f"{uses} is not pinned by the full SHA of a commit")
    for each in steps:
        uses, given = each.get("uses") or "", each.get("with") or {}
        if uses.startswith("extractions/setup-just@") and not EXACT.match(str(given.get("just-version", ""))):
            findings.append(f"{uses}: no exact just-version")
        if uses.startswith("astral-sh/setup-uv@") and not EXACT.match(str(given.get("version", ""))):
            findings.append(f"{uses}: no exact version of uv")
    return findings


def lines_findings(lines: list[str], where: str) -> list[str]:
    """The missing pins of LINES, each named as WHERE and its number."""
    findings = []
    # A line that ends in a backslash goes on with the next line, as the shell reads it.
    joined, start = [], None
    for line in lines:
        if start is not None:
            joined[start] = joined[start][:-1] + " " + line.strip()
            joined.append("")
        else:
            start = len(joined)
            joined.append(line)
        if not joined[start].endswith("\\"):
            start = None
    lines = joined
    for number, line in enumerate(lines, 1):
        match = USES.search(line)
        if match and not LOCAL_OR_IMAGE.match(match.group(1)):
            if not SHA_PIN.match(match.group(1)):
                findings.append(f"{where} {number}: {match.group(1)} is not pinned by the full SHA of a commit")
            if not VERSION_NOTE.match(match.group(2)):
                findings.append(f"{where} {number}: {match.group(1)} has no note # vX.Y.Z with its version")
        for repo, version in RELEASE.findall(line):
            if not EXACT.match(version):
                findings.append(f"{where} {number}: a download of {repo} with no exact version")
        if PIPED.search(line):
            findings.append(f"{where} {number}: a shell runs a downloaded script, and no pin checks it")
        if BREW.search(line):
            findings.append(f"{where} {number}: brew installs a tool, and no pin checks it")
        for download in DOWNLOAD.finditer(line):
            for name in downloaded_files(download.group(2), download.group(1)):
                if name.startswith("$"):
                    continue
                later = "\n".join([line[download.end():]] + lines[number:])
                if re.search(RUN_BY + re.escape(name) + r"(?![\w.-])", later):
                    findings.append(f"{where} {number}: a later line runs {name}, which a download wrote and no pin checks")
    return findings
# --- The end of the scan. ---

FETCH = re.compile(r'^\s*fetch\s+"?([^"\s]+)"?\s+"?([^"\s]+)"?\s*$')
PIN_LINE = re.compile(r"^([0-9a-f]{64})  (\S+)$")
# The tool and the version in the name of a download: jlog-v0.5.0-$target.tar.gz, smalldocs-0.5.13-py3-none-any.whl.
NAME = re.compile(r"^([a-z-]+?)-v?([0-9]+\.[0-9]+\.[0-9]+)-")


def load(path):
    return YAML(typ='safe').load(path.read_text())


def install_step(path, job):
    found = [each for each in load(path)['jobs'][job]['steps'] if each.get('name') == INSTALL_TOOLS]
    assert len(found) == 1, (path.name, job)
    return found[0]


def pinned(run):
    """The pins of an install step: the name of each download and its SHA-256."""
    block = run.split("pins='", 1)[1].split("'", 1)[0]
    found = {}
    for line in filter(None, (line.strip() for line in block.splitlines())):
        match = PIN_LINE.match(line)
        assert match, f'not a pin: {line}'
        assert match.group(2) not in found, f'two pins for {match.group(2)}'
        found[match.group(2)] = match.group(1)
    return found


def fetches(run):
    """The name and the URL of each fetch of an install step."""
    return [FETCH.match(line).groups() for line in run.splitlines() if FETCH.match(line)]


def files_of(name, url):
    """Each file and link of a fetch: one for each machine, or one when the name has no $target."""
    if '$target' not in name + url:
        return [(name, url)]
    return [(name.replace('$target', target), url.replace('$target', target)) for target in TARGETS]


def all_pins():
    """Each pin of each step, as (workflow, job, name, SHA-256)."""
    return [(path.name, job, name, digest) for (path, job) in JOB_TOOLS
            for name, digest in pinned(install_step(path, job)['run']).items()]


def versions():
    """The versions of each tool in the names of the pins of all the workflows."""
    found = {}
    for _, _, name, _ in all_pins():
        tool, version = NAME.match(name).groups()
        found.setdefault(tool, set()).add(version)
    return found


def without_runner(findings):
    """The findings of a probe with no runs-on, without the one about runs-on."""
    return [finding for finding in findings if 'runs-on' not in finding]


class ScanTests(unittest.TestCase):
    def test_no_workflow_has_a_missing_pin(self):
        for path in (VERIFY, DOCS_PUBLISH):
            with self.subTest(workflow=path.name):
                self.assertEqual(pin_findings(path.read_text()), [])

    def test_the_scan_finds_each_missing_pin(self):
        sha = '11d5960a326750d5838078e36cf38b85af677262'
        probes = [
            'jobs: {j: {steps: [{uses: actions/checkout@v4}]}}',
            f'jobs: {{j: {{steps: [{{uses: actions/checkout@{sha[:12]}}}]}}}}',
            f'jobs:\n  j:\n    steps:\n      - uses: actions/checkout@{sha}\n',
            f'jobs:\n  j:\n    steps:\n      - uses: actions/checkout@{sha} # v4\n',
            '#   - uses: actions/checkout@v4\njobs: {}\n',
            f'jobs:\n  j:\n    steps:\n      - uses: extractions/setup-just@{sha} # v3.1.0\n',
            f'jobs:\n  j:\n    steps:\n      - uses: astral-sh/setup-uv@{sha} # v6.8.0\n        with: {{version: latest}}\n',
            "jobs: {j: {steps: [{run: 'curl -fsSL https://astral.sh/uv/install.sh | sh'}]}}",
            "jobs: {j: {steps: [{run: 'curl -fsSL https://github.com/astral-sh/uv/releases/latest/download/uv.tar.gz'}]}}",
            "jobs: {j: {steps: [{run: 'wget -qO- https://astral.sh/uv/install.sh | sh'}]}}",
            "jobs: {j: {steps: [{run: 'wget -O - https://x.example/i.sh | sudo -E bash'}]}}",
            "jobs: {j: {steps: [{run: 'sh -c \"$(curl -fsSL https://just.systems/install.sh)\"'}]}}",
            "jobs: {j: {steps: [{run: 'bash -c \"$(wget -qO- https://x.example/i.sh)\"'}]}}",
            "jobs: {j: {steps: [{run: 'eval \"$(curl -fsSL https://x.example/env)\"'}]}}",
            "jobs: {j: {steps: [{run: 'bash <(curl -fsSL https://x.example/i.sh)'}]}}",
            "jobs: {j: {steps: [{run: 'source <(wget -qO- https://x.example/i.sh)'}]}}",
            "jobs: {j: {steps: [{run: 'curl -fsSL -o i.sh https://x.example/install.sh && sh i.sh'}]}}",
            "jobs: {j: {steps: [{run: 'curl -fsSLo i.sh https://x.example/install.sh; bash ./i.sh'}]}}",
            "jobs: {j: {steps: [{run: 'curl --output=/tmp/i.sh https://x.example/install.sh && sh /tmp/i.sh'}]}}",
            "jobs: {j: {steps: [{run: \"curl -fsSL -o \\\"$RUNNER_TEMP/i.sh\\\" https://x.example/a\\nsh \\\"$RUNNER_TEMP/i.sh\\\"\"}]}}",
            "jobs: {j: {steps: [{run: 'curl -fsSLO https://x.example/install.sh\nchmod +x install.sh\n./install.sh'}]}}",
            "jobs: {j: {steps: [{run: 'wget https://x.example/install.sh && sh install.sh'}]}}",
            "jobs: {j: {steps: [{run: 'wget -O i.sh https://x.example/a && . ./i.sh'}]}}",
            "jobs: {j: {steps: [{run: 'brew install just uv'}]}}",
            "jobs: {j: {steps: [{run: 'pipx install uv'}]}}",
            "jobs: {j: {steps: [{run: 'cargo install --locked just'}]}}",
            # The forms that the first review of this scan found.
            'jobs:\n  j:\n    steps:\n      - "uses": actions/checkout@main\n',
            'jobs:\n  j:\n    steps:\n      - uses : actions/checkout@v4\n',
            'jobs:\n  j:\n    uses: o/r/.github/workflows/w.yml@main\n',
            "jobs: {j: {steps: [{run: 'curl -fsSL https://x.example/i.sh | tee i.sh | sh'}]}}",
            "jobs: {j: {steps: [{run: 'curl -fsSL https://x.example/i.sh | /bin/bash'}]}}",
            "jobs: {j: {steps: [{run: 'curl -fsSL https://x.example/i.sh | env bash'}]}}",
            "jobs: {j: {steps: [{run: 'curl -fsSL https://x.example/i.sh > i.sh && sh i.sh'}]}}",
            "jobs: {j: {steps: [{run: 'curl -fsSL https://x.example/i.sh \\\n  | sh'}]}}",
            "jobs: {j: {steps: [{run: 'pip install rust-just'}]}}",
            "jobs: {j: {steps: [{run: 'cargo install -y just'}]}}",
            "jobs: {j: {steps: [{run: 'gh release download v1 -R casey/just'}]}}",
            "jobs: {j: {steps: [{run: 'sudo apt-get install -y just'}]}}",
            # The forms that the second review found.
            "jobs: {j: {steps: [{run: 'wget -qO i.sh https://x.example/a && sh i.sh'}]}}",
            "jobs: {j: {steps: [{run: 'curl -fsSL https://x.example/a.gz | gunzip | sh'}]}}",
            "jobs: {j: {steps: [{run: 'sh -c \"`curl -fsSL https://x.example/a`\"'}]}}",
            "jobs: {j: {steps: [{run: 'curl -fsSL https://x.example/a 1>i.sh && sh i.sh'}]}}",
            "jobs: {j: {steps: [{run: 'curl -fsSL https://x.example/a | python3'}]}}",
            "jobs: {j: {steps: [{run: 'curl -fsSL https://x.example/a | python3 -'}]}}",
            # The forms that the third review found.
            "jobs: {j: {steps: [{run: 'curl -fsSL \\\n  https://x.example/i.sh \\\n  | sh'}]}}",
            "jobs: {j: {steps: [{run: 'curl -fsSL https://x.example/a | python3 - --version 1.8'}]}}",
            "jobs: {j: {steps: [{run: 'curl -fsSL https://x.example/a | python3 -I'}]}}",
            "jobs: {j: {steps: [{run: 'curl -fsSL https://x.example/a | perl -w'}]}}",
            "jobs: {j: {steps: [{run: 'curl -fsSL -o i.sh https://x.example/a\nsh < i.sh'}]}}",
            "jobs: {j: {steps: [{run: 'curl -fsSL -o i.sh https://x.example/a\nbash -s < i.sh'}]}}",
        ]
        for probe in probes:
            with self.subTest(probe=probe):
                self.assertNotEqual(without_runner(pin_findings(probe)), [])
        for runner in ("", "runs-on: ubuntu-latest", "runs-on: ubuntu-24", "runs-on: [self-hosted, linux]",
                       "runs-on: ${{ matrix.os }}", "runs-on: macos-15"):
            with self.subTest(runner=runner):
                self.assertNotEqual(pin_findings(f"jobs:\n  j:\n    {runner}\n    steps: []\n"), [])
        self.assertEqual(pin_findings("jobs:\n  j:\n    runs-on: ubuntu-26.04\n    steps: []\n"), [])
        good = (f'jobs:\n  j:\n    steps:\n      - uses: astral-sh/setup-uv@{sha} # v6.8.0\n'
                '        with: {version: 0.13.7}\n'
                '      - run: curl -O https://github.com/casey/just/releases/download/1.59.0/just.tar.gz\n'
                '      - run: curl -fsSL -o i.sh https://x.example/a && sha256sum i.sh\n'
                '      - run: wget -q https://x.example/just.tar.gz && tar -xzf just.tar.gz\n')
        self.assertEqual(without_runner(pin_findings(good)), [])
        # An action of the repository, an image by its digest, a quoted value and a flow step are pinned forms.
        fine = (f"jobs:\n  j:\n    steps:\n      - uses: ./.github/actions/x\n"
                f"      - uses: docker://alpine@sha256:{'a' * 64}\n"
                f"      - uses: 'actions/checkout@{sha}' # v4.4.0\n"
                f"      - {{uses: actions/checkout@{sha}}} # v4.4.0\n")
        self.assertEqual(without_runner(pin_findings(fine)), [])
        # python that runs its own code reads the download as data.
        self.assertEqual(without_runner(pin_findings(
            "jobs: {j: {steps: [{run: 'curl -fsSL https://x.example/a | python3 -c pass'}]}}")), [])
        # A package whose name starts with uv- or just- is not uv or just.
        package = "jobs: {j: {steps: [{run: 'pip install uv-dynamic-versioning'}]}}"
        self.assertEqual(without_runner(pin_findings(package)), [])


class StepTests(unittest.TestCase):
    def test_each_action_is_one_that_installs_no_tool(self):
        for path in (VERIFY, DOCS_PUBLISH):
            for job_name, job in load(path)['jobs'].items():
                with self.subTest(workflow=path.name, job=job_name):
                    used = {each['uses'].split('@')[0] for each in job['steps'] if 'uses' in each}
                    self.assertLessEqual(used, ACTIONS)

    def test_each_job_installs_its_tools_with_one_step_before_each_use(self):
        jobs = {(path, name) for path in (VERIFY, DOCS_PUBLISH) for name in load(path)['jobs']}
        self.assertEqual(jobs, set(JOB_TOOLS))
        for (path, job_name), tools in JOB_TOOLS.items():
            with self.subTest(workflow=path.name, job=job_name):
                steps = load(path)['jobs'][job_name]['steps']
                found = install_step(path, job_name)
                self.assertEqual(found['shell'], 'bash')
                self.assertNotIn('if', found)
                index = steps.index(found)
                # Right after the checkout, so each later step has the tools.
                self.assertTrue(steps[index - 1].get('uses', '').startswith('actions/checkout@'))
                self.assertEqual(index, 1)
                names = {NAME.match(name).group(1) for name, _ in fetches(found['run'])}
                self.assertEqual(names, tools)

    def test_each_download_goes_through_fetch(self):
        for (path, job), _ in JOB_TOOLS.items():
            run = install_step(path, job)['run']
            with self.subTest(workflow=path.name, job=job):
                curls = [line.strip() for line in run.splitlines() if re.search(r'\b(curl|wget)\b', line)]
                self.assertEqual(curls, [FETCH_CURL])
        for path in (VERIFY, DOCS_PUBLISH):
            others = [line for line in path.read_text().splitlines()
                      if re.search(r'\b(curl|wget)\b', line) and line.strip() != FETCH_CURL]
            self.assertEqual(others, [], path.name)

    def test_each_download_has_a_pin_for_each_machine_and_no_pin_is_left_over(self):
        for (path, job), _ in JOB_TOOLS.items():
            run = install_step(path, job)['run']
            pins, used = pinned(run), set()
            for name, url in fetches(run):
                for file, link in files_of(name, url):
                    with self.subTest(workflow=path.name, job=job, file=file):
                        self.assertIn(file, pins)
                        used.add(file)
                        self.assertNotIn('$', link)
                        self.assertTrue(link.startswith('https://'))
                        version = NAME.match(file).group(2)
                        if file.endswith('install.sh'):
                            # The script comes from the full SHA of the commit of its tag, not from the tag.
                            self.assertRegex(link, r'^https://raw\.githubusercontent\.com/rahuldave/jlog/[0-9a-f]{40}/')
                        elif file.endswith('.whl'):
                            self.assertEqual(link, f'https://files.pythonhosted.org/packages/py3/s/smalldocs/{file}')
                        else:
                            self.assertRegex(link, rf'/download/v?{re.escape(version)}/')
            self.assertEqual(set(pins), used, f'{path.name} {job}')


    def test_each_download_is_what_the_step_installs(self):
        # A tar of a file that no fetch checked, or an install of smalldocs from PyPI, would install a file that no pin
        # checks.
        for (path, job), tools in JOB_TOOLS.items():
            run = install_step(path, job)['run']
            names = [name for name, _ in fetches(run)]
            with self.subTest(workflow=path.name, job=job):
                tars = re.findall(r'^\s*tar -xzf "\$work/([^"]+)" -C "\$bin" ', run, re.M)
                self.assertEqual(len(tars), len(re.findall(r'^\s*tar\s', run, re.M)))
                self.assertEqual(sorted(tars), sorted(name for name in names if name.endswith('.tar.gz')
                                                      and not name.startswith('jlog-')))
                installs = [line.strip() for line in run.splitlines()
                            if re.search(r'\buv\s+tool\b|\bpip3?\b|\bpipx\b', line)]
                wheels = [name for name in names if name.endswith('.whl')]
                self.assertEqual(installs, [f'uv tool install --quiet "$work/{name}"' for name in wheels])
                if 'jlog' in tools:
                    [version] = versions()['jlog']
                    self.assertIn(f'JLOG_RELEASE_BASE="file://$work/release" sh "$work/jlog-v{version}-install.sh" '
                                  f'--tag v{version} --dir "$bin"', run)
                self.assertEqual(run.rstrip().splitlines()[-1], 'echo "$bin" >> "$GITHUB_PATH"')


class DriftTests(unittest.TestCase):
    """Each tool has one version and one SHA-256 for each download, in all the workflows and in the hints."""

    def test_each_download_has_one_sha256_in_all_the_workflows(self):
        found = {}
        for workflow, job, name, digest in all_pins():
            found.setdefault(name, set()).add(digest)
        for name, digests in found.items():
            with self.subTest(name=name):
                self.assertEqual(len(digests), 1, digests)

    def test_each_job_runs_on_the_same_image(self):
        images = {(path.name, name): job.get('runs-on') for path in (VERIFY, DOCS_PUBLISH)
                  for name, job in load(path)['jobs'].items()}
        self.assertEqual(set(images.values()), {'ubuntu-26.04'}, images)

    def test_each_tool_has_one_version(self):
        found = versions()
        self.assertEqual(set(found), {'just', 'uv', 'jlog', 'jagt', 'smalldocs'})
        for tool, each in found.items():
            with self.subTest(tool=tool):
                self.assertEqual(len(each), 1, each)

    def test_just_and_uv_have_exact_versions(self):
        for tool in ('just', 'uv'):
            [version] = versions()[tool]
            self.assertRegex(version, EXACT)

    def test_the_jlog_of_ci_is_the_jlog_of_the_installer_the_manifest_and_the_docs(self):
        [version] = versions()['jlog']
        tag = f'v{version}'
        hint = f'https://raw.githubusercontent.com/rahuldave/jlog/{tag}/scripts/install.sh | sh -s -- --tag {tag}'
        self.assertIn(hint, install_stack.JLOG_INSTALL)
        self.assertIn(f'git checkout {tag}', install_stack.JLOG_INSTALL)
        manifest = json.loads((REPO / 'skill-package.json').read_text())
        [entry] = [each for each in manifest['executables']['required'] if each['name'] == 'jlog']
        for key in ('description', 'install'):
            self.assertIn(hint, entry[key])
        for page in ('README.md', 'docs/new-project.md', 'docs/stack-guide.md', '.agents/skills/just/SKILL.md'):
            text = (REPO / page).read_text()
            with self.subTest(page=page):
                self.assertIn(hint, text)
                self.assertIn(f'git checkout {tag}', text)
        # Each tag of jlog that a file names is this one.
        files = subprocess.run(['git', 'ls-files', '-z'], cwd=REPO, capture_output=True, text=True,
                               check=True).stdout.split('\0')
        for name in files:
            if not name or name.startswith('notes/') or not (REPO / name).is_file():
                continue
            try:
                text = (REPO / name).read_text()
            except UnicodeDecodeError:
                continue
            for other in re.findall(r'jlog/(?:releases/download/)?(v[0-9]+\.[0-9]+\.[0-9]+)\b|jlog-(v[0-9]+\.[0-9]+\.[0-9]+)-',
                                    text):
                with self.subTest(file=name):
                    self.assertEqual(other[0] or other[1], tag)

    def test_the_jagt_of_ci_is_the_jagt_of_the_hints(self):
        [version] = versions()['jagt']
        hint = f'rahuldave/jagt/v{version}/scripts/install.sh | sh -s -- --tag v{version}'
        self.assertIn(hint, (REPO / 'README.md').read_text())
        self.assertIn(hint, (REPO / 'docs/new-project.md').read_text())

    def test_the_smalldocs_of_ci_is_the_smalldocs_of_the_recipes(self):
        [version] = versions()['smalldocs']
        self.assertIn(f'jn_smalldocs := "uvx --quiet smalldocs@{version}"',
                      (REPO / '.agents/skills/docs/docs.just').read_text())
        self.assertIn(f'smalldocs := "uvx --quiet smalldocs@{version}"', (REPO / 'Justfile').read_text())
        self.assertIn(f'--from smalldocs@{version} python', DOCS_PUBLISH.read_text())


@unittest.skipUnless(os.environ.get('JN_TEST_NETWORK') == '1', 'set JN_TEST_NETWORK=1 to download each pin')
class NetworkTests(unittest.TestCase):
    def test_each_pin_is_the_sha256_of_its_download(self):
        seen = set()
        for (path, job), _ in JOB_TOOLS.items():
            run = install_step(path, job)['run']
            pins = pinned(run)
            for name, url in fetches(run):
                for file, link in files_of(name, url):
                    if file in seen:
                        continue
                    seen.add(file)
                    with self.subTest(file=file), urllib.request.urlopen(link, timeout=60) as response:
                        self.assertEqual(hashlib.sha256(response.read()).hexdigest(), pins[file])

    def test_each_action_sha_is_the_commit_of_its_tag(self):
        for path in (VERIFY, DOCS_PUBLISH):
            for line in path.read_text().splitlines():
                match = USES.search(line)
                if not match:
                    continue
                action, sha = match.group(1).split('@')
                tag = match.group(2).split('#', 1)[1].strip()
                repo = '/'.join(action.split('/')[:2])
                refs = subprocess.run(['git', 'ls-remote', f'https://github.com/{repo}', f'refs/tags/{tag}',
                                       f'refs/tags/{tag}^{{}}'], capture_output=True, text=True, check=True).stdout
                found = dict(reversed(item.split('\t')) for item in refs.splitlines())
                with self.subTest(action=action, tag=tag):
                    self.assertEqual(found.get(f'refs/tags/{tag}^{{}}', found.get(f'refs/tags/{tag}')), sha)

    def test_the_install_sh_of_jlog_is_the_one_of_its_tag(self):
        [version] = versions()['jlog']
        refs = subprocess.run(['git', 'ls-remote', 'https://github.com/rahuldave/jlog', f'refs/tags/v{version}^{{}}'],
                              capture_output=True, text=True, check=True).stdout
        commit = refs.split('\t')[0]
        for (path, job), tools in JOB_TOOLS.items():
            if 'jlog' in tools:
                urls = [url for name, url in fetches(install_step(path, job)['run']) if name.endswith('install.sh')]
                self.assertEqual(urls, [f'https://raw.githubusercontent.com/rahuldave/jlog/{commit}/scripts/install.sh'])


FAKE_CURL = """#!/bin/sh
# A curl for the tests: curl -fsSL --retry 3 -o FILE URL copies the file of the fixtures with the name of the end of
# URL, and writes URL to FAKE_CURL_LOG.
while [ $# -gt 0 ]; do
  case $1 in
    -o) out=$2; shift 2 ;;
    --retry) shift 2 ;;
    -*) shift ;;
    *) url=$1; shift ;;
  esac
done
printf '%s\\n' "$url" >> "$FAKE_CURL_LOG"
cp "$FAKE_RELEASES/${url##*/}" "$out" 2>/dev/null || { echo "curl: (22) 404 $url" >&2; exit 22; }
"""

# The install.sh of jlog for the tests: it checks the archive of the local release against its .sha256, as the real
# one does, then writes a jlog that prints its version.
FAKE_JLOG_INSTALL = """#!/bin/sh
set -eu
printf '%s\\n' "$*" > "$FAKE_JLOG_ARGS"
dir=${JLOG_RELEASE_BASE#file://}/$2
for f in "$dir"/jlog-*.tar.gz; do
  [ "$(awk '{ print $1 }' "$f.sha256")" = "$(shasum -a 256 "$f" | awk '{ print $1 }')" ] || exit 9
done
printf '#!/bin/sh\\necho jlog\\n' > "$4/jlog"
chmod 755 "$4/jlog"
"""

# The uv of the tests: uv tool install WHEEL writes the wheel's name to FAKE_UV_LOG and a smalldocs to ~/.local/bin.
FAKE_UV = """#!/bin/sh
echo "uv $*" >> "$FAKE_UV_LOG"
[ "$1 $2" = "tool install" ] && printf '#!/bin/sh\\n' > "$HOME/.local/bin/smalldocs" && chmod 755 "$HOME/.local/bin/smalldocs"
exit 0
"""


class InstallShellTests(unittest.TestCase):
    """Run each install step, as GitHub runs it, with a fake curl and fake releases. The pins of the step are swapped
    for the hashes of the fake releases."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix='ci-pins-test-')
        self.addCleanup(scratch.cleanup)
        self.base = Path(scratch.name)
        self.tools = self.base / 'tools'
        self.tools.mkdir()
        (self.tools / 'curl').write_text(FAKE_CURL)
        if not shutil.which('sha256sum'):
            (self.tools / 'sha256sum').write_text('#!/bin/sh\nexec shasum -a 256 "$@"\n')
        machine = os.uname().machine
        # The step runs only on Linux, so the tests on macOS give it a uname that says Linux.
        (self.tools / 'uname').write_text(f'#!/bin/sh\n[ "$1" = -s ] && echo Linux || echo {machine}\n')
        for item in self.tools.iterdir():
            item.chmod(0o755)
        self.target = 'x86_64-unknown-linux-musl' if machine in ('x86_64', 'amd64') else 'aarch64-unknown-linux-musl'

    def prepare(self, run):
        """Make a fresh home and the fake release of each fetch of RUN, and return the hashes."""
        self.home = self.base / 'home'
        shutil.rmtree(self.home, ignore_errors=True)
        self.home.mkdir()
        self.releases = self.base / 'releases'
        shutil.rmtree(self.releases, ignore_errors=True)
        self.releases.mkdir()
        hashes = {}
        for name, url in fetches(run):
            file, link = name.replace('$target', self.target), url.replace('$target', self.target)
            path = self.releases / link.rsplit('/', 1)[1]
            if file.endswith('install.sh'):
                path.write_text(FAKE_JLOG_INSTALL)
            elif file.endswith('.whl'):
                path.write_bytes(b'a wheel')
            else:
                tool = NAME.match(file).group(1)
                members = {'uv': [(f'uv-{self.target}/uv', FAKE_UV), (f'uv-{self.target}/uvx', '#!/bin/sh\n')]}.get(
                    tool, [(tool, f'#!/bin/sh\necho {tool}\n')])
                with tarfile.open(path, 'w:gz') as archive:
                    for member, text in members:
                        data = text.encode()
                        info = tarfile.TarInfo(member)
                        info.size, info.mode = len(data), 0o755
                        archive.addfile(info, io.BytesIO(data))
            hashes[file] = hashlib.sha256(path.read_bytes()).hexdigest()
        return hashes

    @staticmethod
    def script(run, hashes):
        block = '\n'.join(f'{digest}  {name}' for name, digest in hashes.items())
        head, rest = run.split("pins='", 1)
        return head + "pins='\n" + block + "\n'" + rest.split("'", 1)[1]

    def bash(self, script):
        self.output = self.base / 'github_path'
        self.output.write_text('')
        self.log = self.base / 'curl.log'
        self.log.write_text('')
        env = {key: value for key, value in os.environ.items() if not key.startswith(('JN_', 'UV_'))}
        env.update(PATH=f'{self.tools}{os.pathsep}{os.environ["PATH"]}', HOME=str(self.home),
                   GITHUB_PATH=str(self.output), FAKE_RELEASES=str(self.releases), FAKE_CURL_LOG=str(self.log),
                   FAKE_JLOG_ARGS=str(self.base / 'jlog.args'), FAKE_UV_LOG=str(self.base / 'uv.log'))
        return subprocess.run(['bash', '--noprofile', '--norc', '-eo', 'pipefail', '-c', script],
                              capture_output=True, text=True, env=env, cwd=self.base)

    def installed(self):
        folder = self.home / '.local/bin'
        return {item.name for item in folder.iterdir()} if folder.is_dir() else set()

    def test_each_step_installs_its_tools_and_puts_their_folder_on_the_path(self):
        programs = {'just': {'just'}, 'uv': {'uv', 'uvx'}, 'jlog': {'jlog'}, 'jagt': {'jagt'},
                    'smalldocs': {'smalldocs'}}
        for (path, job), tools in JOB_TOOLS.items():
            run = install_step(path, job)['run']
            with self.subTest(workflow=path.name, job=job):
                done = self.bash(self.script(run, self.prepare(run)))
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertEqual(self.installed(), set().union(*(programs[tool] for tool in tools)))
                self.assertEqual(self.output.read_text(), f'{self.home}/.local/bin\n')
                if 'jlog' in tools:
                    [version] = versions()['jlog']
                    args = (self.base / 'jlog.args').read_text().split()
                    self.assertEqual(args, ['--tag', f'v{version}', '--dir', f'{self.home}/.local/bin'])
                if 'smalldocs' in tools:
                    [version] = versions()['smalldocs']
                    self.assertIn(f'/smalldocs-{version}-py3-none-any.whl', (self.base / 'uv.log').read_text())

    def test_a_download_that_does_not_match_its_pin_stops_the_step(self):
        run = install_step(VERIFY, 'verify')['run']
        hashes = self.prepare(run)
        for name in hashes:
            shutil.rmtree(self.home / '.local', ignore_errors=True)
            done = self.bash(self.script(run, dict(hashes, **{name: '0' * 64})))
            with self.subTest(download=name):
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                self.assertIn(f'::error::The SHA-256 of {name} is ', done.stdout)
                self.assertNotIn(NAME.match(name).group(1), self.installed())
                self.assertEqual(self.output.read_text(), '')

    def test_a_download_with_no_pin_stops_before_the_download(self):
        run = install_step(DOCS_PUBLISH, 'publish')['run']
        hashes = self.prepare(run)
        [name] = [key for key in hashes if key.startswith('just-')]
        done = self.bash(self.script(run, {key: value for key, value in hashes.items() if key != name}))
        self.assertEqual(done.returncode, 1)
        self.assertIn(f'::error::No pinned SHA-256 for {name}.', done.stdout)
        self.assertEqual(self.log.read_text(), '')

    def test_a_failed_download_stops_the_step(self):
        run = install_step(VERIFY, 'docs')['run']
        hashes = self.prepare(run)
        (self.releases / f'uv-{self.target}.tar.gz').unlink()
        done = self.bash(self.script(run, hashes))
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(self.output.read_text(), '')
        self.assertNotIn('smalldocs', self.installed())


if __name__ == '__main__':
    unittest.main()
