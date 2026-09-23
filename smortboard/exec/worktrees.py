"""cuts, tracks and destroys a git worktree + branch per card

follows the convention the operator's other repos already run by hand: worktrees live under the repo's
`.claude/worktrees/<name>`. S1/S2 showed a card left on main either names its own branch or
deadlocks asking permission to make one, so the worktree handed back is already checked out
on the card's branch — never on `base`.
"""

import contextlib
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


class WorktreeError(Exception):
    """a git worktree/branch operation failed"""


_repo_locks: dict[Path, threading.Lock] = {}
_repo_locks_guard = threading.Lock()


@contextlib.contextmanager
def repo_lock(repo_path: str | Path) -> Iterator[None]:
    """one git write per repo at a time. git fails a held .git/config or ref lock instead of
    waiting, and two cards starting together hit it - measured, 6 of 12 concurrent cuts failed"""
    key = Path(repo_path).resolve()
    with _repo_locks_guard:
        lock = _repo_locks.setdefault(key, threading.Lock())
    with lock:
        yield


@dataclass(frozen=True)
class WorktreeInfo:
    card_id: str
    path: Path
    branch: str
    base_commit: str | None = None


def _worktree_root(repo_path: Path) -> Path:
    return repo_path / ".claude" / "worktrees"


def branch_name(card_id: str) -> str:
    return f"card/{card_id}"


def default_branch(repo: dict) -> str:
    """the repo's own base branch, or "main" - one place for this so a repo pointed at, say,
    "development" is read the same way everywhere: lifecycle, scheduler's sweep, decide.py's
    rejection diff. never hard-code "main" where a repo row is already in hand."""
    return repo.get("default_branch") or "main"


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
    # -b creates the branch and checks the new worktree out onto it in one step. --no-track: a
    # card branch cut from origin/<base> would otherwise track the base; its push sets its own
    with repo_lock(repo_path):
        _run_git(repo_path, "worktree", "add", "--no-track", "-b", branch, str(path), base)
        base_commit = _run_git(path, "rev-parse", "HEAD").stdout.strip()
    return WorktreeInfo(card_id=card_id, path=path, branch=branch, base_commit=base_commit)


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
    with repo_lock(repo_path):
        _run_git(repo_path, "worktree", "add", str(path), branch)
    return WorktreeInfo(card_id=card_id, path=path, branch=branch)


def destroy_worktree(repo_path: str | Path, card_id: str, force: bool = False) -> None:
    """removes the card's worktree; the branch itself is left for review/rejection to handle"""
    repo_path = Path(repo_path).resolve()
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(worktree_path(repo_path, card_id)))
    with repo_lock(repo_path):
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


# a fetch holds repo_lock, so a hung remote used to stall every card on the repo behind it
GIT_FETCH_TIMEOUT_SECONDS = 60


def _fetch(repo_path: str | Path, *args: str) -> subprocess.CompletedProcess:
    """one `git fetch` under repo_lock, killed after GIT_FETCH_TIMEOUT_SECONDS. a timeout raises
    WorktreeError; the lock is released either way"""
    with repo_lock(repo_path):
        try:
            return subprocess.run(
                ["git", "-C", str(Path(repo_path).resolve()), "fetch", *args],
                capture_output=True,
                text=True,
                timeout=GIT_FETCH_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorktreeError(
                f"git fetch {' '.join(args)} did not finish within {GIT_FETCH_TIMEOUT_SECONDS}s"
            ) from exc


def fetch_base(repo_path: str | Path, base: str, remote: str = "origin") -> bool:
    """fetches `base` from `remote`. True on success - never raises, so a network hiccup is the
    caller's decision (fall back to the local base) rather than a card-stopping error. A remote
    that hangs past GIT_FETCH_TIMEOUT_SECONDS is the same hiccup."""
    try:
        result = _fetch(repo_path, remote, base)
    except WorktreeError:
        return False
    return result.returncode == 0


def branch_exists(repo_path: str | Path, card_id: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", "--quiet", branch_name(card_id)],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def branch_diverged_from_origin(
    repo_path: str | Path, branch: str, remote: str = "origin"
) -> bool | None:
    """true if `origin/<branch>` holds a commit the local branch does not - someone else pushed to
    this card's branch since the board's own worktree last saw it.

    Found for real: a worktree held one commit while origin had two newer ones, and the board only
    discovered it when its own (deliberately never-forced) push failed at the very end of a run -
    after the worker, the gates and the reviewer had already spent their turn on work that could
    never land. This lets a caller check before reusing an on-disk worktree instead.

    None when nothing to compare: no remote, the branch was never pushed (normal for a card whose
    first run has not reached a push yet), or the fetch itself failed or timed out - never blocks
    resuming because a compare could not be made, only because one WAS made and found a real
    divergence.
    """
    try:
        fetch = _fetch(repo_path, remote, branch)
    except WorktreeError:
        return None
    if fetch.returncode != 0:
        return None
    is_ancestor = subprocess.run(
        [
            "git",
            "-C",
            str(Path(repo_path).resolve()),
            "merge-base",
            "--is-ancestor",
            f"{remote}/{branch}",
            branch,
        ],
        capture_output=True,
        check=False,
    )
    if is_ancestor.returncode not in (0, 1):
        return None
    return is_ancestor.returncode != 0


def rev_parse(repo_path: str | Path, ref: str) -> str | None:
    """the commit `ref` points at, or None when it does not resolve"""
    result = subprocess.run(
        ["git", "-C", str(Path(repo_path).resolve()), "rev-parse", "--verify", "--quiet", ref],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or None if result.returncode == 0 else None


def delete_branch(repo_path: str | Path, card_id: str) -> None:
    """drops the card's local branch. -D because a rejected attempt is never merged anywhere"""
    with repo_lock(repo_path):
        _run_git(Path(repo_path).resolve(), "branch", "-D", branch_name(card_id))


def _merge_base(repo_path: str | Path, ref: str, branch: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "merge-base", ref, branch],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or None if result.returncode == 0 else None


def _is_ancestor(repo_path: str | Path, older: str, newer: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "merge-base", "--is-ancestor", older, newer],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _diff_start(repo_path: str | Path, base: str, branch: str) -> str:
    """where the card's own changes begin: the newer of its merge bases with `base` and
    `origin/<base>`.

    Measured with a test: a reused branch merges origin/<base> before its worker runs, and when
    the local base lagged origin, the merge base with the local base sat below every upstream
    commit merged in - so they all read as the card's work in the diff the reviewer judged.
    """
    refs = [base] if base.startswith("origin/") else [base, f"origin/{base}"]
    starts = [start for ref in refs if (start := _merge_base(repo_path, ref, branch))]
    if not starts:
        return base
    newest = starts[0]
    for start in starts[1:]:
        if _is_ancestor(repo_path, newest, start):
            newest = start
    return newest


def _glob_pathspecs(patterns: tuple[str, ...], magic: str) -> list[str]:
    return [f":({magic})**/{pattern}" for pattern in patterns]


def branch_diff(
    repo_path: str | Path, base: str, branch: str, exclude: tuple[str, ...] = ()
) -> str:
    """what the card actually changed - what the reviewer reads and what a rejection keeps.

    Against the merge base, so work that landed on `base` (or on origin/<base>, once merged into
    the branch) while the card was running does not show up as something the card did.
    `exclude` holds file globs matched at any depth, e.g. "uv.lock" or "*.min.js".
    """
    start = _diff_start(repo_path, base, branch)
    pathspecs = ["--", *_glob_pathspecs(exclude, "exclude,glob")] if exclude else []
    result = subprocess.run(
        ["git", "-C", str(repo_path), "diff", start, branch, *pathspecs],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else ""


def branch_changed_paths(
    repo_path: str | Path, base: str, branch: str, patterns: tuple[str, ...]
) -> list[str]:
    """the paths matching `patterns` (globs, any depth) that the card changed - the other half of
    branch_diff's `exclude`, so what was left out can still be named"""
    if not patterns:
        return []
    start = _diff_start(repo_path, base, branch)
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo_path),
            "diff",
            "--name-only",
            start,
            branch,
            "--",
            *_glob_pathspecs(patterns, "glob"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return [line for line in result.stdout.splitlines() if line] if result.returncode == 0 else []


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
