"""the test gate. Docker is faked throughout - the suite must not need it, and must never invoke a
real container or a real model."""

import json

import pytest

from smortboard.review.gates import (
    GATE_TIMEOUT_SECONDS,
    GateResult,
    GateUnavailable,
    gate_is_configured,
    run_test_gate,
)
from smortboard.store.api import Store

REPO = {"test_command": "uv run pytest", "image": "card-python:latest"}


def _fake_docker(monkeypatch, returncode=0, stdout="", stderr="", capture=None):
    """fakes the container run - staleness is checked upstream of the gate (rebuild_if_stale,
    the preflight check), so the gate itself only ever sees whichever image it is handed."""
    import subprocess as sp

    class _Completed:
        def __init__(self):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _run(cmd, **kwargs):
        if capture is not None:
            capture.append(cmd)
        return _Completed()

    monkeypatch.setattr(sp, "run", _run)
    monkeypatch.setattr("smortboard.review.gates.docker_available", lambda: True)


def test_a_passing_suite_is_a_passing_gate(tmp_path, monkeypatch):
    _fake_docker(monkeypatch, returncode=0, stdout="12 passed")
    result = run_test_gate(None, "card", tmp_path, REPO)
    assert result.passed
    assert result.blocked_reason_code is None


def test_a_failing_suite_blocks_the_card_with_a_reason(tmp_path, monkeypatch):
    _fake_docker(monkeypatch, returncode=1, stdout="1 failed")
    result = run_test_gate(None, "card", tmp_path, REPO)
    assert not result.passed
    assert result.blocked_reason_code == "TESTS_FAILED"
    assert "1 failed" in result.output


def test_the_gate_needs_no_credential_and_no_network(tmp_path, monkeypatch):
    """the gate makes no model call, so nothing has to hand it a token - and a suite that needs the
    network to pass is one whose result depends on something outside the card."""
    seen = []
    _fake_docker(monkeypatch, capture=seen)
    run_test_gate(None, "card", tmp_path, REPO)
    cmd = seen[0]
    assert "--network" in cmd and cmd[cmd.index("--network") + 1] == "none"
    assert "-e" not in cmd
    assert not any("token" in str(part).lower() for part in cmd)


def test_the_gate_exports_writable_cache_dirs_for_the_readonly_mount(tmp_path, monkeypatch):
    """/workspace is read-only, so ruff/pytest must not try to cache into it (regression for the
    'Failed to initialize cache: Read-only file system' failure)."""
    seen = []
    _fake_docker(monkeypatch, capture=seen)
    run_test_gate(None, "card", tmp_path, REPO)
    shell_command = seen[0][-1]
    assert "RUFF_CACHE_DIR=/tmp/.ruff_cache" in shell_command
    assert "PYTEST_ADDOPTS='-p no:cacheprovider'" in shell_command
    assert REPO["test_command"] in shell_command


def test_the_gate_cannot_change_what_it_is_judging(tmp_path, monkeypatch):
    seen = []
    _fake_docker(monkeypatch, capture=seen)
    run_test_gate(None, "card", tmp_path, REPO)
    mounts = [p for p in seen[0] if ":/workspace" in str(p)]
    assert mounts and all(str(m).endswith(":ro") for m in mounts)


def test_the_repo_image_is_used_so_the_toolchain_is_already_there(tmp_path, monkeypatch):
    seen = []
    _fake_docker(monkeypatch, capture=seen)
    run_test_gate(None, "card", tmp_path, REPO)
    assert "card-python:latest" in seen[0]


def test_a_repo_with_no_test_command_says_so_rather_than_passing(tmp_path, monkeypatch):
    """silently passing an ungated card would be the worst outcome: it reads as approval."""
    monkeypatch.setattr("smortboard.review.gates.docker_available", lambda: True)
    with pytest.raises(GateUnavailable, match="no test_command"):
        run_test_gate(None, "card", tmp_path, {"image": "x"})
    assert not gate_is_configured({"image": "x"})


def test_no_docker_is_refused_rather_than_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr("smortboard.review.gates.docker_available", lambda: False)
    with pytest.raises(GateUnavailable, match="docker"):
        run_test_gate(None, "card", tmp_path, REPO)


def test_a_suite_that_never_finishes_is_a_failure_not_a_hang(tmp_path, monkeypatch):
    import subprocess as sp

    def _run(cmd, **kwargs):
        raise sp.TimeoutExpired(cmd, GATE_TIMEOUT_SECONDS)

    monkeypatch.setattr(sp, "run", _run)
    monkeypatch.setattr("smortboard.review.gates.docker_available", lambda: True)
    result = run_test_gate(None, "card", tmp_path, REPO)
    assert not result.passed
    assert "did not finish" in result.output


def test_the_outcome_lands_in_the_event_log(tmp_path, monkeypatch):
    _fake_docker(monkeypatch, returncode=1, stdout="boom")
    with Store(tmp_path / "board.db") as store:
        board = store.create_board("b")
        card = store.create_card(board["id"], None, "a card")
        run_test_gate(store, card["id"], tmp_path, REPO)
        events = [e for e in store.list_events(card["id"]) if e["kind"] == "test_gate"]
    assert len(events) == 1
    payload = events[0]["payload"]
    payload = json.loads(payload) if isinstance(payload, str) else payload
    assert payload["passed"] is False
    assert payload["command"] == "uv run pytest"


def test_a_long_output_is_tailed_rather_than_stored_whole(tmp_path, monkeypatch):
    _fake_docker(monkeypatch, returncode=1, stdout="x" * 50_000 + "the actual failure")
    result = run_test_gate(None, "card", tmp_path, REPO)
    assert len(result.output) < 50_000
    assert result.output.endswith("the actual failure")


def test_gate_result_reports_its_own_reason_code():
    assert GateResult(True, "c", 0, "").blocked_reason_code is None
    assert GateResult(False, "c", 1, "").blocked_reason_code == "TESTS_FAILED"


# a stale repo image (uv.lock or the base image moved since the last build) is no longer a
# gate-level concern: rebuild_if_stale (smortboard/repo_image.py) rebuilds it at card run-start,
# before the worker or the gate ever touch it, and the same staleness check is surfaced in
# preflight so it is visible before a card starts - see tests/test_repo_image.py and
# tests/test_preflight.py. measured 2026-09-18: a stale image failed cards TESTS_FAILED for
# reasons that had nothing to do with the card's work; fixing it before the gate runs means the
# gate itself never needs to know an image can be stale.


def test_a_fresh_image_still_runs_the_suite(tmp_path, monkeypatch):
    seen = []
    _fake_docker(monkeypatch, returncode=0, stdout="12 passed", capture=seen)
    result = run_test_gate(None, "card", tmp_path, REPO)
    assert result.passed
    assert seen  # the suite ran
