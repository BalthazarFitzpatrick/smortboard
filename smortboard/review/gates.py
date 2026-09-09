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

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smortboard.exec.backends import card_image, docker_available

# a test suite that has not finished in ten minutes is not going to; the card is stuck rather than
# slow, and a gate that waits forever is a card that never reaches the board
GATE_TIMEOUT_SECONDS = 600
# enough of the tail to see which test failed and why, without putting a whole run in the store
OUTPUT_TAIL_CHARS = 4000


# NOT named TestGateUnavailable: pytest collects any class whose name starts with Test, so the
# exception would have been picked up as a test case
class GateUnavailable(RuntimeError):
    """the gate cannot run: no Docker, or the repo declares no test command"""


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
    command = (repo or {}).get("test_command")
    if not command:
        raise GateUnavailable(
            "This repo declares no test_command, so there is nothing to check the card against. "
            "Set one on the repo - it is also what scopes the card's own Bash allowlist."
        )
    if not docker_available():
        raise GateUnavailable("Docker is not running, and the gate runs the suite in a container.")

    work_path = Path(work_path)
    cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
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
            cmd, capture_output=True, text=True, timeout=GATE_TIMEOUT_SECONDS, check=False
        )
        exit_code, output = completed.returncode, (completed.stdout + completed.stderr)
    except subprocess.TimeoutExpired:
        # a timeout is a failure with a reason, not an exception the board has to interpret
        exit_code = -1
        output = f"the suite did not finish within {GATE_TIMEOUT_SECONDS}s"

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
