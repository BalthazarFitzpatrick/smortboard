"""pure projections over the store and the run registry - no model calls, nothing derived by asking.

roster: every card an agent currently holds - a live run only - with a one-line activity read off
its own latest event rather than asked for (see docs/PLAN.md Phase 5's roster unit). a blocked card
belongs to the attention inbox instead, not to this list.
usage: rate-limit windows and model spend, summed straight off the event log.
"""

from __future__ import annotations

from typing import Any

from smortboard import profiles
from smortboard.exec.runner import classify_rate_limit, classify_result
from smortboard.store.api import Store

# tool_use -> the activity phrase it reads as. order matters only for readability; lookup is by key
_TOOL_ACTIVITY = {
    "Edit": "editing {name}",
    "Write": "editing {name}",
    "MultiEdit": "editing {name}",
    "NotebookEdit": "editing {name}",
    "Read": "reading {name}",
    "Grep": "searching",
    "Glob": "searching",
    "StructuredOutput": "writing its verdict",
}

# a running card's lifecycle phase, read straight off RunState.phase - takes priority over its
# latest tool_use, since "testing" is a truer description than whatever Edit it made a minute ago
_PHASE_ACTIVITY = {
    "testing": "running the test gate",
    "reviewing": "reviewing the diff",
    "fixing": "fixing review findings",
    "opening": "opening the pull request",
}


def _basename(file_path: str) -> str:
    return file_path.rsplit("/", 1)[-1]


def _bash_summary(command: str) -> str:
    return f"running {command[:48]}"


def _activity_from_event(event: dict[str, Any] | None) -> str:
    if event is None:
        return "starting"
    content = (event.get("payload") or {}).get("message", {}).get("content") or []
    if not isinstance(content, list):
        content = []
    tool_use = next(
        (b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"), None
    )
    if tool_use is not None:
        name = tool_use.get("name")
        if name == "Bash":
            command = str((tool_use.get("input") or {}).get("command", ""))
            return _bash_summary(command)
        file_path = str((tool_use.get("input") or {}).get("file_path", ""))
        template = _TOOL_ACTIVITY.get(name)
        if template:
            return (
                template.format(name=_basename(file_path))
                if file_path
                else template.format(name="")
            )
    text_block = next((b for b in content if isinstance(b, dict) and b.get("type") == "text"), None)
    if text_block is not None:
        return "thinking"
    return "starting"


def _latest_assistant_event(store: Store, card_id: str) -> dict[str, Any] | None:
    events = [e for e in store.list_events(card_id) if e["kind"] == "assistant"]
    return events[-1] if events else None


def _activity_for_running_card(store: Store, card_id: str, phase: str | None) -> str:
    if phase in _PHASE_ACTIVITY:
        return _PHASE_ACTIVITY[phase]
    return _activity_from_event(_latest_assistant_event(store, card_id))


def roster_rows(store: Store, active_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`active_runs` is `[{"card_id", "phase"}, ...]` - the caller (the http handler) passes
    RunRegistry.active() through in that shape, so telemetry never imports server.runs.

    only cards an agent currently holds - a live run. a blocked card is not an agent working on
    anything; it belongs to the attention inbox (see attention_rows), not here."""
    working = []
    for run in active_runs:
        card = store.get_card(run["card_id"])
        working.append(
            {
                "card_id": card["id"],
                "board_id": card["board_id"],
                "title": card["title"],
                "state": "working",
                "reason": None,
                "activity": _activity_for_running_card(store, card["id"], run.get("phase")),
            }
        )
    return working


def _window_from_rate_limit_event(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """one rate_limit_event -> its window rows, in either shape the CLI has emitted.

    current shape has no utilisation at all: {"rate_limit_info": {"status", "resetsAt",
    "rateLimitType"}}. older builds nested per-window figures under "unifiedWindows". never invent
    a number neither shape provided.
    """
    # "default" for a run recorded before profile attribution existed (runner.py stamps every new
    # rate_limit_event with the profile active when it landed) - never invented for an old event
    profile = payload.get("profile") or profiles.DEFAULT_PROFILE
    info = payload.get("rate_limit_info") or {}
    unified = info.get("unifiedWindows")
    if isinstance(unified, dict):
        return [
            {
                "type": window_type,
                "profile": profile,
                "status": window.get("status") or info.get("status"),
                "resets_at": window.get("resetsAt"),
                "utilization": window.get("utilization"),
            }
            for window_type, window in unified.items()
        ]
    window_type = info.get("rateLimitType")
    if not window_type:
        return []
    return [
        {
            "type": window_type,
            "profile": profile,
            "status": info.get("status"),
            "resets_at": info.get("resetsAt"),
            "utilization": None,
        }
    ]


def usage_projection(store: Store) -> dict[str, Any]:
    rate_events = store.list_events_by_kind(["rate_limit_event"])
    latest_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for event in rate_events:
        for window in _window_from_rate_limit_event(event["payload"]):
            # later events overwrite earlier ones per (window type, profile) - list_events_by_kind
            # is oldest first by wall-clock time, so the last write per key is the true latest
            latest_by_key[(window["type"], window["profile"])] = window
    windows = list(latest_by_key.values())

    result_events = store.list_events_by_kind(["result"])
    models: dict[str, dict[str, float]] = {}
    total_cost = 0.0
    for event in result_events:
        payload = event["payload"]
        total_cost += float(payload.get("total_cost_usd") or 0)
        for model, usage in (payload.get("modelUsage") or {}).items():
            row = models.setdefault(
                model,
                {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_creation_tokens": 0,
                    "cost_usd": 0.0,
                },
            )
            row["input_tokens"] += usage.get("inputTokens") or 0
            row["output_tokens"] += usage.get("outputTokens") or 0
            row["cache_read_tokens"] += usage.get("cacheReadInputTokens") or 0
            row["cache_creation_tokens"] += usage.get("cacheCreationInputTokens") or 0
            row["cost_usd"] += float(usage.get("costUSD") or 0)

    return {
        "windows": windows,
        "models": [{"model": model, **fields} for model, fields in models.items()],
        "total_cost_usd": round(total_cost, 6),
        "runs": len(result_events),
    }


# -- cost telemetry: per-card attempts and the board's cost table --------------------------------
#
# an "attempt" is one lifecycle_started event and everything that follows it, up to the next
# lifecycle_started (or the end of the log) - same split review/outcome.py uses for the latest
# attempt, generalised here to every attempt so a card's history stays visible across re-runs.
#
# a `result` stream event carries no field saying whether it was the worker or the reviewer - both
# run through the same container runtime. it is tagged by what immediately follows it in the log:
# a `worker_summary` (appended right after a worker run) or a `review_gate` (right after a review),
# whichever comes first, before the next `result`. a run that never reported back (blocked or
# crashed) is placed by what came before it instead - see _result_role.
_ROLE_AFTER_RESULT = {"worker_summary": "worker", "review_gate": "reviewer"}


def _attempts(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """splits one card's events into attempts, oldest first. no lifecycle_started at all means the
    whole log is one (pre-telemetry or refused-before-start) attempt."""
    starts = [i for i, event in enumerate(events) if event["kind"] == "lifecycle_started"]
    if not starts:
        return [events] if events else []
    bounds = list(zip(starts, starts[1:] + [len(events)], strict=True))
    return [events[start:end] for start, end in bounds]


def _result_role(segment: list[dict[str, Any]], index: int) -> str | None:
    for event in segment[index + 1 :]:
        if event["kind"] == "result":
            break  # another result arrived first - read the role off what came before instead
        role = _ROLE_AFTER_RESULT.get(event["kind"])
        if role:
            return role
    # a run that blocked or crashed reports nothing after it, but its money was still spent: the
    # lifecycle's own order says whose it was - the reviewer runs straight after the test gate, the
    # worker after the attempt's start or a fix round
    for event in reversed(segment[:index]):
        if event["kind"] == "test_gate":
            return "reviewer"
        if event["kind"] in ("lifecycle_started", "fix_round"):
            return "worker"
    return "worker"


def _denial_target(denial: dict[str, Any]) -> str:
    tool_input = denial.get("tool_input") or {}
    return str(tool_input.get("file_path") or tool_input.get("command") or "")


def _attempt_outcome(segment: list[dict[str, Any]]) -> str:
    """how this attempt ended, read off its own events - honestly means never inventing a reason
    the log does not carry. checked newest-first within the attempt, most decisive signal first."""
    for event in reversed(segment):
        if event["kind"] == "merge_request" and event["payload"].get("url"):
            return "pull request"
    if any(event["kind"] == "run_refused" for event in segment):
        return "refused"
    if any(event["kind"] == "run_stopped" for event in segment):
        return "stopped"
    if any(event["kind"] == "run_orphaned" for event in segment):
        return "blocked: CRASH"  # the board died under it - see server.runs.recover_orphaned_runs
    if any(event["kind"] == "merge_conflict" for event in segment):
        return "blocked: MERGE_CONFLICT"
    for event in reversed(segment):
        if event["kind"] == "rate_limit_event":
            reason = classify_rate_limit(event["payload"])
            if reason:
                return f"blocked: {reason}"
    for event in reversed(segment):
        if event["kind"] == "result":
            reason = classify_result(event["payload"])
            if reason:
                return f"blocked: {reason}"
    for event in reversed(segment):
        if event["kind"] == "review_gate" and not event["payload"].get("approved"):
            return "blocked: REVIEW_REJECTED"
    for event in reversed(segment):
        if event["kind"] == "test_gate" and not event["payload"].get("passed"):
            return "blocked: TESTS_FAILED"
    if not any(event["kind"] == "worker_summary" for event in segment):
        return "refused"  # never reached the worker - no repo, no runtime, no worktree
    return "in progress"


def _add_model_spend(spend: dict[str, float], payload: dict[str, Any]) -> None:
    for model, usage in (payload.get("modelUsage") or {}).items():
        spend[model] = spend.get(model, 0.0) + float((usage or {}).get("costUSD") or 0)


def _main_model(spend: dict[str, float]) -> str | None:
    """the model that did the work - the costliest one, not the helper billed alongside it"""
    return max(spend, key=spend.__getitem__) if spend else None


def _summarize_attempt(segment: list[dict[str, Any]]) -> dict[str, Any]:
    worker_cost = reviewer_cost = 0.0
    worker_turns = reviewer_turns = 0
    # cost per model name - claude code bills a small helper model on the side of every run
    worker_spend: dict[str, float] = {}
    reviewer_spend: dict[str, float] = {}
    denials: list[dict[str, str]] = []
    fix_rounds = 0

    for i, event in enumerate(segment):
        kind, payload = event["kind"], event["payload"]
        if kind == "result":
            role = _result_role(segment, i)
            cost = float(payload.get("total_cost_usd") or 0)
            turns = int(payload.get("num_turns") or 0)
            if role == "reviewer":
                reviewer_cost += cost
                reviewer_turns += turns
                _add_model_spend(reviewer_spend, payload)
            elif role == "worker":
                worker_cost += cost
                worker_turns += turns
                _add_model_spend(worker_spend, payload)
            for denial in payload.get("permission_denials") or []:
                denials.append(
                    {"tool": denial.get("tool_name") or "", "target": _denial_target(denial)}
                )
        elif kind == "fix_round":
            fix_rounds += 1

    return {
        "started_at": segment[0]["created_at"] if segment else None,
        "outcome": _attempt_outcome(segment),
        "worker_model": _main_model(worker_spend),
        "reviewer_model": _main_model(reviewer_spend),
        "worker_cost_usd": round(worker_cost, 6),
        "reviewer_cost_usd": round(reviewer_cost, 6),
        "cost_usd": round(worker_cost + reviewer_cost, 6),
        "worker_turns": worker_turns,
        "reviewer_turns": reviewer_turns,
        "turns": worker_turns + reviewer_turns,
        "fix_rounds": fix_rounds,
        "refusal_count": len(denials),
        "refusals": denials,
    }


def card_telemetry(store: Store, card_id: str) -> dict[str, Any]:
    """every attempt this card has made, oldest first, plus totals across all of them.

    a fresh run of a card that went through review/accept/reject before does not erase the earlier
    attempts here - unlike card_outcome, which only cares about the most recent one.
    """
    return _telemetry_for(store, store.get_card(card_id))


def _telemetry_for(store: Store, card: dict[str, Any]) -> dict[str, Any]:
    attempts = [_summarize_attempt(s) for s in _attempts(store.list_events(card["id"]))]
    total_cost = sum(a["cost_usd"] for a in attempts)
    refusal_cost = sum(a["cost_usd"] for a in attempts if a["refusal_count"])
    return {
        "card_id": card["id"],
        "title": card["title"],
        "model": card.get("model"),
        "attempts": attempts,
        "totals": {
            "cost_usd": round(total_cost, 6),
            "attempts": len(attempts),
            "turns": sum(a["turns"] for a in attempts),
            "fix_rounds": sum(a["fix_rounds"] for a in attempts),
            "refusal_count": sum(a["refusal_count"] for a in attempts),
            # the waste signal: what got spent on a run that also hit a permission denial,
            # whether or not the denial ended up mattering to the outcome
            "refusal_cost_usd": round(refusal_cost, 6),
        },
    }


def board_costs(store: Store, board_id: str) -> list[dict[str, Any]]:
    """one row per card on this board, costliest first - what the orchestrator's model choices
    actually cost, and how much of that rode along with a refused command or file edit."""
    rows = []
    for card in store.list_cards(board_id):
        telemetry = _telemetry_for(store, card)
        attempts = telemetry["attempts"]
        last_outcome = attempts[-1]["outcome"] if attempts else "no attempts yet"
        rows.append(
            {
                "card_id": card["id"],
                "title": card["title"],
                "model": card.get("model"),
                "cost_usd": telemetry["totals"]["cost_usd"],
                "attempts": telemetry["totals"]["attempts"],
                "refusal_count": telemetry["totals"]["refusal_count"],
                "refusal_cost_usd": telemetry["totals"]["refusal_cost_usd"],
                "last_outcome": last_outcome,
            }
        )
    rows.sort(key=lambda r: r["cost_usd"], reverse=True)
    return rows


def _board_overview_row(store: Store, board: dict[str, Any]) -> dict[str, Any]:
    """one board's spend rolled up across all its cards - same attempt data board_costs reads,
    just summed rather than listed per card."""
    cost_usd = 0.0
    runs = 0
    accepted = 0
    prs_opened = 0
    refusal_cost_usd = 0.0
    worker_cost_usd = 0.0
    reviewer_cost_usd = 0.0
    spend_by_model: dict[str, float] = {}

    cards = store.list_cards(board["id"])
    for card in cards:
        telemetry = _telemetry_for(store, card)
        attempts = telemetry["attempts"]
        cost_usd += telemetry["totals"]["cost_usd"]
        runs += telemetry["totals"]["attempts"]
        refusal_cost_usd += telemetry["totals"]["refusal_cost_usd"]
        if card.get("status") == "accepted":
            accepted += 1
        for attempt in attempts:
            if attempt["outcome"] == "pull request":
                prs_opened += 1
            worker_cost_usd += attempt["worker_cost_usd"]
            reviewer_cost_usd += attempt["reviewer_cost_usd"]
            if attempt["worker_model"]:
                spend_by_model[attempt["worker_model"]] = (
                    spend_by_model.get(attempt["worker_model"], 0.0) + attempt["worker_cost_usd"]
                )
            if attempt["reviewer_model"]:
                spend_by_model[attempt["reviewer_model"]] = (
                    spend_by_model.get(attempt["reviewer_model"], 0.0)
                    + attempt["reviewer_cost_usd"]
                )

    return {
        "board_id": board["id"],
        "board_name": board["name"],
        "cost_usd": round(cost_usd, 6),
        "runs": runs,
        "cards": len(cards),
        "cards_accepted": accepted,
        "pull_requests_opened": prs_opened,
        "refusal_cost_usd": round(refusal_cost_usd, 6),
        "worker_cost_usd": round(worker_cost_usd, 6),
        "reviewer_cost_usd": round(reviewer_cost_usd, 6),
        "spend_by_model": [
            {"model": model, "cost_usd": round(cost, 6)} for model, cost in spend_by_model.items()
        ],
        "cost_per_pr_usd": round(cost_usd / prs_opened, 6) if prs_opened else None,
    }


def _sum_spend_by_model(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, float] = {}
    for row in rows:
        for entry in row["spend_by_model"]:
            totals[entry["model"]] = totals.get(entry["model"], 0.0) + entry["cost_usd"]
    return [{"model": model, "cost_usd": round(cost, 6)} for model, cost in totals.items()]


def _card_outcome_group(store: Store, card: dict[str, Any]) -> tuple[str, float] | None:
    """which of the c panel's two groups this card's latest attempt belongs to, and its total
    cost across all attempts - None for a card still running, never run, or blocked on anything
    else (CRASH, USAGE_LIMIT, a lease wait, ...): those count in neither group."""
    telemetry = _telemetry_for(store, card)
    if not telemetry["attempts"]:
        return None
    outcome = telemetry["attempts"][-1]["outcome"]
    if outcome == "pull request":
        return "accepted", telemetry["totals"]["cost_usd"]
    if outcome in ("refused", "blocked: REVIEW_REJECTED"):
        return "refused", telemetry["totals"]["cost_usd"]
    return None


def _empty_group() -> dict[str, Any]:
    return {"cards": 0, "cost_usd": 0.0, "cost_per_pr_usd": None}


def _cost_outcome_groups(store: Store) -> dict[str, Any]:
    """total cost and card count for the c panel's accepted/refused split, across every board -
    an em dash's worth of nothing (None) rather than a divide-by-zero when a group is empty."""
    groups = {"accepted": _empty_group(), "refused": _empty_group()}
    for board in store.list_boards():
        for card in store.list_cards(board["id"]):
            result = _card_outcome_group(store, card)
            if result is None:
                continue
            name, cost = result
            groups[name]["cards"] += 1
            groups[name]["cost_usd"] += cost
    for group in groups.values():
        group["cost_usd"] = round(group["cost_usd"], 6)
        if group["cards"]:
            group["cost_per_pr_usd"] = round(group["cost_usd"] / group["cards"], 6)
    return groups


def boards_overview(store: Store) -> dict[str, Any]:
    """one row per board, costliest first, plus a totals row across every board - the c panel's
    data. orchestrator (mission-control) turns are never counted here: build_board_snapshot's
    turns cost nothing the event log records, so the panel says so rather than guessing a number."""
    rows = [_board_overview_row(store, board) for board in store.list_boards()]
    rows.sort(key=lambda r: r["cost_usd"], reverse=True)

    totals = {
        "board_id": None,
        "board_name": "all boards",
        "cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
        "runs": sum(r["runs"] for r in rows),
        "cards": sum(r["cards"] for r in rows),
        "cards_accepted": sum(r["cards_accepted"] for r in rows),
        "pull_requests_opened": sum(r["pull_requests_opened"] for r in rows),
        "refusal_cost_usd": round(sum(r["refusal_cost_usd"] for r in rows), 6),
        "worker_cost_usd": round(sum(r["worker_cost_usd"] for r in rows), 6),
        "reviewer_cost_usd": round(sum(r["reviewer_cost_usd"] for r in rows), 6),
        "spend_by_model": _sum_spend_by_model(rows),
    }
    total_prs = totals["pull_requests_opened"]
    totals["cost_per_pr_usd"] = round(totals["cost_usd"] / total_prs, 6) if total_prs else None

    return {
        "boards": rows,
        "totals": totals,
        "orchestrator_turns_counted": False,
        "outcome_groups": _cost_outcome_groups(store),
    }


def _finished_card_evidence(store: Store, card: dict[str, Any]) -> dict[str, Any] | None:
    """this card's latest attempt, as evidence for the orchestrator - None while it is still
    running or has never been run, so an in-progress card never counts toward a model's record"""
    attempts = _attempts(store.list_events(card["id"]))
    if not attempts:
        return None
    summary = _summarize_attempt(attempts[-1])
    if summary["outcome"] == "in progress":
        return None
    return {
        "id": card["id"][:8],
        "model": summary["worker_model"] or card.get("model"),
        "cost_usd": summary["cost_usd"],
        "turns": summary["turns"],
        "fix_rounds": summary["fix_rounds"],
        "outcome": summary["outcome"],
    }


def board_evidence(store: Store, board_id: str) -> dict[str, Any]:
    """what the orchestrator sees of this board's own run history: each finished card's cost and
    outcome, plus a scorecard per worker model - kept to finished cards and rounded numbers so it
    stays small in every turn's prompt."""
    finished = [
        row
        for card in store.list_cards(board_id)
        if (row := _finished_card_evidence(store, card)) is not None
    ]

    by_model: dict[str, list[dict[str, Any]]] = {}
    for row in finished:
        if row["model"]:
            by_model.setdefault(row["model"], []).append(row)

    scorecard = []
    for model, rows in sorted(by_model.items()):
        clean_prs = sum(1 for r in rows if r["outcome"] == "pull request" and r["fix_rounds"] == 0)
        scorecard.append(
            {
                "model": model,
                "cards": len(rows),
                "clean_pr_rate": round(clean_prs / len(rows), 2),
                "avg_cost_usd": round(sum(r["cost_usd"] for r in rows) / len(rows), 4),
            }
        )

    return {"cards": finished, "model_scorecard": scorecard}
