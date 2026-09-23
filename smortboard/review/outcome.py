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
from smortboard.telemetry import card_telemetry

# what makes an attempt "real" - it reached something past the worktree cut. an attempt with none
# of these never reached the worker (a refusal before work started) and must not hide the last one
# that did
_OUTCOME_KINDS = frozenset({"worker_summary", "test_gate", "review_gate", "merge_request"})
_LEASE_KINDS = frozenset({"lease_wanted", "lease_expanded"})


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


def _latest_run(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    starts = [i for i, event in enumerate(events) if event["kind"] == "lifecycle_started"]
    return events[starts[-1] :] if starts else events


def _lease_paths(events: list[dict[str, Any]]) -> dict[str, list[str]]:
    """repo-relative paths the latest run wanted in its lease, or had it widened to, in order.

    the latest run itself, not the walk-back above: a run blocked on its lease writes no summary,
    and the paths it wanted are exactly what the operator has to decide on
    """
    found: dict[str, list[str]] = {kind: [] for kind in _LEASE_KINDS}
    for event in _latest_run(events):
        if event["kind"] not in _LEASE_KINDS:
            continue
        paths = event["payload"].get("paths")
        # tolerant of a malformed payload: a bad event drops out rather than breaking the panel
        if not isinstance(paths, list):
            continue
        seen = found[event["kind"]]
        seen.extend(p for p in paths if isinstance(p, str) and p and p not in seen)
    return found


def card_outcome(store: Store, card_id: str) -> dict[str, Any]:
    summary = tests = review = pr_url = None
    fix_rounds = 0
    events = store.list_events(card_id)
    for event in _latest_attempt(events):
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
    lease = _lease_paths(events)
    # every attempt, not just this one - the open card's header says what the card has cost so far
    totals = card_telemetry(store, card_id)["totals"]
    return {
        "summary": summary,
        "tests": tests,
        "review": review,
        "pr_url": pr_url,
        "fix_rounds": fix_rounds,
        "findings_route": store.findings_route(card_id),
        "lease_wanted": lease["lease_wanted"],
        "lease_expanded": lease["lease_expanded"],
        "runs": totals["attempts"],
        "turns": totals["turns"],
        "cost_usd": totals["cost_usd"],
        "cost_estimated": totals["cost_estimated"],
    }
