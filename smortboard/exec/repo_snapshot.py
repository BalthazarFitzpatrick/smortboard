"""read-only clones and operator paths for one mission-control turn.

Mission control plans against files it should be able to read but never change. So per turn it gets
a fresh shallow clone of each board repo, plus any extra host paths the operator named, all mounted
read-only and torn down when the turn ends.

The live checkout is never mounted: it holds card worktrees (under .claude/worktrees, or beside it in the top-dir layout) and lease files,
none of which mission control should see. A `git clone --depth 1 --single-branch --branch <default>`
carries only the default branch's committed tree - no card/* branches and no worktree dirs.
"""

from __future__ import annotations

import subprocess
import tempfile
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smortboard.docker_paths import mount_arg
from smortboard.exec.worktrees import fast_forward_base, fetch_base, has_remote
from smortboard.fsutil import remove_tree

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
    # container path -> host path for every mount above, so an open-mode turn can be told the real
    # paths instead of the container ones
    host_paths: dict[str, str] = field(default_factory=dict)

    def cleanup(self) -> None:
        if self._tmp_dir is not None:
            remove_tree(self._tmp_dir)
            self._tmp_dir = None


def _clone_repo(source: str, branch: str, dest: Path) -> None:
    """a shallow single-branch clone: no history, no other branches, no worktrees

    --no-local copies objects instead of hardlinking them, so the clone shares none with the source
    """
    result = subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--no-local",
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


def _refresh_base(path: str, branch: str) -> None:
    """fetch the base, then fast-forward the local branch when nothing local is in the way"""
    if has_remote(path) and fetch_base(path, branch):
        fast_forward_base(path, branch)


def build_repo_snapshot(
    repos: Iterable[dict[str, Any]],
    extra_paths: Iterable[str],
    cloner: Callable[[str, str, Path], None] | None = None,
    refresher: Callable[[str, str], Any] | None = None,
) -> RepoSnapshot:
    """clones each repo read-only into a temp dir and resolves the operator's extra read paths.

    A repo that will not clone, or an extra path that does not exist, is skipped with a warning for
    the board rather than crashing the turn. `cloner` is injected in tests so no real git runs.
    """
    clone = cloner or _clone_repo
    refresh = refresher or _refresh_base
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"smortboard-mc-{uuid.uuid4().hex[:8]}-"))
    mount_args: list[str] = []
    warnings: list[str] = []
    used_targets: set[str] = set()
    host_paths: dict[str, str] = {}

    for repo in repos:
        name = repo["name"]
        # a repo name reaches both the clone destination and the mount target, so a name carrying a
        # separator or ".." could escape the temp dir - skip it with a message rather than traverse
        if name != Path(name).name or name in {"", ".", ".."}:
            warnings.append(f'the repo "{name}" has an unsafe name and was not cloned for reading')
            continue
        dest = tmp_dir / name
        # the clone reads the folder's local branch, which landing never moved - bring it up to
        # origin first, or mission control plans against the base from before every landing
        refresh(repo["path"], repo["default_branch"])
        try:
            clone(repo["path"], repo["default_branch"], dest)
        except RepoSnapshotError as exc:
            warnings.append(f'the repo "{name}" could not be cloned for reading: {exc}')
            continue
        mount_args += ["-v", mount_arg(dest, f"{REPOS_MOUNT}/{name}", "ro")]
        host_paths[f"{REPOS_MOUNT}/{name}"] = str(dest)

    for raw in extra_paths:
        path = Path(raw)
        if not path.exists():
            warnings.append(
                f'the read path "{raw}" does not exist, so mission control was not given it'
            )
            continue
        # two paths can share a basename (/a/screens, /b/screens) - keep both by disambiguating the
        # second, so one mount never silently overwrites the other
        target = _unique_target(path.name, used_targets)
        mount_args += ["-v", mount_arg(path, f"{EXTRA_MOUNT}/{target}", "ro")]
        host_paths[f"{EXTRA_MOUNT}/{target}"] = str(path)

    return RepoSnapshot(
        mount_args=mount_args, warnings=warnings, _tmp_dir=tmp_dir, host_paths=host_paths
    )


def _unique_target(base: str, used: set[str]) -> str:
    """a mount basename not yet used this turn, suffixing -2, -3 ... on a collision"""
    candidate = base
    n = 2
    while candidate in used:
        candidate = f"{base}-{n}"
        n += 1
    used.add(candidate)
    return candidate


def to_host_paths(text: str, host_paths: dict[str, str]) -> str:
    """`text` with each container mount path replaced by its real host path, longest first so
    /extra/shots is not eaten by /extra. an open-mode turn is told where things really are"""
    for container in sorted(host_paths, key=len, reverse=True):
        text = text.replace(container, host_paths[container])
    return text
