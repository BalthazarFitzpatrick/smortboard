"""local folders a person can pick a repo from, and the one fact the board needs from a picked repo

read-only helpers behind the boards panel's "new board" folder picker. the server binds 127.0.0.1,
so listing folders under the operator's home only ever shows them their own disk
"""

import subprocess
from pathlib import Path


def list_folders(under: str | None = None) -> dict:
    """the folders directly under `under` (default: home), each marked when it is a git repo"""
    here = (Path(under).expanduser() if under else Path.home()).resolve()
    if not here.is_dir():
        raise ValueError(f"not a folder: {here}")
    try:
        children = sorted(here.iterdir(), key=lambda p: p.name.lower())
    except OSError as exc:
        raise ValueError(f"cannot open {here}: {exc}") from exc
    folders = []
    for child in children:
        # hidden folders stay hidden, like a file browser; a repo inside one is still reachable
        if child.name.startswith(".") or not child.is_dir():
            continue
        folders.append({"name": child.name, "path": str(child), "repo": (child / ".git").exists()})
    parent = str(here.parent) if here.parent != here else None
    return {
        "here": str(here),
        "parent": parent,
        "repo": (here / ".git").exists(),
        "folders": folders,
    }


def origin_url(path: str) -> str | None:
    """where the repo pushes - None when it has no origin or git cannot say"""
    try:
        result = subprocess.run(
            ["git", "-C", path, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def detect_default_branch(path: str) -> str:
    """origin's HEAD when the clone knows it, else the branch that is checked out"""
    for args in (
        ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        ["branch", "--show-current"],
    ):
        try:
            result = subprocess.run(
                ["git", "-C", path, *args], capture_output=True, text=True, timeout=5
            )
        except OSError as exc:
            raise ValueError(f"could not run git: {exc}") from exc
        name = result.stdout.strip()
        if result.returncode == 0 and name:
            return name.removeprefix("origin/")
    raise ValueError(f"no default branch found in {path}")
