"""Claude Code command, stream protocol and refusal classification"""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from smortboard.exec.commands import formatter_write_form
from smortboard.exec.leases import LEASE_CONFLICT_PREFIX, write_lease_settings
from smortboard.labs.base import (
    BashPolicy,
    Capabilities,
    GuardFiles,
    LabEvent,
    RunRequest,
    ToolPolicy,
)

SETTING_SOURCES = "project"


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
    lint_command = (repo or {}).get("lint_command")
    return (
        "Read",
        "Edit",
        "Write",
        "Glob",
        "Grep",
        "Bash(git *)",
        *_bash_grants(test_command),
        *(_bash_grants(lint_command) if lint_command else ()),
    )


def _bash_grants(command: str) -> tuple[str, ...]:
    """allow rules for one repo command, each `&&` part on its own - a rule must match every
    subcommand of a compound command. a trailing ` *` also matches the bare part (probed).

    a part that is the repo's formatter run check-only (`ruff format --check ...`) also grants
    its write form - the same declared tokens with `--check` dropped, so a card can see its own
    work is misformatted AND fix it. the grant is derived from this exact declared part; nothing
    admits a bare "ruff format *" or any command the repo did not itself name."""
    parts = [part.strip() for part in command.split("&&") if part.strip()]
    grants: list[str] = []
    for part in parts:
        grants.append(f"Bash({part} *)")
        write_form = formatter_write_form(part)
        if write_form is not None:
            grants.append(f"Bash({write_form} *)")
    return tuple(grants)


DEFAULT_ALLOWED_TOOLS = ("Bash(git *)", "Edit", "Read", "Write", "Glob", "Grep")

DEFAULT_CARD_BUDGET_USD = 5.0

WAITING_TOOLS = ("Monitor", "ScheduleWakeup", "CronCreate", "TaskOutput")


def available_tools(allowed_tools: tuple[str, ...]) -> str:
    """the `--tools` value: each allowed tool's bare name once, in order - `Bash(git *)` is Bash.

    --allowedTools only grants permission; every other built-in tool, skill and slash command is
    still described to the model on every turn. measured in the card image (cli 2.1.273), a worker
    went from 24 tools to 6 and its first turn from 20.0k to 9.6k input tokens. derived from the
    allowlist, so a run is never described a tool it may not use."""
    return ",".join(dict.fromkeys(tool.split("(", 1)[0] for tool in allowed_tools))


def build_command(
    prompt: str,
    settings_path: str | Path | None,
    model: str = "sonnet",
    allowed_tools: tuple[str, ...] = DEFAULT_ALLOWED_TOOLS,
    budget_usd: float | None = DEFAULT_CARD_BUDGET_USD,
    system_prompt: str = "",
    stream_input: bool = False,
    effort: str | None = None,
) -> list[str]:
    """the proven S1 invocation shape, with our lease settings and scoping decision wired in.

    `settings_path` is optional - the orchestrator runs with no lease/hook file at all, so None
    omits `--settings` rather than passing a path that does not exist.

    `stream_input=True` opts into live steering: the prompt is dropped from argv (it goes as the
    first stdin message instead, via `run_process(stream_prompt=...)`) and `--input-format
    stream-json` is added. Reviewer and orchestrator runs keep the old one-shot shape - `prompt` is
    still required from them but ignored here when streaming.
    """
    cmd = ["claude", "-p"]
    if stream_input:
        cmd += ["--input-format", "stream-json"]
    else:
        cmd.append(prompt)
    cmd += [
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "acceptEdits",
        "--setting-sources",
        SETTING_SOURCES,
        "--system-prompt",
        system_prompt,
    ]
    if settings_path is not None:
        cmd += ["--settings", str(settings_path)]
    cmd += ["--model", model]
    # definitions, not permissions: the allow and deny lists below stay the permission layer
    cmd += ["--tools", available_tools(allowed_tools), "--disable-slash-commands"]
    if effort is not None:
        cmd += ["--effort", effort]
    # A CEILING, NOT A TARGET. a card that loops burns real money quietly - measured, a single
    # 13-turn card re-read 209k cached tokens, so a card that thrashes multiplies that. with a
    # budget the run is refused at the limit rather than found afterwards on the bill
    if budget_usd is not None:
        cmd += ["--max-budget-usd", str(budget_usd)]
    for tool in allowed_tools:
        cmd += ["--allowedTools", tool]
    for tool in WAITING_TOOLS:
        cmd += ["--disallowedTools", tool]
    return cmd


def _structured_output(event: dict[str, Any]) -> dict[str, Any] | None:
    """the input of a StructuredOutput tool call in one assistant event, or None"""
    for block in (event.get("message") or {}).get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        if block.get("name") == "StructuredOutput" and isinstance(block.get("input"), dict):
            return block["input"]
    return None


_ASKING_PHRASES = (
    "ok to proceed",
    "should i ",
    "shall i ",
    "may i ",
    "do you want",
    "would you like",
)


def _agent_question_signal(result_event: dict[str, Any]) -> bool:
    """S3: a populated permission_denials plus a question in result.result is the AGENT_QUESTION
    signal — the agent stopped to ask something nobody headless can answer.

    A question is a result that ends asking one, or asks for leave in so many words - not any text
    that mentions a question mark or the word approval somewhere in a report.
    """
    denials = result_event.get("permission_denials") or []
    text = (result_event.get("result") or "").strip().lower()
    asked = text.endswith("?") or any(phrase in text for phrase in _ASKING_PHRASES)
    return bool(denials) and asked


def _lease_conflict_signal(result_event: dict[str, Any]) -> bool:
    """a lease refusal, per S3, is NOT a card failure — it still needs surfacing so the board can
    decide whether to park the card or extend the lease"""
    denials = result_event.get("permission_denials") or []
    if any(d.get("tool_name") in ("Edit", "Write") for d in denials):
        return True
    return LEASE_CONFLICT_PREFIX in (result_event.get("result") or "")


_SESSION_LIMIT_PATTERN = re.compile(
    r"session limit.*?resets\s+(?P<when>.+?)\s*\(UTC\)", re.IGNORECASE
)

_API_UNREACHABLE_PATTERN = re.compile(
    r"unable to connect to (the )?api|connection\s*refused|connection\s*reset|"
    r"\b5\d{2}\b.{0,20}\b(error|status)\b|\boverloaded\b",
    re.IGNORECASE,
)


def _api_unreachable_signal(result_event: dict[str, Any]) -> bool:
    text = result_event.get("result") or ""
    return bool(_API_UNREACHABLE_PATTERN.search(text))


_SESSION_LIMIT_TIME_FORMATS = ("%I:%M%p", "%I%p")

_SESSION_LIMIT_DATE_FORMATS = ("%b %d %Y", "%B %d %Y", "%m/%d %Y", "%Y-%m-%d")


def _parse_session_limit_reset(when: str, now: datetime | None = None) -> float | None:
    """turns "3:40pm" or "Sep 15" into an epoch timestamp - the soonest future UTC moment that
    text could mean, rolling a bare time to tomorrow and a bare date to next year once it has
    already passed. Unrecognised text returns None rather than raising: a caller with no readable
    reset falls back the same way an absent rate_limit_event always has."""
    now = now or datetime.now(UTC)
    when = when.strip()
    # %p wants an upper-case AM/PM marker; the text arrives lower-case ("3:40pm")
    compact = when.upper().replace(" ", "")
    for fmt in _SESSION_LIMIT_TIME_FORMATS:
        try:
            parsed = datetime.strptime(compact, fmt)
        except ValueError:
            continue
        candidate = now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate.timestamp()
    dated = f"{when} {now.year}"
    for fmt in _SESSION_LIMIT_DATE_FORMATS:
        text = when if fmt == "%Y-%m-%d" else dated
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        candidate = now.replace(
            year=parsed.year,
            month=parsed.month,
            day=parsed.day,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        if candidate <= now:
            candidate = candidate.replace(year=candidate.year + 1)
        return candidate.timestamp()
    return None


def _session_limit_resets_at(result_event: dict[str, Any]) -> float | None:
    """the reset time parsed from a session-limit refusal's own text, or None if this result
    carries no such text - distinct from "text matched but the time itself was unreadable", which
    also returns None and just leaves resets_at unset for the caller."""
    text = result_event.get("result") or ""
    match = _SESSION_LIMIT_PATTERN.search(text)
    if not match:
        return None
    return _parse_session_limit_reset(match.group("when"))


def _session_limit_text_signal(result_event: dict[str, Any]) -> bool:
    """true for a session-limit refusal's OWN text, whether or not the reset time inside it could
    be parsed - the classification does not depend on a readable time, only resets_at does."""
    text = result_event.get("result") or ""
    return bool(_SESSION_LIMIT_PATTERN.search(text))


def classify_result(result_event: dict[str, Any]) -> str | None:
    """maps one `result` stream event onto the store's blocked_reason_code vocabulary, or None
    for a clean run. Order matters: a lease conflict is checked before is_error, since S3 showed
    the run still completes `subtype: success` when it hits one. The session-limit text is checked
    before is_error too - a run whose only output is that refusal text was seen classified CRASH
    instead of USAGE_LIMIT, so nothing rotated."""
    if _lease_conflict_signal(result_event):
        return "LEASE_CONFLICT"
    if _agent_question_signal(result_event):
        return "AGENT_QUESTION"
    if _session_limit_text_signal(result_event):
        return "USAGE_LIMIT"
    if _api_unreachable_signal(result_event):
        return "API_UNREACHABLE"
    if result_event.get("is_error"):
        return "CRASH"
    return None


_RATE_LIMIT_OK = frozenset({"allowed", "allowed_warning"})


def classify_rate_limit(rate_limit_event: dict[str, Any]) -> str | None:
    """S2: a refused status in either window maps to USAGE_LIMIT; a warning is not a refusal"""
    info = rate_limit_event.get("rate_limit_info", {})
    if info.get("status") not in _RATE_LIMIT_OK:
        return "USAGE_LIMIT"
    return None


def user_message_line(text: str) -> str:
    """one `--input-format stream-json` input line: a user turn"""
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n"


def policy_tools(policy: ToolPolicy) -> tuple[str, ...]:
    tools = []
    if policy.read:
        tools.append("Read")
    if policy.edit and not policy.read_only:
        tools.extend(("Edit", "Write"))
    if policy.search:
        tools.extend(("Glob", "Grep"))
    tools.extend(f"Bash({command})" for command in policy.bash_allow)
    return tuple(tools)


class ClaudeCodeAdapter:
    lab = "anthropic"
    cli = "claude-code"
    capabilities = Capabilities(True, True, True, True, True, True, True)

    def build_command(self, req: RunRequest) -> list[str]:
        tools = req.allowed_tools
        if tools is None:
            tools = policy_tools(req.tool_policy) if req.tool_policy else DEFAULT_ALLOWED_TOOLS
        cmd = build_command(
            req.prompt,
            req.settings_path,
            req.model,
            tools,
            req.budget_usd,
            req.system_prompt,
            req.stream_input,
            req.effort,
        )
        if req.json_schema is not None:
            cmd += ["--json-schema", json.dumps(req.json_schema)]
        if req.read_only and req.role in ("orchestrator", "fold"):
            for tool in ("Edit", "Write", "NotebookEdit", "Bash", "WebFetch", "WebSearch"):
                cmd += ["--disallowedTools", tool]
        return cmd

    def auth_shell(self, profile: Any = None) -> str:
        return "IFS= read -r CLAUDE_CODE_OAUTH_TOKEN && export CLAUDE_CODE_OAUTH_TOKEN && "

    def container_env(self) -> list[str]:
        return []

    def normalize(self, raw: dict[str, Any]) -> list[LabEvent]:
        kind = raw.get("type")
        events = []
        if kind == "assistant":
            for block in (raw.get("message") or {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    events.append(LabEvent("assistant_text", text=block.get("text")))
                elif block.get("type") == "tool_use":
                    events.append(
                        LabEvent(
                            "tool_use",
                            tool={
                                "name": block.get("name"),
                                "input": block.get("input"),
                                "structured_output": block.get("input")
                                if block.get("name") == "StructuredOutput"
                                else None,
                            },
                        )
                    )
        elif kind == "rate_limit_event":
            info = raw.get("rate_limit_info") or {}
            windows = info.get("unifiedWindows")
            windows = (
                windows
                if isinstance(windows, dict)
                else {info.get("rateLimitType") or "unknown": info}
            )
            for name, window in windows.items():
                status = window.get("status") or info.get("status")
                events.append(
                    LabEvent(
                        "rate_limit",
                        rate_limit={
                            "window": name,
                            "status": {"allowed": "ok", "allowed_warning": "warning"}.get(
                                status, "refused"
                            ),
                            "utilization": window.get("utilization"),
                            "resets_at": window.get("resetsAt"),
                        },
                    )
                )
        elif kind == "result":
            model_usage = raw.get("modelUsage") or {}
            usage = raw.get("usage") or {}
            if model_usage:
                for model, item in model_usage.items():
                    events.append(
                        LabEvent(
                            "usage",
                            model=model,
                            usage={
                                "input_tokens": item.get("inputTokens", 0),
                                "output_tokens": item.get("outputTokens", 0),
                                "cached_tokens": item.get("cacheReadInputTokens", 0),
                                "cache_creation_tokens": item.get("cacheCreationInputTokens", 0),
                                "cost_usd": item.get("costUSD"),
                                "cost_estimated": False,
                            },
                        )
                    )
            else:
                events.append(
                    LabEvent(
                        "usage",
                        usage={
                            "input_tokens": usage.get("input_tokens", 0),
                            "output_tokens": usage.get("output_tokens", 0),
                            "cached_tokens": usage.get("cache_read_input_tokens", 0),
                            "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
                            "cost_usd": raw.get("total_cost_usd"),
                            "cost_estimated": False,
                        },
                    )
                )
            for denial in raw.get("permission_denials") or []:
                events.append(LabEvent("tool_denied", tool=denial))
            events.append(
                LabEvent(
                    "result",
                    result={
                        "ok": not bool(raw.get("is_error")),
                        "subtype": raw.get("subtype"),
                        "session_id": raw.get("session_id"),
                        "num_turns": raw.get("num_turns"),
                        "text": raw.get("result"),
                        "structured_output": raw.get("structured_output"),
                        "auth_failed": raw.get("api_error_status") == 401,
                        "blocked_reason_code": classify_result(raw),
                        "resets_at": _session_limit_resets_at(raw),
                        "cost_usd": raw.get("total_cost_usd"),
                    },
                )
            )
        return events

    def classify(self, final: LabEvent) -> str | None:
        return (final.result or {}).get("blocked_reason_code")

    def steering_line(self, text: str) -> str:
        return user_message_line(text)

    def guard_files(self, lease: list[str], bash: BashPolicy) -> GuardFiles:
        path = write_lease_settings(
            bash.worktree_path,
            lease,
            remembered_globs=bash.remembered_globs,
            python=bash.python,
            guard_dir=bash.guard_dir,
            root=bash.root,
        )
        return GuardFiles(path, tuple(path.parent.iterdir()))

    def preflight(self, profile: Any) -> list[dict[str, Any]]:
        return []

    def guard_mounts(self, settings_path: str | Path | None) -> list[str]:
        return []

    def setup_hint(self) -> str:
        return "claude setup-token"
