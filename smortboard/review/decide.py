"""the human's call on a finished card: accept it, or reject it and start clean.

The board still never merges. Accepting records the decision and releases the worktree; the pull
request stays open and merging it is operator's. Rejecting keeps the attempt as a read-only diff on
the card, closes the pull request without deleting its branch, and destroys the local worktree and
branch - so the next run cuts fresh from base instead of building on work that was turned down.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from smortboard.exec.worktrees import (
    branch_diff,
    branch_exists,
    branch_name,
    delete_branch,
    destroy_worktree,
    worktree_path,
)
from smortboard.review.merge_request import close_merge_request
from smortboard.review.outcome import card_outcome
from smortboard.store.api import Store

BOARD_AUTHOR = "smortboard"

# a card nobody has finished judging can be rejected from doing (blocked, or orphaned by a board
# restart) as well as from checking. only checking can be accepted - it is what the gates passed
REJECTABLE = ("doing", "checking")

# dependents already decided are left alone; only work still to come rests on the rejected card
UNDECIDED = ("todo", "doing", "checking")


class DecisionRefused(RuntimeError):
    """the card is not in a state this decision applies to"""


def _release_worktree(repo_path: str, card_id: str) -> None:
    # --force: the worktree holds the leases and settings the board wrote, which are untracked
    if worktree_path(repo_path, card_id).exists():
        destroy_worktree(repo_path, card_id, force=True)


def accept_card(store: Store, card_id: str) -> dict[str, Any]:
    card = store.get_card(card_id)
    if card["status"] != "checking" or card["blocked_reason_code"]:
        raise DecisionRefused(
            f"only a card in checking with nothing blocking it can be accepted; this one is "
            f"{card['status']}{' / ' + card['blocked_reason_code'] if card['blocked_reason_code'] else ''}"
        )
    if card.get("repo_id"):
        # the branch stays: the open pull request is built on it
        _release_worktree(store.get_repo(card["repo_id"])["path"], card_id)
    store.update_card(card_id, status="accepted", review_flag=False)
    store.append_event(card_id, "decision", {"decision": "accepted"})
    return store.get_card(card_id)


def reject_card(
    store: Store,
    card_id: str,
    close_pr: Callable[[str, str], str | None] | None = None,
) -> dict[str, Any]:
    """`close_pr(repo_path, url)` returns a refusal, or None once the pull request is closed"""
    close_pr = close_pr or close_merge_request
    card = store.get_card(card_id)
    if card["status"] not in REJECTABLE:
        raise DecisionRefused(f"a card in {card['status']} cannot be rejected")

    attachment = pr_refusal = None
    pr_url = card_outcome(store, card_id)["pr_url"]
    if card.get("repo_id"):
        repo = store.get_repo(card["repo_id"])
        path, base = repo["path"], repo.get("default_branch") or "main"
        if branch_exists(path, card_id):
            diff = branch_diff(path, base, branch_name(card_id))
            if diff:
                attempt = 1 + sum(
                    a["filename"].startswith("attempt-") for a in store.list_attachments(card_id)
                )
                attachment = store.add_attachment(
                    card_id, f"attempt-{attempt}.diff", "text/x-diff", diff.encode()
                )["filename"]
        if pr_url:
            pr_refusal = close_pr(path, pr_url)
        _release_worktree(path, card_id)
        if branch_exists(path, card_id):
            delete_branch(path, card_id)

    store.update_card(card_id, status="rejected", blocked_reason_code=None, review_flag=False)
    note = "Rejected. The next run cuts a fresh worktree from base."
    if attachment:
        note += f" This attempt is kept as {attachment}."
    if pr_url:
        note += f"\nPull request: {pr_url} - " + (
            f"could not be closed: {pr_refusal}" if pr_refusal else "closed, branch kept."
        )
    store.add_comment(card_id, author=BOARD_AUTHOR, body=note)

    for dependent_id in card["depended_on_by"]:
        if store.get_card(dependent_id)["status"] in UNDECIDED:
            store.update_card(
                dependent_id, blocked_reason_code="DEPENDENCY_REJECTED", review_flag=True
            )
            store.add_comment(
                dependent_id,
                author=BOARD_AUTHOR,
                body=f"A card this one depends on was rejected: {card['title']}",
            )

    store.append_event(
        card_id,
        "decision",
        {
            "decision": "rejected",
            "attachment": attachment,
            "pr_url": pr_url,
            "pr_refusal": pr_refusal,
        },
    )
    return store.get_card(card_id)
