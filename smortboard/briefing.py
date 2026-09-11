"""RESUME BRIEFING: what a re-run of a card is told about its own earlier attempts.

A resumed card (an inbox answer arriving, or a re-run after a block) keeps its worktree and its
commits - see lifecycle.py's existing_worktree/add_worktree branch. Without this, the agent has no
memory of any of that and has to re-read the worktree from scratch to rediscover what it already
did. resume_briefing turns the event log (telemetry.py's own attempt-splitting logic) into a short
text: the latest attempt that reached the worker, in detail, plus one line per older attempt.

Deliberately reuses telemetry.py's private attempt-splitting and outcome classification rather than
re-deriving it, so this briefing and the cost telemetry never disagree about how an attempt ended.
"""

from __future__ import annotations

from typing import Any

from smortboard.store.api import Store
from smortboard.telemetry import _attempt_outcome, _attempts, _denial_target
from smortboard.timeline import _basename

_BRIEFING_CAP = 2500

_EDIT_TOOLS = {"Edit", "MultiEdit", "NotebookEdit", "Write"}


def _reached_worker(segment: list[dict[str, Any]]) -> bool:
    return any(event["kind"] == "worker_summary" for event in segment)


def _tool_use_blocks(segment: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocks = []
    for event in segment:
        if event["kind"] != "assistant":
            continue
        content = (event.get("payload") or {}).get("message", {}).get("content") or []
        if not isinstance(content, list):
            continue
        blocks += [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
    return blocks


def _files_touched(segment: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for block in _tool_use_blocks(segment):
        if block.get("name") not in _EDIT_TOOLS:
            continue
        file_path = str((block.get("input") or {}).get("file_path", ""))
        name = _basename(file_path)
        if name and name not in seen:
            seen.append(name)
    return seen


def _commands_run(segment: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for block in _tool_use_blocks(segment):
        if block.get("name") != "Bash":
            continue
        command = str((block.get("input") or {}).get("command", "")).strip()
        if command and command not in seen:
            seen.append(command)
    return seen


def _refused_commands(segment: list[dict[str, Any]]) -> list[str]:
    refused: list[str] = []
    for event in segment:
        if event["kind"] != "result":
            continue
        for denial in (event.get("payload") or {}).get("permission_denials") or []:
            if denial.get("tool_name") != "Bash":
                continue
            target = _denial_target(denial)
            if target and target not in refused:
                refused.append(target)
    return refused


def _latest_summary(segment: list[dict[str, Any]]) -> str | None:
    text = None
    for event in segment:
        if event["kind"] == "worker_summary":
            text = event["payload"].get("text")
    return text


def _latest_review_findings(segment: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    findings = None
    for event in segment:
        if event["kind"] == "review_gate":
            findings = event["payload"].get("findings") or []
    return findings


def _latest_test_gate(segment: list[dict[str, Any]]) -> dict[str, Any] | None:
    gate = None
    for event in segment:
        if event["kind"] == "test_gate":
            gate = event["payload"]
    return gate


def _format_finding(finding: dict[str, Any]) -> str:
    location = finding.get("file") or ""
    if finding.get("line") is not None:
        location += f":{finding['line']}"
    prefix = f"{location}: " if location else ""
    return f"- {finding.get('severity', '')} {finding.get('category', '')} {prefix}{finding.get('message', '')}".strip()


def _detail_lines(segment: list[dict[str, Any]], worktree_reused: bool) -> list[str]:
    lines: list[str] = []

    outcome = _attempt_outcome(segment)
    lines.append(f"Ended: {outcome}")

    gate = _latest_test_gate(segment)
    if gate is not None:
        verdict = "passed" if gate.get("passed") else "failed"
        lines.append(f"Test gate ({gate.get('command')}): {verdict}, exit {gate.get('exit_code')}")

    findings = _latest_review_findings(segment)
    if findings:
        lines.append("Reviewer findings:")
        lines += [_format_finding(f) for f in findings]
    elif findings is not None:
        lines.append("Reviewer: approved, no findings")

    files = _files_touched(segment)
    if files:
        lines.append(f"Files edited or written: {', '.join(files)}")

    commands = _commands_run(segment)
    if commands:
        lines.append("Commands run:")
        lines += [f"- {c}" for c in commands]

    refused = _refused_commands(segment)
    if refused:
        lines.append("Commands refused:")
        lines += [f"- {c}" for c in refused]

    summary = _latest_summary(segment)
    if summary:
        lines.append(f"Final worker summary: {summary}")

    # a rejected card's branch is deleted, so its next run starts clean from the base branch
    if worktree_reused:
        lines.append("Its commits are already on this branch.")
    else:
        lines.append("Its branch is gone: this run starts fresh from the base branch.")
    return lines


def _truncate(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    return text[:cap].rstrip() + "\n...[truncated]"


def resume_briefing(store: Store, card_id: str, worktree_reused: bool = True) -> str | None:
    """a compact summary of earlier attempts, for a resumed card's brief.

    None when no earlier attempt ever reached the worker - a fresh card, or one refused before it
    got that far, has nothing worth summarising.
    """
    segments = _attempts(store.list_events(card_id))
    # the lifecycle asks after recording this run's own start - that run is not an earlier attempt
    if segments and all(event["kind"] == "lifecycle_started" for event in segments[-1]):
        segments = segments[:-1]
    worker_indexes = [i for i, segment in enumerate(segments) if _reached_worker(segment)]
    if not worker_indexes:
        return None

    latest_index = worker_indexes[-1]
    lines = [f"Attempt {latest_index + 1} of {len(segments)} (most recent that ran):"]
    lines += _detail_lines(segments[latest_index], worktree_reused)

    older = [(i, s) for i, s in enumerate(segments) if i != latest_index]
    if older:
        lines += ["", "Earlier attempts:"]
        lines += [f"- attempt {i + 1}: {_attempt_outcome(s)}" for i, s in older]

    return _truncate("\n".join(lines), _BRIEFING_CAP)
