"""what the latest run of a card left behind, read back out of the event log for a human deciding.

A projection and nothing else: there is no second record of any of this to drift from the log.
Only events after the latest `lifecycle_started` count, so a card rejected and run again shows the
new attempt rather than a mix of both.

The worker summary is the agent's own account of what it did. It sits beside the two verdicts, not
in place of them - the gates exist because the board does not take the agent's word.
"""

from __future__ import annotations

from typing import Any

from smortboard.store.api import Store


def _latest_attempt(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    starts = [i for i, event in enumerate(events) if event["kind"] == "lifecycle_started"]
    return events[starts[-1] :] if starts else events


def card_outcome(store: Store, card_id: str) -> dict[str, Any]:
    summary = tests = review = pr_url = None
    fix_rounds = 0
    for event in _latest_attempt(store.list_events(card_id)):
        kind, payload = event["kind"], event["payload"]
        # the first summary is the work; a fix round's summary only describes the fix
        if kind == "worker_summary" and summary is None:
            summary = payload.get("text")
        elif kind == "test_gate":
            tests = payload
        elif kind == "review_gate":
            review = payload
        elif kind == "merge_request" and payload.get("url"):
            pr_url = payload["url"]
        elif kind == "fix_round":
            fix_rounds += 1
    return {
        "summary": summary,
        "tests": tests,
        "review": review,
        "pr_url": pr_url,
        "fix_rounds": fix_rounds,
        "findings_route": store.findings_route(card_id),
    }
