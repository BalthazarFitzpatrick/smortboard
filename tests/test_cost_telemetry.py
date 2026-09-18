"""per-card cost telemetry, the board cost table, and the orchestrator's run-history evidence.

fixtures build a card's event log by hand, the same shape the runner and lifecycle actually
append - a worker `result` immediately followed by `worker_summary`, a reviewer `result`
immediately followed by `review_gate`, a `merge_request` once one opens.
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from smortboard.orchestrator import build_board_snapshot
from smortboard.server.app import build_server
from smortboard.store.api import Store
from smortboard.telemetry import board_costs, board_evidence, card_telemetry


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
    """the simplest possible attempt: one worker run, one review, a pull request"""
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


def test_card_telemetry_splits_attempts_and_totals_cost(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "a card", model="haiku")
    _clean_run(store, card["id"])

    telemetry = card_telemetry(store, card["id"])
    assert telemetry["model"] == "haiku"
    assert len(telemetry["attempts"]) == 1
    attempt = telemetry["attempts"][0]
    assert attempt["outcome"] == "pull request"
    assert attempt["worker_model"] == "anthropic/claude-sonnet-4"
    assert attempt["reviewer_model"] == "anthropic/claude-sonnet-4"
    assert attempt["worker_cost_usd"] == pytest.approx(0.10)
    assert attempt["reviewer_cost_usd"] == pytest.approx(0.03)
    assert attempt["cost_usd"] == pytest.approx(0.13)
    assert attempt["refusal_count"] == 0
    assert telemetry["totals"]["cost_usd"] == pytest.approx(0.13)
    assert telemetry["totals"]["attempts"] == 1
    assert telemetry["totals"]["refusal_cost_usd"] == 0


def test_card_telemetry_names_the_working_model_not_the_helper(store):
    # the real shape: claude code bills a haiku helper next to the model doing the work
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "a card")
    store.append_event(card["id"], "lifecycle_started", {})
    result = _worker_result(cost=0.2485)
    result["modelUsage"] = {
        "claude-haiku-4-5-20251001": {"outputTokens": 17, "costUSD": 0.0009},
        "claude-sonnet-5": {"outputTokens": 822, "costUSD": 0.2476},
    }
    store.append_event(card["id"], "result", result)
    store.append_event(card["id"], "worker_summary", {"text": "did the thing"})

    attempt = card_telemetry(store, card["id"])["attempts"][0]
    assert attempt["worker_model"] == "anthropic/claude-sonnet-5"


def test_card_telemetry_counts_refusals_and_their_cost(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "a card")
    store.append_event(card["id"], "lifecycle_started", {})
    denials = [
        {"tool_name": "Bash", "tool_use_id": "t1", "tool_input": {"command": "uv run ruff ."}},
        {"tool_name": "Bash", "tool_use_id": "t2", "tool_input": {"command": "git push"}},
    ]
    store.append_event(card["id"], "result", _worker_result(cost=0.88, denials=denials))
    store.append_event(card["id"], "worker_summary", {"text": "blocked by denials"})

    telemetry = card_telemetry(store, card["id"])
    attempt = telemetry["attempts"][0]
    assert attempt["refusal_count"] == 2
    assert {d["target"] for d in attempt["refusals"]} == {"uv run ruff .", "git push"}
    assert attempt["cost_usd"] == pytest.approx(0.88)
    assert telemetry["totals"]["refusal_cost_usd"] == pytest.approx(0.88)
    assert telemetry["totals"]["refusal_count"] == 2


def test_card_telemetry_reports_fix_rounds(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "a card")
    store.append_event(card["id"], "lifecycle_started", {})
    store.append_event(card["id"], "result", _worker_result())
    store.append_event(card["id"], "worker_summary", {"text": "first pass"})
    store.append_event(card["id"], "review_gate", {"approved": False, "findings": [{"a": 1}]})
    store.append_event(card["id"], "fix_round", {"round": 1})
    store.append_event(card["id"], "result", _worker_result(cost=0.02))
    store.append_event(card["id"], "worker_summary", {"text": "fixed it"})
    store.append_event(card["id"], "review_gate", {"approved": True, "findings": []})
    store.append_event(card["id"], "merge_request", {"url": "https://example.test/pr/2"})

    telemetry = card_telemetry(store, card["id"])
    attempt = telemetry["attempts"][0]
    assert attempt["fix_rounds"] == 1
    assert attempt["outcome"] == "pull request"


def test_card_telemetry_reads_blocked_and_refused_outcomes(store):
    board = store.create_board("b")

    blocked_tests = store.create_card(board["id"], None, "fails tests")
    store.append_event(blocked_tests["id"], "lifecycle_started", {})
    store.append_event(blocked_tests["id"], "result", _worker_result())
    store.append_event(blocked_tests["id"], "worker_summary", {"text": "done"})
    store.append_event(
        blocked_tests["id"], "test_gate", {"passed": False, "command": "pytest", "exit_code": 1}
    )
    assert (
        card_telemetry(store, blocked_tests["id"])["attempts"][0]["outcome"]
        == "blocked: TESTS_FAILED"
    )

    never_started = store.create_card(board["id"], None, "no repo")
    store.append_event(never_started["id"], "lifecycle_started", {})
    assert card_telemetry(store, never_started["id"])["attempts"][0]["outcome"] == "refused"


def test_card_telemetry_with_no_events_has_no_attempts(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "never run")
    telemetry = card_telemetry(store, card["id"])
    assert telemetry["attempts"] == []
    assert telemetry["totals"]["cost_usd"] == 0


def test_card_telemetry_second_run_does_not_lose_the_first(store):
    """a card run twice keeps both attempts, unlike card_outcome which only shows the latest"""
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "rerun me")
    _clean_run(store, card["id"], pr=False)
    store.append_event(card["id"], "decision", {"decision": "rejected"})
    _clean_run(store, card["id"])

    telemetry = card_telemetry(store, card["id"])
    assert len(telemetry["attempts"]) == 2
    assert telemetry["attempts"][1]["outcome"] == "pull request"


# -- board cost table -----------------------------------------------------------------------


def test_board_costs_orders_costliest_first_and_carries_refusals(store):
    board = store.create_board("b")
    cheap = store.create_card(board["id"], None, "cheap card", model="haiku")
    _clean_run(store, cheap["id"])

    pricey = store.create_card(board["id"], None, "pricey card", model="opus")
    store.append_event(pricey["id"], "lifecycle_started", {})
    store.append_event(
        pricey["id"],
        "result",
        _worker_result(
            cost=0.88, denials=[{"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}]
        ),
    )
    store.append_event(pricey["id"], "worker_summary", {"text": "blocked"})

    rows = board_costs(store, board["id"])
    assert [r["title"] for r in rows] == ["pricey card", "cheap card"]
    assert rows[0]["refusal_count"] == 1
    assert rows[0]["refusal_cost_usd"] == pytest.approx(0.88)
    assert rows[1]["refusal_count"] == 0
    assert rows[0]["model"] == "opus"


def test_board_costs_includes_a_card_never_run(store):
    board = store.create_board("b")
    store.create_card(board["id"], None, "idle card")
    rows = board_costs(store, board["id"])
    assert rows == [
        {
            "card_id": rows[0]["card_id"],
            "title": "idle card",
            "model": None,
            "lab": None,
            "cost_estimated": False,
            "unknown_costs": 0,
            "cost_usd": 0,
            "attempts": 0,
            "refusal_count": 0,
            "refusal_cost_usd": 0,
            "last_outcome": "no attempts yet",
        }
    ]


# -- orchestrator evidence and scorecard -----------------------------------------------------


def test_board_evidence_only_counts_finished_cards(store):
    board = store.create_board("b")
    finished = store.create_card(board["id"], None, "done card", model="sonnet")
    _clean_run(store, finished["id"])

    running = store.create_card(board["id"], None, "still running", model="sonnet")
    store.append_event(running["id"], "lifecycle_started", {})
    store.append_event(running["id"], "result", _worker_result())
    store.append_event(running["id"], "worker_summary", {"text": "mid-run"})

    idle = store.create_card(board["id"], None, "never run")

    evidence = board_evidence(store, board["id"])
    ids = {row["id"] for row in evidence["cards"]}
    assert finished["id"][:8] in ids
    assert running["id"][:8] not in ids
    assert idle["id"][:8] not in ids


def test_board_evidence_scorecard_tracks_clean_pr_rate_and_avg_cost(store):
    board = store.create_board("b")

    clean = store.create_card(board["id"], None, "clean run", model="sonnet")
    _clean_run(store, clean["id"], model="claude-sonnet-4")

    messy = store.create_card(board["id"], None, "needed a fix", model="sonnet")
    store.append_event(messy["id"], "lifecycle_started", {})
    store.append_event(messy["id"], "result", _worker_result(cost=0.20, model="claude-sonnet-4"))
    store.append_event(messy["id"], "worker_summary", {"text": "first pass"})
    store.append_event(messy["id"], "review_gate", {"approved": False, "findings": [{"a": 1}]})
    store.append_event(messy["id"], "fix_round", {"round": 1})
    store.append_event(messy["id"], "result", _worker_result(cost=0.05, model="claude-sonnet-4"))
    store.append_event(messy["id"], "worker_summary", {"text": "fixed"})
    store.append_event(messy["id"], "review_gate", {"approved": True, "findings": []})
    store.append_event(messy["id"], "merge_request", {"url": "https://example.test/pr/3"})

    evidence = board_evidence(store, board["id"])
    row = next(r for r in evidence["model_scorecard"] if r["model"] == "anthropic/claude-sonnet-4")
    assert row["cards"] == 2
    assert row["clean_pr_rate"] == 0.5  # one of the two reached a pr with zero fix rounds
    assert row["avg_cost_usd"] == pytest.approx((0.13 + 0.25) / 2, rel=1e-3)


def test_orchestrator_snapshot_carries_evidence_with_no_host_paths(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "a card", model="sonnet")
    _clean_run(store, card["id"])

    snapshot = build_board_snapshot(store, board["id"])
    assert "evidence" in snapshot
    assert snapshot["evidence"]["cards"][0]["id"] == card["id"][:8]
    assert "/Users/" not in str(snapshot)
    assert str(store.path) not in str(snapshot)


# -- the two routes, end to end over real http --------------------------------------------


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


def test_card_telemetry_route(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "b"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "c"},
    )
    status, body = _request(f"{running_server}/api/cards/{card['id']}/telemetry")
    assert status == 200
    assert body["card_id"] == card["id"]
    assert body["attempts"] == []

    status, _ = _request(f"{running_server}/api/cards/does-not-exist/telemetry")
    assert status == 404


def test_board_costs_route(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "b"})
    _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "c"},
    )
    status, rows = _request(f"{running_server}/api/boards/{board['id']}/costs")
    assert status == 200
    assert rows[0]["title"] == "c"

    status, _ = _request(f"{running_server}/api/boards/does-not-exist/costs")
    assert status == 404
