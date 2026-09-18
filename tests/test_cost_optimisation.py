"""GET /api/costs/optimisation and telemetry.cost_optimisation - cap fit, suggested caps, waste,
model fit. events are built by hand, the same shape the runner and lifecycle append - see
test_cost_telemetry.py's docstring."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from smortboard.server.app import build_server
from smortboard.store.api import Store
from smortboard.telemetry import cost_optimisation


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "board.db") as s:
        yield s


def _worker_result(cost=0.10, turns=4, model="claude-sonnet-4", subtype=None):
    result = {
        "total_cost_usd": cost,
        "num_turns": turns,
        "permission_denials": [],
        "modelUsage": {model: {"inputTokens": 100, "outputTokens": 50, "costUSD": cost}},
        "result": "done",
    }
    if subtype:
        result["subtype"] = subtype
    return result


def _accepted_run(store, card_id, cost=0.10, model="claude-sonnet-4"):
    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(card_id, "result", _worker_result(cost=cost, model=model))
    store.append_event(card_id, "worker_summary", {"text": "did the thing"})
    store.append_event(card_id, "test_gate", {"passed": True, "command": "x", "exit_code": 0})
    store.append_event(card_id, "result", _worker_result(cost=0.02, model="claude-sonnet-4"))
    store.append_event(card_id, "review_gate", {"approved": True, "findings": []})
    store.append_event(card_id, "merge_request", {"url": "https://example.test/pr/1"})


def _capped_run(store, card_id, cost=5.0, model="claude-sonnet-4"):
    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(
        card_id, "result", _worker_result(cost=cost, model=model, subtype="error_max_budget_usd")
    )


def _refused_run(store, card_id):
    store.append_event(card_id, "lifecycle_started", {})


def _crashed_run(store, card_id, cost=0.05):
    store.append_event(card_id, "lifecycle_started", {})
    result = _worker_result(cost=cost)
    result["is_error"] = True
    store.append_event(card_id, "result", result)


def test_shape_has_the_four_sections(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "c", complexity=1)
    _accepted_run(store, card["id"])
    optimisation = cost_optimisation(store)
    assert set(optimisation) == {"board_id", "cap_fit", "suggested_caps", "waste", "model_fit"}
    assert optimisation["board_id"] is None


def test_cap_fit_splits_within_and_over_cap(store):
    board = store.create_board("b")
    store.set_setting("worker_budget_usd", 1.0)
    low = store.create_card(board["id"], None, "low", complexity=1)
    _accepted_run(store, low["id"], cost=0.10)
    over = store.create_card(board["id"], None, "over cap", complexity=1)
    _capped_run(store, over["id"], cost=1.5)

    rows = cost_optimisation(store)["cap_fit"]["rows"]
    row = next(r for r in rows if r["complexity"] == 1 and r["source"] == "rated")
    assert row["runs"] == 2
    assert row["within_cap"] == 1
    assert row["hit_cap"] == 1
    assert row["max_cost_usd"] == pytest.approx(1.5)


def test_cap_fit_marks_estimated_rows(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "unrated")
    _accepted_run(store, card["id"], cost=0.05)
    rows = cost_optimisation(store)["cap_fit"]["rows"]
    assert any(r["source"] == "estimated" for r in rows)


def test_suggested_caps_needs_five_runs(store):
    board = store.create_board("b")
    for i in range(3):
        card = store.create_card(board["id"], None, f"c{i}")
        _accepted_run(store, card["id"], cost=0.10)
    caps = cost_optimisation(store)["suggested_caps"]
    assert caps["worker"]["note"] == "not enough runs"
    assert caps["orchestrator"]["note"] == "not enough runs"


def test_suggested_caps_p90_and_rounding(store):
    board = store.create_board("b")
    costs = [0.10, 0.20, 0.30, 0.40, 1.90]  # p90 (nearest-rank) is the max, 1.90 -> ceil to 2.0
    for i, cost in enumerate(costs):
        card = store.create_card(board["id"], None, f"c{i}")
        _accepted_run(store, card["id"], cost=cost)
    caps = cost_optimisation(store)["suggested_caps"]["worker"]
    assert caps["runs"] == 5
    assert caps["p90_cost_usd"] == pytest.approx(1.90)
    assert caps["suggested_cap_usd"] == pytest.approx(2.0)
    assert caps["runs_that_would_be_cut"] == 0


def test_waste_groups_by_reason(store):
    board = store.create_board("b")
    refused = store.create_card(board["id"], None, "refused")
    _refused_run(store, refused["id"])
    crashed = store.create_card(board["id"], None, "crashed")
    _crashed_run(store, crashed["id"], cost=0.05)
    capped = store.create_card(board["id"], None, "capped")
    _capped_run(store, capped["id"], cost=1.5)

    waste = cost_optimisation(store)["waste"]
    reasons = {row["reason"]: row for row in waste["rows"]}
    assert "refused" in reasons
    assert "crashed" in reasons
    assert reasons["crashed"]["cost_usd"] == pytest.approx(0.05)
    assert "hit cap" in reasons
    assert reasons["hit cap"]["cost_usd"] == pytest.approx(1.5)


def test_model_fit_per_model_and_complexity(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "c", model="opus", complexity=3)
    _accepted_run(store, card["id"], cost=0.5, model="opus")
    store.update_card(card["id"], status="accepted")

    rows = cost_optimisation(store)["model_fit"]
    row = next(r for r in rows if r["model"] == "anthropic/opus" and r["complexity"] == 3)
    assert row["cards"] == 1
    assert row["accepted_cards"] == 1
    assert row["cost_per_accepted_card_usd"] > 0


def test_board_scoping(store):
    b1 = store.create_board("b1")
    b2 = store.create_board("b2")
    c1 = store.create_card(b1["id"], None, "c1")
    _accepted_run(store, c1["id"], cost=0.10)
    c2 = store.create_card(b2["id"], None, "c2")
    _accepted_run(store, c2["id"], cost=0.20)

    scoped = cost_optimisation(store, b1["id"])
    assert scoped["cap_fit"]["rows"][0]["runs"] == 1


# -- the http route ----------------------------------------------------------------------


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


def test_cost_optimisation_route(running_server):
    status, body = _request(f"{running_server}/api/costs/optimisation")
    assert status == 200
    assert set(body) == {"board_id", "cap_fit", "suggested_caps", "waste", "model_fit"}


def test_cost_optimisation_route_scoped_to_a_board(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "b"})
    status, body = _request(f"{running_server}/api/costs/optimisation?board={board['id']}")
    assert status == 200
    assert body["board_id"] == board["id"]

    status, _ = _request(f"{running_server}/api/costs/optimisation?board=does-not-exist")
    assert status == 404


def test_mission_control_turn_spend_feeds_its_suggested_cap(tmp_path):
    with Store(tmp_path / "b.db") as store:
        board = store.create_board("b")
        for cost in (0.2, 0.3, 0.4, 0.5, 0.9):
            store.add_board_spend(board["id"], "orchestrator", cost)
        caps = cost_optimisation(store)["suggested_caps"]
    assert caps["orchestrator"]["runs"] == 5
    assert caps["orchestrator"]["suggested_cap_usd"] > 0
