"""the reviewer: is the diff safe and decent - the second Phase 3 gate, sibling to gates.py.

Same instinct as the test gate - refuse rather than quietly pass - but a different shape of
independence. The test gate needs no credential and no network; the reviewer makes a model call,
so it runs like a card does: in a throwaway container, the card credential handed over on stdin,
never as an env var. See docker_available/read_card_token in backends.py, reused rather than
reinvented here.

It answers exactly four questions about the diff, per docs/PLAN.md Phase 3, and no others:
vulnerabilities, leaked credentials, best practices, efficient coding. IT DOES NOT JUDGE
ACCEPTANCE CRITERIA - that is the test gate's job, from criteria written before the work started.
A reviewer that also judged "did it meet the criteria" would be judging a target the same run
could have moved.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smortboard.exec.backends import card_image, docker_available, guard_mount, read_card_token
from smortboard.exec.runner import RunResult, build_command, run_process
from smortboard.store.api import Store

CATEGORIES = ("vulnerability", "leaked_credential", "best_practice", "efficiency")
SEVERITIES = ("low", "medium", "high", "critical")

# a finding at this severity or above blocks approval outright - a review that only ever notes
# things is not a gate. leaked_credential blocks at ANY severity (see _compute_approved): a model
# that calls a leaked key "low" severity is not a case to trust the label on.
BLOCKING_SEVERITIES = {"high", "critical"}

# the reviewer inspects, it never edits: no Edit, no Write, and no Bash at all - the diff is
# handed in rather than letting it run git, so it never needs a shell to do its job
REVIEWER_ALLOWED_TOOLS: tuple[str, ...] = ("Read", "Grep", "Glob")

# a review reads a diff and answers four questions; it should not cost what the work cost
DEFAULT_REVIEW_BUDGET_USD = 0.50

REVIEW_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "file": {"type": "string"},
                    "line": {"type": ["integer", "null"]},
                    "message": {"type": "string"},
                },
                "required": ["category", "severity", "file", "message"],
            },
        }
    },
    "required": ["findings"],
}

REVIEW_PROMPT_HEADER = (
    "Review the diff below and answer exactly four questions - no others:\n"
    "1. vulnerabilities - injection, unsafe deserialisation, path traversal, unchecked input\n"
    "2. leaked credentials - a key, token or password reaching the diff at all\n"
    "3. best practices - the project's own conventions and the language's\n"
    "4. efficient coding - work done repeatedly that could be done once, and obvious waste\n\n"
    "Do NOT judge whether the diff meets its acceptance criteria - a separate gate does that.\n"
    "Return JSON matching the given schema. An empty findings list means the diff is clean.\n\n"
    "--- diff ---\n"
)


class ReviewUnavailable(RuntimeError):
    """the reviewer cannot run: no Docker, or no card credential"""


@dataclass
class ReviewFinding:
    category: str
    severity: str
    file: str
    message: str
    line: int | None = None


@dataclass
class ReviewResult:
    approved: bool
    findings: list[ReviewFinding] = field(default_factory=list)
    # set when the model's response could not be trusted at all - malformed json, a run that
    # crashed, an empty result. distinct from a finding: it is not a claim about the code, it is
    # the reviewer failing to deliver a verdict, and that also blocks rather than passing quietly
    error: str | None = None

    @property
    def blocked_reason_code(self) -> str | None:
        return None if self.approved else "REVIEW_REJECTED"


def _compute_approved(findings: list[ReviewFinding]) -> bool:
    for finding in findings:
        if finding.category == "leaked_credential":
            return False
        if finding.severity in BLOCKING_SEVERITIES:
            return False
    return True


def _image_for(repo: dict[str, Any] | None) -> str:
    if repo and repo.get("image"):
        return str(repo["image"])
    return card_image()


def _build_prompt(diff: str) -> str:
    return REVIEW_PROMPT_HEADER + diff


def _docker_command(
    work_path: str | Path,
    prompt: str,
    settings_path: str | Path,
    model: str,
    repo: dict[str, Any] | None,
    budget_usd: float | None,
) -> list[str]:
    mount, inner_settings = guard_mount(settings_path)
    claude_cmd = build_command(
        prompt,
        inner_settings,
        model=model,
        allowed_tools=REVIEWER_ALLOWED_TOOLS,
        budget_usd=budget_usd,
    )
    claude_cmd += ["--json-schema", json.dumps(REVIEW_JSON_SCHEMA)]
    # same stdin handoff as ContainerBackend: the token touches no disk and no env var, so
    # `docker inspect` shows nothing
    inner = (
        "IFS= read -r CLAUDE_CODE_OAUTH_TOKEN && export CLAUDE_CODE_OAUTH_TOKEN && "
        + shlex.join(claude_cmd)
        + " < /dev/null"
    )
    return [
        "docker",
        "run",
        "--rm",
        "-i",
        "-v",
        f"{Path(work_path)}:/workspace:ro",  # the reviewer inspects, it never writes
        *mount,
        "-w",
        "/workspace",
        _image_for(repo),
        "sh",
        "-c",
        inner,
    ]


def _parse(run_result: RunResult) -> ReviewResult:
    if run_result.blocked_reason_code is not None:
        # a crash, a lease conflict, an unanswered question - none of those is a verdict, so
        # refuse rather than treat "no findings reported" as approval
        return ReviewResult(
            approved=False,
            error=f"reviewer run did not complete cleanly: {run_result.blocked_reason_code}",
        )
    text = run_result.result_text
    if not text:
        return ReviewResult(approved=False, error="reviewer produced no output")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ReviewResult(approved=False, error="reviewer response was not valid json")
    raw_findings = data.get("findings") if isinstance(data, dict) else None
    if not isinstance(raw_findings, list):
        return ReviewResult(approved=False, error="reviewer response had no findings list")

    findings = []
    dropped = 0
    for item in raw_findings:
        if (
            not isinstance(item, dict)
            or item.get("category") not in CATEGORIES
            or item.get("severity") not in SEVERITIES
        ):
            dropped += 1
            continue
        findings.append(
            ReviewFinding(
                category=item["category"],
                severity=item["severity"],
                file=str(item.get("file", "")),
                line=item.get("line") if isinstance(item.get("line"), int) else None,
                message=str(item.get("message", "")),
            )
        )

    if dropped:
        # a partly-unparseable response is not a response we can trust the rest of either
        return ReviewResult(
            approved=False,
            findings=findings,
            error=f"{dropped} finding(s) in the reviewer's response could not be parsed",
        )
    return ReviewResult(approved=_compute_approved(findings), findings=findings)


def _record(store: Store | None, card_id: str, result: ReviewResult) -> None:
    if store is None:
        return
    store.append_event(
        card_id,
        "review_gate",
        json.loads(
            json.dumps(
                {
                    "approved": result.approved,
                    "error": result.error,
                    "findings": [
                        {
                            "category": f.category,
                            "severity": f.severity,
                            "file": f.file,
                            "line": f.line,
                            "message": f.message,
                        }
                        for f in result.findings
                    ],
                }
            )
        ),
    )


def run_review(
    store: Store | None,
    card_id: str,
    diff: str,
    work_path: str | Path,
    settings_path: str | Path,
    repo: dict[str, Any] | None = None,
    model: str = "sonnet",
    budget_usd: float | None = DEFAULT_REVIEW_BUDGET_USD,
    token_path: str | Path | None = None,
) -> ReviewResult:
    """runs the reviewer over `diff` in a throwaway container, and records the verdict.

    `work_path` is mounted read-only so Read/Grep/Glob can see surrounding context; the reviewer
    never touches git and never writes, so no clone and no fetch-back are needed the way a card's
    container needs them.
    """
    if not docker_available():
        raise ReviewUnavailable("Docker is not running, and the reviewer runs in a container.")
    token = read_card_token(token_path)  # raises CardTokenMissing if there is none

    if not diff.strip():
        result = ReviewResult(approved=True, findings=[])
        _record(store, card_id, result)
        return result

    cmd = _docker_command(work_path, _build_prompt(diff), settings_path, model, repo, budget_usd)
    run_result = run_process(store, card_id, cmd, cwd=work_path, stdin_text=token + "\n")
    result = _parse(run_result)
    _record(store, card_id, result)
    return result


def reviewer_is_configured(token_path: str | Path | None = None) -> bool:
    """whether a card on this repo can be reviewed at all - mirrors gate_is_configured."""
    from smortboard.exec.backends import card_token_available

    return docker_available() and card_token_available(token_path)
