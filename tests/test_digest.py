"""the morning digest: pull requests in merge order, the one question each blocked card needs,
runs and spend - all read straight off events and comments already on the board."""

from __future__ import annotations

import time

import pytest

from smortboard.digest import board_digest
from smortboard.store.api import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "board.db")
    yield s
    s.close()


@pytest.fixture
def board_and_repo(store):
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "r", "/tmp/r", "main")
    return board["id"], repo["id"]


def test_an_empty_board_digests_cleanly(store, board_and_repo):
    board_id, _ = board_and_repo
    result = board_digest(store, board_id, since=0)
    assert result["pull_requests"] == []
    assert result["waiting_on_operator"] == []
    assert result["runs_and_spend"] == {"runs": 0, "total_cost_usd": 0}


def test_an_unknown_board_is_a_lookup_error(store):
    from smortboard.store.errors import NotFoundError

    with pytest.raises(NotFoundError):
        board_digest(store, "nope", since=0)


def test_a_pull_request_opened_since_the_cutoff_is_reported(store, board_and_repo):
    board_id, repo_id = board_and_repo
    card = store.create_card(board_id, repo_id, "fix the thing")
    store.append_event(card["id"], "merge_request", {"url": "https://example/pr/1"})

    result = board_digest(store, board_id, since=0)
    assert len(result["pull_requests"]) == 1
    row = result["pull_requests"][0]
    assert row["card_id"] == card["id"]
    assert row["url"] == "https://example/pr/1"


def test_a_pull_request_opened_before_the_cutoff_is_excluded(store, board_and_repo):
    board_id, repo_id = board_and_repo
    card = store.create_card(board_id, repo_id, "fix the thing")
    store.append_event(card["id"], "merge_request", {"url": "https://example/pr/1"})

    future = time.time() + 3600
    result = board_digest(store, board_id, since=future)
    assert result["pull_requests"] == []


def test_dependencies_are_ordered_before_their_dependents(store, board_and_repo):
    board_id, repo_id = board_and_repo
    base = store.create_card(board_id, repo_id, "base")
    dependent = store.create_card(board_id, repo_id, "dependent")
    store.add_dependency(dependent["id"], base["id"])
    # dependent's PR recorded first in the event log, base's second - the digest must still put
    # base ahead, because that is the order they need to be merged in
    store.append_event(dependent["id"], "merge_request", {"url": "https://example/pr/dependent"})
    store.append_event(base["id"], "merge_request", {"url": "https://example/pr/base"})

    result = board_digest(store, board_id, since=0)
    ordered_ids = [row["card_id"] for row in result["pull_requests"]]
    assert ordered_ids.index(base["id"]) < ordered_ids.index(dependent["id"])
    dependent_row = next(r for r in result["pull_requests"] if r["card_id"] == dependent["id"])
    assert dependent_row["merge_after"] == [base["id"]]
    assert dependent_row["dependency_not_in_batch"] is False


def test_a_dependency_not_in_the_batch_is_flagged(store, board_and_repo):
    board_id, repo_id = board_and_repo
    base = store.create_card(board_id, repo_id, "base")  # never opens a PR in this window
    dependent = store.create_card(board_id, repo_id, "dependent")
    store.add_dependency(dependent["id"], base["id"])
    store.append_event(dependent["id"], "merge_request", {"url": "https://example/pr/dependent"})

    result = board_digest(store, board_id, since=0)
    row = result["pull_requests"][0]
    assert row["dependency_not_in_batch"] is True
    assert row["merge_after"] == []


def test_agent_question_uses_the_agents_own_final_text(store, board_and_repo):
    board_id, repo_id = board_and_repo
    card = store.create_card(board_id, repo_id, "needs a decision")
    store.update_card(card["id"], status="doing", blocked_reason_code="AGENT_QUESTION")
    store.append_event(card["id"], "worker_summary", {"text": "which auth flow should I use?"})

    result = board_digest(store, board_id, since=0)
    row = result["waiting_on_operator"][0]
    assert row["reason"] == "AGENT_QUESTION"
    assert row["question"] == "which auth flow should I use?"


def test_every_other_block_reason_uses_the_boards_own_note(store, board_and_repo):
    board_id, repo_id = board_and_repo
    card = store.create_card(board_id, repo_id, "broke the tests")
    store.update_card(card["id"], status="doing", blocked_reason_code="TESTS_FAILED")
    store.add_comment(card["id"], author="smortboard", body="`pytest` exited 1.")

    result = board_digest(store, board_id, since=0)
    row = result["waiting_on_operator"][0]
    assert row["question"] == "`pytest` exited 1."


def test_a_card_with_no_block_reason_is_not_waiting_on_anyone(store, board_and_repo):
    board_id, repo_id = board_and_repo
    store.create_card(board_id, repo_id, "still todo")
    result = board_digest(store, board_id, since=0)
    assert result["waiting_on_operator"] == []


def test_runs_and_spend_are_summed_since_the_cutoff(store, board_and_repo):
    board_id, repo_id = board_and_repo
    card = store.create_card(board_id, repo_id, "a card")
    store.append_event(card["id"], "lifecycle_started", {})
    store.append_event(card["id"], "result", {"total_cost_usd": 0.42})

    result = board_digest(store, board_id, since=0)
    assert result["runs_and_spend"] == {"runs": 1, "total_cost_usd": 0.42}


def test_runs_before_the_cutoff_are_not_counted(store, board_and_repo):
    board_id, repo_id = board_and_repo
    card = store.create_card(board_id, repo_id, "a card")
    store.append_event(card["id"], "lifecycle_started", {})
    store.append_event(card["id"], "result", {"total_cost_usd": 0.42})

    result = board_digest(store, board_id, since=time.time() + 3600)
    assert result["runs_and_spend"] == {"runs": 0, "total_cost_usd": 0}
