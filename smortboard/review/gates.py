"""the test gate: does the card's work actually do what the card asked for.

THE BOARD RUNS THE TESTS, NOT THE AGENT. An agent reporting "tests pass" is the agent grading its
own homework, and a card that convinced itself is exactly the case a gate exists to catch. So the
gate re-runs the repo's own test command against what the card committed, in a container the agent
never touched.

That independence buys two things for free. The gate needs NO CREDENTIAL - there is no model call in
it - and it needs NO NETWORK, because the repo's image already carries its toolchain. It is the most
locked-down thing in the system, which is the right shape for the thing deciding whether work is
trustworthy.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smortboard.exec.backends import (
    CONTAINER_HARDENING_FLAGS,
    card_image,
    container_name,
    docker_available,
)
from smortboard.store.errors import NotFoundError

# a test suite that has not finished in ten minutes is not going to; the card is stuck rather than
# slow, and a gate that waits forever is a card that never reaches the board
GATE_TIMEOUT_SECONDS = 600
# enough of the tail to see which test failed and why, without putting a whole run in the store
OUTPUT_TAIL_CHARS = 4000


# NOT named TestGateUnavailable: pytest collects any class whose name starts with Test, so the
# exception would have been picked up as a test case
class GateUnavailable(RuntimeError):
    """the gate cannot run: no Docker, or the repo declares no test command"""


class NoTestCommand(GateUnavailable):
    """the repo has no test_command - a repo-level setting, not a fault in this card's run.

    its own subclass so the caller can show a one-line pointer at the repo setting instead of
    the boilerplate explanation, which the pre-flight checklist already carries once per repo.
    """


@dataclass
class GateResult:
    passed: bool
    command: str
    exit_code: int
    output: str

    @property
    def blocked_reason_code(self) -> str | None:
        return None if self.passed else "TESTS_FAILED"


def _image_for(repo: dict[str, Any] | None) -> str:
    if repo and repo.get("image"):
        return str(repo["image"])
    return card_image()


def _timeout_seconds(store: Any) -> int:
    """the board's gate_timeout_seconds setting, else GATE_TIMEOUT_SECONDS"""
    get_settings = getattr(store, "get_settings", None)
    raw = get_settings().get("gate_timeout_seconds") if get_settings else None
    try:
        value = int(raw) if raw is not None else GATE_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        return GATE_TIMEOUT_SECONDS
    return value if value > 0 else GATE_TIMEOUT_SECONDS


def _as_text(data: bytes | str | None) -> str:
    """TimeoutExpired carries bytes whatever text= said"""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""


def _remove_container(name: str) -> None:
    """a timeout kills the docker client, not the named container - which kept the suite running
    on the machine's cpu after the gate had given up on it"""
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30, check=False)


def _current_repo(store: Any, repo: dict[str, Any] | None) -> dict[str, Any] | None:
    """the repo row fresh off the store, not whatever the caller happened to be holding.

    a card on the fix route loops through this gate several times in one run, and an operator
    edit to test_command between rounds must reach the very next one - not only a card started
    after the edit. falls back to the given `repo` when there is no store or id to re-read by
    (tests pass a bare dict; an orchestrator turn passes no store at all).
    """
    repo_id = (repo or {}).get("id")
    if store is None or not repo_id:
        return repo
    try:
        return store.get_repo(repo_id)
    except NotFoundError:
        return repo


def run_test_gate(
    store: Any,
    card_id: str,
    work_path: str | Path,
    repo: dict[str, Any] | None,
) -> GateResult:
    """runs the repo's test command against the card's work, and records the outcome.

    `--network none`: the gate has no reason to reach anything, and a test suite that needs the
    network to pass is one whose result depends on something outside the card.
    """
    repo = _current_repo(store, repo)
    command = (repo or {}).get("test_command")
    if not command:
        raise NoTestCommand(
            "This repo declares no test_command, so there is nothing to check the card against. "
            "Set one on the repo - it is also what scopes the card's own Bash allowlist."
        )
    if not docker_available():
        raise GateUnavailable("Docker is not running, and the gate runs the suite in a container.")

    work_path = Path(work_path)
    name = container_name("gate", card_id)
    timeout = _timeout_seconds(store)
    cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        "none",
        *CONTAINER_HARDENING_FLAGS,
        "-v",
        f"{work_path}:/workspace:ro",  # the gate reads the work, it never changes it
        "-w",
        "/workspace",
        _image_for(repo),
        "sh",
        "-c",
        str(command),
    ]
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        exit_code, output = completed.returncode, (completed.stdout + completed.stderr)
    except subprocess.TimeoutExpired as exc:
        # a timeout is a failure with a reason, not an exception the board has to interpret.
        # the output so far says where it stalled - measured, the message alone said nothing
        exit_code = -1
        partial = _as_text(exc.stdout) + _as_text(exc.stderr)
        output = (
            f"{partial}\n\nthe suite did not finish within {timeout}s - above is as far as it got"
        )
        _remove_container(name)

    result = GateResult(
        passed=exit_code == 0,
        command=str(command),
        exit_code=exit_code,
        output=output[-OUTPUT_TAIL_CHARS:],
    )
    if store is not None:
        store.append_event(
            card_id,
            "test_gate",
            json.loads(
                json.dumps(
                    {
                        "passed": result.passed,
                        "command": result.command,
                        "exit_code": result.exit_code,
                        "output": result.output,
                    }
                )
            ),
        )
    return result


def gate_is_configured(repo: dict[str, Any] | None) -> bool:
    """whether a card on this repo can be gated at all - the board shows this rather than
    discovering it when a card finishes and has nothing to be judged against"""
    return bool((repo or {}).get("test_command")) and shutil.which("docker") is not None
