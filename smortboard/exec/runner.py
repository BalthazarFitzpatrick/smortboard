"""headless `claude -p` runner: builds the invocation, streams events into the store, and
classifies the outcome into the store's blocked_reason_code vocabulary

Proven shapes come from S1/S2/S3 (`docs/spikes/S1-S2-findings.md`, `docs/spikes/S3-findings.md`).
`< /dev/null` is required or the process waits 3s for stdin on every card.
"""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smortboard.exec.leases import LEASE_CONFLICT_PREFIX
from smortboard.store.api import Store

# scoping decision (see report): a card run does NOT inherit the operator's personal playbook.
# `--setting-sources project` drops the user-level ~/.claude/settings.json and ~/.claude/CLAUDE.md
# that block-main-commit and friends live in — those rules are written for an interactive human who
# can answer a prompt, and S3 showed a card with nobody to ask just deadlocks on them. Only the
# repo's own project settings and our card-specific --settings file apply.
SETTING_SOURCES = "project"

# replaces the ambient personal CLAUDE.md a card would otherwise inherit. it states the two facts
# that CLAUDE.md would have supplied for an interactive session, so the agent neither re-derives
# them nor stops to ask: the branch already exists, and the lease is enforced, not a suggestion
SYSTEM_PROMPT = (
    "You are a headless worker executing one card in its own git worktree, already checked out on "
    "its branch. Do not create or switch branches, and do not ask for permission to commit — "
    "committing to this branch is expected. Writes outside your declared path lease are blocked by "
    "a hook; if one is refused, do not retry it, note it and continue with the rest of the task.\n\n"
    "House conventions:\n"
    "- commit messages: lowercase, past tense, no trailing period\n"
    "- no emojis anywhere\n"
    "- code comments: lowercase, one to three lines\n"
    "- run python via `uv run`, never `python`/`python3` directly"
)

DEFAULT_ALLOWED_TOOLS = ("Bash(git *)", "Edit", "Read", "Write")


def allowed_tools_for_repo(repo: dict[str, Any] | None) -> tuple[str, ...]:
    """derives a card's Bash allowlist from its repo's test_command.

    a card needs to run tests (Phase 3), but "let it run tests" must not mean "let it run any
    Bash command" - so the allowlist is scoped to exactly the repo's declared test invocation,
    plus git. a repo with no test_command declared keeps today's narrower default rather than
    being granted an unscoped Bash.
    """
    test_command = (repo or {}).get("test_command")
    if not test_command:
        return DEFAULT_ALLOWED_TOOLS
    return (
        "Read",
        "Edit",
        "Write",
        "Glob",
        "Grep",
        "Bash(git *)",
        f"Bash({test_command} *)",
    )


def build_command(
    prompt: str,
    settings_path: str | Path,
    model: str = "sonnet",
    allowed_tools: tuple[str, ...] = DEFAULT_ALLOWED_TOOLS,
) -> list[str]:
    """the proven S1 invocation shape, with our lease settings and scoping decision wired in"""
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "acceptEdits",
        "--setting-sources",
        SETTING_SOURCES,
        "--system-prompt",
        SYSTEM_PROMPT,
        "--settings",
        str(settings_path),
        "--model",
        model,
    ]
    for tool in allowed_tools:
        cmd += ["--allowedTools", tool]
    return cmd


@dataclass(frozen=True)
class RunResult:
    subtype: str | None
    is_error: bool
    blocked_reason_code: str | None
    session_id: str | None
    total_cost_usd: float | None
    num_turns: int | None
    result_text: str | None


def _agent_question_signal(result_event: dict[str, Any]) -> bool:
    """S3: a populated permission_denials plus a question in result.result is the AGENT_QUESTION
    signal — the agent stopped to ask something nobody headless can answer"""
    denials = result_event.get("permission_denials") or []
    text = (result_event.get("result") or "").lower()
    asked = any(marker in text for marker in ("?", "ok to proceed", "approval"))
    return bool(denials) and asked


def _lease_conflict_signal(result_event: dict[str, Any]) -> bool:
    """a lease refusal, per S3, is NOT a card failure — it still needs surfacing so the board can
    decide whether to park the card or extend the lease"""
    denials = result_event.get("permission_denials") or []
    if any(d.get("tool_name") in ("Edit", "Write") for d in denials):
        return True
    return LEASE_CONFLICT_PREFIX in (result_event.get("result") or "")


def classify_result(result_event: dict[str, Any]) -> str | None:
    """maps one `result` stream event onto the store's blocked_reason_code vocabulary, or None
    for a clean run. Order matters: a lease conflict is checked before is_error, since S3 showed
    the run still completes `subtype: success` when it hits one"""
    if _lease_conflict_signal(result_event):
        return "LEASE_CONFLICT"
    if _agent_question_signal(result_event):
        return "AGENT_QUESTION"
    if result_event.get("is_error"):
        return "CRASH"
    return None


def classify_rate_limit(rate_limit_event: dict[str, Any]) -> str | None:
    """S2: any status other than "allowed" across either window maps to USAGE_LIMIT"""
    info = rate_limit_event.get("rate_limit_info", {})
    if info.get("status") != "allowed":
        return "USAGE_LIMIT"
    return None


def parse_line(line: str) -> dict[str, Any] | None:
    """one stream-json line -> its event dict, or None for a blank/unparsable line"""
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def result_to_run_result(result_event: dict[str, Any]) -> RunResult:
    return RunResult(
        subtype=result_event.get("subtype"),
        is_error=bool(result_event.get("is_error")),
        blocked_reason_code=classify_result(result_event),
        session_id=result_event.get("session_id"),
        total_cost_usd=result_event.get("total_cost_usd"),
        num_turns=result_event.get("num_turns"),
        result_text=result_event.get("result"),
    )


def run_card(
    store: Store,
    card_id: str,
    worktree_path: str | Path,
    prompt: str,
    settings_path: str | Path,
    model: str = "sonnet",
    repo: dict[str, Any] | None = None,
) -> RunResult:
    """runs one card headlessly, recording every stream event into the store as it arrives

    subprocess is managed directly rather than via `claude --bg` + `claude stop`: we need to record
    each line into the event log as it streams, and a plain Popen gives us that plus a straightforward
    kill path (see stop_card) without a second process to poll for logs.
    """
    cmd = build_command(
        prompt, settings_path, model=model, allowed_tools=allowed_tools_for_repo(repo)
    )
    process = subprocess.Popen(
        cmd,
        cwd=str(worktree_path),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    result_event: dict[str, Any] | None = None
    blocked_reason_code: str | None = None
    assert process.stdout is not None
    for raw_line in process.stdout:
        event = parse_line(raw_line)
        if event is None:
            continue
        store.append_event(card_id, event.get("type", "unknown"), event)

        if event.get("type") == "result":
            result_event = event
        elif event.get("type") == "rate_limit_event":
            reason = classify_rate_limit(event)
            if reason:
                blocked_reason_code = reason

    process.wait()

    if result_event is None:
        # the process exited without ever emitting a result event - a crash, not a graceful stop
        return RunResult(
            subtype=None,
            is_error=True,
            blocked_reason_code=blocked_reason_code or "CRASH",
            session_id=None,
            total_cost_usd=None,
            num_turns=None,
            result_text=process.stderr.read() if process.stderr else None,
        )

    run_result = result_to_run_result(result_event)
    # a rate-limit block takes priority over whatever the result event alone would classify
    if blocked_reason_code and run_result.blocked_reason_code is None:
        run_result = RunResult(
            **{**run_result.__dict__, "blocked_reason_code": blocked_reason_code}
        )
    return run_result


def stop_card(process: subprocess.Popen) -> None:
    """stops a running card's subprocess. terminate() sends SIGTERM first so `claude` can exit
    cleanly rather than being killed mid-write; callers needing a hard stop can kill() directly"""
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
