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

# what makes an attempt "real" - it reached something past the worktree cut. an attempt with none
# of these never reached the worker (a refusal before work started) and must not hide the last one
# that did
_OUTCOME_KINDS = frozenset({"worker_summary", "test_gate", "review_gate", "merge_request"})


def _latest_attempt(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    starts = [i for i, event in enumerate(events) if event["kind"] == "lifecycle_started"]
    if not starts:
        return events
    bounds = list(zip(starts, starts[1:] + [len(events)], strict=True))
    # walk back from the most recent attempt to the first one that produced an outcome
    for start, end in reversed(bounds):
        segment = events[start:end]
        if any(event["kind"] in _OUTCOME_KINDS for event in segment):
            return segment
    return events[starts[-1] :]


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
