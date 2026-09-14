"""the human's call on a finished card: accept it, or reject it and start clean.

The board still never merges. Accepting records the decision and releases the worktree; the pull
request stays open and merging it is Fabian's. Rejecting keeps the attempt as a read-only diff on
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
    default_branch,
    delete_branch,
    destroy_worktree,
    worktree_path,
)
from smortboard.review.merge_request import close_merge_request
from smortboard.review.outcome import card_outcome
from smortboard.store.api import Store

BOARD_AUTHOR = "smortboard"

# a card nobody has finished judging can be rejected from doing (blocked, or orphaned by a board
# restart) as well as from checking. a decision can also be reversed: accepted to rejected, and a
# rejected card accepted again. otherwise only checking can be accepted - it is what the gates passed
REJECTABLE = ("doing", "checking", "accepted")

# dependents already decided are left alone; only work still to come rests on the rejected card
UNDECIDED = ("todo", "doing", "checking")


class DecisionRefused(RuntimeError):
    """the card is not in a state this decision applies to"""


def _release_worktree(repo_path: str, card_id: str) -> None:
    # --force: the worktree holds the leases and settings the board wrote, which are untracked
    if worktree_path(repo_path, card_id).exists():
        destroy_worktree(repo_path, card_id, force=True)


def _release_dependents(store: Store, card: dict[str, Any]) -> None:
    """undoes reject_card's DEPENDENCY_REJECTED on work still to come, once nothing it rests on is
    rejected any more - a card can depend on two, and only one of them came back"""
    for dependent_id in card["depended_on_by"]:
        dependent = store.get_card(dependent_id)
        if dependent["status"] not in UNDECIDED:
            continue
        if dependent["blocked_reason_code"] != "DEPENDENCY_REJECTED":
            continue
        if any(store.get_card(dep)["status"] == "rejected" for dep in dependent["depends_on"]):
            continue
        store.update_card(dependent_id, blocked_reason_code=None, review_flag=False)
        store.add_comment(
            dependent_id,
            author=BOARD_AUTHOR,
            body=f"A card this one depends on was accepted after all: {card['title']}",
        )


def accept_card(store: Store, card_id: str) -> dict[str, Any]:
    card = store.get_card(card_id)
    reversing = card["status"] == "rejected"
    if not reversing and (card["status"] != "checking" or card["blocked_reason_code"]):
        raise DecisionRefused(
            f"only a rejected card, or one in checking with nothing blocking it, can be accepted; "
            f"this one is "
            f"{card['status']}{' / ' + card['blocked_reason_code'] if card['blocked_reason_code'] else ''}"
        )
    if card.get("repo_id") and not reversing:
        # the branch stays: the open pull request is built on it
        _release_worktree(store.get_repo(card["repo_id"])["path"], card_id)
    store.update_card(card_id, status="accepted", review_flag=False)
    if reversing:
        # rejecting closed the pull request and kept its branch on github; the board cannot reopen
        # one (its gh allowlist has no reopen), so it says where the work still is
        store.add_comment(
            card_id,
            author=BOARD_AUTHOR,
            body="Accepted after being rejected. Rejecting closed its pull request and kept the "
            "branch on GitHub - reopen the pull request there if this work should land.",
        )
        _release_dependents(store, card)
    store.append_event(card_id, "decision", {"decision": "accepted", "reversed": reversing})
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
        path, base = repo["path"], default_branch(repo)
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
    if card["status"] == "accepted":
        note += (
            "\nIt had been accepted: if its pull request was already merged, that merge stands - "
            "revert it on main by hand."
        )
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
