"""lands a finished card on its base - usually a repo's development branch.

The board lands each finished card itself, so the next card starts on top of it instead of every
card waiting on a human merge. a board's off-limit branches (main, master and trunk unless its list
says otherwise, Store.off_limit_branches) stay out of reach: `integrate` refuses them.

THE CARD'S OWN PULL REQUEST IS WHAT MERGES. `gh pr merge --merge --match-head-commit <tip>`: a
two-parent merge (one card stays revertable with `git revert -m 1`), refused by github if the head
moved after the gates passed, and accepted by a ruleset that only takes pull requests - a direct push
is not. The caller has merged the base into the branch and retested, and the base is fetched again
right before the merge, so the merged tree is the tested one. The one gap left: a push to the base
by something outside the landing lock in the seconds between that fetch and github's merge.
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


# github's answers that mean the base or the head moved since the sync - syncing again can help.
# anything else (a ruleset wanting checks, a token without merge rights) will not change on retry
_RETRYABLE = ("not mergeable", "head branch was modified", "not up to date", "is behind")


def integrate(
    tree_path: str | Path,
    branch: str,
    base: str,
    title: str,
    url: str | None,
    remote: str = "origin",
    off_limits: frozenset[str] = PROTECTED_BRANCHES,
) -> IntegrateResult:
    """merges the card's pull request `url` into base, pinned to the commit the gates passed.
    Never an off-limit base, never forced; the caller has merged base into `branch` and retested"""
    if base in off_limits:
        return IntegrateResult(None, f"{base} is off limits - merging into it is the operator's")
    if not url:
        return IntegrateResult(None, "there is no pull request to merge")
    base_ref = f"{remote}/{base}"
    # fetched now, so a base that moved since the sync is caught here rather than merged untested
    _git(tree_path, "fetch", remote, base)
    if _git(tree_path, "merge-base", "--is-ancestor", base_ref, branch).returncode != 0:
        return IntegrateResult(None, f"{branch} does not contain {base_ref} yet", moved=True)
    # the pull request's head follows the branch, so the merged commits are the reviewed ones
    if not push_branch(tree_path, branch, remote):
        return IntegrateResult(None, f"pushing {branch} failed")

    tip = _git(tree_path, "rev-parse", branch).stdout.strip()
    # --match-head-commit: github refuses if anything reached the head after the gates passed
    merged = _gh(
        [
            "pr",
            "merge",
            url,
            "--merge",
            "--match-head-commit",
            tip,
            "--subject",
            f"merged {branch} into {base}: {title}",
        ],
        cwd=tree_path,
    )
    if merged.returncode != 0:
        said = (merged.stderr or merged.stdout).strip()
        retry = any(phrase in said.lower() for phrase in _RETRYABLE)
        return IntegrateResult(None, f"github refused the merge: {said}", moved=retry)

    _git(tree_path, "fetch", remote, base)
    sha = _git(tree_path, "rev-parse", base_ref).stdout.strip()
    if _git(tree_path, "merge-base", "--is-ancestor", tip, base_ref).returncode != 0:
        return IntegrateResult(None, f"github said merged, but {base_ref} does not hold {tip[:10]}")
    return IntegrateResult(sha)


def open_release_request(repo_path: str | Path, base: str, into: str = "main") -> str | None:
    """the one standing pull request from base into main, opened if there is none. Merging it is
    the operator's; this only makes sure there is something to merge. None when gh cannot say"""
    if base == into:
        return None  # a card landed on main itself - there is nothing left to release
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
            f"every card the board merged into {base} since {into} last took it. each card's own "
            f"pull request is linked from its merge commit on {base}.",
        ],
        cwd=repo_path,
    )
    if created.returncode != 0:
        return None
    return next((t for t in created.stdout.split() if t.startswith("https://")), None)
