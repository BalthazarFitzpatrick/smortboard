"""a run that blocks or crashes writes no worker_summary or review_gate after its result - its money
was still spent, and a refused command in it is exactly the waste the cost views exist to show"""

import pytest

from smortboard.store.api import Store
from smortboard.telemetry import board_costs, card_telemetry


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "board.db") as s:
        yield s


def _result(cost, denials=()):
    return {
        "total_cost_usd": cost,
        "num_turns": 4,
        "permission_denials": list(denials),
        "modelUsage": {"claude-opus-4-5": {"costUSD": cost}},
    }


def test_a_worker_run_that_blocked_still_counts_as_worker_spend(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "blocked on a question")
    store.append_event(card["id"], "lifecycle_started", {})
    denial = {"tool_name": "Bash", "tool_input": {"command": "aws kms list-keys"}}
    store.append_event(card["id"], "result", _result(0.58, [denial]))

    attempt = card_telemetry(store, card["id"])["attempts"][0]
    assert attempt["worker_cost_usd"] == pytest.approx(0.58)
    assert attempt["worker_model"] == "claude-opus-4-5"
    row = board_costs(store, board["id"])[0]
    assert row["cost_usd"] == pytest.approx(0.58)
    assert row["refusal_cost_usd"] == pytest.approx(0.58)


def test_a_review_that_crashed_after_the_test_gate_counts_as_reviewer_spend(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "reviewer fell over")
    store.append_event(card["id"], "lifecycle_started", {})
    store.append_event(card["id"], "result", _result(0.30))
    store.append_event(card["id"], "worker_summary", {"text": "done"})
    store.append_event(card["id"], "test_gate", {"passed": True, "command": "true", "exit_code": 0})
    store.append_event(card["id"], "result", _result(0.12))

    attempt = card_telemetry(store, card["id"])["attempts"][0]
    assert attempt["worker_cost_usd"] == pytest.approx(0.30)
    assert attempt["reviewer_cost_usd"] == pytest.approx(0.12)
