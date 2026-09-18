"""the one place every run start is checked against the operator's spend caps.

Two caps, both optional (unset means no limit):
  - a board's daily_budget_usd (migration 17) - today's (UTC) board spend, scheduler + mission
    control + fold, all summed by telemetry.board_spend_today
  - a card's card_total_budget_usd setting - that card's own spend across every run it has ever
    made, restarts included, summed straight from its `result` events

spend_refusal is called before every run start: RunRegistry.start's callers, the scheduler, and
attention.answer_card (which approve_lease also goes through). A refusal means nothing starts.
"""

from __future__ import annotations

from typing import Any

from smortboard import telemetry
from smortboard.labs.events import cost_sum, event_cost, result_fields
from smortboard.store.api import Store


def _card_total_spend(store: Store, card_id: str) -> float | None:
    return cost_sum(
        event_cost(event)
        for event in store.list_events(card_id)
        if result_fields(event) is not None
    )


def spend_refusal(store: Store, card: dict[str, Any]) -> str | None:
    """a human reason this card must not start, or None if it may"""
    board = store.get_board(card["board_id"])
    daily_budget = board.get("daily_budget_usd")
    if daily_budget is not None:
        spent_today = telemetry.board_spend_today(store, board["id"])
        if spent_today is None:
            return "daily spend is unknown; configure model prices before enforcing a daily budget"
        if spent_today >= daily_budget:
            return (
                f"daily budget of ${daily_budget:.2f} for {board['name']} is spent "
                f"(${spent_today:.2f} today); it resets at 00:00 UTC"
            )

    raw_cap = store.get_settings().get("card_total_budget_usd")
    if raw_cap is not None:
        cap = float(raw_cap)
        spent = _card_total_spend(store, card["id"])
        if spent is None:
            return "this card's spend is unknown; configure model prices before enforcing its total cap"
        if spent >= cap:
            return f"this card has spent ${spent:.2f} of its ${cap:.2f} total cap"

    return None
