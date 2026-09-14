"""lands a finished card on a base that is not protected - a repo's development branch.

The operator runs off main and merges development into main when they choose; the board lands each
finished card on development itself, so the next card starts on top of it instead of every card
waiting on a human merge. main, master and trunk stay out of reach: `integrate` refuses them the
same way merge_request._push does, and `gh pr merge` is still not on merge_request's allowlist.

THE MERGE IS BUILT WITHOUT A CHECKOUT. The caller has already merged the base into the card branch
(and rerun the tests when that brought anything in), so the merged tree IS the card's tree: `git
commit-tree` makes the two-parent commit and a plain push of it fast-forwards the base. Two parents
keep one card revertable with `git revert -m 1` after others have built on it. A base that moved
between the fetch and the push is a rejected push, never an overwrite - the caller syncs again.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from smortboard.review.merge_request import PROTECTED_BRANCHES, _gh
from smortboard.review.mergeable import push_branch

# a push that has not answered in two minutes is a network problem, not slow work
INTEGRATE_TIMEOUT_SECONDS = 120

# one card lands on a given base at a time. not repo_lock: that one guards worktree cuts, and a
# card holding it through a test rerun would stall every other card's start for minutes
_integration_locks: dict[tuple[Path, str], threading.Lock] = {}
_integration_guard = threading.Lock()


@contextmanager
def integration_lock(repo_path: str | Path, base: str) -> Iterator[None]:
    key = (Path(repo_path).resolve(), base)
    with _integration_guard:
        lock = _integration_locks.setdefault(key, threading.Lock())
    with lock:
        yield


@dataclass
class IntegrateResult:
    """`sha` is the merge commit now on the base; otherwise `reason` says why not, and `moved`
    says whether syncing again and retrying could help"""

    sha: str | None
    reason: str | None = None
    moved: bool = False


def _git(path: str | Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        timeout=INTEGRATE_TIMEOUT_SECONDS,
        check=False,
    )


def integrate(
    tree_path: str | Path, branch: str, base: str, title: str, remote: str = "origin"
) -> IntegrateResult:
    """merges `branch` (checked out at `tree_path`, already containing `remote`/`base`) into base
    as one merge commit and pushes it. Never touches a protected base, never forces"""
    if base in PROTECTED_BRANCHES:
        return IntegrateResult(None, f"{base} is protected - merging into it is the operator's")
    base_ref = f"{remote}/{base}"
    if _git(tree_path, "merge-base", "--is-ancestor", base_ref, branch).returncode != 0:
        return IntegrateResult(None, f"{branch} does not contain {base_ref} yet", moved=True)
    # the pull request's head follows the branch, so the merged commits are the reviewed ones
    if not push_branch(tree_path, branch, remote):
        return IntegrateResult(None, f"pushing {branch} failed")

    tip = _git(tree_path, "rev-parse", branch).stdout.strip()
    base_tip = _git(tree_path, "rev-parse", base_ref).stdout.strip()
    tree = _git(tree_path, "rev-parse", f"{branch}^{{tree}}").stdout.strip()
    made = _git(
        tree_path,
        "commit-tree",
        tree,
        "-p",
        base_tip,
        "-p",
        tip,
        "-m",
        f"merged {branch} into {base}: {title}",
    )
    if made.returncode != 0:
        return IntegrateResult(None, f"could not make the merge commit: {made.stderr.strip()}")
    sha = made.stdout.strip()

    pushed = _git(tree_path, "push", remote, f"{sha}:refs/heads/{base}")
    if pushed.returncode != 0:
        # rejected as not a fast-forward: another card landed first, so sync and try again
        return IntegrateResult(None, f"pushing {base} failed: {pushed.stderr.strip()}", moved=True)
    return IntegrateResult(sha)


def open_release_request(repo_path: str | Path, base: str, into: str = "main") -> str | None:
    """the one standing pull request from base into main, opened if there is none. Merging it is
    the operator's; this only makes sure there is something to merge. None when gh cannot say"""
    listed = _gh(
        [
            "pr",
            "list",
            "--base",
            into,
            "--head",
            base,
            "--state",
            "open",
            "--json",
            "url",
            "--jq",
            ".[0].url // empty",
        ],
        cwd=repo_path,
    )
    if listed.returncode == 0 and listed.stdout.strip():
        return listed.stdout.strip()
    created = _gh(
        [
            "pr",
            "create",
            "--base",
            into,
            "--head",
            base,
            "--title",
            f"{base} into {into}",
            "--body",
            f"Every card the board merged into {base} since {into} last took it. Each card's own "
            f"pull request is linked from its merge commit on {base}.",
        ],
        cwd=repo_path,
    )
    if created.returncode != 0:
        return None
    return next((t for t in created.stdout.split() if t.startswith("https://")), None)
