"""the findings route's precedence, the outcome projection, and the decisions' refusals"""

import re

import pytest

from smortboard.review.decide import DecisionRefused, accept_card, reject_card
from smortboard.review.outcome import card_outcome
from smortboard.store.api import Store
from smortboard.store.errors import UnknownFieldError


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "b.db")
    yield s
    s.close()


@pytest.fixture
def card_id(store):
    board = store.create_board("b")
    return store.create_card(board["id"], None, "c")["id"]


def test_a_card_with_no_route_gets_attention(store, card_id):
    assert store.findings_route(card_id) == "attention"


def test_a_card_route_applies_while_the_global_is_unset(store, card_id):
    store.update_card(card_id, findings_route="fix")
    assert store.findings_route(card_id) == "fix"


def test_the_global_route_forces_every_card(store, card_id):
    store.update_card(card_id, findings_route="fix")
    store.set_setting("findings_route", "attention")
    assert store.findings_route(card_id) == "attention"
    store.set_setting("findings_route", None)
    assert store.findings_route(card_id) == "fix"
    assert store.get_settings() == {
        "findings_route": None,
        "orchestrator_model": None,
        "worker_model": None,
        "reviewer_model": None,
        "max_parallel": None,
        "resume_briefing": None,
        "gate_timeout_seconds": None,
        "auto_switch_profiles": None,
        "mission_control_read_paths": [],
    }


def test_an_unknown_route_or_setting_is_refused(store, card_id):
    with pytest.raises(ValueError):
        store.update_card(card_id, findings_route="ignore")
    with pytest.raises(ValueError):
        store.set_setting("findings_route", "ignore")
    with pytest.raises(UnknownFieldError):
        store.set_setting("theme", "dark")


def test_mission_control_read_paths_add_refuse_remove(store, tmp_path):
    folder = tmp_path / "screenshots"
    folder.mkdir()

    added = store.set_setting("mission_control_read_paths", [str(folder)])
    assert added["mission_control_read_paths"] == [str(folder)]

    missing = tmp_path / "does-not-exist"
    with pytest.raises(ValueError, match=re.escape(str(missing))):
        store.set_setting("mission_control_read_paths", [str(folder), str(missing)])
    # a refused patch must not touch the list that was already stored
    assert store.get_settings()["mission_control_read_paths"] == [str(folder)]

    removed = store.set_setting("mission_control_read_paths", [])
    assert removed["mission_control_read_paths"] == []


def test_mission_control_read_paths_expand_and_reject_relative(store, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Documents" / "screenshots").mkdir(parents=True)

    settings = store.set_setting("mission_control_read_paths", ["~/Documents/screenshots"])
    assert settings["mission_control_read_paths"] == [str(tmp_path / "Documents" / "screenshots")]

    with pytest.raises(ValueError, match="absolute"):
        store.set_setting("mission_control_read_paths", ["relative/path"])
    with pytest.raises(ValueError):
        store.set_setting("mission_control_read_paths", [str(tmp_path / "not-a-dir.txt")])


def test_settings_travel_in_the_export_bundle(store, tmp_path):
    store.set_setting("findings_route", "fix")
    store.export(tmp_path / "bundle.json")
    with Store(tmp_path / "other.db") as other:
        other.import_bundle(tmp_path / "bundle.json")
        assert other.get_settings() == {
            "findings_route": "fix",
            "orchestrator_model": None,
            "worker_model": None,
            "reviewer_model": None,
            "max_parallel": None,
            "resume_briefing": None,
            "gate_timeout_seconds": None,
            "auto_switch_profiles": None,
            "mission_control_read_paths": [],
        }


def test_the_outcome_only_reads_the_latest_attempt(store, card_id):
    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(card_id, "worker_summary", {"text": "first try"})
    store.append_event(card_id, "merge_request", {"url": "https://x/pull/1"})
    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(card_id, "worker_summary", {"text": "second try"})
    outcome = card_outcome(store, card_id)
    assert outcome["summary"] == "second try"
    assert outcome["pr_url"] is None
    assert outcome["tests"] is None and outcome["review"] is None


def test_the_outcome_falls_back_past_an_attempt_that_never_reached_the_worker(store, card_id):
    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(card_id, "worker_summary", {"text": "first try"})
    store.append_event(card_id, "merge_request", {"url": "https://x/pull/1"})
    # a refused rerun: lifecycle_started fires, then nothing - no worker_summary, no gates, no pr
    store.append_event(card_id, "lifecycle_started", {})
    outcome = card_outcome(store, card_id)
    assert outcome["summary"] == "first try"
    assert outcome["pr_url"] == "https://x/pull/1"


def test_only_an_unblocked_checking_card_can_be_accepted(store, card_id):
    with pytest.raises(DecisionRefused):
        accept_card(store, card_id)
    store.update_card(card_id, status="checking", blocked_reason_code="TESTS_FAILED")
    with pytest.raises(DecisionRefused):
        accept_card(store, card_id)
    store.update_card(card_id, blocked_reason_code=None)
    assert accept_card(store, card_id)["status"] == "accepted"
    with pytest.raises(DecisionRefused):
        accept_card(store, card_id)  # already accepted


def test_undecided_and_rejected_cards_cannot_be_rejected(store, card_id):
    for status in ("todo", "rejected"):
        store.update_card(card_id, status=status)
        with pytest.raises(DecisionRefused):
            reject_card(store, card_id, close_pr=lambda *a: None)


def test_an_accepted_card_can_still_be_rejected(store, card_id):
    # y then x: the decision flips, and the note says a merge that already happened stands
    store.update_card(card_id, status="accepted")
    card = reject_card(store, card_id, close_pr=lambda *a: None)
    assert card["status"] == "rejected"
    assert "already merged" in card["comments"][-1]["body"]


def test_a_rejected_card_can_be_accepted_again(store, card_id):
    store.update_card(card_id, status="rejected")
    card = accept_card(store, card_id)
    assert card["status"] == "accepted"
    assert "reopen the pull request" in card["comments"][-1]["body"]


def test_accepting_a_rejected_card_again_releases_its_dependents(store, card_id):
    board_id = store.get_card(card_id)["board_id"]
    waiting = store.create_card(board_id, None, "waiting")["id"]
    store.add_dependency(waiting, card_id)
    store.update_card(card_id, status="checking")
    reject_card(store, card_id, close_pr=lambda *a: None)
    assert store.get_card(waiting)["blocked_reason_code"] == "DEPENDENCY_REJECTED"
    accept_card(store, card_id)
    assert store.get_card(waiting)["blocked_reason_code"] is None


def test_rejecting_leaves_already_decided_dependents_alone(store, card_id):
    board_id = store.get_card(card_id)["board_id"]
    done = store.create_card(board_id, None, "done", status="accepted")["id"]
    waiting = store.create_card(board_id, None, "waiting")["id"]
    store.add_dependency(done, card_id)
    store.add_dependency(waiting, card_id)
    store.update_card(card_id, status="checking")
    reject_card(store, card_id, close_pr=lambda *a: None)
    assert store.get_card(done)["blocked_reason_code"] is None
    assert store.get_card(waiting)["blocked_reason_code"] == "DEPENDENCY_REJECTED"
