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

from dataclasses import dataclass
from typing import Any

from smortboard.store.api import Store
from smortboard.telemetry import _attempt_outcome, _attempts, _denial_target
from smortboard.timeline import _basename

_BRIEFING_CAP = 2500

_EDIT_TOOLS = {"Edit", "MultiEdit", "NotebookEdit", "Write"}


# what a gate's result depends on besides the code. measured on the live board: 38 failed gates,
# none caused by card code - a missing tool in the image, a read-only cache, a board-written lint
# command. a rebuilt image or an edited test command is a change worth a gate-only re-run
ENVIRONMENT_KEYS = {"image": "image", "image_id": "image", "test_command": "test command"}


@dataclass(frozen=True)
class Resume:
    """a re-run that picks up at the test gate, because the worker has nothing new to do"""

    reason: str
    # the last clean worker run's own summary - the screenshot step reads its SCREENSHOT: line
    summary: str | None = None
    # the reviewer already approved this exact head, so only the gate re-runs
    review_approved: bool = False


def _reached_worker(segment: list[dict[str, Any]]) -> bool:
    return any(event["kind"] == "worker_summary" for event in segment)


def _ran(segment: list[dict[str, Any]]) -> bool:
    """reached the worker, or skipped it on purpose for a gate-only re-run"""
    return any(event["kind"] in ("worker_summary", "worker_skipped") for event in segment)


def _started(segment: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (e["payload"] for e in reversed(segment) if e["kind"] == "attempt_fingerprint"), None
    )


def _previous_attempts(store: Store, card_id: str) -> list[list[dict[str, Any]]]:
    segments = _attempts(store.list_events(card_id))
    # the lifecycle asks after recording this run's own start - that run is not an earlier attempt
    if segments and all(event["kind"] == "lifecycle_started" for event in segments[-1]):
        segments = segments[:-1]
    return segments


def repeats_failed_attempt(store: Store, card_id: str, fingerprint: dict[str, Any]) -> bool:
    """true when the last attempt that reached the worker (or skipped it for a gate-only re-run)
    ended on a failed test gate and started from exactly this fingerprint - same branch head, same
    base head, same operator notes, same image and test command.

    Nothing the worker sees or the gate runs would differ, so another run only repeats the failure
    at the cost of a worker run. Measured 2026-09-19: three cards each re-ran a worker that said
    "all work already committed" (~$0.25 a run) into the same gate failure.
    """
    previous = next((s for s in reversed(_previous_attempts(store, card_id)) if _ran(s)), None)
    if previous is None:
        return False
    gate = _latest_test_gate(previous)
    return _started(previous) == fingerprint and gate is not None and not gate.get("passed")


def _last_worker_run(segment: list[dict[str, Any]]) -> dict[str, Any] | None:
    """the segment's last worker run, if it finished cleanly and recorded its head"""
    last = None
    for event in segment:
        if event["kind"] == "worker_summary":
            last = event["payload"]
        elif event["kind"] == "fix_round":
            last = None  # a fix run started; only its own summary says it finished
    return last if last and last.get("clean") and last.get("head") else None


def _verdict_for(segments: list[list[dict[str, Any]]], head: str) -> dict[str, Any] | None:
    """the latest review that judged exactly `head` - a failed review run is not one"""
    reviews = [
        event["payload"]
        for segment in segments
        for event in segment
        if event["kind"] == "review_gate" and event["payload"].get("head") == head
    ]
    verdicts = [payload for payload in reviews if not payload.get("error")]
    return verdicts[-1] if verdicts else None


def resume_point(store: Store, card_id: str, fingerprint: dict[str, Any]) -> Resume | None:
    """a gate-and-review re-run instead of a worker run, or None when the worker has to run.

    The worker is skipped only when its last clean run left exactly this branch head, no operator
    note arrived since that run started, every attempt after it skipped the worker too, and no
    reviewer finding on this head waits for a fix. A red gate also needs its image or test command
    to have changed since - otherwise the worker runs, as before, to fix what the gate names.
    Measured on the live board: a reviewer outage or usage limit re-ran the worker (avg $1.12, up
    to ~$5) only to reach the same review again.
    """
    segments = _previous_attempts(store, card_id)
    index = next((i for i in reversed(range(len(segments))) if _reached_worker(segments[i])), None)
    if index is None:
        return None
    worker = segments[index]
    run, started = _last_worker_run(worker), _started(worker)
    if run is None or started is None or run["head"] != fingerprint.get("head"):
        return None
    if started.get("notes") != fingerprint.get("notes"):
        return None  # a note is for the worker
    later = [s for s in segments[index + 1 :] if _started(s) is not None]
    if any(not any(e["kind"] == "worker_skipped" for e in s) for s in later):
        return None  # a later worker run started and never finished cleanly
    verdict = _verdict_for(segments[index:], run["head"])
    if verdict is not None and not verdict.get("approved"):
        return None  # the reviewer's findings are the worker's to fix
    last = later[-1] if later else worker
    gate = _latest_test_gate(last)
    if gate is not None and not gate.get("passed"):
        before = _started(last) or {}
        changed = sorted(
            {
                label
                for key, label in ENVIRONMENT_KEYS.items()
                if before.get(key) != fingerprint.get(key)
            }
        )
        if not changed:
            return None
        reason = f"the repo's {' and '.join(changed)} changed since its tests failed"
    else:
        reason = "its last worker run already finished this branch head"
    return Resume(reason=reason, summary=run.get("text"), review_approved=verdict is not None)


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


def _latest_merge_conflict(segment: list[dict[str, Any]]) -> dict[str, Any] | None:
    conflict = None
    for event in segment:
        if event["kind"] == "merge_conflict":
            conflict = event["payload"]
    return conflict


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

    conflict = _latest_merge_conflict(segment)
    if conflict is not None:
        files = ", ".join(conflict.get("files") or [])
        lines.append(
            f"Merging {conflict.get('base_ref')} into this branch conflicted in: {files}. "
            f"Merge {conflict.get('base_ref')} yourself, resolve those files, then commit the merge."
        )

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
    segments = _previous_attempts(store, card_id)
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
