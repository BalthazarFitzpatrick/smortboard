"""the reviewer gate. Docker and the model are faked throughout - the suite must never invoke a
real container or a real `claude` process, only the functions that would otherwise shell out."""

import json
import shlex

import pytest

from smortboard.exec.runner import RunResult
from smortboard.review.reviewer import (
    REVIEWER_ALLOWED_TOOLS,
    ReviewFinding,
    ReviewResult,
    ReviewUnavailable,
    run_review,
)
from smortboard.store.api import Store

REPO = {"image": "card-python:latest"}
DIFF = "diff --git a/x.py b/x.py\n+print('hello')\n"


def _clean_result(text='{"findings": []}'):
    return RunResult(
        subtype="success",
        is_error=False,
        blocked_reason_code=None,
        session_id="s1",
        total_cost_usd=0.01,
        num_turns=1,
        result_text=text,
    )


def _wire(monkeypatch, run_result=None, capture=None, docker=True, token="t0k3n"):
    monkeypatch.setattr("smortboard.review.reviewer.docker_available", lambda: docker)
    monkeypatch.setattr("smortboard.review.reviewer.read_card_token", lambda p=None: token)

    def _fake_run_process(store, card_id, cmd, cwd=None, env=None, stdin_text=None):
        if capture is not None:
            capture.append({"cmd": cmd, "stdin": stdin_text})
        return run_result if run_result is not None else _clean_result()

    monkeypatch.setattr("smortboard.review.reviewer.run_process", _fake_run_process)


def test_a_clean_diff_is_approved(tmp_path, monkeypatch):
    _wire(monkeypatch)
    result = run_review(None, "card", DIFF, tmp_path, tmp_path / "s.json", REPO)
    assert result.approved
    assert result.findings == []
    assert result.error is None


def test_an_empty_diff_is_approved_without_a_model_call(tmp_path, monkeypatch):
    calls = []
    _wire(monkeypatch, capture=calls)
    result = run_review(None, "card", "   \n", tmp_path, tmp_path / "s.json", REPO)
    assert result.approved
    assert calls == []  # no point paying for a review of nothing


def test_a_finding_above_threshold_blocks(tmp_path, monkeypatch):
    payload = json.dumps(
        {
            "findings": [
                {
                    "category": "vulnerability",
                    "severity": "high",
                    "file": "x.py",
                    "line": 3,
                    "message": "unchecked input reaches a shell call",
                }
            ]
        }
    )
    _wire(monkeypatch, run_result=_clean_result(payload))
    result = run_review(None, "card", DIFF, tmp_path, tmp_path / "s.json", REPO)
    assert not result.approved
    assert result.blocked_reason_code == "REVIEW_REJECTED"
    assert result.findings[0].category == "vulnerability"


def test_a_low_severity_finding_does_not_block_on_its_own(tmp_path, monkeypatch):
    payload = json.dumps(
        {
            "findings": [
                {
                    "category": "efficiency",
                    "severity": "low",
                    "file": "x.py",
                    "line": 1,
                    "message": "this loop could be a comprehension",
                }
            ]
        }
    )
    _wire(monkeypatch, run_result=_clean_result(payload))
    result = run_review(None, "card", DIFF, tmp_path, tmp_path / "s.json", REPO)
    assert result.approved
    assert result.findings[0].severity == "low"


def test_a_leaked_credential_blocks_regardless_of_reported_severity(tmp_path, monkeypatch):
    """a reviewer that found a leaked credential does not get to return approved with notes -
    labelling it "low" is not a case to trust the label on."""
    payload = json.dumps(
        {
            "findings": [
                {
                    "category": "leaked_credential",
                    "severity": "low",
                    "file": "x.py",
                    "line": 5,
                    "message": "an API key literal was added",
                }
            ]
        }
    )
    _wire(monkeypatch, run_result=_clean_result(payload))
    result = run_review(None, "card", DIFF, tmp_path, tmp_path / "s.json", REPO)
    assert not result.approved


@pytest.mark.parametrize(
    "category", ["vulnerability", "leaked_credential", "best_practice", "efficiency"]
)
def test_each_category_survives_a_round_trip(tmp_path, monkeypatch, category):
    payload = json.dumps(
        {
            "findings": [
                {
                    "category": category,
                    "severity": "medium",
                    "file": "x.py",
                    "line": 2,
                    "message": "note",
                }
            ]
        }
    )
    _wire(monkeypatch, run_result=_clean_result(payload))
    result = run_review(None, "card", DIFF, tmp_path, tmp_path / "s.json", REPO)
    assert result.findings[0].category == category
    assert isinstance(result.findings[0], ReviewFinding)


def test_a_malformed_response_does_not_crash_the_board(tmp_path, monkeypatch):
    _wire(monkeypatch, run_result=_clean_result("not json at all"))
    result = run_review(None, "card", DIFF, tmp_path, tmp_path / "s.json", REPO)
    assert not result.approved
    assert result.error is not None


def test_a_response_missing_the_findings_key_does_not_crash_the_board(tmp_path, monkeypatch):
    _wire(monkeypatch, run_result=_clean_result(json.dumps({"ok": True})))
    result = run_review(None, "card", DIFF, tmp_path, tmp_path / "s.json", REPO)
    assert not result.approved
    assert result.error is not None


def test_an_unparseable_finding_blocks_rather_than_being_silently_dropped(tmp_path, monkeypatch):
    payload = json.dumps({"findings": [{"category": "not-a-real-category", "severity": "low"}]})
    _wire(monkeypatch, run_result=_clean_result(payload))
    result = run_review(None, "card", DIFF, tmp_path, tmp_path / "s.json", REPO)
    assert not result.approved
    assert result.error is not None


def test_a_crashed_run_is_not_treated_as_approval(tmp_path, monkeypatch):
    crashed = RunResult(
        subtype=None,
        is_error=True,
        blocked_reason_code="CRASH",
        session_id=None,
        total_cost_usd=None,
        num_turns=None,
        result_text=None,
    )
    _wire(monkeypatch, run_result=crashed)
    result = run_review(None, "card", DIFF, tmp_path, tmp_path / "s.json", REPO)
    assert not result.approved


def test_no_docker_is_refused_rather_than_skipped(tmp_path, monkeypatch):
    _wire(monkeypatch, docker=False)
    with pytest.raises(ReviewUnavailable, match="Docker"):
        run_review(None, "card", DIFF, tmp_path, tmp_path / "s.json", REPO)


def test_the_credential_never_appears_as_an_env_var(tmp_path, monkeypatch):
    calls = []
    _wire(monkeypatch, capture=calls, token="s3cr3t-token")
    run_review(None, "card", DIFF, tmp_path, tmp_path / "s.json", REPO)
    call = calls[0]
    joined = shlex.join(call["cmd"])
    assert "-e" not in call["cmd"] and "--env" not in call["cmd"]
    assert "s3cr3t-token" not in joined
    assert call["stdin"] == "s3cr3t-token\n"


def test_the_reviewer_is_never_handed_edit_or_write(tmp_path, monkeypatch):
    calls = []
    _wire(monkeypatch, capture=calls)
    run_review(None, "card", DIFF, tmp_path, tmp_path / "s.json", REPO)
    joined = shlex.join(calls[0]["cmd"])
    assert "Edit" not in REVIEWER_ALLOWED_TOOLS
    assert "Write" not in REVIEWER_ALLOWED_TOOLS
    assert "--allowedTools Edit" not in joined
    assert "--allowedTools Write" not in joined
    assert "Bash" not in joined  # no shell at all - the diff is passed in, not fetched with git


def test_the_work_path_is_mounted_read_only(tmp_path, monkeypatch):
    calls = []
    _wire(monkeypatch, capture=calls)
    run_review(None, "card", DIFF, tmp_path, tmp_path / "s.json", REPO)
    mounts = [p for p in calls[0]["cmd"] if ":/workspace" in str(p)]
    assert mounts and all(str(m).endswith(":ro") for m in mounts)


def test_the_settings_path_is_the_one_inside_the_container(tmp_path, monkeypatch):
    calls = []
    _wire(monkeypatch, capture=calls)
    run_review(None, "card", DIFF, tmp_path, tmp_path / "guards" / "s.json", REPO)
    cmd = calls[0]["cmd"]
    assert f"{tmp_path / 'guards'}:/smortboard:ro" in cmd
    assert "--settings /smortboard/s.json" in cmd[-1]


def test_the_verdict_lands_in_the_event_log(tmp_path, monkeypatch):
    _wire(monkeypatch, run_result=_clean_result(json.dumps({"findings": []})))
    with Store(tmp_path / "board.db") as store:
        board = store.create_board("b")
        card = store.create_card(board["id"], None, "a card")
        run_review(store, card["id"], DIFF, tmp_path, tmp_path / "s.json", REPO)
        events = [e for e in store.list_events(card["id"]) if e["kind"] == "review_gate"]
    assert len(events) == 1
    payload = events[0]["payload"]
    payload = json.loads(payload) if isinstance(payload, str) else payload
    assert payload["approved"] is True


def test_review_result_reports_its_own_reason_code():
    assert ReviewResult(True).blocked_reason_code is None
    assert ReviewResult(False).blocked_reason_code == "REVIEW_REJECTED"


def test_a_stored_reviewer_header_reaches_the_review_prompt(tmp_path, monkeypatch):
    """the layered prompt - see smortboard/prompts.py - overrides REVIEW_PROMPT_HEADER"""
    calls = []
    _wire(monkeypatch, capture=calls)
    with Store(tmp_path / "b.db") as store:
        store.set_prompt("reviewer", "a custom reviewer header\n")
        board = store.create_board("b")
        card = store.create_card(board["id"], None, "a card")
        run_review(store, card["id"], DIFF, tmp_path, tmp_path / "s.json", REPO)
    joined = shlex.join(calls[0]["cmd"])
    assert "a custom reviewer header" in joined
    assert "vulnerabilities" not in joined  # the default header, not layered on top of it
