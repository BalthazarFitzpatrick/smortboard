"""the operator's github repos and a clone of one, behind the boards panel's "from online repo"

every call goes through gh, so the login the operator already made is what authorises it - the
board holds no github credential of its own. nothing here writes a board: the caller hands the
fresh clone to the same setup and registration a local folder gets
"""

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

_LIST_LIMIT = 200
_LIST_TIMEOUT_S = 30
_CLONE_TIMEOUT_S = 600
_NAME_WITH_OWNER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$")

Runner = Callable[..., subprocess.CompletedProcess]


def default_runner(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    # a gh that cannot start or outlives its timeout is a failed call, not an exception
    try:
        return subprocess.run(cmd, **kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr=str(exc))


def _require_gh(run: Runner) -> None:
    if shutil.which("gh") is None:
        raise ValueError(
            "gh is not installed. Install the GitHub CLI (https://cli.github.com), then "
            "`gh auth login`."
        )
    status = run(["gh", "auth", "status"], capture_output=True, text=True, timeout=10, check=False)
    if status.returncode != 0:
        raise ValueError("gh is not logged in. Run `gh auth login`, then try again.")


def list_online_repos(run: Runner = default_runner) -> list[dict]:
    """the repos the logged-in account can see, newest activity first (gh's own order)"""
    _require_gh(run)
    result = run(
        [
            "gh",
            "repo",
            "list",
            "--limit",
            str(_LIST_LIMIT),
            "--json",
            "nameWithOwner,isPrivate,description",
        ],
        capture_output=True,
        text=True,
        timeout=_LIST_TIMEOUT_S,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"gh could not list your repos: {result.stderr.strip() or 'no output'}")
    try:
        rows = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError("gh returned a repo list that could not be read") from exc
    return [
        {
            "name_with_owner": row["nameWithOwner"],
            "private": bool(row.get("isPrivate")),
            "description": row.get("description") or "",
        }
        for row in rows
    ]


def clone_online_repo(name_with_owner: str, folder: str, run: Runner = default_runner) -> str:
    """clones `owner/name` into `folder/name` and returns that path.

    Refuses before running anything when the name is not a plain owner/name, the folder is not a
    folder, or the target already exists - a clone never lands on top of something.
    """
    # "." and ".." fit the name pattern but resolve to the folder itself or its parent
    if not _NAME_WITH_OWNER.match(name_with_owner or "") or name_with_owner.endswith(("/.", "/..")):
        raise ValueError(f"not a github owner/name: {name_with_owner!r}")
    parent = Path(folder or "").expanduser()
    # an empty or relative folder would resolve against the server's own working directory
    if not parent.is_absolute():
        raise ValueError("folder must be an absolute folder path")
    if not parent.is_dir():
        raise ValueError(f"not a folder: {parent}")
    target = parent.resolve() / name_with_owner.split("/", 1)[1]
    if target.exists():
        raise ValueError(
            f"{target} already exists. Pick a different folder, or use 'new board' on it "
            "if it is already a clone."
        )
    _require_gh(run)
    result = run(
        ["gh", "repo", "clone", name_with_owner, str(target)],
        capture_output=True,
        text=True,
        timeout=_CLONE_TIMEOUT_S,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"the clone failed: {result.stderr.strip() or 'gh printed nothing'}")
    return str(target)
