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
    assert overview["orchestrator_turns_counted"] is True


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
    assert row["spend_by_model"] == [
        {
            "model": "anthropic/claude-sonnet-4",
            "cost_usd": pytest.approx(0.13),
            "known_cost_usd": pytest.approx(0.13),
            "unknown_costs": 0,
            "cost_estimated": False,
        }
    ]

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
    assert overview["orchestrator_turns_counted"] is True


# -- the c panel's redesigned 3x3 grid: total / accepted / refused, keyed off card status --------


def test_cost_groups_are_empty_with_no_cards(store):
    groups = boards_overview(store)["cost_groups"]
    empty = {
        "cards": 0,
        "cost_usd": 0,
        "known_cost_usd": 0,
        "unknown_costs": 0,
        "cost_estimated": False,
        "prs": 0,
        "cost_per_card_usd": None,
        "cost_per_pr_usd": None,
        "known_cost_per_card_usd": None,
        "known_cost_per_pr_usd": None,
    }
    assert groups["total"] == empty
    assert groups["accepted"] == empty
    assert groups["refused"] == empty


def test_cost_groups_count_an_accepted_card_by_status_not_outcome(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "a card")
    _clean_run(store, card["id"])
    store.update_card(card["id"], status="accepted")

    groups = boards_overview(store)["cost_groups"]
    assert groups["accepted"]["cards"] == 1
    assert groups["accepted"]["prs"] == 1
    assert groups["accepted"]["cost_usd"] == pytest.approx(0.13)
    assert groups["accepted"]["cost_per_card_usd"] == pytest.approx(0.13)
    assert groups["accepted"]["cost_per_pr_usd"] == pytest.approx(0.13)
    assert groups["refused"]["cards"] == 0
    assert groups["refused"]["cost_per_card_usd"] is None
    assert groups["total"]["cards"] == 1
    assert groups["total"]["cost_usd"] == pytest.approx(0.13)


def test_cost_groups_count_a_rejected_card_as_refused(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "a card")
    store.append_event(card["id"], "lifecycle_started", {})
    store.append_event(card["id"], "result", _worker_result(cost=0.05))
    store.update_card(card["id"], status="rejected")

    groups = boards_overview(store)["cost_groups"]
    assert groups["refused"] == {
        "cards": 1,
        "cost_usd": pytest.approx(0.05),
        "known_cost_usd": pytest.approx(0.05),
        "unknown_costs": 0,
        "cost_estimated": False,
        "prs": 0,
        "cost_per_card_usd": pytest.approx(0.05),
        "cost_per_pr_usd": None,
        "known_cost_per_card_usd": pytest.approx(0.05),
        "known_cost_per_pr_usd": None,
    }
    assert groups["accepted"]["cards"] == 0
    assert groups["total"]["cards"] == 1


def test_cost_groups_count_a_still_open_card_in_total_only(store):
    # todo/doing/checking/blocked count toward total spend but neither accepted nor refused -
    # the card has not reached either final status yet
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "a card")
    store.append_event(card["id"], "lifecycle_started", {})
    store.append_event(card["id"], "result", _worker_result(cost=0.40))
    store.append_event(card["id"], "worker_summary", {"text": "working"})
    store.append_event(card["id"], "run_orphaned", {})

    groups = boards_overview(store)["cost_groups"]
    assert groups["total"]["cards"] == 1
    assert groups["total"]["cost_usd"] == pytest.approx(0.40)
    assert groups["accepted"]["cards"] == 0
    assert groups["refused"]["cards"] == 0


def test_cost_groups_include_a_card_never_run_in_total_only(store):
    board = store.create_board("b")
    store.create_card(board["id"], None, "never run")
    groups = boards_overview(store)["cost_groups"]
    assert groups["total"]["cards"] == 1
    assert groups["total"]["cost_usd"] == 0
    assert groups["accepted"]["cards"] == 0
    assert groups["refused"]["cards"] == 0


# -- an old run with no recorded price keeps the known sum and is counted, not a blank total ----


def _unpriced_run(store, card_id):
    """a worker run whose result carries no cost at all - an old log, or a lab with no price"""
    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(card_id, "result", {"num_turns": 3, "result": "done"})
    store.append_event(card_id, "worker_summary", {"text": "did the thing"})


def test_boards_overview_keeps_known_spend_beside_unknown_runs(store):
    mixed = store.create_board("mixed")
    priced = store.create_card(mixed["id"], None, "priced")
    store.update_card(priced["id"], status="accepted")
    _clean_run(store, priced["id"])
    unpriced = store.create_card(mixed["id"], None, "unpriced")
    store.update_card(unpriced["id"], status="accepted")
    _unpriced_run(store, unpriced["id"])
    only_unknown = store.create_board("only unknown")
    _unpriced_run(store, store.create_card(only_unknown["id"], None, "old")["id"])
    store.add_board_spend(mixed["id"], "fold", 0.25)
    store.add_board_spend(mixed["id"], "fold", None)

    overview = boards_overview(store)
    rows = {row["board_name"]: row for row in overview["boards"]}
    assert [row["board_name"] for row in overview["boards"]] == ["mixed", "only unknown"]

    row = rows["mixed"]
    assert row["cost_usd"] is None
    assert row["known_cost_usd"] == pytest.approx(0.13)
    assert row["unknown_costs"] == 1
    assert row["worker_known_cost_usd"] == pytest.approx(0.10)
    assert row["worker_unknown_costs"] == 1
    assert row["reviewer_known_cost_usd"] == pytest.approx(0.03)
    assert row["reviewer_unknown_costs"] == 0
    assert row["known_cost_per_pr_usd"] == pytest.approx(0.13)

    alone = rows["only unknown"]
    assert alone["cost_usd"] is None
    assert alone["known_cost_usd"] == 0
    assert alone["unknown_costs"] == 1
    assert alone["known_cost_per_pr_usd"] is None

    totals = overview["totals"]
    assert totals["cost_usd"] is None
    assert totals["known_cost_usd"] == pytest.approx(0.13)
    assert totals["unknown_costs"] == 2
    assert totals["worker_cost_usd"] is None
    assert totals["worker_known_cost_usd"] == pytest.approx(0.10)
    assert totals["worker_unknown_costs"] == 2
    assert totals["reviewer_cost_usd"] == pytest.approx(0.03)
    assert totals["reviewer_unknown_costs"] == 0
    assert totals["turn_cost_usd"] is None
    assert totals["turn_known_cost_usd"] == pytest.approx(0.25)
    assert totals["turn_unknown_costs"] == 1

    accepted = overview["cost_groups"]["accepted"]
    assert accepted["cost_usd"] is None
    assert accepted["cost_per_card_usd"] is None
    assert accepted["known_cost_usd"] == pytest.approx(0.13)
    assert accepted["unknown_costs"] == 1
    assert accepted["known_cost_per_card_usd"] == pytest.approx(0.065)
    assert accepted["known_cost_per_pr_usd"] == pytest.approx(0.13)
    assert overview["cost_groups"]["total"]["unknown_costs"] == 2


def test_boards_overview_refusal_spend_keeps_known_part(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "a card")
    store.append_event(card["id"], "lifecycle_started", {})
    denial = [{"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}]
    store.append_event(card["id"], "result", _worker_result(cost=0.40, denials=denial))
    store.append_event(card["id"], "worker_summary", {"text": "blocked"})
    store.append_event(card["id"], "result", {"num_turns": 1, "result": "reviewed"})

    row = boards_overview(store)["boards"][0]
    assert row["refusal_cost_usd"] is None
    assert row["refusal_known_cost_usd"] == pytest.approx(0.40)
    assert row["refusal_unknown_costs"] == 1


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
