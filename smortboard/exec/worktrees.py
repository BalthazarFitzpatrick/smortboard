"""cuts, tracks and destroys a git worktree + branch per card

follows the convention consumer-app already runs by hand: worktrees live under the repo's
`.claude/worktrees/<name>`. S1/S2 showed a card left on main either names its own branch or
deadlocks asking permission to make one, so the worktree handed back is already checked out
on the card's branch — never on `base`.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path


class WorktreeError(Exception):
    """a git worktree/branch operation failed"""


@dataclass(frozen=True)
class WorktreeInfo:
    card_id: str
    path: Path
    branch: str


def _worktree_root(repo_path: Path) -> Path:
    return repo_path / ".claude" / "worktrees"


def _branch_name(card_id: str) -> str:
    return f"card/{card_id}"


def _run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise WorktreeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def create_worktree(repo_path: str | Path, card_id: str, base: str = "main") -> WorktreeInfo:
    """cuts a worktree + branch for card_id off base, and returns it already on that branch"""
    repo_path = Path(repo_path).resolve()
    branch = _branch_name(card_id)
    worktree_path = _worktree_root(repo_path) / card_id
    if worktree_path.exists():
        raise WorktreeError(f"worktree already exists at {worktree_path}")
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    # -b creates the branch and checks the new worktree out onto it in one step
    _run_git(repo_path, "worktree", "add", "-b", branch, str(worktree_path), base)
    return WorktreeInfo(card_id=card_id, path=worktree_path, branch=branch)


def destroy_worktree(repo_path: str | Path, card_id: str, force: bool = False) -> None:
    """removes the card's worktree; the branch itself is left for review/rejection to handle"""
    repo_path = Path(repo_path).resolve()
    worktree_path = _worktree_root(repo_path) / card_id
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(worktree_path))
    _run_git(repo_path, *args)


def repo_root_of_worktree(worktree_path: str | Path) -> Path:
    """resolves the main repo a card worktree belongs to.

    a worktree's `.git` is a pointer file into the parent repo's `.git/worktrees/<name>` - this
    walks that indirection with `git rev-parse` rather than assuming a fixed directory shape, so
    the container backend can fetch the card's commits back into the repo that owns them.
    """
    worktree_path = Path(worktree_path).resolve()
    result = subprocess.run(
        [
            "git",
            "-C",
            str(worktree_path),
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise WorktreeError(
            f"could not resolve repo root for {worktree_path}: {result.stderr.strip()}"
        )
    common_dir = Path(result.stdout.strip())
    # a non-bare repo's common git dir is "<repo_root>/.git"
    return common_dir.parent


def current_branch(worktree_path: str | Path) -> str:
    """the branch a worktree is checked out on - S1/S3 require this to already be the card's
    branch, never `base`, so callers can trust it rather than re-deriving the name themselves"""
    result = _run_git(Path(worktree_path).resolve(), "branch", "--show-current")
    return result.stdout.strip()


def list_worktrees(repo_path: str | Path) -> list[WorktreeInfo]:
    """lists worktrees under the card convention — plain `git worktree list` includes the main
    checkout too, which is not a card worktree, so it is filtered out here"""
    repo_path = Path(repo_path).resolve()
    root = _worktree_root(repo_path)
    result = _run_git(repo_path, "worktree", "list", "--porcelain")
    infos = []
    path: Path | None = None
    branch: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree "))
        elif line.startswith("branch "):
            branch = line.removeprefix("branch ").removeprefix("refs/heads/")
        elif line == "" and path is not None:
            if root in path.parents:
                infos.append(WorktreeInfo(card_id=path.name, path=path, branch=branch or ""))
            path, branch = None, None
    if path is not None and root in path.parents:
        infos.append(WorktreeInfo(card_id=path.name, path=path, branch=branch or ""))
    return infos
