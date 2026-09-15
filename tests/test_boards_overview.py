"""the c panel's data: one row per board, spend rolled up across every card, plus a totals row.

fixtures build event logs by hand, the same shapes tests/test_cost_telemetry.py uses.
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from smortboard.server.app import build_server
from smortboard.store.api import Store
from smortboard.telemetry import boards_overview


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "board.db") as s:
        yield s


def _worker_result(cost=0.10, turns=4, model="claude-sonnet-4", denials=None):
    return {
        "total_cost_usd": cost,
        "num_turns": turns,
        "permission_denials": denials or [],
        "modelUsage": {model: {"inputTokens": 100, "outputTokens": 50, "costUSD": cost}},
        "result": "done",
    }


def _clean_run(store, card_id, model="claude-sonnet-4", pr=True):
    """one worker run, one review, a pull request - same shape test_cost_telemetry.py uses"""
    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(card_id, "result", _worker_result(model=model))
    store.append_event(card_id, "worker_summary", {"text": "did the thing"})
    store.append_event(card_id, "test_gate", {"passed": True, "command": "pytest", "exit_code": 0})
    store.append_event(
        card_id, "result", _worker_result(cost=0.03, turns=2, model="claude-sonnet-4")
    )
    store.append_event(card_id, "review_gate", {"approved": True, "findings": []})
    if pr:
        store.append_event(card_id, "merge_request", {"url": "https://example.test/pr/1"})


def test_boards_overview_with_no_boards(store):
    overview = boards_overview(store)
    assert overview["boards"] == []
    assert overview["totals"]["cost_usd"] == 0
    assert overview["totals"]["cost_per_pr_usd"] is None
    assert overview["orchestrator_turns_counted"] is False


def test_boards_overview_rolls_up_one_board(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "a card", model="haiku")
    store.update_card(card["id"], status="accepted")
    _clean_run(store, card["id"])

    overview = boards_overview(store)
    assert len(overview["boards"]) == 1
    row = overview["boards"][0]
    assert row["board_id"] == board["id"]
    assert row["board_name"] == "b"
    assert row["cost_usd"] == pytest.approx(0.13)
    assert row["runs"] == 1
    assert row["cards"] == 1
    assert row["cards_accepted"] == 1
    assert row["pull_requests_opened"] == 1
    assert row["worker_cost_usd"] == pytest.approx(0.10)
    assert row["reviewer_cost_usd"] == pytest.approx(0.03)
    assert row["cost_per_pr_usd"] == pytest.approx(0.13)
    assert row["spend_by_model"] == [{"model": "claude-sonnet-4", "cost_usd": pytest.approx(0.13)}]

    totals = overview["totals"]
    assert totals["cost_usd"] == pytest.approx(0.13)
    assert totals["pull_requests_opened"] == 1
    assert totals["cost_per_pr_usd"] == pytest.approx(0.13)


def test_boards_overview_sorts_costliest_first_and_counts_refusal_spend(store):
    cheap_board = store.create_board("cheap board")
    cheap_card = store.create_card(cheap_board["id"], None, "cheap card")
    _clean_run(store, cheap_card["id"])

    pricey_board = store.create_board("pricey board")
    pricey_card = store.create_card(pricey_board["id"], None, "pricey card", model="opus")
    store.append_event(pricey_card["id"], "lifecycle_started", {})
    store.append_event(
        pricey_card["id"],
        "result",
        _worker_result(
            cost=0.88, denials=[{"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}]
        ),
    )
    store.append_event(pricey_card["id"], "worker_summary", {"text": "blocked"})

    overview = boards_overview(store)
    assert [b["board_name"] for b in overview["boards"]] == ["pricey board", "cheap board"]
    assert overview["boards"][0]["refusal_cost_usd"] == pytest.approx(0.88)
    assert overview["boards"][0]["pull_requests_opened"] == 0
    assert overview["boards"][0]["cost_per_pr_usd"] is None
    assert overview["totals"]["refusal_cost_usd"] == pytest.approx(0.88)


def test_boards_overview_never_counts_orchestrator_turns(store):
    # no event kind carries an orchestrator/mission-control turn's cost - the panel must say so,
    # never estimate a number the event log does not have
    board = store.create_board("b")
    store.create_card(board["id"], None, "a card")
    overview = boards_overview(store)
    assert overview["orchestrator_turns_counted"] is False


# -- 1db20994's accepted/refused outcome groups, for the c panel's redesigned cost overview -----


def test_outcome_groups_are_empty_with_no_cards(store):
    groups = boards_overview(store)["outcome_groups"]
    assert groups["accepted"] == {"cards": 0, "cost_usd": 0, "cost_per_pr_usd": None}
    assert groups["refused"] == {"cards": 0, "cost_usd": 0, "cost_per_pr_usd": None}


def test_outcome_groups_count_a_merged_pr_as_accepted(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "a card")
    _clean_run(store, card["id"])

    groups = boards_overview(store)["outcome_groups"]
    assert groups["accepted"]["cards"] == 1
    assert groups["accepted"]["cost_usd"] == pytest.approx(0.13)
    assert groups["accepted"]["cost_per_pr_usd"] == pytest.approx(0.13)
    assert groups["refused"]["cards"] == 0
    assert groups["refused"]["cost_per_pr_usd"] is None


def test_outcome_groups_count_a_run_that_never_reached_the_worker_as_refused(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "a card")
    store.append_event(card["id"], "lifecycle_started", {})
    store.append_event(card["id"], "result", _worker_result(cost=0.05))
    # no worker_summary event - _attempt_outcome reads that as "refused": never reached the worker

    groups = boards_overview(store)["outcome_groups"]
    assert groups["refused"] == {
        "cards": 1,
        "cost_usd": pytest.approx(0.05),
        "cost_per_pr_usd": pytest.approx(0.05),
    }
    assert groups["accepted"]["cards"] == 0


def test_outcome_groups_count_a_rejected_review_as_refused_not_accepted(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "a card")
    store.append_event(card["id"], "lifecycle_started", {})
    store.append_event(card["id"], "result", _worker_result(cost=0.20))
    store.append_event(card["id"], "worker_summary", {"text": "did the thing"})
    store.append_event(card["id"], "review_gate", {"approved": False, "findings": ["nope"]})

    groups = boards_overview(store)["outcome_groups"]
    assert groups["refused"]["cards"] == 1
    assert groups["refused"]["cost_usd"] == pytest.approx(0.20)
    assert groups["accepted"]["cards"] == 0


def test_outcome_groups_exclude_a_card_still_blocked_on_crash(store):
    # CRASH and USAGE_LIMIT count in neither group - the run stopped short of both a pull request
    # and a refusal, so it names neither outcome
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "a card")
    store.append_event(card["id"], "lifecycle_started", {})
    store.append_event(card["id"], "result", _worker_result(cost=0.40))
    store.append_event(card["id"], "worker_summary", {"text": "working"})
    store.append_event(card["id"], "run_orphaned", {})

    groups = boards_overview(store)["outcome_groups"]
    assert groups["accepted"]["cards"] == 0
    assert groups["refused"]["cards"] == 0


def test_outcome_groups_ignore_a_card_never_run(store):
    board = store.create_board("b")
    store.create_card(board["id"], None, "never run")
    groups = boards_overview(store)["outcome_groups"]
    assert groups["accepted"]["cards"] == 0
    assert groups["refused"]["cards"] == 0


# -- the /api/costs route end to end over real http -----------------------------------------


@pytest.fixture
def running_server(tmp_path):
    ready = threading.Event()
    holder = {}

    def _run():
        server_store = Store(tmp_path / "board.db")
        server = build_server(server_store, port=0, host="127.0.0.1")
        holder["server"] = server
        ready.set()
        server.serve_forever()
        server_store.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    ready.wait()
    port = holder["server"].server_address[1]
    yield f"http://127.0.0.1:{port}"
    holder["server"].shutdown()
    thread.join()
    holder["server"].server_close()


def _request(url, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            payload = resp.read()
            return resp.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        return exc.code, json.loads(payload) if payload else None


def test_costs_route(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "b"})
    _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "c"},
    )
    status, body = _request(f"{running_server}/api/costs")
    assert status == 200
    assert body["boards"][0]["board_name"] == "b"
    assert body["totals"]["board_name"] == "all boards"
