"""pure projections over the store and the run registry - no model calls, nothing derived by asking.

roster: every card an agent currently holds - a live run only - with a one-line activity read off
its own latest event rather than asked for (see docs/PLAN.md Phase 5's roster unit). a blocked card
belongs to the attention inbox instead, not to this list.
usage: rate-limit windows and model spend, summed straight off the event log.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from math import ceil
from typing import Any

from smortboard import profiles
from smortboard.exec.runner import classify_rate_limit, classify_result
from smortboard.store.api import Store

# result subtype set when a run is stopped by --max-budget-usd - see review.reviewer.BUDGET_STOP
_BUDGET_STOP_SUBTYPE = "error_max_budget_usd"
_MIN_RUNS_FOR_CAP_SUGGESTION = 5

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


def estimate_complexity(
    criteria_count: int,
    task_count: int,
    lease_glob_count: int,
    has_broad_lease: bool,
    model: str | None,
) -> int:
    """a guess at an unrated card's complexity (1/2/3), used only by cost analysis - never written
    back to the card, since a real rating always wins once set.

    score = criteria_count + task_count, plus 2 for a broad lease (one glob holding `**`, which
    reaches arbitrarily deep) or 1 for any lease at all, plus 2 more if the model is an opus alias
    (mission control reaches for opus when it already expects the work to need more judgement).
    score <= 3 is low (1), <= 6 is medium (2), anything higher is high (3).
    """
    score = criteria_count + task_count
    if has_broad_lease:
        score += 2
    elif lease_glob_count:
        score += 1
    if model and "opus" in model.lower():
        score += 2
    if score <= 3:
        return 1
    if score <= 6:
        return 2
    return 3


def _estimate_card_complexity(card: dict[str, Any]) -> int:
    """estimate_complexity fed straight from one card's own fields"""
    globs = [lease["path_glob"] for lease in card.get("leases") or []]
    return estimate_complexity(
        criteria_count=len(card.get("criteria") or []),
        task_count=len(card.get("tasks") or []),
        lease_glob_count=len(globs),
        has_broad_lease=any("**" in glob for glob in globs),
        model=card.get("model"),
    )


def _card_complexity(card: dict[str, Any]) -> tuple[int, bool]:
    """(level, rated) - the card's own rating if set, else an estimate"""
    rated = card.get("complexity")
    if rated in (1, 2, 3):
        return rated, True
    return _estimate_card_complexity(card), False


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


def board_spend_today(store: Store, board_id: str, *, today: str | None = None) -> float:
    """this board's spend so far today (UTC): each card's own `result` events plus mission
    control/fold spend (board_spend, migration 18) - what budgets.spend_refusal and
    scheduler._board_daily_budget compare daily_budget_usd against before starting anything new.
    `today` is a YYYY-MM-DD override, for tests only."""
    day = today or datetime.now(UTC).date().isoformat()
    total = 0.0
    for card in store.list_cards(board_id):
        for event in store.list_events(card["id"]):
            if event["kind"] != "result" or not str(event["created_at"]).startswith(day):
                continue
            total += float(event["payload"].get("total_cost_usd") or 0)
    for row in store.list_board_spend(board_id):
        if str(row["created_at"]).startswith(day):
            total += float(row["cost_usd"] or 0)
    return round(total, 6)


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


def _card_prs_opened(telemetry: dict[str, Any]) -> int:
    """how many of this card's attempts ended in a pull request - a card can be re-run after
    a merge, so this is a count, not a bool"""
    return sum(1 for a in telemetry["attempts"] if a["outcome"] == "pull request")


def _empty_group() -> dict[str, Any]:
    return {
        "cards": 0,
        "cost_usd": 0.0,
        "prs": 0,
        "cost_per_card_usd": None,
        "cost_per_pr_usd": None,
    }


def _add_to_group(group: dict[str, Any], cost: float, prs: int) -> None:
    group["cards"] += 1
    group["cost_usd"] += cost
    group["prs"] += prs


def _finalize_group(group: dict[str, Any]) -> None:
    group["cost_usd"] = round(group["cost_usd"], 6)
    if group["cards"]:
        group["cost_per_card_usd"] = round(group["cost_usd"] / group["cards"], 6)
    if group["prs"]:
        group["cost_per_pr_usd"] = round(group["cost_usd"] / group["prs"], 6)


def _cost_outcome_groups(store: Store) -> dict[str, Any]:
    """the c panel's 3x3 grid: total (every card), accepted (status == accepted), refused
    (status == rejected) - each with its own spend, price per card and price per pr, so the two
    divisions can be checked against the card/pr counts shown beside them. a queued or still-
    running card counts in total only, never in accepted or refused. an em dash's worth of
    nothing (None) rather than a divide-by-zero when a group is empty."""
    groups = {"total": _empty_group(), "accepted": _empty_group(), "refused": _empty_group()}
    for board in store.list_boards():
        for card in store.list_cards(board["id"]):
            telemetry = _telemetry_for(store, card)
            cost = telemetry["totals"]["cost_usd"]
            prs = _card_prs_opened(telemetry)
            _add_to_group(groups["total"], cost, prs)
            if card.get("status") == "accepted":
                _add_to_group(groups["accepted"], cost, prs)
            elif card.get("status") == "rejected":
                _add_to_group(groups["refused"], cost, prs)
    for group in groups.values():
        _finalize_group(group)
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
        "cost_groups": _cost_outcome_groups(store),
    }


def _attempt_capped(segment: list[dict[str, Any]]) -> bool:
    """whether this attempt was stopped by its worker's --max-budget-usd cap"""
    return any(
        event["kind"] == "result" and event["payload"].get("subtype") == _BUDGET_STOP_SUBTYPE
        for event in segment
    )


def _cap_fit_rows(store: Store, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """per complexity level, split rated vs estimated, worker run cost against the current
    worker cap - how often a level's runs actually fit inside it"""
    from smortboard.exec.runner import DEFAULT_CARD_BUDGET_USD

    cap = store.spend_cap("worker_budget_usd", DEFAULT_CARD_BUDGET_USD)
    buckets: dict[tuple[int, bool], list[float]] = {}
    for card in cards:
        level, rated = _card_complexity(card)
        for segment in _attempts(store.list_events(card["id"])):
            summary = _summarize_attempt(segment)
            if summary["worker_cost_usd"] <= 0:
                continue
            buckets.setdefault((level, rated), []).append(summary["worker_cost_usd"])
    rows = []
    for (level, rated), costs in sorted(buckets.items(), key=lambda kv: (kv[0][0], not kv[0][1])):
        hit_cap = sum(1 for c in costs if c >= cap)
        rows.append(
            {
                "complexity": level,
                "source": "rated" if rated else "estimated",
                "runs": len(costs),
                "within_cap": len(costs) - hit_cap,
                "hit_cap": hit_cap,
                "median_cost_usd": round(statistics.median(costs), 6),
                "max_cost_usd": round(max(costs), 6),
            }
        )
    return {"worker_cap_usd": cap, "rows": rows}


def _p90(costs: list[float]) -> float:
    """nearest-rank 90th percentile - the ordinary reading of "p90", not an interpolated one"""
    ordered = sorted(costs)
    index = max(0, ceil(0.9 * len(ordered)) - 1)
    return ordered[index]


def _suggested_caps(store: Store, role_costs: dict[str, list[float]]) -> dict[str, dict[str, Any]]:
    from smortboard.consolidate import FOLD_BUDGET_USD
    from smortboard.exec.runner import DEFAULT_CARD_BUDGET_USD
    from smortboard.orchestrator import DEFAULT_TURN_BUDGET_USD
    from smortboard.review.reviewer import DEFAULT_REVIEW_BUDGET_USD

    role_defaults = {
        "worker": ("worker_budget_usd", DEFAULT_CARD_BUDGET_USD),
        "reviewer": ("reviewer_budget_usd", DEFAULT_REVIEW_BUDGET_USD),
        "orchestrator": ("orchestrator_budget_usd", DEFAULT_TURN_BUDGET_USD),
        "fold": ("fold_budget_usd", FOLD_BUDGET_USD),
    }
    result = {}
    for role, (setting_key, default) in role_defaults.items():
        current_cap = store.spend_cap(setting_key, default)
        costs = role_costs.get(role, [])
        if len(costs) < _MIN_RUNS_FOR_CAP_SUGGESTION:
            result[role] = {"current_cap_usd": current_cap, "note": "not enough runs"}
            continue
        p90 = _p90(costs)
        suggested = ceil(p90 / 0.25) * 0.25
        would_cut = [c for c in costs if c > suggested]
        result[role] = {
            "current_cap_usd": current_cap,
            "runs": len(costs),
            "p90_cost_usd": round(p90, 6),
            "suggested_cap_usd": round(suggested, 6),
            "runs_that_would_be_cut": len(would_cut),
            "spend_difference_usd": round(sum(c - suggested for c in would_cut), 6),
        }
    return result


_WASTE_REASONS = {
    "hit cap": lambda outcome, capped, card_status, last: capped,
    "refused": lambda outcome, capped, card_status, last: outcome == "refused",
    "crashed": lambda outcome, capped, card_status, last: outcome == "blocked: CRASH",
    "rejected (review)": lambda outcome, capped, card_status, last: (
        outcome == "blocked: REVIEW_REJECTED"
    ),
    "rejected (operator)": lambda outcome, capped, card_status, last: (
        last and card_status == "rejected" and outcome == "pull request"
    ),
}


def _waste(store: Store, cards: list[dict[str, Any]]) -> dict[str, Any]:
    """spend on attempts that never turned into accepted work, grouped by why - the cap check
    goes first, since a capped run can also read as "refused" further down the chain"""
    totals: dict[str, dict[str, float]] = {}
    for card in cards:
        attempts = _attempts(store.list_events(card["id"]))
        for i, segment in enumerate(attempts):
            summary = _summarize_attempt(segment)
            capped = _attempt_capped(segment)
            is_last = i == len(attempts) - 1
            for reason, test in _WASTE_REASONS.items():
                if test(summary["outcome"], capped, card.get("status"), is_last):
                    bucket = totals.setdefault(reason, {"count": 0, "cost_usd": 0.0})
                    bucket["count"] += 1
                    bucket["cost_usd"] += summary["cost_usd"]
                    break
    rows = [
        {"reason": reason, "count": int(v["count"]), "cost_usd": round(v["cost_usd"], 6)}
        for reason, v in totals.items()
    ]
    rows.sort(key=lambda r: r["cost_usd"], reverse=True)
    return {"rows": rows, "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 6)}


def _model_fit(cards: list[dict[str, Any]], store: Store) -> list[dict[str, Any]]:
    """per model x complexity: how many cards, how many got accepted, what each accepted card
    cost, and how often the attempt needed a fix round before it got there"""
    groups: dict[tuple[str, int], dict[str, Any]] = {}
    for card in cards:
        level, _rated = _card_complexity(card)
        attempts = _attempts(store.list_events(card["id"]))
        summaries = [_summarize_attempt(s) for s in attempts]
        model = (
            card.get("model")
            or next((s["worker_model"] for s in reversed(summaries) if s["worker_model"]), None)
            or "default"
        )
        key = (model, level)
        group = groups.setdefault(
            key,
            {
                "model": model,
                "complexity": level,
                "cards": 0,
                "accepted_cards": 0,
                "accepted_cost_usd": 0.0,
                "attempts": 0,
                "attempts_needing_fix": 0,
            },
        )
        group["cards"] += 1
        group["attempts"] += len(summaries)
        group["attempts_needing_fix"] += sum(1 for s in summaries if s["fix_rounds"] > 0)
        if card.get("status") == "accepted":
            group["accepted_cards"] += 1
            group["accepted_cost_usd"] += sum(s["cost_usd"] for s in summaries)
    rows = []
    for group in groups.values():
        rows.append(
            {
                "model": group["model"],
                "complexity": group["complexity"],
                "cards": group["cards"],
                "accepted_cards": group["accepted_cards"],
                "cost_per_accepted_card_usd": (
                    round(group["accepted_cost_usd"] / group["accepted_cards"], 6)
                    if group["accepted_cards"]
                    else None
                ),
                "fix_round_share": (
                    round(group["attempts_needing_fix"] / group["attempts"], 4)
                    if group["attempts"]
                    else None
                ),
            }
        )
    rows.sort(key=lambda r: (r["model"], r["complexity"]))
    return rows


def cost_optimisation(store: Store, board_id: str | None = None) -> dict[str, Any]:
    """the cost-optimisation view's data: cap fit, suggested caps, waste and model fit, scoped to
    one board when `board_id` is given, else across every board"""
    boards = [store.get_board(board_id)] if board_id else store.list_boards()
    cards = [card for board in boards for card in store.list_cards(board["id"])]

    role_costs: dict[str, list[float]] = {"worker": [], "reviewer": []}
    for card in cards:
        for segment in _attempts(store.list_events(card["id"])):
            summary = _summarize_attempt(segment)
            if summary["worker_cost_usd"] > 0:
                role_costs["worker"].append(summary["worker_cost_usd"])
            if summary["reviewer_cost_usd"] > 0:
                role_costs["reviewer"].append(summary["reviewer_cost_usd"])

    return {
        "board_id": board_id,
        "cap_fit": _cap_fit_rows(store, cards),
        "suggested_caps": _suggested_caps(store, role_costs),
        "waste": _waste(store, cards),
        "model_fit": _model_fit(cards, store),
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
