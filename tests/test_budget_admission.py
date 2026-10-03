"""recorded spend gates every new model call, including retries and board-only roles"""

import pytest

from smortboard.budgets import board_spend_refusal, spend_refusal
from smortboard.labs.events import event_cost, neutral_events
from smortboard.orchestrator import _real_runner
from smortboard.store import Store
from smortboard.telemetry import board_spend_today
from tests.test_orchestrator_runner import _wire


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "b.db") as value:
        yield value


@pytest.mark.parametrize("role", ["orchestrator", "fold"])
def test_each_board_call_rechecks_daily_spend(store, monkeypatch, role):
    calls = []
    _wire(monkeypatch, calls)
    board = store.create_board("b")
    store.set_board_daily_budget(board["id"], 0.01)
    run = _real_runner(store, board["id"], None, "rules", [], role=role)
    run("brief", "sonnet", 1)
    with pytest.raises(RuntimeError, match="daily budget"):
        run("another turn or screenshot", "sonnet", 1)
    assert len(calls) == 1


@pytest.mark.parametrize("role", ["orchestrator", "fold"])
def test_fallback_cannot_bypass_daily_spend(store, monkeypatch, role):
    from dataclasses import replace

    from smortboard import orchestrator, profiles

    calls = []
    _wire(monkeypatch, calls)
    original = orchestrator.run_process

    def limited(*args, **kwargs):
        return replace(original(*args, **kwargs), blocked_reason_code="USAGE_LIMIT")

    monkeypatch.setattr(orchestrator, "run_process", limited)
    profiles.add_profile("openai-work", "test-key", lab="openai", kind="api_key")
    board = store.create_board("b")
    store.set_board_daily_budget(board["id"], 0.01)
    store.set_setting("usage_limit_route", "switch")
    store.set_setting(f"{role}_cross_lab_fallback", [{"ref": "openai/gpt-5.6-sol"}])
    run = _real_runner(store, board["id"], None, "rules", [], role=role)
    with pytest.raises(RuntimeError, match="daily budget"):
        run("brief", "sonnet", 1)
    assert len(calls) == 1


def test_unknown_history_recovers_without_changing_raw_or_actual_costs(store, monkeypatch):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "c")
    identity = {"lab": "openai", "model": "priced", "profile": "work"}
    usage = {
        "input_tokens": 1000000,
        "output_tokens": 1000000,
        "cached_tokens": 500000,
        "cost_usd": None,
    }
    event = store.append_event(
        card["id"],
        "result",
        {
            "neutral": [
                {**identity, "kind": "usage", "usage": usage},
                {**identity, "kind": "result", "result": {"ok": False}},
            ]
        },
    )
    store.add_board_spend(board["id"], "fold", None, lab="openai", model="priced", tokens=usage)
    store.add_board_spend(
        board["id"], "orchestrator", 0.25, lab="openai", model="priced", tokens=usage
    )
    store.set_board_daily_budget(board["id"], 20)
    assert "unknown" in board_spend_refusal(store, board["id"])
    prices = {"input": 2, "cached_input": 1, "output": 3}
    monkeypatch.setattr(
        "smortboard.labs.catalog.load_catalog",
        lambda: {"openai": {"models": [{"id": "priced", "price_per_mtok": prices}]}},
    )
    assert event_cost(event) == 4.5
    recovered = neutral_events(event)[0]["usage"]
    assert recovered["cost_estimated"] is True
    assert recovered["cost_provenance"]["price_per_mtok"] == prices
    assert event["payload"]["neutral"][0]["usage"]["cost_usd"] is None
    assert board_spend_today(store, board["id"]) == 9.25
    assert spend_refusal(store, card) is None
    rows = store.list_board_spend(board["id"])
    assert rows[0]["cost_provenance"]["method"] == "historical_estimate"
    assert rows[1]["cost_usd"] == 0.25
    assert "cost_provenance" not in rows[1]
    assert (
        store._conn.execute("SELECT cost_usd FROM board_spend WHERE role = 'fold'").fetchone()[0]
        is None
    )


def test_missing_historical_counts_stay_unknown_even_with_prices(store, monkeypatch):
    monkeypatch.setattr(
        "smortboard.labs.catalog.load_catalog",
        lambda: {
            "openai": {
                "models": [
                    {"id": "priced", "price_per_mtok": {"input": 2, "cached_input": 1, "output": 3}}
                ]
            }
        },
    )
    board = store.create_board("b")
    store.add_board_spend(board["id"], "fold", None, lab="openai", model="priced")
    assert board_spend_today(store, board["id"]) is None


def test_spend_start_marker_requires_a_started_process(store, tmp_path):
    import sys

    from smortboard.exec.runner import run_process

    board = store.create_board("b")
    card = store.create_card(board["id"], None, "c")
    with pytest.raises(FileNotFoundError):
        run_process(store, card["id"], [str(tmp_path / "missing-program")])
    assert store.list_events(card["id"]) == []
    run_process(
        store,
        card["id"],
        [sys.executable, "-c", 'print(\'{"type":"result","total_cost_usd":0.1}\')'],
        model="sonnet",
    )
    events = store.list_events(card["id"])
    assert events[0]["kind"] == "model_call_started"
    assert events[0]["payload"]["role"] == "worker"
    assert board_spend_today(store, board["id"]) == 0.1


@pytest.mark.parametrize("container_name", [None, "test-container"])
def test_failed_start_recording_stops_and_reaps_the_process(monkeypatch, container_name):
    import sqlite3
    import subprocess
    import sys

    from smortboard.exec import runner

    class BrokenStore:
        def append_event(self, *args):
            raise sqlite3.OperationalError("database is locked")

    children = []
    removed = []
    launch = subprocess.Popen

    def start(*args, **kwargs):
        process = launch(*args, **kwargs)
        children.append(process)
        return process

    def remove(cmd, **kwargs):
        removed.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(runner.subprocess, "Popen", start)
    monkeypatch.setattr(runner.subprocess, "run", remove)
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        runner.run_process(
            BrokenStore(),
            "c",
            [sys.executable, "-c", "import time; time.sleep(60)"],
            container_name=container_name,
        )
    assert len(children) == 1
    assert children[0].poll() is not None
    assert removed == ([["docker", "rm", "-f", container_name]] if container_name else [])
