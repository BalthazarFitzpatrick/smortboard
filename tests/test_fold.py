"""fold: the board, not the model, merges cards. the runner is always faked - no docker, no model."""

import json
import threading
import time

import pytest

from smortboard import consolidate
from smortboard.consolidate import apply_folds, run_fold_turn
from smortboard.store.api import Store
from tests import test_mission_control_http as http

# the mission control suite's server fixture, bound here so pytest finds it for this module too
running_server = http.running_server


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "board.db") as s:
        yield s


@pytest.fixture
def board(store):
    b = store.create_board("dev")
    repo = store.create_repo(b["id"], "repo", "/repo", "main", test_command="uv run pytest")
    other = store.create_repo(b["id"], "other", "/other", "main", test_command="uv run pytest")
    return b["id"], repo["id"], other["id"]


def _card(store, board_id, repo_id, title, **fields):
    return store.create_card(board_id, repo_id, title, **fields)


def _titles(store, board_id):
    return sorted(card["title"] for card in store.list_cards(board_id))


def test_a_group_becomes_one_card_carrying_every_criterion_lease_and_dependency(store, board):
    board_id, repo_id, _ = board
    base = _card(store, board_id, repo_id, "base", status="accepted")
    a = _card(
        store, board_id, repo_id, "a", leases=["x.py"], criteria=["a works"], ledger_task="t1"
    )
    b = _card(
        store,
        board_id,
        repo_id,
        "b",
        leases=["x.py", "y.py"],
        criteria=["b works"],
        ledger_task="t2",
    )
    busy = _card(store, board_id, repo_id, "busy", status="doing")
    later = _card(store, board_id, repo_id, "later")
    store.add_dependency(b["id"], base["id"])
    store.add_dependency(later["id"], a["id"])

    group = {
        "cards": [a["id"][:8], b["id"][:8], busy["id"][:8]],
        "title": "a and b",
        "description": "GOAL: both",
        "criteria": [],
        "leases": ["z.py"],
        "reason": "same module",
    }
    lines = apply_folds(store, board_id, [group])

    cards = {card["title"]: card for card in store.list_cards(board_id)}
    assert "a" not in cards and "b" not in cards
    merged = cards["a and b"]
    assert [c["text"] for c in merged["criteria"]] == ["a works", "b works"]
    assert sorted(lease["path_glob"] for lease in merged["leases"]) == ["x.py", "y.py", "z.py"]
    assert merged["depends_on"] == [base["id"]]
    assert cards["later"]["depends_on"] == [merged["id"]]
    assert merged["ledger_task"] == "t1"
    assert "- t2" in merged["description"]
    assert cards["busy"]["status"] == "doing"
    assert "doing, only todo cards fold" in lines[0]
    # folded, not lost: both originals sit in the backups restore_card reads
    assert {bk["card_id"] for bk in store.list_card_backups(board_id)} == {a["id"], b["id"]}


def test_cards_on_another_repo_never_fold_and_a_lone_card_is_left_alone(store, board):
    board_id, repo_id, other_id = board
    a = _card(store, board_id, repo_id, "a")
    o = _card(store, board_id, other_id, "o")
    group = {"cards": [a["id"], o["id"]], "title": "ao", "description": "", "criteria": []}
    lines = apply_folds(store, board_id, [group])
    assert _titles(store, board_id) == ["a", "o"]
    assert lines[0].startswith("not folded") and "another repo" in lines[0]


def test_the_snapshot_lists_which_todo_cards_share_a_lease(store, board):
    board_id, repo_id, other_id = board
    a = _card(store, board_id, repo_id, "a", leases=["ui/*.js"])
    b = _card(store, board_id, repo_id, "b", leases=["ui/board.js"])
    _card(store, board_id, repo_id, "c", leases=["server.py"])
    _card(store, board_id, other_id, "o", leases=["ui/board.js"])
    snapshot = consolidate.build_fold_snapshot(store, board_id)
    cards = {card["title"]: card for card in snapshot["cards"]}
    assert cards["a"]["overlaps"] == [b["id"][:8]]
    assert cards["b"]["overlaps"] == [a["id"][:8]]
    assert cards["c"]["overlaps"] == []
    # the same file on another repo is another file
    assert cards["o"]["overlaps"] == []


def test_the_board_refuses_a_group_past_one_agents_worth(store, board):
    board_id, repo_id, _ = board
    five = [_card(store, board_id, repo_id, f"c{i}", leases=["x.py"]) for i in range(5)]
    everyone = {"cards": [c["id"] for c in five], "title": "all", "criteria": []}
    assert "5 cards is more than one agent's worth" in apply_folds(store, board_id, [everyone])[0]
    wordy = {
        "cards": [five[0]["id"], five[1]["id"]],
        "title": "two",
        "criteria": [f"criterion {i}" for i in range(13)],
    }
    assert "13 criteria is more than one agent's worth" in apply_folds(store, board_id, [wordy])[0]
    assert len(store.list_cards(board_id)) == 5


def test_a_card_that_shares_no_lease_with_the_group_is_refused(store, board):
    board_id, repo_id, _ = board
    a = _card(store, board_id, repo_id, "a", leases=["x.py"])
    b = _card(store, board_id, repo_id, "b", leases=["y.py"])
    group = {"cards": [a["id"], b["id"]], "title": "ab", "criteria": []}
    assert "shares no lease with the rest" in apply_folds(store, board_id, [group])[0]
    assert _titles(store, board_id) == ["a", "b"]


def test_fewer_than_two_todo_cards_never_starts_a_run(store, board):
    board_id, repo_id, _ = board
    _card(store, board_id, repo_id, "only")
    _card(store, board_id, repo_id, "done", status="accepted")

    def run(prompt, model, budget_usd):
        raise AssertionError("a board with nothing to fold must not cost a run")

    result = run_fold_turn(store, board_id, runner=run)
    assert "nothing to fold" in result.lines[0]
    assert "nothing to fold" in store.list_orchestrator_messages(board_id)[-1]["body"]


def test_an_unparseable_proposal_changes_nothing_and_says_so(store, board):
    board_id, repo_id, _ = board
    _card(store, board_id, repo_id, "a")
    _card(store, board_id, repo_id, "b")
    result = run_fold_turn(store, board_id, runner=lambda prompt, model, budget_usd: "not json")
    assert result.error and "nothing changed" in result.error
    assert _titles(store, board_id) == ["a", "b"]
    assert "nothing changed" in store.list_orchestrator_messages(board_id)[-1]["body"]


def test_a_turn_folds_what_was_proposed_and_reports_the_old_ids(store, board):
    board_id, repo_id, _ = board
    a = _card(store, board_id, repo_id, "a", leases=["x.py"])
    b = _card(store, board_id, repo_id, "b", leases=["x.py"])
    seen = {}

    def run(prompt, model, budget_usd):
        seen["prompt"] = prompt
        group = {
            "cards": [a["id"][:8], b["id"][:8]],
            "title": "ab",
            "description": "",
            "criteria": ["ab works"],
            "leases": [],
            "reason": "both edit x.py",
        }
        return json.dumps({"summary": "a and b share x.py", "groups": [group]})

    result = run_fold_turn(store, board_id, runner=run)
    assert result.error is None
    assert '"x.py"' in seen["prompt"]
    assert _titles(store, board_id) == ["ab"]
    body = store.list_orchestrator_messages(board_id)[-1]["body"]
    assert a["id"][:8] in body and b["id"][:8] in body and "both edit x.py" in body


def test_one_fold_at_a_time_per_board_over_http(running_server, monkeypatch):
    base_url, _ = running_server
    gate = threading.Event()

    def slow(store, board_id, token_path=None, runner=None):
        gate.wait(5)
        return consolidate.FoldResult()

    monkeypatch.setattr(consolidate, "run_fold_turn", slow)
    _, board = http._request(f"{base_url}/api/boards", "POST", {"name": "dev"})
    url = f"{base_url}/api/boards/{board['id']}/fold"
    assert http._request(url, "POST")[0] == 202
    assert http._request(url, "POST")[0] == 409
    assert http._request(url)[1]["running"] is True
    gate.set()
    deadline = time.monotonic() + 5
    while http._request(url)[1]["running"] and time.monotonic() < deadline:
        time.sleep(0.05)
    assert http._request(url)[1]["running"] is False
    assert http._request(f"{base_url}/api/boards/nope/fold", "POST")[0] == 404
