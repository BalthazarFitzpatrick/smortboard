"""cuts, tracks and destroys a git worktree + branch per card

follows the convention wowtomate already runs by hand: worktrees live under the repo's
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
