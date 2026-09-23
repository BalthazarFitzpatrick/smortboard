"""the usage-limit route: switch to a fallback model unasked, or block and ask in the inbox - plus
the plumbing that makes either retry actually fire: on_finish for every run start, a ticker that
does not wait for the ui, a startup requeue for the retries a restart dropped, and a backoff for a
runtime that is not ready yet.

Docker, the model and the lifecycle are faked. A FakeRuns drives the scheduler synchronously; the
http tests swap runs.run_card_lifecycle for a function that returns at once.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from smortboard import profiles
from smortboard import scheduler as scheduler_module
from smortboard.attention import attention_rows, handled_by_board, with_actions
from smortboard.labs.catalog import load_catalog
from smortboard.labs.routing import run_ref
from smortboard.lifecycle import RUNTIME_NOT_READY, LifecycleResult
from smortboard.scheduler import (
    RUNTIME_BACKOFF_MINUTES,
    BoardScheduler,
    SchedulerRegistry,
    SchedulerTicker,
    _latest_reset,
)
from smortboard.server import runs as runs_module
from smortboard.server.app import build_server, start_background
from smortboard.store.api import Store


class FakeRuns:
    """RunRegistry's shape: start() records the card and holds on_finish until finish()"""

    def __init__(self):
        self.started: list[str] = []
        self._callbacks: dict[str, object] = {}

    def start(self, card_id, runner=None, on_finish=None):
        self.started.append(card_id)
        if on_finish is not None:
            self._callbacks[card_id] = on_finish
        state = SimpleNamespace(card_id=card_id, running=True)
        state.as_dict = lambda: {"card_id": card_id, "running": True}
        return state

    def get(self, card_id):
        return None

    def active(self):
        return [SimpleNamespace(card_id=cid, running=True) for cid in self._callbacks]

    def finish(self, card_id, blocked_reason_code=None, **state):
        callback = self._callbacks.pop(card_id)
        callback(SimpleNamespace(blocked_reason_code=blocked_reason_code, **state))


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "board.db")
    yield s
    s.close()


@pytest.fixture
def board(store):
    board_id = store.create_board("b")["id"]
    store.set_board_merge_mode(board_id, "free")
    repo_id = store.create_repo(board_id, "r", "/tmp/r", "main")["id"]
    return board_id, repo_id


@pytest.fixture
def fallback_model(store):
    """an openai fallback for the worker, with a usable openai credential behind it"""
    model = load_catalog()["openai"]["models"][0]["id"]
    profiles.add_profile("work", "key", lab="openai", kind="api_key")
    store.set_setting("worker_cross_lab_fallback", [f"openai/{model}"])
    return model


def _limit_run(store, card_id, runs, resets_at=None):
    """what a real run leaves behind when it hits the limit: its own attributed stream event,
    the card blocked by the lifecycle, then on_finish"""
    store.append_event(
        card_id,
        "rate_limit_event",
        {
            "lab": "anthropic",
            "profile": "default",
            "model": "opus",
            "role": "worker",
            "rate_limit_info": {
                "status": "rejected",
                "resetsAt": resets_at or time.time() + 3600,
            },
        },
    )
    store.update_card(card_id, status="doing", blocked_reason_code="USAGE_LIMIT", review_flag=True)
    runs.finish(card_id, blocked_reason_code="USAGE_LIMIT")


# -- the setting --------------------------------------------------------------------------


def test_the_route_validates_like_findings_route(store):
    assert store.get_settings()["usage_limit_route"] is None
    assert store.set_setting("usage_limit_route", "attention")["usage_limit_route"] == "attention"
    assert store.set_setting("usage_limit_route", "fallback")["usage_limit_route"] == "fallback"
    with pytest.raises(ValueError, match="usage_limit_route"):
        store.set_setting("usage_limit_route", "ask")
    assert store.set_setting("usage_limit_route", None)["usage_limit_route"] is None


# -- fallback mode: today's behaviour -------------------------------------------------------


def test_fallback_mode_switches_model_and_requeues(store, board, fallback_model):
    board_id, repo_id = board
    card = store.create_card(board_id, repo_id, "a")
    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()

    _limit_run(store, card["id"], runs)

    kinds = [e["kind"] for e in store.list_events(card["id"])]
    assert "lab_fallback" in kinds
    assert runs.started == [card["id"], card["id"]], "requeued at the front and started again"
    assert run_ref(store, "worker", store.get_card(card["id"])) == ("openai", fallback_model)


# -- attention mode: no model switch, the inbox asks ---------------------------------------


def test_attention_mode_blocks_and_asks_in_the_inbox(store, board, fallback_model):
    board_id, repo_id = board
    store.set_setting("usage_limit_route", "attention")
    card = store.create_card(board_id, repo_id, "a")
    runs = FakeRuns()
    registry = SchedulerRegistry(store.path, runs)
    registry.get(board_id).start_all()

    _limit_run(store, card["id"], runs)

    assert "lab_fallback" not in [e["kind"] for e in store.list_events(card["id"])]
    assert runs.started == [card["id"]], "not restarted on another model"
    assert "anthropic" in registry.paused_labs()
    # the reset retry stands - it is still scheduled, on its own model
    assert registry.retry_at(store, store.get_card(card["id"])) is not None

    (row,) = attention_rows(store, registry)
    assert row["card_id"] == card["id"]
    assert row["reason"] == "USAGE_LIMIT"
    assert row["fallback"] == f"openai/{fallback_model}"
    assert row["action"].startswith(f"opus limit - retry on openai/{fallback_model}? or waits to")
    assert row["action"].endswith("UTC")
    # the board draws it in attention too, still saying when the reset retry runs
    (shown,) = with_actions(store, [store.get_card(card["id"])], registry)
    assert shown["handled_by_board"] is False
    assert shown["next"].startswith("retry at")


def test_attention_mode_with_no_usable_fallback_says_so(store, board):
    board_id, repo_id = board
    store.set_setting("usage_limit_route", "attention")
    card = store.create_card(board_id, repo_id, "a")
    runs = FakeRuns()
    registry = SchedulerRegistry(store.path, runs)
    registry.get(board_id).start_all()

    _limit_run(store, card["id"], runs)

    (row,) = attention_rows(store, registry)
    assert row["fallback"] is None
    assert "no usable fallback" in row["action"]


def test_attention_mode_reruns_on_its_own_model_at_the_reset(store, board, fallback_model):
    board_id, repo_id = board
    store.set_setting("usage_limit_route", "attention")
    card = store.create_card(board_id, repo_id, "a")
    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    _limit_run(store, card["id"], runs)

    scheduler.process_due(time.time() + 7200)

    assert runs.started == [card["id"], card["id"]]
    assert run_ref(store, "worker", store.get_card(card["id"]))[0] == "anthropic"


def test_orchestrator_loop_in_attention_mode_does_not_switch_lab(tmp_path, monkeypatch):
    from smortboard.exec.runner import RunResult
    from smortboard.orchestrator import _real_runner

    profiles.add_profile("openai-work", "test-key", lab="openai", kind="api_key")
    monkeypatch.setattr("smortboard.orchestrator.docker_available", lambda: True)
    monkeypatch.setattr("smortboard.orchestrator.read_card_token", lambda path=None: "claude-test")
    labs = []

    def run_process(*args, **kwargs):
        labs.append(kwargs["lab"])
        return RunResult(
            subtype="error",
            is_error=True,
            blocked_reason_code="USAGE_LIMIT",
            session_id="s",
            total_cost_usd=0.1,
            num_turns=1,
            result_text=None,
        )

    monkeypatch.setattr("smortboard.orchestrator.run_process", run_process)
    with Store(tmp_path / "b.db") as store:
        board_id = store.create_board("b")["id"]
        store.set_setting("orchestrator_cross_lab_fallback", ["openai/gpt-5.6-sol"])
        store.set_setting("usage_limit_route", "attention")
        run = _real_runner(store, board_id, None, "rules", [], role="orchestrator")
        with pytest.raises(RuntimeError, match="USAGE_LIMIT"):
            run("brief", "sonnet", 1)
    assert labs == ["anthropic"]


# -- handled_by_board only when a retry is really scheduled --------------------------------


def test_a_usage_limit_card_with_nothing_scheduled_is_not_handled(store, board):
    board_id, repo_id = board
    card = store.create_card(board_id, repo_id, "a")
    store.update_card(
        card["id"], status="doing", blocked_reason_code="USAGE_LIMIT", review_flag=True
    )
    registry = SchedulerRegistry(store.path, FakeRuns())
    card = store.get_card(card["id"])

    assert handled_by_board(store, card, registry) is None
    assert handled_by_board(store, card) is None
    (shown,) = with_actions(store, [card], registry)
    assert shown["handled_by_board"] is False
    assert [row["card_id"] for row in attention_rows(store, registry)] == [card["id"]]


def test_a_queued_usage_limit_card_is_handled_in_fallback_mode(store, board):
    board_id, repo_id = board
    card = store.create_card(board_id, repo_id, "a")
    runs = FakeRuns()
    registry = SchedulerRegistry(store.path, runs)
    registry.get(board_id).start_all()
    _limit_run(store, card["id"], runs)

    note = handled_by_board(store, store.get_card(card["id"]), registry)
    assert note is not None and note.startswith("retry at")
    assert attention_rows(store, registry) == []


# -- every run start gets on_finish ----------------------------------------------------------


def _call(url, method="GET"):
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else None)


def _wait(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@contextlib.contextmanager
def _served(tmp_path, setup):
    """a real server whose store is built and seeded in its own thread - a sqlite connection
    belongs to the thread that opened it"""
    ready = threading.Event()
    holder = {}

    def _serve():
        store = Store(tmp_path / "board.db")
        holder["seed"] = setup(store)
        srv = build_server(store, port=0, host="127.0.0.1")
        holder["server"] = srv
        ready.set()
        srv.serve_forever()
        store.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    ready.wait()
    server = holder["server"]
    try:
        yield holder["seed"], f"http://127.0.0.1:{server.server_address[1]}", server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_a_manual_run_that_hits_the_limit_gets_a_retry_scheduled(tmp_path, monkeypatch):
    def _limited(store, card_id, **kwargs):
        store.update_card(card_id, status="doing", blocked_reason_code="USAGE_LIMIT")
        return LifecycleResult(card_id=card_id, phase="blocked", blocked_reason_code="USAGE_LIMIT")

    monkeypatch.setattr(runs_module, "run_card_lifecycle", _limited)

    def setup(store):
        board_id = store.create_board("b")["id"]
        repo_id = store.create_repo(board_id, "r", "/tmp/r", "main")["id"]
        return store.create_card(board_id, repo_id, "a", leases=["src/**"])["id"]

    with _served(tmp_path, setup) as (card_id, base, server):
        assert _call(f"{base}/api/cards/{card_id}/run", method="POST")[0] == 202
        with Store(tmp_path / "board.db") as reader:
            assert _wait(
                lambda: server.scheduler.retry_at(reader, reader.get_card(card_id)) is not None
            )
            kinds = [e["kind"] for e in reader.list_events(card_id)]
            assert "profile_limited" in kinds
            assert handled_by_board(reader, reader.get_card(card_id), server.scheduler)


def test_fallback_run_starts_one_run_on_the_next_ref_and_consumes_it(tmp_path, monkeypatch):
    model = load_catalog()["openai"]["models"][0]["id"]
    profiles.add_profile("work", "key", lab="openai", kind="api_key")
    refs = []

    def _consuming(store, card_id, **kwargs):
        # the lifecycle consumes the one-shot fallback when the worker starts
        refs.append(run_ref(store, "worker", store.get_card(card_id), consume=True))
        return LifecycleResult(card_id=card_id, phase="opened")

    monkeypatch.setattr(runs_module, "run_card_lifecycle", _consuming)

    def setup(store):
        board_id = store.create_board("b")["id"]
        repo_id = store.create_repo(board_id, "r", "/tmp/r", "main")["id"]
        store.set_setting("worker_cross_lab_fallback", [f"openai/{model}"])
        store.set_setting("usage_limit_route", "attention")
        card = store.create_card(board_id, repo_id, "a", leases=["src/**"])
        store.update_card(
            card["id"], status="doing", blocked_reason_code="USAGE_LIMIT", review_flag=True
        )
        return card["id"]

    with _served(tmp_path, setup) as (card_id, base, server):
        status, body = _call(f"{base}/api/cards/{card_id}/fallback-run", method="POST")
        assert status == 202, body
        assert _wait(lambda: _call(f"{base}/api/cards/{card_id}/run")[1]["running"] is False)
        assert refs == [("openai", model)], "exactly one run, on the fallback"
        with Store(tmp_path / "board.db") as reader:
            card = reader.get_card(card_id)
            assert card["blocked_reason_code"] is None
            assert run_ref(reader, "worker", card) == ("anthropic", "sonnet"), "consumed"
            kinds = [e["kind"] for e in reader.list_events(card_id)]
            assert kinds.count("lab_fallback") == 1 and "fallback_consumed" in kinds
        # not usage limited any more - a second press is refused, nothing starts
        status, body = _call(f"{base}/api/cards/{card_id}/fallback-run", method="POST")
        assert status == 409
        assert refs == [("openai", model)]


def test_fallback_run_with_no_usable_fallback_is_a_409(tmp_path):
    def setup(store):
        board_id = store.create_board("b")["id"]
        repo_id = store.create_repo(board_id, "r", "/tmp/r", "main")["id"]
        card = store.create_card(board_id, repo_id, "a", leases=["src/**"])
        store.update_card(
            card["id"], status="doing", blocked_reason_code="USAGE_LIMIT", review_flag=True
        )
        return card["id"]

    with _served(tmp_path, setup) as (card_id, base, server):
        status, body = _call(f"{base}/api/cards/{card_id}/fallback-run", method="POST")
        assert status == 409
        assert "no usable fallback" in body["error"]
        assert server.runs.get(card_id) is None


# -- startup requeue --------------------------------------------------------------------------


def _relabeled_card(store, board, resets_at):
    """shaped like wowtomate card f0d85b17: a limited run on 09-14, relabeled CRASH->USAGE_LIMIT
    at a restart, never requeued - no profile_limited, the reset only in its own stream"""
    board_id, repo_id = board
    card = store.create_card(board_id, repo_id, "relabeled", leases=["src/**"])
    card_id = card["id"]
    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(
        card_id,
        "rate_limit_event",
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "rejected",
                "resetsAt": resets_at,
                "rateLimitType": "five_hour",
            },
        },
    )
    store.append_event(
        card_id,
        "result",
        {"type": "result", "is_error": True, "api_error_status": 429, "result": "limit"},
    )
    store.append_event(card_id, "run_ended", {"phase": "blocked"})
    store.append_event(card_id, "relabeled", {"from": "CRASH", "to": "USAGE_LIMIT"})
    store.update_card(card_id, status="doing", blocked_reason_code="USAGE_LIMIT", review_flag=True)
    return card_id


def test_a_relabeled_card_whose_reset_passed_is_requeued_on_startup(store, board):
    lapsed = _relabeled_card(store, board, resets_at=time.time() - 3600)
    pending = _relabeled_card(store, board, resets_at=time.time() + 3600)
    runs = FakeRuns()
    registry = SchedulerRegistry(store.path, runs)

    assert registry.requeue_lapsed() == [lapsed]
    assert runs.started == [lapsed]
    assert pending not in runs.started


def test_an_api_unreachable_card_owed_a_retry_is_requeued_on_startup(store, board):
    board_id, repo_id = board
    owed = store.create_card(board_id, repo_id, "owed")["id"]
    store.update_card(owed, status="doing", blocked_reason_code="API_UNREACHABLE", review_flag=True)
    store.append_event(owed, "api_unreachable_retry", {"attempt": 1, "retry_at": time.time() - 1})
    spent = store.create_card(board_id, repo_id, "spent")["id"]
    store.update_card(
        spent, status="doing", blocked_reason_code="API_UNREACHABLE", review_flag=True
    )
    for n in range(scheduler_module.API_UNREACHABLE_MAX_RETRIES):
        store.append_event(spent, "api_unreachable_retry", {"attempt": n + 1, "retry_at": 1.0})

    assert SchedulerRegistry(store.path, FakeRuns()).requeue_lapsed() == [owed]


def test_start_background_requeues_and_server_close_stops_the_ticker(tmp_path, monkeypatch):
    started = []

    def _quiet(store, card_id, **kwargs):
        started.append(card_id)
        return LifecycleResult(card_id=card_id, phase="opened")

    monkeypatch.setattr(runs_module, "run_card_lifecycle", _quiet)
    with Store(tmp_path / "board.db") as store:
        board_id = store.create_board("b")["id"]
        repo_id = store.create_repo(board_id, "r", "/tmp/r", "main")["id"]
        card_id = _relabeled_card(store, (board_id, repo_id), resets_at=time.time() - 60)
        server = build_server(store, port=0, host="127.0.0.1")
        try:
            assert not server.ticker.running, "build_server alone starts nothing"
            assert start_background(server) == [card_id]
            assert server.ticker.running
            assert _wait(lambda: started == [card_id])
        finally:
            server.server_close()
        assert not server.ticker.running


# -- the ticker -----------------------------------------------------------------------------------


def test_the_ticker_fires_a_due_timer_without_schedule_view(store, board, monkeypatch):
    board_id, repo_id = board
    card = store.create_card(board_id, repo_id, "a")
    runs = FakeRuns()
    registry = SchedulerRegistry(store.path, runs)
    registry.get(board_id).start_all()
    runs.finish(card["id"], blocked_reason_code="API_UNREACHABLE")  # a 2 minute timer
    runs.started.clear()
    sweeps = []
    monkeypatch.setattr(registry, "sweep_all", lambda: sweeps.append(1))
    now = [time.time()]
    ticker = SchedulerTicker(registry, sweep_interval=60, clock=lambda: now[0])

    ticker.run_once()
    assert runs.started == [], "not due yet"
    now[0] += 121
    ticker.run_once()
    assert runs.started == [card["id"]]
    assert len(sweeps) == 2, "a sweep at the first pass and again once its interval passed"


def test_the_ticker_thread_starts_and_stops_cleanly(store):
    passes = []
    registry = SchedulerRegistry(store.path, FakeRuns())
    registry.process_due = lambda now=None: passes.append(now)
    registry.sweep_all = lambda: None
    ticker = SchedulerTicker(registry, interval=0.01)

    ticker.start()
    assert _wait(lambda: len(passes) >= 2)
    ticker.stop()
    assert not ticker.running
    assert not any(t.name == "scheduler-ticker" for t in threading.enumerate())


def test_a_tick_on_the_request_thread_never_sweeps(store, board, monkeypatch):
    board_id, repo_id = board
    store.create_card(board_id, repo_id, "a")
    monkeypatch.setattr(
        scheduler_module,
        "_sweep_checking_prs",
        lambda *a, **k: pytest.fail("a tick must not run the git-fetching sweep"),
    )
    scheduler = BoardScheduler(board_id, store.path, FakeRuns())
    scheduler.start_all()
    scheduler.schedule_view()


# -- the reset lookup ------------------------------------------------------------------------------


def test_latest_reset_reads_only_recent_rate_limit_rows(store, board, monkeypatch):
    board_id, repo_id = board
    card = store.create_card(board_id, repo_id, "a")
    store.append_event(
        card["id"], "rate_limit_event", {"rate_limit_info": {"resetsAt": 1800000000}}
    )
    monkeypatch.setattr(
        store,
        "list_events_by_kind",
        lambda kinds: pytest.fail("the whole events table must not be read"),
    )
    assert _latest_reset(store, "anthropic", "default") == 1800000000


# -- a runtime that is not ready --------------------------------------------------------------------


def test_a_runtime_refusal_backs_off_then_gives_up_to_the_inbox(store, board):
    board_id, repo_id = board
    card_id = store.create_card(board_id, repo_id, "a")["id"]
    runs = FakeRuns()
    registry = SchedulerRegistry(store.path, runs)
    scheduler = registry.get(board_id)
    scheduler.start_all()
    refusal = f"{RUNTIME_NOT_READY}:\nDocker is not running."

    for attempt, minutes in enumerate(RUNTIME_BACKOFF_MINUTES, start=1):
        # the lifecycle's own refusal: flagged, no reason code, and a doing card is not queueable
        store.update_card(card_id, status="doing", review_flag=True)
        store.append_event(card_id, "run_refused", {"note": refusal})
        runs.started.clear()
        runs.finish(card_id, phase="refused", refusal=refusal)
        retry_at = scheduler.schedule_view()["card_retry_at"][card_id]
        assert retry_at == pytest.approx(time.time() + minutes * 60, abs=5)
        assert runs.started == []
        note = handled_by_board(store, store.get_card(card_id), registry)
        assert note is not None and note.startswith("retry at"), "out of the inbox meanwhile"
        assert attention_rows(store, registry) == []
        scheduler.process_due(retry_at + 1)
        assert runs.started == [card_id], f"attempt {attempt} ran again on its own"

    store.append_event(card_id, "run_refused", {"note": refusal})
    runs.finish(card_id, phase="refused", refusal=refusal)
    assert card_id not in scheduler.schedule_view()["card_retry_at"]
    retries = [e for e in store.list_events(card_id) if e["kind"] == "runtime_retry"]
    assert len(retries) == len(RUNTIME_BACKOFF_MINUTES)
    (row,) = attention_rows(store, registry)
    assert row["reason"] == "refused"


def test_the_open_card_and_the_board_agree_on_a_usage_limit(tmp_path):
    """the card route and the board list share one scheduler, so an open card never says it needs
    you while the board says it is retrying, or the other way round"""

    def setup(store):
        board_id = store.create_board("b")["id"]
        repo_id = store.create_repo(board_id, "r", str(tmp_path), "main", test_command="true")["id"]
        card = store.create_card(board_id, repo_id, "limited", leases=["a.py"])
        store.update_card(card["id"], status="doing", blocked_reason_code="USAGE_LIMIT")
        return board_id, card["id"]

    with _served(tmp_path, setup) as ((board_id, card_id), base, server):
        # a retry the board holds - the case where a route without the scheduler disagreed
        board_scheduler = server.scheduler.get(board_id)
        with board_scheduler._lock:
            board_scheduler._card_retry_at[card_id] = time.time() + 600
        _, listed = _call(f"{base}/api/boards/{board_id}/cards")
        _, opened = _call(f"{base}/api/cards/{card_id}")
    row = next(card for card in listed if card["id"] == card_id)
    assert row["handled_by_board"] is True
    assert opened["handled_by_board"] == row["handled_by_board"]
    assert opened["next_action"] == row["next_action"]
