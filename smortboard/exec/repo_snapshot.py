"""read-only clones and operator paths for one mission-control turn.

Mission control plans against files it should be able to read but never change. So per turn it gets
a fresh shallow clone of each board repo, plus any extra host paths the operator named, all mounted
read-only and torn down when the turn ends.

The live checkout is never mounted: it holds card worktrees under .claude/worktrees and lease files,
none of which mission control should see. A `git clone --depth 1 --single-branch --branch <default>`
carries only the default branch's committed tree - no card/* branches and no worktree dirs.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# where clones and operator paths land inside the container - the prompt names only these, never a
# host path
REPOS_MOUNT = "/repos"
EXTRA_MOUNT = "/extra"
# the working dir is the parent of every mount and is not itself a repo, so build_command's
# `--setting-sources project` finds no cloned .claude/settings.json to apply
MOUNT_PARENT = "/"


class RepoSnapshotError(RuntimeError):
    """a repo could not be cloned for a mission-control turn"""


@dataclass
class RepoSnapshot:
    """the docker -v args for a turn's read-only mounts, plus any board messages the build produced.

    `cleanup()` removes the temp dir holding the clones and must be called once the turn is done.
    """

    mount_args: list[str]
    warnings: list[str]
    _tmp_dir: Path | None = None

    def cleanup(self) -> None:
        if self._tmp_dir is not None:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None


def _clone_repo(source: str, branch: str, dest: Path) -> None:
    """a shallow single-branch clone: no history, no other branches, no worktrees"""
    result = subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--single-branch",
            "--branch",
            branch,
            source,
            str(dest),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RepoSnapshotError(result.stderr.strip())


def build_repo_snapshot(
    repos: Iterable[dict[str, Any]],
    extra_paths: Iterable[str],
    cloner: Callable[[str, str, Path], None] | None = None,
) -> RepoSnapshot:
    """clones each repo read-only into a temp dir and resolves the operator's extra read paths.

    A repo that will not clone, or an extra path that does not exist, is skipped with a warning for
    the board rather than crashing the turn. `cloner` is injected in tests so no real git runs.
    """
    clone = cloner or _clone_repo
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"smortboard-mc-{uuid.uuid4().hex[:8]}-"))
    mount_args: list[str] = []
    warnings: list[str] = []

    for repo in repos:
        name = repo["name"]
        dest = tmp_dir / name
        try:
            clone(repo["path"], repo["default_branch"], dest)
        except RepoSnapshotError as exc:
            warnings.append(f'the repo "{name}" could not be cloned for reading: {exc}')
            continue
        mount_args += ["-v", f"{dest}:{REPOS_MOUNT}/{name}:ro"]

    for raw in extra_paths:
        path = Path(raw)
        if not path.exists():
            warnings.append(
                f'the read path "{raw}" does not exist, so mission control was not given it'
            )
            continue
        mount_args += ["-v", f"{path}:{EXTRA_MOUNT}/{path.name}:ro"]

    return RepoSnapshot(mount_args=mount_args, warnings=warnings, _tmp_dir=tmp_dir)
