"""a reviewer that answered keeps its answer, and a gate that timed out says where it stalled.

Measured on the smolsmort board 2026-09-12: the train.py card's reviewer submitted findings, then
hit its $0.50 budget, and the gate recorded 'CRASH' with no findings; two cards' gates timed out at
600 s with nothing kept but that message, and the timed-out container kept running.
"""

import json
import subprocess
import sys

from smortboard.exec.runner import RunResult, run_process
from smortboard.review import gates
from smortboard.review.reviewer import BUDGET_STOP, DEFAULT_REVIEW_BUDGET_USD, _parse
from smortboard.store.api import Store

FINDINGS = {
    "findings": [
        {
            "category": "best_practice",
            "severity": "high",
            "file": "smolsmort/review/train.py",
            "line": 55,
            "message": "the port drops the abort path",
        }
    ]
}


def _budget_stop(structured_output=None):
    return RunResult(
        subtype=BUDGET_STOP,
        is_error=True,
        blocked_reason_code="CRASH",
        session_id="s",
        total_cost_usd=0.503,
        num_turns=1,
        result_text=None,
        structured_output=structured_output,
    )


def test_run_process_keeps_a_structured_answer_given_before_the_budget_stop():
    lines = [
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "StructuredOutput", "input": FINDINGS}]
            },
        },
        {"type": "result", "subtype": BUDGET_STOP, "is_error": True, "total_cost_usd": 0.503},
    ]
    script = "import sys\nfor l in sys.argv[1:]: print(l)"
    cmd = [sys.executable, "-c", script, *[json.dumps(line) for line in lines]]
    result = run_process(None, "c", cmd)
    assert result.subtype == BUDGET_STOP
    assert result.structured_output == FINDINGS


def test_a_budget_stop_after_the_answer_keeps_the_findings():
    review = _parse(_budget_stop(FINDINGS))
    assert review.error is None
    assert [f.file for f in review.findings] == ["smolsmort/review/train.py"]
    assert review.approved is False  # the high finding blocks, as any verdict would


def test_a_budget_stop_before_any_answer_says_so_rather_than_crash():
    review = _parse(_budget_stop())
    assert review.approved is False
    assert "budget" in review.error
    assert "CRASH" not in review.error


def test_the_review_budget_fits_a_large_diff():
    assert DEFAULT_REVIEW_BUDGET_USD >= 1.0  # 0.50 stopped a 361-line port's review at $0.503


def _gate_with_fake_run(tmp_path, monkeypatch, fake_run, store=None, card_id="card-1"):
    monkeypatch.setattr(gates, "docker_available", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake_run)
    repo = {"test_command": "uv run pytest -q", "image": "img"}
    return gates.run_test_gate(store, card_id, tmp_path, repo)


def test_a_timed_out_gate_keeps_its_output_and_removes_its_container(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(
                cmd, 600, output=b"....x....\ntests/test_slow.py ", stderr=b""
            )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    result = _gate_with_fake_run(tmp_path, monkeypatch, fake_run)
    assert result.passed is False
    assert "tests/test_slow.py" in result.output
    assert "did not finish within 600s" in result.output
    gate_name = calls[0][calls[0].index("--name") + 1]
    assert calls[1] == ["docker", "rm", "-f", gate_name]


def test_the_gate_timeout_comes_from_the_board_setting(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    with Store(tmp_path / "b.db") as store:
        # the gate records its outcome as an event, which needs a real card to hang off
        card_id = store.create_card(store.create_board("b")["id"], None, "c")["id"]
        store.set_setting("gate_timeout_seconds", "1800")
        _gate_with_fake_run(tmp_path, monkeypatch, fake_run, store, card_id)
        assert seen["timeout"] == 1800
        store.set_setting("gate_timeout_seconds", "nonsense")
        _gate_with_fake_run(tmp_path, monkeypatch, fake_run, store, card_id)
        assert seen["timeout"] == gates.GATE_TIMEOUT_SECONDS
