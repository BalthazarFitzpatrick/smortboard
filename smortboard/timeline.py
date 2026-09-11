"""RUN REPLAY: a card's event log turned into a flat, ordered list of steps for the UI to scrub.

An attempt is the same span outcome.py already isolates: everything between one `lifecycle_started`
and the next (or the end of the log). This module lists every attempt, and flattens one of them -
by default the latest that reached the worker, same rule as outcome.py's `_latest_attempt`, so the
timeline and the outcome panel agree on which run they are both describing.

step kinds: narration, read, edit, write, run (Bash), search (Grep/Glob), tool (any other tool_use),
refused (a tool_use denied by name via result.permission_denials), test, review, pull request,
decision, fix round, summary. `ok` is True/False for anything with a pass/fail verdict, None for
narration and board bookkeeping that carries no verdict of its own.

payload sizes are bounded so one huge Write or a page of Bash output cannot bloat the response -
see _clip and the per-field caps below.
"""

from __future__ import annotations

from typing import Any

from smortboard.store.api import Store

# what makes an attempt "real" - mirrors review/outcome.py's _OUTCOME_KINDS exactly, so the two
# projections never disagree about which attempt is "the" run
_OUTCOME_KINDS = frozenset({"worker_summary", "test_gate", "review_gate", "merge_request"})

_EDIT_TOOLS = {"Edit", "MultiEdit", "NotebookEdit"}
_SEARCH_TOOLS = {"Grep", "Glob"}

# caps in characters, not bytes - good enough to keep one step from dominating the response
_TEXT_CAP = 4000
_STRING_CAP = 2000
_PREVIEW_CAP = 2000
_OUTPUT_CAP = 2000


def _clip(text: str | None, cap: int) -> tuple[str, bool]:
    if text is None:
        return "", False
    if len(text) <= cap:
        return text, False
    return text[:cap], True


def _basename(file_path: str) -> str:
    return file_path.rsplit("/", 1)[-1] if file_path else ""


def _attempt_bounds(events: list[dict[str, Any]]) -> list[tuple[int, int]]:
    starts = [i for i, event in enumerate(events) if event["kind"] == "lifecycle_started"]
    if not starts:
        return [(0, len(events))] if events else []
    return list(zip(starts, starts[1:] + [len(events)], strict=True))


def list_attempts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """one row per attempt, oldest first - the UI's attempt switcher"""
    attempts = []
    for index, (start, end) in enumerate(_attempt_bounds(events), start=1):
        segment = events[start:end]
        attempts.append(
            {
                "attempt": index,
                "started_at": segment[0]["created_at"] if segment else None,
                "step_count": end - start,
                "reached_worker": any(event["kind"] in _OUTCOME_KINDS for event in segment),
            }
        )
    return attempts


def _default_attempt(attempts: list[dict[str, Any]]) -> int:
    """the latest attempt that reached the worker, else the latest attempt at all -
    same fallback rule outcome.py uses so a refusal-only attempt never hides the last real run"""
    for attempt in reversed(attempts):
        if attempt["reached_worker"]:
            return attempt["attempt"]
    return attempts[-1]["attempt"] if attempts else 1


def _tool_results(segment: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """tool_use_id -> {content, is_error} for every tool_result in this attempt"""
    results: dict[str, dict[str, Any]] = {}
    for event in segment:
        if event["kind"] != "user":
            continue
        content = (event.get("payload") or {}).get("message", {}).get("content") or []
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tool_use_id = block.get("tool_use_id")
                if tool_use_id:
                    results[tool_use_id] = {
                        "content": block.get("content"),
                        "is_error": bool(block.get("is_error")),
                    }
    return results


def _denied_tool_use_ids(segment: list[dict[str, Any]]) -> set[str]:
    """tool_use ids the CLI's own permission system refused, read off `result` events -
    the only place a refusal is distinguishable from a tool that simply errored"""
    denied = set()
    for event in segment:
        if event["kind"] != "result":
            continue
        for denial in (event.get("payload") or {}).get("permission_denials") or []:
            tool_use_id = denial.get("tool_use_id")
            if tool_use_id:
                denied.add(tool_use_id)
    return denied


def _result_text(content: Any) -> str | None:
    """tool_result content is either a plain string or a list of {type: text, text} blocks"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(parts) if parts else None
    return None


def _step_for_tool_use(
    seq: int,
    created_at: str,
    block: dict[str, Any],
    results: dict[str, dict[str, Any]],
    denied: set[str],
) -> dict[str, Any]:
    name = block.get("name", "")
    tool_input = block.get("input") or {}
    tool_use_id = block.get("id")
    result = results.get(tool_use_id, {})
    is_error = bool(result.get("is_error"))
    refused = tool_use_id in denied

    file_path = str(tool_input.get("file_path", ""))
    command = str(tool_input.get("command", ""))
    pattern = str(tool_input.get("pattern", ""))
    detail: dict[str, Any] = {}

    if refused:
        kind = "refused"
        label = f"refused: {name} on {_basename(file_path) or command[:40] or pattern}".strip()
        detail["reason"] = "permission denied"
    elif name in _EDIT_TOOLS:
        kind = "edit"
        label = f"edited {_basename(file_path)}"
        old_text, old_clipped = _clip(str(tool_input.get("old_string", "")), _STRING_CAP)
        new_text, new_clipped = _clip(str(tool_input.get("new_string", "")), _STRING_CAP)
        detail = {
            "file_path": file_path,
            "old_text": old_text,
            "old_truncated": old_clipped,
            "new_text": new_text,
            "new_truncated": new_clipped,
        }
    elif name == "Write":
        kind = "write"
        label = f"wrote {_basename(file_path)}"
        preview, clipped = _clip(str(tool_input.get("content", "")), _PREVIEW_CAP)
        detail = {"file_path": file_path, "preview": preview, "truncated": clipped}
    elif name == "Read":
        kind = "read"
        label = f"read {_basename(file_path)}"
        detail = {"file_path": file_path}
    elif name == "Bash":
        kind = "run"
        label = f"ran {command[:60]}"
        detail = {"command": command}
    elif name in _SEARCH_TOOLS:
        kind = "search"
        label = f"searched for {pattern}" if pattern else f"ran {name}"
        detail = {"pattern": pattern, "path": tool_input.get("path", "")}
    else:
        kind = "tool"
        label = f"used {name}"
        detail = {"input": tool_input}

    output_text = _result_text(result.get("content"))
    if output_text is not None:
        clipped_output, output_truncated = _clip(output_text, _OUTPUT_CAP)
        detail["output"] = clipped_output
        detail["output_truncated"] = output_truncated

    ok: bool | None
    if refused or is_error:
        ok = False
    elif tool_use_id in results:
        ok = True
    else:
        ok = None  # no matching tool_result reached the log - e.g. the run was cut off mid-call

    return {
        "seq": seq,
        "created_at": created_at,
        "kind": kind,
        "label": label,
        "detail": detail,
        "ok": ok,
    }


def _steps_from_assistant(
    event: dict[str, Any], results: dict[str, dict[str, Any]], denied: set[str]
) -> list[dict[str, Any]]:
    content = (event.get("payload") or {}).get("message", {}).get("content") or []
    if not isinstance(content, list):
        return []
    steps = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text, truncated = _clip(block.get("text", ""), _TEXT_CAP)
            if not text.strip():
                continue
            label = text.splitlines()[0][:100] if text else "narration"
            steps.append(
                {
                    "seq": event["seq"],
                    "created_at": event["created_at"],
                    "kind": "narration",
                    "label": label,
                    "detail": {"text": text, "truncated": truncated},
                    "ok": None,
                }
            )
        elif block_type == "tool_use":
            steps.append(
                _step_for_tool_use(event["seq"], event["created_at"], block, results, denied)
            )
        # "thinking" blocks are deliberately skipped - internal reasoning, not an action taken
    return steps


def _step_for_other_kind(event: dict[str, Any]) -> dict[str, Any] | None:
    kind, payload = event["kind"], event["payload"]
    seq, created_at = event["seq"], event["created_at"]
    if kind == "worker_summary":
        text, truncated = _clip(payload.get("text"), _TEXT_CAP)
        return {
            "seq": seq,
            "created_at": created_at,
            "kind": "summary",
            "label": "worker summary",
            "detail": {"text": text, "truncated": truncated},
            "ok": None,
        }
    if kind == "test_gate":
        output, truncated = _clip(payload.get("output"), _OUTPUT_CAP)
        passed = payload.get("passed")
        return {
            "seq": seq,
            "created_at": created_at,
            "kind": "test",
            "label": "tests passed" if passed else "tests failed",
            "detail": {
                "command": payload.get("command"),
                "exit_code": payload.get("exit_code"),
                "output": output,
                "output_truncated": truncated,
            },
            "ok": bool(passed),
        }
    if kind == "review_gate":
        approved = payload.get("approved")
        return {
            "seq": seq,
            "created_at": created_at,
            "kind": "review",
            "label": "review approved" if approved else "review not approved",
            "detail": {"error": payload.get("error"), "findings": payload.get("findings") or []},
            "ok": bool(approved),
        }
    if kind == "merge_request":
        opened = payload.get("opened")
        return {
            "seq": seq,
            "created_at": created_at,
            "kind": "pull request",
            "label": "opened pull request" if opened else "pull request not opened",
            "detail": {
                "url": payload.get("url"),
                "branch": payload.get("branch"),
                "refusal": payload.get("refusal"),
                "already_existed": payload.get("already_existed"),
            },
            "ok": bool(opened) if payload.get("refusal") is None else False,
        }
    if kind == "fix_round":
        round_no = payload.get("round")
        return {
            "seq": seq,
            "created_at": created_at,
            "kind": "fix round",
            "label": f"fix round {round_no}",
            "detail": {"round": round_no},
            "ok": None,
        }
    if kind == "decision":
        decision = payload.get("decision")
        return {
            "seq": seq,
            "created_at": created_at,
            "kind": "decision",
            "label": f"board {decision}" if decision else "board decision",
            "detail": {"decision": decision, "reversed": payload.get("reversed")},
            "ok": (decision == "accepted") if decision else None,
        }
    return None


# event kinds that never produce their own step - either folded into another step (user/result) or
# pure CLI bookkeeping the timeline has no use for (system, rate_limit_event, lifecycle_started)
_SKIP_KINDS = frozenset({"system", "rate_limit_event", "lifecycle_started", "user", "result"})


def _flatten(segment: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = _tool_results(segment)
    denied = _denied_tool_use_ids(segment)
    steps: list[dict[str, Any]] = []
    for event in segment:
        kind = event["kind"]
        if kind == "assistant":
            steps.extend(_steps_from_assistant(event, results, denied))
        elif kind in _SKIP_KINDS:
            continue
        else:
            step = _step_for_other_kind(event)
            if step is not None:
                steps.append(step)
    return steps


def card_timeline(store: Store, card_id: str, attempt: int | None = None) -> dict[str, Any]:
    events = store.list_events(card_id)
    attempts = list_attempts(events)
    bounds = _attempt_bounds(events)

    if not attempts:
        return {"attempt": None, "attempts": [], "steps": []}

    chosen = attempt if attempt is not None else _default_attempt(attempts)
    chosen = max(1, min(chosen, len(attempts)))
    start, end = bounds[chosen - 1]
    steps = _flatten(events[start:end])

    return {"attempt": chosen, "attempts": attempts, "steps": steps}
