"""keeps a card's branch mergeable against its base while its pull request waits.

Measured 2026-09-14 (card 59727ba3, PR #112): a branch cut once at the start of a run and never
updated drifted 34 commits behind main while its PR waited in checking, and conflicted in
smortboard/store/api.py, ui/settings.js, tests/js/settings.mjs and
tests/test_settings_and_decisions.py - all changed by other PRs merged while it waited.
Dependencies only order runs and leases only stop two running cards overlapping; neither keeps a
WAITING branch current. Run start and the waiting-PR sweep now rebase instead (rebase_guard.py);
this module keeps the non-destructive check and the real merge the hand-over, the landing and a
stacked child's retarget still use.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from smortboard.exec.worktrees import fetch_base, has_remote, repo_lock

# a merge check or attempt that has not answered in two minutes is a network or lock problem
MERGE_TIMEOUT_SECONDS = 120

# the ls-files-unmerged-style lines git merge-tree prints ahead of a conflict: mode, blob, stage,
# tab, path. only the path is wanted, and only once per file (three stages, one file)
_CONFLICT_LINE = re.compile(r"^\d+ [0-9a-f]+ [123]\t(?P<path>.+)$")


@dataclass
class MergeSyncResult:
    """what happened trying to bring a branch up to date with its base.

    `clean` true means the branch already includes base, or now does after `merge_branch`.
    `merged` true only when `merge_branch` actually made a merge commit - `check_mergeable` never
    sets it, since it touches nothing. `behind` distinguishes "nothing to do" from "behind, but a
    merge would land clean" - both report `clean=True`, and a caller deciding whether to spend a
    real merge on it needs to tell them apart. `conflicting_files` is set only on a real conflict.
    """

    clean: bool
    behind: bool = False
    merged: bool = False
    conflicting_files: list[str] = field(default_factory=list)


def _run(repo_path: str | Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
        timeout=MERGE_TIMEOUT_SECONDS,
        check=False,
    )


def _is_behind(repo_path: str | Path, branch: str, remote_ref: str) -> bool:
    ahead = _run(repo_path, "rev-list", "--count", f"{branch}..{remote_ref}")
    return ahead.returncode == 0 and ahead.stdout.strip() not in ("", "0")


def _parse_conflicts(output: str) -> list[str]:
    files: list[str] = []
    for line in output.splitlines():
        match = _CONFLICT_LINE.match(line)
        if match and match.group("path") not in files:
            files.append(match.group("path"))
    return files


def check_mergeable(repo_path: str | Path, branch: str, remote_ref: str) -> MergeSyncResult:
    """whether `branch` still merges cleanly with `remote_ref` (e.g. `origin/main`), without
    touching the worktree - `git merge-tree --write-tree` writes a tree object and reports, but
    never checks anything out. Safe to run against a worktree the sweep does not otherwise own.
    """
    if not _is_behind(repo_path, branch, remote_ref):
        return MergeSyncResult(clean=True, behind=False)
    result = _run(repo_path, "merge-tree", "--write-tree", branch, remote_ref)
    if result.returncode == 0:
        return MergeSyncResult(clean=True, behind=True)
    return MergeSyncResult(
        clean=False, behind=True, conflicting_files=_parse_conflicts(result.stdout)
    )


def merge_branch(repo_path: str | Path, remote_ref: str) -> MergeSyncResult:
    """merges `remote_ref` into whatever branch is checked out at `repo_path` - a card's own
    worktree, already on its branch. A clean merge is committed; a conflicting one is aborted
    immediately and the worktree is left exactly as it was, so the caller can block the card
    instead of opening or updating its pull request.
    """
    with repo_lock(repo_path):
        branch = _run(repo_path, "branch", "--show-current").stdout.strip()
        if not _is_behind(repo_path, branch, remote_ref):
            return MergeSyncResult(clean=True, behind=False)
        merge = _run(
            repo_path, "merge", "--no-edit", "-m", f"merged {remote_ref} into {branch}", remote_ref
        )
        if merge.returncode == 0:
            return MergeSyncResult(clean=True, behind=True, merged=True)
        conflicts = _run(repo_path, "diff", "--name-only", "--diff-filter=U")
        _run(repo_path, "merge", "--abort")
        files = sorted(f for f in conflicts.stdout.splitlines() if f.strip())
        return MergeSyncResult(clean=False, behind=True, conflicting_files=files)


def push_branch(repo_path: str | Path, branch: str, remote: str = "origin") -> bool:
    """pushes `branch` after a sweep merges base into it, so an open pull request stays current.

    Reuses `merge_request`'s protected-branch guard rather than a second copy of it - this is the
    same push it does after opening a pull request, just called again once main has moved.
    """
    from smortboard.review.merge_request import PROTECTED_BRANCHES

    if branch in PROTECTED_BRANCHES:
        return False
    refspec = f"refs/heads/{branch}:refs/heads/{branch}"
    with repo_lock(repo_path):
        result = _run(repo_path, "push", remote, refspec)
    return result.returncode == 0


def sync_with_base(
    repo_path: str | Path, base: str, remote: str = "origin"
) -> MergeSyncResult | None:
    """fetches `base` from `remote` and merges it into whatever branch is checked out, for real.

    None when there is no remote or the fetch failed - the caller falls back to proceeding as it
    would have before this module existed, same as `lifecycle._base_for_fresh_cut`.
    """
    if not has_remote(repo_path, remote):
        return None
    if not fetch_base(repo_path, base, remote=remote):
        return None
    return merge_branch(repo_path, f"{remote}/{base}")
