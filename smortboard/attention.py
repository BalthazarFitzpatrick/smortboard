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

from smortboard.actions import next_action, short_action
from smortboard.lifecycle import BOARD_AUTHOR
from smortboard.scheduler import conflicting_run
from smortboard.store.api import Store

RESUMABLE_REASONS = frozenset(
    {"AGENT_QUESTION", "TESTS_FAILED", "REVIEW_REJECTED", "CRASH", "LEASE_CONFLICT"}
)
# USAGE_LIMIT clears on its own once the rate-limit window resets - answering it does not change
# the model's limit, so an answer would only be spent confusing the agent on its next run.
# DEPENDENCY_REJECTED is not this card's fault - a message to it cannot un-reject the dependency;
# accept_card already clears it automatically once the dependency comes back (see review/decide.py)


def _flag_reason(store: Store, card_id: str) -> str:
    """why a card with no reason code is flagged: its latest run was stopped or refused, or else
    it is a checking card waiting on a decision"""
    for event in reversed(store.list_events(card_id)):
        if event["kind"] == "run_stopped":
            return "stopped"
        if event["kind"] == "run_refused":
            return "refused"
        if event["kind"] == "lifecycle_started":
            break
    return "review"


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


def waiting_reason(store: Store, card: dict[str, Any]) -> str | None:
    """why this card waits on a person - its reason code, or refused/stopped/review - or None"""
    if not _needs_attention(card):
        return None
    return card.get("blocked_reason_code") or _flag_reason(store, card["id"])


def with_actions(store: Store, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """each card gains next_action and next_action_short - None unless it waits on a person - so
    the board can say what to do on the strip itself, not only in the inbox"""
    for card in cards:
        reason = waiting_reason(store, card)
        card["next_action"] = next_action(reason)
        card["next_action_short"] = short_action(reason)
    return cards


class AnswerRefused(Exception):
    """the store's state means this card cannot be resumed by an answer right now"""


def answer_card(store: Store, runs: Any, card_id: str, message: str) -> dict[str, Any]:
    """records operator's answer as a comment and resumes the card, or refuses and says why.

    Resumable reasons are the ones an answer can actually unstick: AGENT_QUESTION (he answers the
    question), TESTS_FAILED / REVIEW_REJECTED / CRASH (a note the next run reads before trying
    again), LEASE_CONFLICT (the note says what to do instead - widening the lease itself is
    Store.set_leases, since the guard reads the card's lease rows, never a note). USAGE_LIMIT is
    not resumable here - it clears itself once the rate-limit window resets, and an answer changes
    nothing about that.
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
    # refused before the comment is stored, so a retry once the other card finishes is clean
    active = getattr(runs, "active", None)
    conflict = conflicting_run(store, card, [s.card_id for s in active()]) if active else None
    if conflict:
        raise AnswerRefused(f"not resumed: {conflict} - answer again once it finishes")

    store.add_comment(card_id, author="operator", body=message)
    store.update_card(card_id, blocked_reason_code=None, review_flag=False)
    return runs.start(card_id).as_dict()


def attention_rows(store: Store) -> list[dict[str, Any]]:
    """one row per card across every board that is waiting on operator, oldest first"""
    rows = []
    for board in store.list_boards():
        for card in store.list_cards(board["id"]):
            reason = waiting_reason(store, card)
            if reason is None:
                continue
            answerable = reason in RESUMABLE_REASONS
            rows.append(
                {
                    "card_id": card["id"],
                    "board_id": board["id"],
                    "board_name": board["name"],
                    "title": card["title"],
                    "reason": reason,
                    "question": _question_for(store, card),
                    "since": card["updated_at"],
                    "answerable": answerable,
                    # the call to action, for every row; hint repeats it where no answer box shows
                    "action": next_action(reason) or "",
                    "hint": "" if answerable else (next_action(reason) or ""),
                }
            )
    rows.sort(key=lambda r: r["since"])
    return rows
