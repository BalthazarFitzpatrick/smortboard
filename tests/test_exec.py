import json
import subprocess

import pytest

from smortboard.exec.leases import LEASE_CONFLICT_PREFIX, write_lease_settings
from smortboard.exec.runner import (
    classify_rate_limit,
    classify_result,
    parse_line,
    result_to_run_result,
)
from smortboard.exec.worktrees import (
    WorktreeError,
    create_worktree,
    destroy_worktree,
    list_worktrees,
)
from smortboard.store.api import Store


def _init_repo(path):
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "initial"], check=True, capture_output=True
    )


# -- worktrees ---------------------------------------------------------------


def test_create_worktree_round_trip(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    info = create_worktree(repo, "card-1")
    assert info.path.exists()
    assert info.branch == "card/card-1"

    listed = list_worktrees(repo)
    assert [w.card_id for w in listed] == ["card-1"]

    destroy_worktree(repo, "card-1")
    assert not info.path.exists()
    assert list_worktrees(repo) == []


def test_worktree_is_already_checked_out_on_its_branch(tmp_path):
    """S1/S3: a card left on main deadlocks or invents its own branch - the handed-back checkout
    must already be on the card's branch"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    info = create_worktree(repo, "card-2")
    current = subprocess.run(
        ["git", "-C", str(info.path), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert current == "card/card-2"


def test_create_worktree_twice_raises(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    create_worktree(repo, "card-3")
    with pytest.raises(WorktreeError):
        create_worktree(repo, "card-3")


# -- leases -------------------------------------------------------------------


def _run_hook(worktree, file_path):
    settings_path = write_lease_settings(worktree, ["src/allowed.py"])
    settings = json.loads(settings_path.read_text())
    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    payload = json.dumps({"tool_input": {"file_path": str(file_path)}})
    return subprocess.run(
        command.split(" ", 1),
        input=payload,
        capture_output=True,
        text=True,
    )


def test_lease_hook_allows_in_lease_path(tmp_path):
    worktree = tmp_path / "wt"
    (worktree / "src").mkdir(parents=True)
    result = _run_hook(worktree, worktree / "src" / "allowed.py")
    assert result.returncode == 0


def test_lease_hook_refuses_out_of_lease_path(tmp_path):
    worktree = tmp_path / "wt"
    (worktree / "src").mkdir(parents=True)
    result = _run_hook(worktree, worktree / "src" / "other.py")
    assert result.returncode == 2
    assert LEASE_CONFLICT_PREFIX in result.stderr


# -- runner: parsing and classification ---------------------------------------


def test_parse_line_skips_blank_and_bad_json():
    assert parse_line("") is None
    assert parse_line("   \n") is None
    assert parse_line("not json") is None
    assert parse_line('{"type": "result"}') == {"type": "result"}


def test_classify_successful_result_is_not_blocked():
    result_event = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "session_id": "abc",
        "total_cost_usd": 0.05,
        "num_turns": 5,
        "permission_denials": [],
        "result": "done",
    }
    assert classify_result(result_event) is None
    run_result = result_to_run_result(result_event)
    assert run_result.subtype == "success"
    assert run_result.blocked_reason_code is None
    assert run_result.session_id == "abc"


def test_classify_rate_limit_event_maps_to_usage_limit():
    event = {
        "rate_limit_info": {
            "status": "rejected",
            "rateLimitType": "five_hour",
            "unifiedWindows": {
                "five_hour": {"utilization": 1.0, "resetsAt": 1788909000},
                "seven_day": {"utilization": 0.9, "resetsAt": 1789401600},
            },
        }
    }
    assert classify_rate_limit(event) == "USAGE_LIMIT"


def test_classify_rate_limit_event_allowed_is_not_blocked():
    event = {"rate_limit_info": {"status": "allowed"}}
    assert classify_rate_limit(event) is None


def test_lease_conflict_is_not_a_crash():
    """S3: a lease block still ends subtype success, is_error false - a card failure it is not"""
    result_event = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "session_id": "abc",
        "total_cost_usd": 0.02,
        "num_turns": 3,
        "permission_denials": [
            {"tool_name": "Edit", "tool_use_id": "t1", "tool_input": {"file_path": "billing.py"}}
        ],
        "result": "Blocked by a PreToolUse lease-guard hook on billing.py.",
    }
    run_result = result_to_run_result(result_event)
    assert run_result.is_error is False
    assert run_result.subtype == "success"
    assert run_result.blocked_reason_code == "LEASE_CONFLICT"


def test_agent_question_is_detected():
    result_event = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "permission_denials": [
            {
                "tool_name": "Bash",
                "tool_use_id": "t1",
                "tool_input": {"command": "git checkout -b x"},
            }
        ],
        "result": "I need your approval to create a branch off main - OK to proceed?",
    }
    assert classify_result(result_event) == "AGENT_QUESTION"


def test_crash_when_is_error_and_no_other_signal():
    result_event = {
        "type": "result",
        "subtype": "error_during_execution",
        "is_error": True,
        "permission_denials": [],
        "result": "something went wrong",
    }
    assert classify_result(result_event) == "CRASH"


# -- events land in the store ---------------------------------------------------


def test_events_recorded_into_store(tmp_path):
    with Store(tmp_path / "board.sqlite3") as store:
        board = store.create_board("b")
        card = store.create_card(board["id"], None, "a card")
        store.append_event(card["id"], "result", {"subtype": "success"})
        events = store.list_events(card["id"])
        assert len(events) == 1
        assert events[0]["kind"] == "result"
        assert events[0]["payload"]["subtype"] == "success"
