"""a repo's test command, and whether it has any tests yet, read from its committed tree.

Tests are required: the gate refuses a card on a repo with no test command (review/gates.py), and
the same command scopes the worker's own shell. This finds the command for a repo that already has
a runner, and says when a repo has no tests at all, so the board can ask for them up front instead
of failing a card minutes into its run.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

# the gate runs offline in the repo image, where the dependencies are already installed - a plain
# `uv run` would try to sync first and fail without a network
PYTHON_COMMAND = "uv run --no-sync pytest -q"
NPM_COMMAND = "npm test"
NODE_COMMAND = "node --test"
# what `npm init` writes: a test script that only fails, so it counts as none
_NPM_PLACEHOLDER = re.compile(r"no test specified")

_TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__)/"
    r"|(^|/)test_[^/]*\.py$"
    r"|_test\.(py|go)$"
    r"|\.(test|spec)\.[cm]?[jt]sx?$"
)

# a command mission control proposes is model output built from untrusted repo text, and it ends
# up as `sh -c` in the gate and in the worker's shell allowlist - so a runner first, no shell syntax
_RUNNERS = frozenset(
    {"uv", "pytest", "python", "python3", "npm", "pnpm", "yarn", "node", "go", "cargo", "make"}
)
_SHELL_SYNTAX = re.compile(r"[;&|<>`$(){}\[\]\\'\"*?!#~\n]")


@dataclass(frozen=True)
class RepoTests:
    """what add-repo, preflight and mission control need: a command to run, and whether any test
    exists for it to find"""

    command: str | None
    has_tests: bool


def tracked_files(path: str, ref: str) -> list[str]:
    """every path committed on `ref`, or [] when git cannot say - one per line, so a path with a
    space in it stays one path"""
    try:
        out = subprocess.run(
            ["git", "-C", path, "ls-tree", "-r", "--name-only", ref],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return out.stdout.splitlines() if out.returncode == 0 else []


def has_tests(files: list[str]) -> bool:
    return any(_TEST_PATH.search(path) for path in files)


def _package_test_script(path: str, ref: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", path, "show", f"{ref}:package.json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        scripts = json.loads(out.stdout).get("scripts") or {}
    except (json.JSONDecodeError, AttributeError):
        return None
    script = scripts.get("test") if isinstance(scripts, dict) else None
    return script if isinstance(script, str) and not _NPM_PLACEHOLDER.search(script) else None


def detect_tests(path: str, ref: str, files: list[str] | None = None) -> RepoTests:
    """the test command for the repo's stack, and whether it has tests yet. python wins over node,
    as in repo_image.detect_stack; an unknown stack gets no command"""
    files = tracked_files(path, ref) if files is None else files
    root = {name for name in files if "/" not in name}
    if root & {"uv.lock", "pyproject.toml"}:
        command = PYTHON_COMMAND
    elif "package.json" in root:
        command = NPM_COMMAND if _package_test_script(path, ref) else NODE_COMMAND
    else:
        command = None
    return RepoTests(command=command, has_tests=has_tests(files))


def safe_test_command(command: str) -> bool:
    """a known test runner first and no shell syntax at all, 200 characters at most"""
    words = command.split()
    return (
        bool(words)
        and len(command) <= 200
        and words[0] in _RUNNERS
        and not _SHELL_SYNTAX.search(command)
    )
