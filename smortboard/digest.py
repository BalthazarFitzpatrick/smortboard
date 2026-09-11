"""the morning digest: a projection over the event log, exactly as review/outcome.py is one card's.

Nothing here is stored. `board_digest` re-derives pull requests, blocked cards and spend from events
and comments already on the board, for whatever window `since` names - the same "no second record to
drift from the log" rule outcome.py states for a single card, applied board-wide.
"""

from __future__ import annotations

from typing import Any

from smortboard.review.outcome import card_outcome
from smortboard.store.api import Store

BOARD_AUTHOR = "smortboard"  # lifecycle.py's own constant, duplicated rather than imported - this
# module reads the store, lifecycle runs it; importing lifecycle here would pull in exec/review


def _card_events_since(store: Store, card_id: str, since: float) -> list[dict[str, Any]]:
    return [e for e in store.list_events(card_id) if _epoch(e["created_at"]) >= since]


def _epoch(iso: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(iso).timestamp()


def _merge_request_events(store: Store, card_id: str, since: float) -> list[dict[str, Any]]:
    return [
        e
        for e in _card_events_since(store, card_id, since)
        if e["kind"] == "merge_request" and e["payload"].get("url")
    ]


def _pull_requests(store: Store, cards: list[dict[str, Any]], since: float) -> list[dict[str, Any]]:
    """every PR opened since `since`, ordered dependencies-first.

    A card's dependencies are also in this list far more often than not (mission control plans a
    workstream as one unit), so a straight topological sort over depends_on already gives the merge
    order Fabian needs - a card's own PR never has to wait behind one that is not in the batch.
    """
    opened: dict[str, dict[str, Any]] = {}
    for card in cards:
        events = _merge_request_events(store, card["id"], since)
        if not events:
            continue
        opened[card["id"]] = {
            "card_id": card["id"],
            "title": card["title"],
            "url": events[-1]["payload"]["url"],
            "opened_at": events[-1]["created_at"],
            "depends_on": list(card.get("depends_on") or []),
        }

    # dependencies-first: repeatedly emit any not-yet-emitted PR whose in-batch dependencies are
    # already emitted. a dependency outside this batch cannot be ordered against, so it is ignored
    # for ordering (still flagged below) rather than deadlocking the sort
    ids = list(opened)
    emitted: list[str] = []
    remaining = set(ids)
    while remaining:
        ready = [
            cid
            for cid in ids
            if cid in remaining and all(dep not in remaining for dep in opened[cid]["depends_on"])
        ]
        if not ready:
            ready = sorted(remaining)  # a dependency cycle in-batch - break it by id, don't hang
        ready.sort(key=lambda cid: opened[cid]["opened_at"])
        for cid in ready:
            emitted.append(cid)
            remaining.discard(cid)

    result = []
    for cid in emitted:
        row = dict(opened[cid])
        deps_in_batch = [d for d in row["depends_on"] if d in opened]
        deps_outside = [d for d in row["depends_on"] if d not in opened]
        row["merge_after"] = deps_in_batch
        # scheduler._dependency_wait now only starts a dependent once its dependency's PR is
        # actually merged on GitHub, so this card's worktree is guaranteed to carry every in-batch
        # dependency's code. a dependency outside this window (merged earlier, or never run) is
        # still worth a glance, so it stays flagged - just as informational, not as a real gap
        row["dependency_not_in_batch"] = bool(deps_outside)
        del row["depends_on"]
        result.append(row)
    return result


def _one_question(store: Store, card: dict[str, Any]) -> str | None:
    """the single thing this blocked card needs from Fabian.

    AGENT_QUESTION means the agent itself is asking - its own final text is the question, read off
    the outcome projection exactly as the card panel shows it. Every other reason code is the
    board's own block note (lifecycle._block posts it as a comment before flagging review), which
    already states in plain language what stopped the card and why.
    """
    if card["blocked_reason_code"] == "AGENT_QUESTION":
        summary = card_outcome(store, card["id"])["summary"]
        if summary:
            return summary
    comments = [c for c in card.get("comments") or [] if c["author"] == BOARD_AUTHOR]
    return comments[-1]["body"] if comments else None


def _waiting_on_fabian(store: Store, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for card in cards:
        if not card.get("blocked_reason_code"):
            continue
        rows.append(
            {
                "card_id": card["id"],
                "title": card["title"],
                "reason": card["blocked_reason_code"],
                "question": _one_question(store, card),
            }
        )
    return rows


def _runs_and_spend(store: Store, cards: list[dict[str, Any]], since: float) -> dict[str, Any]:
    runs = 0
    total_cost = 0.0
    for card in cards:
        for event in _card_events_since(store, card["id"], since):
            if event["kind"] == "lifecycle_started":
                runs += 1
            elif event["kind"] == "result":
                total_cost += float(event["payload"].get("total_cost_usd") or 0)
    return {"runs": runs, "total_cost_usd": round(total_cost, 6)}


def board_digest(store: Store, board_id: str, since: float) -> dict[str, Any]:
    store.get_board(board_id)  # 404 for an unknown board rather than an empty digest
    cards = store.list_cards(board_id)
    return {
        "board_id": board_id,
        "since": since,
        "pull_requests": _pull_requests(store, cards, since),
        "waiting_on_fabian": _waiting_on_fabian(store, cards),
        "runs_and_spend": _runs_and_spend(store, cards, since),
    }
