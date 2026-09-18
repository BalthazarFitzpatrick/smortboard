"""the test gate. Docker is faked throughout - the suite must not need it, and must never invoke a
real container or a real model."""

import json

import pytest

from smortboard.repo_image import ImageFreshness
from smortboard.review.gates import (
    GATE_TIMEOUT_SECONDS,
    GateResult,
    GateUnavailable,
    StaleRepoImage,
    gate_is_configured,
    run_test_gate,
)
from smortboard.store.api import Store

REPO = {"test_command": "uv run pytest", "image": "card-python:latest"}


def _fake_docker(monkeypatch, returncode=0, stdout="", stderr="", capture=None):
    """fakes both the container run and the freshness check - the two are orthogonal, and a test
    exercising the suite run should not have to also fabricate a valid `docker image inspect`
    response just to reach it. tests of staleness itself override check_image_freshness."""
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
    monkeypatch.setattr(
        "smortboard.review.gates.check_image_freshness",
        lambda repo, **kwargs: ImageFreshness(True, None, "fresh"),
    )


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
    with pytest.raises(GateUnavailable, match="Docker"):
        run_test_gate(None, "card", tmp_path, REPO)


def test_a_suite_that_never_finishes_is_a_failure_not_a_hang(tmp_path, monkeypatch):
    import subprocess as sp

    def _run(cmd, **kwargs):
        raise sp.TimeoutExpired(cmd, GATE_TIMEOUT_SECONDS)

    monkeypatch.setattr(sp, "run", _run)
    monkeypatch.setattr("smortboard.review.gates.docker_available", lambda: True)
    monkeypatch.setattr(
        "smortboard.review.gates.check_image_freshness",
        lambda repo, **kwargs: ImageFreshness(True, None, "fresh"),
    )
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


def test_a_stale_repo_image_blocks_before_the_suite_ever_runs(tmp_path, monkeypatch):
    """measured 2026-09-18: a stale image failed cards TESTS_FAILED for reasons that had nothing
    to do with the card's work. the check must run, and block, before the suite does."""
    seen = []
    _fake_docker(monkeypatch, capture=seen)
    monkeypatch.setattr(
        "smortboard.review.gates.check_image_freshness",
        lambda repo, **kwargs: ImageFreshness(
            False, "lock", "a dependency moved. rebuild it - docker build -t x ."
        ),
    )
    with pytest.raises(StaleRepoImage, match="dependency moved"):
        run_test_gate(None, "card", tmp_path, REPO)
    assert seen == []  # the suite never ran


def test_a_stale_repo_image_is_never_reported_as_tests_failed(tmp_path, monkeypatch):
    _fake_docker(monkeypatch, returncode=0, stdout="12 passed")
    monkeypatch.setattr(
        "smortboard.review.gates.check_image_freshness",
        lambda repo, **kwargs: ImageFreshness(False, "base", "the base image moved."),
    )
    with pytest.raises(StaleRepoImage) as excinfo:
        run_test_gate(None, "card", tmp_path, REPO)
    assert excinfo.value.reason_code == "STALE_IMAGE"


def test_a_fresh_image_still_runs_the_suite(tmp_path, monkeypatch):
    seen = []
    _fake_docker(monkeypatch, returncode=0, stdout="12 passed", capture=seen)
    result = run_test_gate(None, "card", tmp_path, REPO)
    assert result.passed
    assert seen  # the suite ran
