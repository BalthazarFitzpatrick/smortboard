"""event-backed parent branches for review-required cards"""

from smortboard.exec.worktrees import default_branch
from smortboard.review.merge_request import PROTECTED_BRANCHES


def integrated_event(store, card_id, base=None):
    for event in reversed(store.list_events(card_id)):
        if event["kind"] == "lifecycle_started":
            break
        if event["kind"] == "integrated" and (base is None or event["payload"].get("base") == base):
            return event
    return None


def dependency_landed(store, card):
    if card["status"] != "accepted":
        return False
    if not card.get("repo_id"):
        return True
    repo = store.get_repo(card["repo_id"])
    base = default_branch(repo)
    if base not in PROTECTED_BRANCHES:
        return True
    if integrated_event(store, card["id"], base):
        return True
    url = None
    for event in reversed(store.list_events(card["id"])):
        if event["kind"] == "lifecycle_started":
            break
        if event["kind"] == "merge_request":
            url = event["payload"].get("url")
            break
    if not url:
        return False
    # accepting a protected-base card records approval, not a merge
    from smortboard.review.land_card import _pr_reached_base
    from smortboard.scheduler import _cached_pr_view

    state = _cached_pr_view(repo["path"], url)
    if state.error or not state.merged:
        return False
    # a merged PR's own recorded base never changes, so one retargeted before merging (found for
    # real: smolsmort #9's parent branch merged into main via a base changed before merge) would
    # never satisfy an exact string match again - same widening as already_landed (#208)
    return _pr_reached_base(store, card, repo, base, state)


def active_stack(store, card_id):
    for event in reversed(store.list_events(card_id)):
        if event["kind"] == "stacked_retargeted":
            return None
        if event["kind"] == "stacked_on":
            return event["payload"]
    return None


def stacking_parent(store, card):
    if store.board_merges_freely(card["board_id"]):
        return None
    parents = []
    for dependency in card["depends_on"]:
        parent = store.get_card(dependency)
        if dependency_landed(store, parent):
            continue
        if parent["status"] not in ("checking", "accepted") or parent["blocked_reason_code"]:
            raise ValueError("a dependency is not ready for stacking")
        if parent.get("repo_id") != card.get("repo_id"):
            raise ValueError("an unaccepted dependency belongs to another repo")
        parents.append(parent)
    if len(parents) > 1:
        raise ValueError("waiting for all but one dependency to land")
    if not parents:
        return None
    parent = parents[0]
    seen = {card["id"]}
    cursor = parent["id"]
    depth = 1
    while cursor:
        if cursor in seen or depth >= 3:
            raise ValueError("a stack may contain at most three cards")
        seen.add(cursor)
        depth += 1
        stack = active_stack(store, cursor)
        cursor = stack["parent_id"] if stack else None
    return parent


def stacked_children(store, card):
    return [
        child
        for child in (store.get_card(cid) for cid in card["depended_on_by"])
        if child["status"] in ("todo", "doing", "checking")
        and (stack := active_stack(store, child["id"]))
        and stack["parent_id"] == card["id"]
    ]


def integration_base(store, card_id, base):
    stack = active_stack(store, card_id)
    return stack["branch"] if stack else base
