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
        "worker_lab": None,
        "reviewer_lab": None,
        "orchestrator_lab": None,
        "fold_lab": None,
        "fold_model": None,
        "worker_cross_lab_fallback": [],
        "reviewer_cross_lab_fallback": [],
        "orchestrator_cross_lab_fallback": [],
        "fold_cross_lab_fallback": [],
        "max_parallel": None,
        "resume_briefing": None,
        "gate_timeout_seconds": None,
        "auto_switch_profiles": None,
        "mall_cam_interval_seconds": None,
        "enable_mouse": None,
        "worker_budget_usd": None,
        "reviewer_budget_usd": None,
        "orchestrator_budget_usd": None,
        "fold_budget_usd": None,
        "card_total_budget_usd": None,
        "worker_effort": None,
        "reviewer_effort": None,
        "orchestrator_effort": None,
        "fold_effort": None,
        "mission_control_read_paths": [],
    }


def test_an_unknown_route_or_setting_is_refused(store, card_id):
    with pytest.raises(ValueError):
        store.update_card(card_id, findings_route="ignore")
    with pytest.raises(ValueError):
        store.set_setting("findings_route", "ignore")
    with pytest.raises(UnknownFieldError):
        store.set_setting("theme", "dark")


def test_effort_takes_only_the_named_levels_per_role(store, card_id):
    for bad in ("max", "xhigh", "LOW", "", 1, True, "--effort"):
        with pytest.raises(ValueError):
            store.set_setting("reviewer_effort", bad)
    settings = store.set_settings({"worker_effort": "low", "fold_effort": "high"})
    assert (settings["worker_effort"], settings["fold_effort"]) == ("low", "high")
    assert settings["reviewer_effort"] is None and settings["orchestrator_effort"] is None
    assert store.set_setting("worker_effort", None)["worker_effort"] is None


def test_max_parallel_refuses_zero_negatives_and_non_numbers(store, card_id):
    for bad in (0, -1, "0", "-3", "two", 1.5, True):
        with pytest.raises(ValueError):
            store.set_setting("max_parallel", bad)
    store.set_setting("max_parallel", 3)  # a good value still works after the bad ones refused
    assert (
        store.get_settings()["max_parallel"] == "3"
    )  # settings are stored as text, like every other one
    store.set_setting("max_parallel", None)  # null clears it back to the scheduler's default
    assert store.get_settings()["max_parallel"] is None


def test_mall_cam_interval_refuses_zero_negatives_and_non_numbers(store, card_id):
    for bad in (0, -1, "0", "-3", "two", 1.5, True):
        with pytest.raises(ValueError):
            store.set_setting("mall_cam_interval_seconds", bad)
    store.set_setting("mall_cam_interval_seconds", 15)
    assert store.get_settings()["mall_cam_interval_seconds"] == "15"
    store.set_setting("mall_cam_interval_seconds", None)  # null clears it back to chat.js's default
    assert store.get_settings()["mall_cam_interval_seconds"] is None


def test_a_board_max_parallel_refuses_zero_negatives_and_non_numbers(store):
    board = store.create_board("b2")
    for bad in (0, -1, 1.5, True):
        with pytest.raises(ValueError):
            store.set_board_max_parallel(board["id"], bad)
    updated = store.set_board_max_parallel(board["id"], 1)
    assert updated["max_parallel"] == 1
    cleared = store.set_board_max_parallel(board["id"], None)
    assert cleared["max_parallel"] is None


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


def test_mission_control_read_paths_json_string_is_checked_like_the_list(store, tmp_path):
    import json

    folder = tmp_path / "screenshots"
    folder.mkdir()

    # a string arrives already-serialized and must be checked exactly like the list form,
    # not stored as given - a string of a bad path is refused the same way the list is
    with pytest.raises(ValueError, match="absolute"):
        store.set_setting("mission_control_read_paths", json.dumps(["relative/path"]))

    settings = store.set_setting("mission_control_read_paths", json.dumps([str(folder)]))
    assert settings["mission_control_read_paths"] == [str(folder)]


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
            "worker_lab": None,
            "reviewer_lab": None,
            "orchestrator_lab": None,
            "fold_lab": None,
            "fold_model": None,
            "worker_cross_lab_fallback": [],
            "reviewer_cross_lab_fallback": [],
            "orchestrator_cross_lab_fallback": [],
            "fold_cross_lab_fallback": [],
            "max_parallel": None,
            "resume_briefing": None,
            "gate_timeout_seconds": None,
            "auto_switch_profiles": None,
            "mall_cam_interval_seconds": None,
            "enable_mouse": None,
            "worker_budget_usd": None,
            "reviewer_budget_usd": None,
            "orchestrator_budget_usd": None,
            "fold_budget_usd": None,
            "card_total_budget_usd": None,
            "worker_effort": None,
            "reviewer_effort": None,
            "orchestrator_effort": None,
            "fold_effort": None,
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


def test_the_outcome_has_empty_lease_lists_without_lease_events(store, card_id):
    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(card_id, "worker_summary", {"text": "done"})
    outcome = card_outcome(store, card_id)
    assert outcome["lease_wanted"] == []
    assert outcome["lease_expanded"] == []


def test_the_outcome_reads_the_latest_runs_lease_paths(store, card_id):
    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(card_id, "lease_wanted", {"paths": ["old/path.py"]})
    store.append_event(card_id, "worker_summary", {"text": "first try"})
    # a run blocked on its lease writes no summary - its wanted paths still have to show
    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(card_id, "lease_wanted", {"paths": ["src/a.py", "src/b.py"]})
    store.append_event(card_id, "lease_wanted", {"paths": ["src/a.py", "src/c.py"]})
    store.append_event(card_id, "lease_expanded", {"paths": ["tests/test_a.py"]})
    outcome = card_outcome(store, card_id)
    assert outcome["lease_wanted"] == ["src/a.py", "src/b.py", "src/c.py"]
    assert outcome["lease_expanded"] == ["tests/test_a.py"]
    assert outcome["summary"] == "first try"


def test_the_outcome_skips_a_malformed_lease_event(store, card_id):
    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(card_id, "lease_wanted", {"paths": "src/a.py"})
    store.append_event(card_id, "lease_wanted", {})
    store.append_event(card_id, "lease_expanded", {"paths": [None, 3, "", "src/ok.py"]})
    outcome = card_outcome(store, card_id)
    assert outcome["lease_wanted"] == []
    assert outcome["lease_expanded"] == ["src/ok.py"]


def test_the_outcome_counts_runs_turns_and_cost_across_attempts(store, card_id):
    for cost, turns in ((0.5, 4), (1.25, 6)):
        store.append_event(card_id, "lifecycle_started", {})
        store.append_event(
            card_id,
            "result",
            {"total_cost_usd": cost, "num_turns": turns, "modelUsage": {}, "result": "done"},
        )
        store.append_event(card_id, "worker_summary", {"text": "done"})
    outcome = card_outcome(store, card_id)
    assert outcome["runs"] == 2
    assert outcome["turns"] == 10
    assert outcome["cost_usd"] == 1.75
    assert outcome["cost_estimated"] is False


def test_a_never_run_outcome_has_no_runs(store, card_id):
    outcome = card_outcome(store, card_id)
    assert (outcome["runs"], outcome["turns"], outcome["cost_usd"]) == (0, 0, 0)


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
