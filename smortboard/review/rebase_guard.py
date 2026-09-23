"""replays a card's own commits onto a base that moved under it, in a fresh tree.

The operator, 2026-09-23: people also work outside the board, so a card's base moves while the card
waits. When origin/<base> has commits the card branch lacks, the card's own commits are rebased onto
it in a throwaway worktree - never the card's own worktree, which a run may be using - and the card
branch only moves once that rebase is clean. A branch already on origin (a waiting pull request) is
then pushed with a lease on the tip the board last saw, so someone else's push is never overwritten.

A rebase that conflicts means the card is outdated: its branch stays exactly as it was and the card
blocks OUTDATED with the files named. Run start (lifecycle.py) and the checking-PR sweep
(scheduler.py) both go through rebase_onto_base, so the two cannot drift.

Answering an OUTDATED card (attention.answer_card) goes through reset_to_base: the old tip is kept
on refs/smortboard/outdated/<card-id>/<stamp>, and the branch restarts at the current base for the
worker to redo the change. The card's old branch on origin is then superseded, not someone else's
work - see remote_superseded and push_over_superseded.

STACKS. A stacked child (review/stacks.py) is never passed here while its parent is unlanded. Its
base is the parent's branch, which the board owns and keeps current as an ordinary card. Rebasing
the child onto origin/<base> would replay the parent's unlanded commits as the child's own, and
rebasing it onto a rewritten parent needs the parent's old tip to find the child's own commits.
Once the parent lands, retarget_children moves the child onto the base and it is guarded like any
other card.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smortboard.actions import with_next
from smortboard.exec.worktrees import (
    WorktreeError,
    _fetch,
    add_worktree,
    branch_exists,
    branch_name,
    destroy_worktree,
    fetch_base,
    has_remote,
    repo_lock,
    rev_parse,
    worktree_path,
)

OUTDATED = "OUTDATED"

# the old tip of every reset card, one ref per reset - never fetched or pushed, and a gc root
BACKUP_PREFIX = "refs/smortboard/outdated"

# a fresh checkout plus a replay of every card commit; a large repo needs more than a merge check
REBASE_TIMEOUT_SECONDS = 300
PUSH_TIMEOUT_SECONDS = 120

# the board's own git runs: no repo hooks in a throwaway tree, and none of the operator's rebase
# settings that would move other branches or keep merge commits
_GIT_CONFIG = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "rebase.updateRefs=false",
    "-c",
    "rebase.autoStash=false",
    "-c",
    "rebase.rebaseMerges=false",
)
_GIT_ENV = {"GIT_EDITOR": "true", "GIT_LFS_SKIP_SMUDGE": "1", "GIT_TERMINAL_PROMPT": "0"}

# what git says when a named branch is not on the remote at all, as opposed to a failed fetch
_MISSING_REMOTE_REF = "couldn't find remote ref"

_BOARD_AUTHOR = "smortboard"

# events that prove a worker ran since a reset, and so already had it explained
_WORKER_RAN = frozenset({"worker_summary", "worker_skipped", "result"})


@dataclass
class RebaseResult:
    """what one attempt to put a card branch on top of its base did.

    `outcome` is one of: "current" (already contains the base), "rebased" (moved to `new_tip`,
    pushed when `pushed`), "outdated" (the rebase conflicts in `conflicting_files`, nothing moved),
    "push_refused" (the lease refused the push, nothing moved), "skipped" (could not try - `detail`
    says why, nothing moved).
    """

    outcome: str
    base_ref: str = ""
    base_sha: str | None = None
    old_tip: str | None = None
    new_tip: str | None = None
    pushed: bool = False
    conflicting_files: list[str] = field(default_factory=list)
    detail: str = ""


class ResetRefused(RuntimeError):
    """an OUTDATED card could not be reset onto a fresh tree"""


def _git(
    path: str | Path, *args: str, timeout: int = REBASE_TIMEOUT_SECONDS, own_config: bool = True
) -> subprocess.CompletedProcess:
    """one git call. `own_config` False keeps the repo's hooks, as the board's other pushes do"""
    return subprocess.run(
        ["git", *(_GIT_CONFIG if own_config else ()), "-C", str(path), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, **_GIT_ENV},
        check=False,
    )


def _is_ancestor(repo_path: str | Path, older: str, newer: str) -> bool:
    return _git(repo_path, "merge-base", "--is-ancestor", older, newer).returncode == 0


def _own_commits(repo_path: str | Path, base_sha: str, tip: str) -> int:
    """the card's own commits on top of the base - merges of an older base in are not the card's"""
    result = _git(repo_path, "rev-list", "--count", "--no-merges", f"{base_sha}..{tip}")
    return int(result.stdout.strip() or 0) if result.returncode == 0 else 0


def _dirty(tree: Path) -> bool:
    return bool(_git(tree, "status", "--porcelain", "--untracked-files=no").stdout.strip())


def _backups(repo_path: str | Path, card_id: str) -> list[str]:
    result = _git(
        repo_path, "for-each-ref", "--format=%(objectname)", f"{BACKUP_PREFIX}/{card_id}/"
    )
    return result.stdout.split() if result.returncode == 0 else []


def _superseded(repo_path: str | Path, card_id: str, sha: str) -> bool:
    return any(_is_ancestor(repo_path, sha, backup) for backup in _backups(repo_path, card_id))


def _remote_tip(repo_path: str | Path, branch: str, remote: str) -> tuple[str, str | None]:
    """("absent", None) when the branch was never pushed, ("present", sha) after a fresh fetch,
    ("error", None) when origin could not be asked - never guessed from a stale tracking ref"""
    try:
        fetched = _fetch(repo_path, remote, branch)
    except WorktreeError:
        return "error", None
    if fetched.returncode != 0:
        missing = _MISSING_REMOTE_REF in (fetched.stderr or "").lower()
        return ("absent" if missing else "error"), None
    sha = rev_parse(repo_path, f"{remote}/{branch}")
    return ("present", sha) if sha else ("error", None)


@dataclass
class _Replay:
    new_tip: str | None = None
    files: list[str] = field(default_factory=list)
    error: str = ""


def _replay(repo_path: str | Path, old_tip: str, base_sha: str) -> _Replay:
    """rebases `old_tip` onto `base_sha` in a worktree made for this and removed after it"""
    scratch = Path(tempfile.mkdtemp(prefix="smortboard-rebase-"))
    tree = scratch / "tree"
    try:
        with repo_lock(repo_path):
            added = _git(repo_path, "worktree", "add", "--detach", str(tree), old_tip)
        if added.returncode != 0:
            return _Replay(error=added.stderr.strip() or "git worktree add failed")
        rebase = _git(tree, "rebase", "--empty=drop", base_sha)
        if rebase.returncode == 0:
            return _Replay(new_tip=rev_parse(tree, "HEAD"))
        unmerged = _git(tree, "diff", "--name-only", "--diff-filter=U")
        _git(tree, "rebase", "--abort")
        files = sorted({line for line in unmerged.stdout.splitlines() if line.strip()})
        return _Replay(files=files, error="" if files else rebase.stderr.strip())
    except subprocess.TimeoutExpired:
        return _Replay(error=f"the rebase did not finish within {REBASE_TIMEOUT_SECONDS}s")
    finally:
        with repo_lock(repo_path):
            if _git(repo_path, "worktree", "remove", "--force", str(tree)).returncode != 0:
                _git(repo_path, "worktree", "prune")
        shutil.rmtree(scratch, ignore_errors=True)


def _move_branch(repo_path: str | Path, card_id: str, old: str, new: str) -> bool:
    """moves the card branch from `old` to `new`, and its worktree with it when it has one.

    reset --keep refuses rather than overwrite a local change, and update-ref only moves a branch
    still at `old` - either way a branch that moved meanwhile is left alone
    """
    branch = branch_name(card_id)
    tree = worktree_path(repo_path, card_id)
    with repo_lock(repo_path):
        if tree.exists():
            on_branch = _git(tree, "branch", "--show-current").stdout.strip() == branch
            if not on_branch or rev_parse(tree, "HEAD") != old:
                return False
            return _git(tree, "reset", "--keep", new).returncode == 0
        return _git(repo_path, "update-ref", f"refs/heads/{branch}", new, old).returncode == 0


def _force_push(
    repo_path: str | Path, branch: str, new_tip: str, expected: str, remote: str
) -> tuple[bool, str]:
    """pushes `new_tip` to the card branch only while origin still holds `expected`"""
    # never anything but a card branch - main, master, trunk and development are never forced
    if not branch.startswith("card/"):
        return False, f"refusing to force-push {branch}: only card branches are ever rewritten"
    with repo_lock(repo_path):
        try:
            result = _git(
                repo_path,
                "push",
                f"--force-with-lease=refs/heads/{branch}:{expected}",
                remote,
                f"{new_tip}:refs/heads/{branch}",
                timeout=PUSH_TIMEOUT_SECONDS,
                own_config=False,
            )
        except subprocess.TimeoutExpired:
            return False, f"the push did not finish within {PUSH_TIMEOUT_SECONDS}s"
    return result.returncode == 0, result.stderr.strip()


def _skip(
    store: Any, card_id: str, result: RebaseResult, detail: str, *, record: bool = False
) -> RebaseResult:
    """nothing moved. `record` notes why on the card, once per reason and base, so a sweep that
    keeps finding the same thing does not write it every five minutes"""
    result.outcome, result.detail = "skipped", detail
    if record:
        last = next(
            (e for e in reversed(store.list_events(card_id)) if e["kind"].startswith("rebase")),
            None,
        )
        payload = {"reason": detail, "base_ref": result.base_ref, "base_sha": result.base_sha}
        if last is None or last["kind"] != "rebase_skipped" or last["payload"] != payload:
            store.append_event(card_id, "rebase_skipped", payload)
    return result


def rebase_onto_base(
    store: Any,
    card_id: str,
    repo_path: str | Path,
    base: str,
    *,
    fetched: bool = False,
    remote: str = "origin",
) -> RebaseResult:
    """puts the card branch on top of `remote`/`base`, or reports why it could not.

    `fetched` is for a caller that just fetched the base itself (the sweep, once per repo). Never
    raises for git itself failing or hanging - that is a skip, so one card cannot stop a sweep.
    """
    result = RebaseResult(outcome="skipped", base_ref=f"{remote}/{base}")
    try:
        return _rebase(store, card_id, repo_path, base, fetched, remote, result)
    except (subprocess.SubprocessError, OSError) as exc:
        return _skip(store, card_id, result, f"git failed: {exc}", record=True)


def _rebase(
    store: Any,
    card_id: str,
    repo_path: str | Path,
    base: str,
    fetched: bool,
    remote: str,
    result: RebaseResult,
) -> RebaseResult:
    branch = branch_name(card_id)
    if not branch_exists(repo_path, card_id):
        return _skip(store, card_id, result, "the card has no branch yet")
    if not has_remote(repo_path, remote):
        return _skip(store, card_id, result, f"the repo has no {remote} remote")
    if not fetched and not fetch_base(repo_path, base, remote=remote):
        return _skip(store, card_id, result, f"could not fetch {result.base_ref}")
    result.base_sha = rev_parse(repo_path, result.base_ref)
    result.old_tip = rev_parse(repo_path, branch)
    if result.base_sha is None or result.old_tip is None:
        return _skip(store, card_id, result, f"could not resolve {result.base_ref} or {branch}")
    if _is_ancestor(repo_path, result.base_sha, result.old_tip):
        result.outcome = "current"
        return result

    tree = worktree_path(repo_path, card_id)
    if tree.exists() and _dirty(tree):
        return _skip(
            store, card_id, result, "the card's worktree has uncommitted changes", record=True
        )
    # push only over a remote tip this branch already contains, so nothing on origin is lost
    state, remote_tip = _remote_tip(repo_path, branch, remote)
    if state == "error":
        return _skip(store, card_id, result, f"could not fetch {branch} from {remote}")
    push = False
    if state == "present" and remote_tip is not None:
        if _is_ancestor(repo_path, remote_tip, result.old_tip):
            push = True
        elif not _superseded(repo_path, card_id, remote_tip):
            return _skip(
                store,
                card_id,
                result,
                f"{remote}/{branch} has commits this branch lacks - someone else pushed to it",
                record=True,
            )

    # a branch with nothing of its own yet simply restarts at the current base
    new_tip = result.base_sha
    if _own_commits(repo_path, result.base_sha, result.old_tip):
        replay = _replay(repo_path, result.old_tip, result.base_sha)
        if replay.files:
            result.outcome, result.conflicting_files = "outdated", replay.files
            store.append_event(
                card_id,
                "rebase_conflict",
                {
                    "base_ref": result.base_ref,
                    "base_sha": result.base_sha,
                    "old_tip": result.old_tip,
                    "files": replay.files,
                },
            )
            return result
        if replay.new_tip is None:
            return _skip(store, card_id, result, f"the rebase failed: {replay.error}", record=True)
        new_tip = replay.new_tip

    # the local branch moves first: a refused push puts it straight back
    if not _move_branch(repo_path, card_id, result.old_tip, new_tip):
        return _skip(
            store,
            card_id,
            result,
            "the card's branch moved while it was rebased, or a file in its worktree was in the way",
            record=True,
        )
    if push and remote_tip is not None:
        ok, error = _force_push(repo_path, branch, new_tip, remote_tip, remote)
        if not ok:
            restored = _move_branch(repo_path, card_id, new_tip, result.old_tip)
            result.outcome, result.detail = "push_refused", error
            store.append_event(
                card_id,
                "rebase_push_refused",
                {
                    "base_ref": result.base_ref,
                    "base_sha": result.base_sha,
                    "old_tip": result.old_tip,
                    "new_tip": new_tip,
                    "expected_remote_tip": remote_tip,
                    "local_restored": restored,
                    "error": error,
                },
            )
            return result
    result.outcome, result.new_tip, result.pushed = "rebased", new_tip, push
    store.append_event(
        card_id,
        "rebased_onto_base",
        {
            "base_ref": result.base_ref,
            "base_sha": result.base_sha,
            "old_tip": result.old_tip,
            "new_tip": new_tip,
            "pushed": push,
        },
    )
    return result


def outdated_note(result: RebaseResult) -> str:
    """the block note for an OUTDATED card, the same from run start and from the sweep"""
    files = ", ".join(result.conflicting_files) or "unknown files"
    return (
        f"OUTDATED: {result.base_ref} moved to {(result.base_sha or '')[:8]} and this card's "
        f"commits no longer rebase onto it - they conflict in: {files}. The branch was left "
        f"exactly as it was, at {(result.old_tip or '')[:8]}."
    )


def flag_outdated(store: Any, card_id: str, result: RebaseResult) -> None:
    """blocks a card the sweep found outdated - the card keeps its status, as every block does"""
    store.update_card(card_id, blocked_reason_code=OUTDATED, review_flag=True)
    store.add_comment(
        card_id, author=_BOARD_AUTHOR, body=with_next(outdated_note(result), OUTDATED)
    )


def _backup_ref(repo_path: str | Path, card_id: str) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    ref, suffix = f"{BACKUP_PREFIX}/{card_id}/{stamp}", 1
    while rev_parse(repo_path, ref):
        ref, suffix = f"{BACKUP_PREFIX}/{card_id}/{stamp}-{suffix}", suffix + 1
    return ref


def reset_to_base(
    store: Any, card_id: str, repo_path: str | Path, base: str, remote: str = "origin"
) -> dict[str, Any]:
    """restarts an OUTDATED card on a fresh tree at the current base, keeping its old tip on a
    backup ref first. Raises ResetRefused when that cannot be done without losing something."""
    base_ref = base
    if has_remote(repo_path, remote) and fetch_base(repo_path, base, remote=remote):
        base_ref = f"{remote}/{base}"
    base_sha = rev_parse(repo_path, base_ref)
    if base_sha is None:
        raise ResetRefused(f"could not resolve {base_ref}")
    branch = branch_name(card_id)
    old_tip = rev_parse(repo_path, branch)
    tree = worktree_path(repo_path, card_id)
    if tree.exists() and _dirty(tree):
        raise ResetRefused(
            f"{tree} has uncommitted changes - commit or discard them, then answer again"
        )
    backup = None
    if old_tip:
        backup = _backup_ref(repo_path, card_id)
        with repo_lock(repo_path):
            made = _git(repo_path, "update-ref", backup, old_tip, "")
        if made.returncode != 0:
            raise ResetRefused(f"could not keep the old tip on {backup}: {made.stderr.strip()}")
    try:
        if tree.exists():
            destroy_worktree(repo_path, card_id, force=True)
        with repo_lock(repo_path):
            moved = _git(repo_path, "update-ref", f"refs/heads/{branch}", base_sha)
        if moved.returncode != 0:
            raise ResetRefused(f"could not move {branch}: {moved.stderr.strip()}")
        add_worktree(repo_path, card_id)
    except WorktreeError as exc:
        raise ResetRefused(f"could not cut a fresh tree: {exc}") from exc
    conflict = next(
        (
            e["payload"]
            for e in reversed(store.list_events(card_id))
            if e["kind"] == "rebase_conflict"
        ),
        {},
    )
    payload = {
        "old_tip": old_tip,
        "backup_ref": backup,
        "base_ref": base_ref,
        "base_sha": base_sha,
        "files": conflict.get("files") or [],
    }
    store.append_event(card_id, "outdated_reset", payload)
    return payload


def pending_reset(store: Any, card_id: str) -> dict[str, Any] | None:
    """the outdated_reset no worker has run since - an attempt refused before its worker (docker
    not up, say) leaves the reset still to be explained to the next one"""
    for event in reversed(store.list_events(card_id)):
        if event["kind"] in _WORKER_RAN:
            return None
        if event["kind"] == "outdated_reset":
            return event["payload"]
    return None


def reset_briefing(reset: dict[str, Any]) -> str:
    """what the worker is told on the run after an OUTDATED reset"""
    files = ", ".join(reset.get("files") or []) or "the files it changed"
    return (
        f"The base branch moved and this card's earlier commits no longer rebased onto "
        f"{reset.get('base_ref')} - they conflicted in: {files}. The operator chose to redo the "
        f"card: this branch was reset to a fresh tree at {reset.get('base_ref')} "
        f"({(reset.get('base_sha') or '')[:8]}), so none of the earlier commits are here. Redo "
        "the change against the current code; the earlier attempt below is context, not a patch "
        "to reapply."
    )


def remote_superseded(repo_path: str | Path, card_id: str, remote: str = "origin") -> str | None:
    """origin's copy of the card branch, when it is only the card's own pre-reset history - kept on
    a backup ref and not in the local branch. Reads the tracking ref as last fetched."""
    branch = branch_name(card_id)
    sha = rev_parse(repo_path, f"{remote}/{branch}")
    local = rev_parse(repo_path, branch)
    if sha is None or local is None or _is_ancestor(repo_path, sha, local):
        return None
    return sha if _superseded(repo_path, card_id, sha) else None


def push_over_superseded(
    store: Any, card_id: str, repo_path: str | Path, remote: str = "origin"
) -> bool:
    """replaces origin's pre-reset copy of the card branch with the redone one, so the pull request
    that stayed open gets the new commits. True only when it pushed."""
    branch = branch_name(card_id)
    # only a card reset from OUTDATED has a backup ref - every other card skips the fetch
    if not _backups(repo_path, card_id) or not has_remote(repo_path, remote):
        return False
    if _remote_tip(repo_path, branch, remote)[0] != "present":
        return False
    expected = remote_superseded(repo_path, card_id, remote)
    local = rev_parse(repo_path, branch)
    if expected is None or local is None:
        return False
    ok, error = _force_push(repo_path, branch, local, expected, remote)
    store.append_event(
        card_id,
        "superseded_branch_replaced" if ok else "rebase_push_refused",
        {"old_remote_tip": expected, "new_tip": local, "error": None if ok else error},
    )
    return ok
