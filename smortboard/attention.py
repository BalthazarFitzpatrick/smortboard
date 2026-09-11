"""the attention inbox: every card across every board that needs operator, in one list.

A card needs him when it carries a blocked_reason_code, or when review_flag is set (a checking
card waiting to be accepted/rejected also raises review_flag, but that path already has its own
surface - the board's checking column - so this module only counts a card that is still short of
a decision: not accepted, not rejected).

ORDER: oldest first. The point of an inbox is that nothing sits forever - the card that has been
waiting longest surfaces first, same as any other inbox.
"""

from __future__ import annotations

from typing import Any

from smortboard.lifecycle import BOARD_AUTHOR
from smortboard.store.api import Store

RESUMABLE_REASONS = frozenset(
    {"AGENT_QUESTION", "TESTS_FAILED", "REVIEW_REJECTED", "CRASH", "LEASE_CONFLICT"}
)
# USAGE_LIMIT clears on its own once the rate-limit window resets - answering it does not change
# the model's limit, so an answer would only be spent confusing the agent on its next run.
# DEPENDENCY_REJECTED is not this card's fault - a message to it cannot un-reject the dependency;
# accept_card already clears it automatically once the dependency comes back (see review/decide.py)

# what the inbox says in place of an answer box, for the rows an answer cannot move
_WHY_NOT_ANSWERABLE = {
    "review": "a decision, not a question - open the card and press y to accept or x to reject",
    "USAGE_LIMIT": "clears itself once the rate-limit window resets",
    "DEPENDENCY_REJECTED": "waiting on a rejected dependency - clears once that card is accepted",
}


def _trim(text: str | None, limit: int = 4000) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n... (trimmed)"


def _question_for(store: Store, card: dict[str, Any]) -> str:
    """the most useful text to show operator for this blocked card.

    AGENT_QUESTION: the agent's own final result text, from the card's latest `result` stream
    event - that is what it actually said when it stopped to ask. every other reason: the latest
    board-authored comment, which is the block note the lifecycle already wrote (test output,
    review findings, the crash, the lease conflict).
    """
    if card.get("blocked_reason_code") == "AGENT_QUESTION":
        results = [e for e in store.list_events(card["id"]) if e["kind"] == "result"]
        if results:
            return _trim(results[-1]["payload"].get("result"))
    board_notes = [c for c in card.get("comments") or [] if c.get("author") == BOARD_AUTHOR]
    if board_notes:
        return _trim(board_notes[-1]["body"])
    return ""


def _needs_attention(card: dict[str, Any]) -> bool:
    if card["status"] in ("accepted", "rejected"):
        return False
    return bool(card.get("blocked_reason_code")) or bool(card.get("review_flag"))


class AnswerRefused(Exception):
    """the store's state means this card cannot be resumed by an answer right now"""


def answer_card(store: Store, runs: Any, card_id: str, message: str) -> dict[str, Any]:
    """records operator's answer as a comment and resumes the card, or refuses and says why.

    Resumable reasons are the ones an answer can actually unstick: AGENT_QUESTION (he answers the
    question), TESTS_FAILED / REVIEW_REJECTED / CRASH (a note the next run reads before trying
    again), LEASE_CONFLICT (he can widen the lease in the note). USAGE_LIMIT is not resumable here
    - it clears itself once the rate-limit window resets, and an answer changes nothing about that.
    Neither is DEPENDENCY_REJECTED - the dependency, not this card, is what needs fixing, and
    accept_card already un-blocks it automatically once that dependency is accepted after all.
    A review_flag with no reason code (a checking card waiting on accept/reject) is not a resumable
    block either - that is a decision, not a question, and belongs on the card panel's y/x.
    """
    state = runs.get(card_id)
    if state is not None and state.running:
        raise AnswerRefused("this card is still running")

    card = store.get_card(card_id)
    reason = card.get("blocked_reason_code")
    if not _needs_attention(card):
        raise AnswerRefused("this card is not waiting on an answer")
    if reason not in RESUMABLE_REASONS:
        raise AnswerRefused(f"{reason or 'this block'} cannot be resumed by answering here")

    store.add_comment(card_id, author="operator", body=message)
    store.update_card(card_id, blocked_reason_code=None, review_flag=False)
    return runs.start(card_id).as_dict()


def attention_rows(store: Store) -> list[dict[str, Any]]:
    """one row per card across every board that is waiting on operator, oldest first"""
    rows = []
    for board in store.list_boards():
        for card in store.list_cards(board["id"]):
            if not _needs_attention(card):
                continue
            reason = card.get("blocked_reason_code") or "review"
            rows.append(
                {
                    "card_id": card["id"],
                    "board_id": board["id"],
                    "board_name": board["name"],
                    "title": card["title"],
                    "reason": reason,
                    "question": _question_for(store, card),
                    "since": card["updated_at"],
                    "answerable": reason in RESUMABLE_REASONS,
                    "hint": _WHY_NOT_ANSWERABLE.get(reason, ""),
                }
            )
    rows.sort(key=lambda r: r["since"])
    return rows
