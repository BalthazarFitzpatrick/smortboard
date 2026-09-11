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


def branch_name(card_id: str) -> str:
    return f"card/{card_id}"


def worktree_path(repo_path: str | Path, card_id: str) -> Path:
    return _worktree_root(Path(repo_path).resolve()) / card_id


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
    branch = branch_name(card_id)
    path = worktree_path(repo_path, card_id)
    if path.exists():
        raise WorktreeError(f"worktree already exists at {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    # -b creates the branch and checks the new worktree out onto it in one step
    _run_git(repo_path, "worktree", "add", "-b", branch, str(path), base)
    return WorktreeInfo(card_id=card_id, path=path, branch=branch)


def existing_worktree(repo_path: str | Path, card_id: str) -> WorktreeInfo:
    """the worktree already cut for this card, as-is - the resume path when a blocked card's
    earlier worktree is still on disk, prior commits and all"""
    repo_path = Path(repo_path).resolve()
    return WorktreeInfo(
        card_id=card_id, path=worktree_path(repo_path, card_id), branch=branch_name(card_id)
    )


def add_worktree(repo_path: str | Path, card_id: str) -> WorktreeInfo:
    """adds a worktree onto the card's branch, which already exists but has no worktree checked
    out on it - the resume path for a blocked card whose earlier worktree was cleaned up"""
    repo_path = Path(repo_path).resolve()
    branch = branch_name(card_id)
    path = worktree_path(repo_path, card_id)
    if path.exists():
        raise WorktreeError(f"worktree already exists at {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    _run_git(repo_path, "worktree", "add", str(path), branch)
    return WorktreeInfo(card_id=card_id, path=path, branch=branch)


def destroy_worktree(repo_path: str | Path, card_id: str, force: bool = False) -> None:
    """removes the card's worktree; the branch itself is left for review/rejection to handle"""
    repo_path = Path(repo_path).resolve()
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(worktree_path(repo_path, card_id)))
    _run_git(repo_path, *args)


def has_remote(repo_path: str | Path, remote: str = "origin") -> bool:
    """whether this repo has the named remote at all - a local-only repo has nowhere to fetch a
    merged dependency from, so the caller falls back to the local base without trying."""
    result = subprocess.run(
        ["git", "-C", str(Path(repo_path).resolve()), "remote"],
        capture_output=True,
        text=True,
        check=False,
    )
    return remote in result.stdout.split()


def fetch_base(repo_path: str | Path, base: str, remote: str = "origin") -> bool:
    """fetches `base` from `remote`. True on success - never raises, so a network hiccup is the
    caller's decision (fall back to the local base) rather than a card-stopping error."""
    result = subprocess.run(
        ["git", "-C", str(Path(repo_path).resolve()), "fetch", remote, base],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def branch_exists(repo_path: str | Path, card_id: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", "--quiet", branch_name(card_id)],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def delete_branch(repo_path: str | Path, card_id: str) -> None:
    """drops the card's local branch. -D because a rejected attempt is never merged anywhere"""
    _run_git(Path(repo_path).resolve(), "branch", "-D", branch_name(card_id))


def branch_diff(repo_path: str | Path, base: str, branch: str) -> str:
    """what the card actually changed - what the reviewer reads and what a rejection keeps.

    Three dots: the diff against the merge base, so work that landed on `base` while the card was
    running does not show up as something the card did.
    """
    result = subprocess.run(
        ["git", "-C", str(repo_path), "diff", f"{base}...{branch}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else ""


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
