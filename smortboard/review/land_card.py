"""shared landing transaction for automatic and operator-triggered accepts"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from smortboard.store.api import Store

if TYPE_CHECKING:
    from smortboard.lifecycle import LifecycleResult

from smortboard.actions import with_next
from smortboard.exec.worktrees import branch_name, fetch_base
from smortboard.review.decide import DecisionRefused, accept_card
from smortboard.review.integrate import _git, integrate, integration_lock, open_release_request
from smortboard.review.landing import landing_lock, resolve_repo_key
from smortboard.review.merge_request import PROTECTED_BRANCHES, pr_view
from smortboard.review.stacks import active_stack, dependency_landed, integrated_event

INTEGRATE_ATTEMPTS = 3


def already_landed(store, card, repo, base, url):
    if integrated_event(store, card["id"], base):
        return True
    proof = None
    if url:
        state = pr_view(repo["path"], url)
        if (
            not state.error
            and state.merged
            and (state.base == base or (state.base is None and not active_stack(store, card["id"])))
        ):
            proof = "github"
    if proof is None and fetch_base(repo["path"], base):
        result = _git(
            repo["path"], "merge-base", "--is-ancestor", branch_name(card["id"]), f"origin/{base}"
        )
        if result.returncode == 0:
            proof = "git"
    if proof is None:
        return False
    store.append_event(card["id"], "integrated", {"base": base, "url": url, "via": proof})
    return True


def land_card(
    store: Store,
    state: LifecycleResult,
    card: dict[str, Any],
    tree: Any,
    repo: dict[str, Any],
    base: str,
    url: str,
    *,
    sync=None,
    integrate_fn=None,
    release_fn=None,
) -> LifecycleResult:
    """lands the card on a base that is not protected, then accepts it. a card that cannot land
    keeps its open pull request and waits for the operator, as on main"""
    from smortboard.lifecycle import _note, _sync_and_retest

    sync = sync or _sync_and_retest
    integrate_fn = integrate_fn or integrate
    release_fn = release_fn or open_release_request
    card_id = card["id"]
    if base in PROTECTED_BRANCHES:
        raise DecisionRefused(f"{base} is protected")
    stack = active_stack(store, card_id)
    if stack and store.get_card(stack["parent_id"])["status"] != "accepted":
        raise DecisionRefused("accept the stacked parent before landing this card")
    waited = False

    def on_wait(position):
        nonlocal waited
        if not waited:
            _note(store, card_id, f"Waiting for the landing lock on {base} (queue {position}).")
            waited = True

    landed, reason = None, "the base kept moving while it was merged"
    repo_key = resolve_repo_key(repo["path"], store)
    # the in-process lock is cheap and covers same-process races instantly; the db-backed one is
    # what keeps another board process or an outside agent's smortboard-land off the same base
    # while this card's sync+push runs
    with (
        integration_lock(repo["path"], base),
        landing_lock(store, repo_key, card_id, tree.branch, base, on_wait=on_wait),
    ):
        for _ in range(INTEGRATE_ATTEMPTS):
            if (blocked := sync(store, state, card_id, tree, repo, base)) is not None:
                return blocked
            result = integrate_fn(tree.path, tree.branch, base, card["title"])
            if result.sha or not result.moved:
                landed, reason = result.sha, result.reason or reason
                break

    if landed is None:
        _note(
            store,
            card_id,
            with_next(
                f"Tests passed and the reviewer approved, but the board could not merge it into "
                f"{base}: {reason}\n\nThe pull request is open:\n{url}",
                "review",
            ),
        )
        store.update_card(card_id, review_flag=True)
    else:
        store.append_event(card_id, "integrated", {"base": base, "sha": landed, "url": url})
        accept_card(store, card_id)
        release = release_fn(repo["path"], base)
        _note(
            store,
            card_id,
            f"Tests passed, the reviewer approved, and the board merged it into {base} as "
            f"{landed[:10]}:\n{url}\n\nMerging {base} into main is yours"
            + (f":\n{release}" if release else "."),
        )
        retarget_children(store, card, repo, base)
        from smortboard.scheduler import sweep_checking_prs

        sweep_checking_prs(store, card["board_id"], repo_path=repo["path"])
    state.phase, state.pr_url = "opened", url
    return state


def retarget_children(store, card, repo, base):
    with integration_lock(repo["path"], base):
        _retarget_children(store, card, repo, base)


def _retarget_children(store, card, repo, base):
    from smortboard.exec.worktrees import existing_worktree, worktree_path
    from smortboard.review.merge_request import retarget_merge_request
    from smortboard.review.mergeable import merge_branch, push_branch
    from smortboard.review.outcome import card_outcome
    from smortboard.review.stacks import stacked_children

    for child in stacked_children(store, card):
        if child["status"] == "doing":
            continue
        url = card_outcome(store, child["id"])["pr_url"]
        if url and pr_view(repo["path"], url).base == base:
            store.append_event(child["id"], "stacked_retargeted", {"base": base})
            continue
        if not worktree_path(repo["path"], child["id"]).exists():
            continue
        if not fetch_base(repo["path"], base):
            store.add_comment(
                child["id"],
                author="smortboard",
                body="Could not fetch the base to retarget this stacked card.",
            )
            continue
        tree = existing_worktree(repo["path"], child["id"])
        result = merge_branch(tree.path, f"origin/{base}")
        if not result.clean:
            store.update_card(child["id"], blocked_reason_code="MERGE_CONFLICT", review_flag=True)
            store.append_event(
                child["id"],
                "merge_conflict",
                {"base_ref": f"origin/{base}", "files": result.conflicting_files},
            )
            store.add_comment(
                child["id"],
                author="smortboard",
                body="The parent landed, but merging the base into this card conflicts: "
                + ", ".join(result.conflicting_files),
            )
            continue
        if not push_branch(tree.path, tree.branch):
            store.add_comment(
                child["id"],
                author="smortboard",
                body="The parent landed, but pushing this stacked card failed; its PR target is unchanged.",
            )
            continue
        refusal = retarget_merge_request(repo, url, base) if url else None
        if refusal:
            store.add_comment(
                child["id"],
                author="smortboard",
                body=f"Could not retarget the stacked pull request: {refusal}",
            )
        else:
            store.append_event(child["id"], "stacked_retargeted", {"base": base})


def retry_retarget(store, card):
    stack = active_stack(store, card["id"])
    if not stack:
        return False
    parent = store.get_card(stack["parent_id"])
    if dependency_landed(store, parent):
        from smortboard.exec.worktrees import default_branch

        repo = store.get_repo(card["repo_id"])
        retarget_children(store, parent, repo, default_branch(repo))
    return active_stack(store, card["id"]) is not None
