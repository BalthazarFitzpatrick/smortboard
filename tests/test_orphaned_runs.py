"""a board that dies mid-run: the next board to start finds the card it left and blocks it.

The run registry is in memory, so a card whose board process died keeps showing doing with nothing
behind it. These prove the mark a finished run leaves, and the recovery that reads its absence.
"""

import time

import pytest

from smortboard.lifecycle import LifecycleResult
from smortboard.server import runs as runs_module
from smortboard.server.app import build_server
from smortboard.store import Store
from smortboard.telemetry import _attempt_outcome


def _wait(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "board.db")
    yield s
    s.close()


@pytest.fixture
def board_id(store):
    return store.create_board("b")["id"]


def _started(store, board_id, *kinds):
    """a card whose run began - lifecycle_started, then any later events - and is still doing"""
    card = store.create_card(board_id, None, "mid-run")
    store.append_event(card["id"], "lifecycle_started", {})
    for kind in kinds:
        store.append_event(card["id"], kind, {})
    store.update_card(card["id"], status="doing")
    return card["id"]


def test_a_card_left_mid_run_is_blocked_as_a_crash(store, board_id):
    card_id = _started(store, board_id, "assistant")
    assert runs_module.recover_orphaned_runs(store) == [card_id]
    card = store.get_card(card_id)
    assert card["status"] == "doing"
    assert card["blocked_reason_code"] == "CRASH"
    assert card["review_flag"] == 1
    last = card["comments"][-1]["body"]
    assert last.startswith(runs_module.ORPHANED_NOTE)
    assert "\n\nNext: " in last  # the last comment says what to do


def test_a_run_that_ended_is_left_alone(store, board_id):
    card_id = _started(store, board_id, "assistant", "run_ended")
    assert runs_module.recover_orphaned_runs(store) == []
    assert store.get_card(card_id)["blocked_reason_code"] is None


def test_an_earlier_ended_run_does_not_cover_a_later_orphaned_one(store, board_id):
    card_id = _started(store, board_id, "run_ended", "lifecycle_started")
    assert runs_module.recover_orphaned_runs(store) == [card_id]


def test_blocked_flagged_and_queued_cards_are_left_alone(store, board_id):
    blocked = _started(store, board_id)
    store.update_card(blocked, blocked_reason_code="LEASE_CONFLICT", review_flag=True)
    flagged = _started(store, board_id)
    store.update_card(flagged, review_flag=True)
    queued = store.create_card(board_id, None, "never ran")["id"]
    assert runs_module.recover_orphaned_runs(store) == []
    assert store.get_card(blocked)["blocked_reason_code"] == "LEASE_CONFLICT"
    assert store.get_card(queued)["blocked_reason_code"] is None


def test_the_board_recovers_orphans_when_it_starts(store, board_id):
    card_id = _started(store, board_id)
    server = build_server(store, 0)
    try:
        assert server.recovered == [card_id]
        assert store.get_card(card_id)["blocked_reason_code"] == "CRASH"
    finally:
        server.server_close()


@pytest.mark.parametrize("fails", [False, True])
def test_every_run_writes_run_ended_even_when_its_thread_dies(store, board_id, fails):
    card_id = store.create_card(board_id, None, "a card")["id"]

    def _runner(run_store, cid, **kwargs):
        run_store.append_event(cid, "lifecycle_started", {})
        if fails:
            raise RuntimeError("boom")
        return LifecycleResult(card_id=cid, phase="opened")

    registry = runs_module.RunRegistry(store.path)
    state = registry.start(card_id, runner=_runner)
    assert _wait(lambda: not state.running)
    ended = [e for e in store.list_events(card_id) if e["kind"] == "run_ended"]
    assert [e["payload"]["phase"] for e in ended] == ["blocked" if fails else "opened"]


def test_run_ended_is_written_while_the_run_still_counts_as_running(store, board_id, monkeypatch):
    """a reader that sees running flip off must find run_ended already there - the other order
    let the parallel suite see a finished run with no end mark"""
    card_id = store.create_card(board_id, None, "a card")["id"]
    seen = []
    original = runs_module.Store.append_event

    def _spy(self, cid, kind, payload):
        if kind == "run_ended":
            # through the registry: the thread can reach here before start() has returned
            seen.append(registry.get(cid).running)
        return original(self, cid, kind, payload)

    registry = runs_module.RunRegistry(store.path)
    monkeypatch.setattr(runs_module.Store, "append_event", _spy)
    state = registry.start(
        card_id, runner=lambda s, cid, **kw: LifecycleResult(card_id=cid, phase="opened")
    )
    assert _wait(lambda: not state.running)
    assert seen == [True]


def test_telemetry_reads_an_orphaned_attempt_as_a_crash_not_in_progress():
    segment = [
        {"kind": "lifecycle_started", "payload": {}},
        {"kind": "worker_summary", "payload": {}},
        {"kind": "run_orphaned", "payload": {}},
    ]
    assert _attempt_outcome(segment) == "blocked: CRASH"
