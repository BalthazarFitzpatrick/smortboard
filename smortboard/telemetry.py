"""pure projections over the store and the run registry - no model calls, nothing derived by asking.

roster: every card an agent currently holds, working or blocked, with a one-line activity read off
its own latest event rather than asked for (see docs/PLAN.md Phase 5's roster unit).
usage: rate-limit windows and model spend, summed straight off the event log.
"""

from __future__ import annotations

from typing import Any

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
    RunRegistry.active() through in that shape, so telemetry never imports server.runs."""
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

    running_ids = {r["card_id"] for r in active_runs}
    blocked = []
    for card in store.list_doing_cards_blocked():
        if card["id"] in running_ids:
            continue  # a card can be both "doing" and mid-retry-run; the run wins
        blocked.append(
            {
                "card_id": card["id"],
                "board_id": card["board_id"],
                "title": card["title"],
                "state": "blocked",
                "reason": card["blocked_reason_code"],
                "activity": f"blocked: {card['blocked_reason_code']}",
            }
        )

    return working + blocked


def _window_from_rate_limit_event(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """one rate_limit_event -> its window rows, in either shape the CLI has emitted.

    current shape has no utilisation at all: {"rate_limit_info": {"status", "resetsAt",
    "rateLimitType"}}. older builds nested per-window figures under "unifiedWindows". never invent
    a number neither shape provided.
    """
    info = payload.get("rate_limit_info") or {}
    unified = info.get("unifiedWindows")
    if isinstance(unified, dict):
        return [
            {
                "type": window_type,
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
            "status": info.get("status"),
            "resets_at": info.get("resetsAt"),
            "utilization": None,
        }
    ]


def usage_projection(store: Store) -> dict[str, Any]:
    rate_events = store.list_events_by_kind(["rate_limit_event"])
    latest_by_type: dict[str, dict[str, Any]] = {}
    for event in rate_events:
        for window in _window_from_rate_limit_event(event["payload"]):
            # later events overwrite earlier ones per type - list_events_by_kind is oldest first
            latest_by_type[window["type"]] = window
    windows = list(latest_by_type.values())

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
