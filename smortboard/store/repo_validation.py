"""registering a repo on a board: checks a person can act on before the row is ever written

path expands ~, must exist, must be a git repository, and default_branch must exist in it - all
checked with plain git subprocess calls rather than a library, since the rest of the store has no
git dependency
"""

import subprocess
from pathlib import Path


def validate_repo(name: str, path: str, default_branch: str) -> str:
    """raises ValueError with an actionable message, or returns the expanded, resolved path"""
    if not name or not name.strip():
        raise ValueError("name must not be empty")
    if not default_branch or not default_branch.strip():
        raise ValueError("default_branch must not be empty")

    expanded = Path(path).expanduser()
    if not expanded.exists():
        raise ValueError(f"path does not exist: {expanded}")
    if not expanded.is_dir():
        raise ValueError(f"path is not a directory: {expanded}")

    try:
        result = subprocess.run(
            ["git", "-C", str(expanded), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except OSError as exc:
        raise ValueError(f"could not run git: {exc}") from exc
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise ValueError(f"not a git repository: {expanded}")

    try:
        branches = subprocess.run(
            ["git", "-C", str(expanded), "branch", "--list", default_branch],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except OSError as exc:
        raise ValueError(f"could not inspect branches: {exc}") from exc
    if not branches.stdout.strip():
        raise ValueError(f"branch {default_branch!r} does not exist in {expanded}")

    return str(expanded)
