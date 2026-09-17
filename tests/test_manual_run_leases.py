"""a start that never passes through the scheduler still obeys its lease rule.

Found driving the smolsmort board: POST /run went straight to RunRegistry.start, so a card whose
lease overlapped a running card's could be started beside it by hand, and the scheduler in turn
only counted the runs it had started itself.
"""

import json
import threading
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from smortboard.attention import AnswerRefused, answer_card
from smortboard.lifecycle import LifecycleResult
from smortboard.scheduler import BoardScheduler, conflicting_run
from smortboard.server import runs as runs_module
from smortboard.server.app import build_server
from smortboard.store import Store


def _wait(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _call(url, method="GET"):
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else None)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "board.db")
    yield s
    s.close()


@pytest.fixture
def board(store):
    board_id = store.create_board("b")["id"]
    repo_id = store.create_repo(board_id, "r", "/tmp/r", "main")["id"]
    return board_id, repo_id


def test_conflicting_run_names_the_overlapping_card(store, board):
    board_id, repo_id = board
    running = store.create_card(board_id, repo_id, "the running one", leases=["src/**"])
    card = store.create_card(board_id, repo_id, "wants to start", leases=["src/api/x.py"])
    reason = conflicting_run(store, card, [running["id"]])
    assert "the running one" in reason


def test_conflicting_run_ignores_other_repos_disjoint_leases_and_itself(store, board):
    board_id, repo_id = board
    other_repo = store.create_repo(board_id, "r2", "/tmp/r2", "main")["id"]
    elsewhere = store.create_card(board_id, other_repo, "elsewhere", leases=["src/**"])
    disjoint = store.create_card(board_id, repo_id, "disjoint", leases=["docs/**"])
    card = store.create_card(board_id, repo_id, "wants to start", leases=["src/**"])
    ids = [elsewhere["id"], disjoint["id"], card["id"], "gone"]
    assert conflicting_run(store, card, ids) is None


class _Runs:
    """a registry shape with one card already running by hand"""

    def __init__(self, manual_id):
        self.manual_id = manual_id
        self.started = []

    def active(self):
        return [SimpleNamespace(card_id=self.manual_id, running=True)]

    def get(self, card_id):
        return None

    def start(self, card_id, runner=None, on_finish=None):
        self.started.append(card_id)
        return SimpleNamespace(card_id=card_id, running=True, as_dict=lambda: {"running": True})


def test_the_scheduler_waits_a_card_whose_lease_overlaps_a_manual_run(store, board):
    board_id, repo_id = board
    store.set_setting("max_parallel", "5")
    manual = store.create_card(board_id, repo_id, "by hand", status="doing", leases=["src/**"])
    queued = store.create_card(board_id, repo_id, "queued", leases=["src/ui/*"])
    runs = _Runs(manual["id"])
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    assert runs.started == []
    assert manual["id"] in scheduler.schedule_view()["waiting"][queued["id"]]


def test_an_answer_does_not_resume_a_card_beside_an_overlapping_run(store, board):
    board_id, repo_id = board
    manual = store.create_card(board_id, repo_id, "by hand", status="doing", leases=["src/**"])
    card = store.create_card(board_id, repo_id, "blocked", leases=["src/**"])
    store.update_card(card["id"], blocked_reason_code="LEASE_CONFLICT", review_flag=True)
    runs = _Runs(manual["id"])
    with pytest.raises(AnswerRefused, match="by hand"):
        answer_card(store, runs, card["id"], "go on")
    assert runs.started == []
    after = store.get_card(card["id"])
    assert after["blocked_reason_code"] == "LEASE_CONFLICT"
    assert [c for c in after["comments"] if c["author"] == "operator"] == []


def test_a_manual_run_beside_an_overlapping_one_is_a_409(tmp_path, monkeypatch):
    release = threading.Event()

    def _slow_lifecycle(store, cid, **kwargs):
        release.wait(timeout=5)
        return LifecycleResult(card_id=cid, phase="opened")

    monkeypatch.setattr(runs_module, "run_card_lifecycle", _slow_lifecycle)
    ready = threading.Event()
    holder = {}

    def _serve():
        store = Store(tmp_path / "board.db")
        board_id = store.create_board("b")["id"]
        repo_id = store.create_repo(board_id, "r", "/tmp/r", "main")["id"]
        holder["a"] = store.create_card(board_id, repo_id, "first", leases=["src/**"])["id"]
        holder["b"] = store.create_card(board_id, repo_id, "second", leases=["src/x.py"])["id"]
        holder["c"] = store.create_card(board_id, repo_id, "apart", leases=["docs/**"])["id"]
        srv = build_server(store, port=0, host="127.0.0.1")
        holder["server"] = srv
        ready.set()
        srv.serve_forever()
        store.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    ready.wait()
    base = f"http://127.0.0.1:{holder['server'].server_address[1]}"
    try:
        assert _call(f"{base}/api/cards/{holder['a']}/run", method="POST")[0] == 202
        status, body = _call(f"{base}/api/cards/{holder['b']}/run", method="POST")
        assert status == 409
        assert "first" in body["error"]
        # disjoint leases still run together, and pressing r again on the running card is fine
        assert _call(f"{base}/api/cards/{holder['c']}/run", method="POST")[0] == 202
        assert _call(f"{base}/api/cards/{holder['a']}/run", method="POST")[0] == 202
    finally:
        release.set()
        assert _wait(lambda: _call(f"{base}/api/cards/{holder['a']}/run")[1]["running"] is False)
        holder["server"].shutdown()
        thread.join()
        holder["server"].server_close()


def _run_with_seeded_store(tmp_path, setup):
    """a real server with the store built and seeded inside its own thread - sqlite3 connections
    are bound to the thread that opened them, so setup has to happen there too"""
    ready = threading.Event()
    holder = {}

    def _serve():
        store = Store(tmp_path / "board.db")
        holder["card_id"] = setup(store)
        srv = build_server(store, port=0, host="127.0.0.1")
        holder["server"] = srv
        ready.set()
        srv.serve_forever()
        store.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    ready.wait()
    try:
        yield holder["card_id"], f"http://127.0.0.1:{holder['server'].server_address[1]}"
    finally:
        holder["server"].shutdown()
        thread.join(timeout=2)


def test_a_manual_run_past_the_daily_budget_is_a_409_with_the_reason(tmp_path):
    def setup(store):
        board_id = store.create_board("b")["id"]
        repo_id = store.create_repo(board_id, "r", "/tmp/r", "main")["id"]
        store.set_board_daily_budget(board_id, 1.0)
        card = store.create_card(board_id, repo_id, "over budget")
        store.append_event(card["id"], "result", {"total_cost_usd": 1.5})
        return card["id"]

    for card_id, base in _run_with_seeded_store(tmp_path, setup):
        status, body = _call(f"{base}/api/cards/{card_id}/run", method="POST")
        assert status == 409
        assert "daily budget" in body["error"]
        assert "b" in body["error"]


def test_a_manual_run_past_the_card_total_cap_is_a_409_with_the_reason(tmp_path):
    def setup(store):
        board_id = store.create_board("b")["id"]
        repo_id = store.create_repo(board_id, "r", "/tmp/r", "main")["id"]
        store.set_setting("card_total_budget_usd", "2.0")
        card = store.create_card(board_id, repo_id, "capped")
        store.append_event(card["id"], "result", {"total_cost_usd": 2.5})
        return card["id"]

    for card_id, base in _run_with_seeded_store(tmp_path, setup):
        status, body = _call(f"{base}/api/cards/{card_id}/run", method="POST")
        assert status == 409
        assert "total cap" in body["error"]
