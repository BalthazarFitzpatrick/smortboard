"""the parallel scheduler: dependency order, lease conflicts, the parallel cap, USAGE_LIMIT parking.

Nothing here spawns a thread. FakeRuns records what .start() was asked to do and lets the test
decide when a run "finishes" by calling .finish() - so the whole scheduler loop is driven one tick
at a time, deterministically, exactly as the module's own docstring promises.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from smortboard import scheduler as scheduler_module
from smortboard.review.merge_request import PullRequestState
from smortboard.scheduler import (
    DEFAULT_MAX_PARALLEL,
    BoardScheduler,
    SchedulerRegistry,
    globs_may_overlap,
)
from smortboard.server import runs as runs_module
from smortboard.server.app import build_server
from smortboard.store.api import Store


class FakeRuns:
    """stands in for server.runs.RunRegistry: .start() is synchronous and records the on_finish
    callback rather than firing it, so a test controls exactly when a card "completes"."""

    def __init__(self):
        self.started: list[str] = []
        self._callbacks: dict[str, object] = {}

    def start(self, card_id, runner=None, on_finish=None):
        self.started.append(card_id)
        if on_finish is not None:
            self._callbacks[card_id] = on_finish
        return SimpleNamespace(card_id=card_id, running=True)

    def finish(self, card_id, blocked_reason_code=None):
        callback = self._callbacks.pop(card_id, None)
        assert callback is not None, f"{card_id} was never started"
        callback(SimpleNamespace(blocked_reason_code=blocked_reason_code))


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


@pytest.fixture(autouse=True)
def _clear_pr_state_cache():
    # the cache is module-level (it must survive across scheduler ticks) - a stale answer from a
    # previous test's url would otherwise never expire within a single test run
    scheduler_module._pr_state_cache.clear()
    yield
    scheduler_module._pr_state_cache.clear()


def _fake_pr_view(monkeypatch, **states):
    """states maps a url to a PullRequestState - the merge_request module's real gh call is never
    reached, matching the suite-wide rule that gh is faked, never invoked for real"""

    def _pr_view(repo_path, url):
        return states[url]

    monkeypatch.setattr(scheduler_module, "pr_view", _pr_view)


def _merge(store, card_id, url):
    store.append_event(card_id, "merge_request", {"opened": True, "branch": "b", "url": url})


# -- lease overlap ------------------------------------------------------------------


def test_identical_globs_overlap():
    assert globs_may_overlap("src/api/*.py", "src/api/*.py")


def test_a_wildcard_segment_against_a_literal_may_overlap():
    assert globs_may_overlap("src/api/*.py", "src/api/routes.py")


def test_disjoint_literal_segments_never_overlap():
    assert not globs_may_overlap("src/api/*", "src/ui/*")


def test_a_double_star_overlaps_anything_below_it():
    assert globs_may_overlap("src/**", "src/ui/widgets/button.py")


def test_a_directory_prefix_overlaps_what_it_contains():
    # "src/api" runs out before "src/api/routes.py" does - it is a prefix, so conservatively True
    assert globs_may_overlap("src/api", "src/api/routes.py")


# -- dependencies --------------------------------------------------------------------


def test_a_card_waits_for_its_dependency_to_be_accepted(store, board_and_repo):
    board_id, repo_id = board_and_repo
    dep = store.create_card(board_id, repo_id, "base card")
    card = store.create_card(board_id, repo_id, "dependent card")
    store.add_dependency(card["id"], dep["id"])
    # dep is still `todo` - the scheduler starts it, but that is not `accepted` either

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()

    view = scheduler.schedule_view()
    assert card["id"] not in runs.started
    assert dep["id"] in runs.started
    assert card["id"] in view["waiting"]
    assert "dependency" in view["waiting"][card["id"]]


def test_an_accepted_dependency_with_no_pull_request_still_waits(store, board_and_repo):
    board_id, repo_id = board_and_repo
    dep = store.create_card(board_id, repo_id, "base card")
    card = store.create_card(board_id, repo_id, "dependent card")
    store.add_dependency(card["id"], dep["id"])
    store.update_card(dep["id"], status="accepted")  # accepted, but no merge_request event

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()

    assert card["id"] not in runs.started
    assert "no pull request" in scheduler.schedule_view()["waiting"][card["id"]]


def test_an_accepted_dependency_with_an_open_pull_request_still_waits(
    store, board_and_repo, monkeypatch
):
    board_id, repo_id = board_and_repo
    dep = store.create_card(board_id, repo_id, "base card")
    card = store.create_card(board_id, repo_id, "dependent card")
    store.add_dependency(card["id"], dep["id"])
    store.update_card(dep["id"], status="accepted")
    url = "https://github.com/o/r/pull/1"
    _merge(store, dep["id"], url)
    _fake_pr_view(monkeypatch, **{url: PullRequestState(merged=False, state="OPEN")})

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()

    assert card["id"] not in runs.started
    assert "still open" in scheduler.schedule_view()["waiting"][card["id"]]


def test_a_pull_request_closed_without_merging_still_waits(store, board_and_repo, monkeypatch):
    board_id, repo_id = board_and_repo
    dep = store.create_card(board_id, repo_id, "base card")
    card = store.create_card(board_id, repo_id, "dependent card")
    store.add_dependency(card["id"], dep["id"])
    store.update_card(dep["id"], status="accepted")
    url = "https://github.com/o/r/pull/2"
    _merge(store, dep["id"], url)
    _fake_pr_view(monkeypatch, **{url: PullRequestState(merged=False, state="CLOSED")})

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()

    assert card["id"] not in runs.started
    assert "closed without merging" in scheduler.schedule_view()["waiting"][card["id"]]


def test_github_unreachable_waits_rather_than_guesses(store, board_and_repo, monkeypatch):
    board_id, repo_id = board_and_repo
    dep = store.create_card(board_id, repo_id, "base card")
    card = store.create_card(board_id, repo_id, "dependent card")
    store.add_dependency(card["id"], dep["id"])
    store.update_card(dep["id"], status="accepted")
    url = "https://github.com/o/r/pull/3"
    _merge(store, dep["id"], url)
    _fake_pr_view(monkeypatch, **{url: PullRequestState(merged=False, error="gh is not installed")})

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()

    assert card["id"] not in runs.started
    assert "could not ask GitHub" in scheduler.schedule_view()["waiting"][card["id"]]


def test_a_dependent_starts_once_its_dependencys_pull_request_is_merged(
    store, board_and_repo, monkeypatch
):
    board_id, repo_id = board_and_repo
    dep = store.create_card(board_id, repo_id, "base card")
    card = store.create_card(board_id, repo_id, "dependent card")
    store.add_dependency(card["id"], dep["id"])
    store.update_card(dep["id"], status="accepted")
    url = "https://github.com/o/r/pull/4"
    _merge(store, dep["id"], url)
    _fake_pr_view(monkeypatch, **{url: PullRequestState(merged=True, state="MERGED")})

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()

    assert card["id"] in runs.started
    assert card["id"] not in scheduler.schedule_view()["waiting"]


def test_the_pull_request_answer_is_cached_for_a_minute(store, board_and_repo, monkeypatch):
    board_id, repo_id = board_and_repo
    dep = store.create_card(board_id, repo_id, "base card")
    card = store.create_card(board_id, repo_id, "dependent card")
    store.add_dependency(card["id"], dep["id"])
    store.update_card(dep["id"], status="accepted")
    url = "https://github.com/o/r/pull/5"
    _merge(store, dep["id"], url)
    calls: list[str] = []

    def _pr_view(repo_path, u):
        calls.append(u)
        return PullRequestState(merged=False, state="OPEN")

    monkeypatch.setattr(scheduler_module, "pr_view", _pr_view)

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    scheduler.schedule_view()
    scheduler._tick()
    assert calls == [url]  # a second tick within the ttl reused the cached answer


def test_a_deleted_dependency_blocks_nothing(store, board_and_repo):
    board_id, repo_id = board_and_repo
    dep = store.create_card(board_id, repo_id, "base card")
    card = store.create_card(board_id, repo_id, "dependent card")
    store.add_dependency(card["id"], dep["id"])
    store.delete_card(dep["id"])

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    assert card["id"] in runs.started


# -- leases -----------------------------------------------------------------------


def test_two_cards_with_overlapping_leases_never_run_together(store, board_and_repo):
    board_id, repo_id = board_and_repo
    store.set_setting("max_parallel", "5")  # rule out the parallel cap as the reason
    a = store.create_card(board_id, repo_id, "a", leases=["src/api/*.py"])
    b = store.create_card(board_id, repo_id, "b", leases=["src/api/routes.py"])

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()

    assert len(runs.started) == 1
    started, waiting = runs.started[0], scheduler.schedule_view()["waiting"]
    other = b["id"] if started == a["id"] else a["id"]
    assert other in waiting
    assert "lease" in waiting[other]


def test_non_overlapping_leases_run_together(store, board_and_repo):
    board_id, repo_id = board_and_repo
    store.set_setting("max_parallel", "5")
    a = store.create_card(board_id, repo_id, "a", leases=["src/api/*"])
    b = store.create_card(board_id, repo_id, "b", leases=["src/ui/*"])

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    assert set(runs.started) == {a["id"], b["id"]}


def test_a_card_with_no_lease_touches_nothing_so_it_never_conflicts(store, board_and_repo):
    """the lease hook (exec/leases.py) refuses every write when path_globs is empty - so an
    unleased card can write nowhere and can never step on another card's files. the lifecycle
    refuses it before its agent runs; the scheduler just must not hold the leased card up"""
    board_id, repo_id = board_and_repo
    store.set_setting("max_parallel", "5")
    a = store.create_card(board_id, repo_id, "a")  # no leases
    b = store.create_card(board_id, repo_id, "b", leases=["src/*"])

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    assert set(runs.started) == {a["id"], b["id"]}


def test_leases_in_different_repos_never_conflict(store, tmp_path):
    board = Store(tmp_path / "b2.db")
    board_id = board.create_board("b")["id"]
    repo1 = board.create_repo(board_id, "r1", "/tmp/r1", "main")["id"]
    repo2 = board.create_repo(board_id, "r2", "/tmp/r2", "main")["id"]
    board.set_setting("max_parallel", "5")
    a = board.create_card(board_id, repo1, "a", leases=["src/*"])
    b = board.create_card(board_id, repo2, "b", leases=["src/*"])

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, board.path, runs)
    scheduler.start_all()
    assert set(runs.started) == {a["id"], b["id"]}
    board.close()


# -- the parallel cap ---------------------------------------------------------------


def test_default_max_parallel_is_two(store, board_and_repo):
    board_id, repo_id = board_and_repo
    ids = [store.create_card(board_id, repo_id, f"c{i}")["id"] for i in range(3)]

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()

    assert len(runs.started) == DEFAULT_MAX_PARALLEL == 2
    view = scheduler.schedule_view()
    remaining = [c for c in ids if c not in runs.started]
    assert remaining == view["queued"]


def test_a_finished_card_frees_a_slot_for_the_next_queued_one(store, board_and_repo):
    board_id, repo_id = board_and_repo
    [store.create_card(board_id, repo_id, f"c{i}") for i in range(3)]

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    assert len(runs.started) == 2

    runs.finish(runs.started[0])
    assert len(runs.started) == 3  # the third card started once a slot freed
    assert scheduler.schedule_view()["queued"] == []
    assert scheduler.schedule_view()["running"] == sorted(set(runs.started) - {runs.started[0]})


def test_setting_max_parallel_raises_the_cap(store, board_and_repo):
    board_id, repo_id = board_and_repo
    store.set_setting("max_parallel", "3")
    [store.create_card(board_id, repo_id, f"c{i}") for i in range(3)]

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    assert len(runs.started) == 3


# -- USAGE_LIMIT parking -------------------------------------------------------------


def test_a_usage_limit_run_pauses_new_starts_without_failing_the_card(store, board_and_repo):
    board_id, repo_id = board_and_repo
    store.set_setting("max_parallel", "1")
    a = store.create_card(board_id, repo_id, "a")
    b = store.create_card(board_id, repo_id, "b")

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    assert runs.started == [a["id"]]

    runs.finish(a["id"], blocked_reason_code="USAGE_LIMIT")
    # b is still queued, not started, and the schedule is parked
    view = scheduler.schedule_view()
    assert b["id"] not in runs.started
    assert view["paused_until"] is not None
    assert b["id"] in view["queued"]


def test_the_reset_time_comes_from_the_latest_rate_limit_event(store, board_and_repo):
    board_id, repo_id = board_and_repo
    store.set_setting("max_parallel", "1")
    a = store.create_card(board_id, repo_id, "a")
    store.create_card(board_id, repo_id, "b")
    resets_at = time.time() + 3600
    store.append_event(
        a["id"],
        "rate_limit_event",
        {
            "rate_limit_info": {
                "status": "allowed",
                "rateLimitType": "five_hour",
                "resetsAt": resets_at,
            }
        },
    )

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    runs.finish(a["id"], blocked_reason_code="USAGE_LIMIT")
    assert scheduler.schedule_view()["paused_until"] == resets_at


def test_the_queue_resumes_once_the_pause_expires(store, board_and_repo, monkeypatch):
    board_id, repo_id = board_and_repo
    store.set_setting("max_parallel", "1")
    a = store.create_card(board_id, repo_id, "a")
    b = store.create_card(board_id, repo_id, "b")

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    runs.finish(a["id"], blocked_reason_code="USAGE_LIMIT")
    assert b["id"] not in runs.started

    scheduler._paused_until = time.time() - 1  # the window has rolled over
    view = scheduler.schedule_view()  # polling resumes the queue - no separate timer needed
    assert view["paused_until"] is None
    assert b["id"] in runs.started


# -- stop ---------------------------------------------------------------------------


def test_stop_clears_the_queue_but_not_what_is_already_running(store, board_and_repo):
    board_id, repo_id = board_and_repo
    store.set_setting("max_parallel", "1")
    [store.create_card(board_id, repo_id, f"c{i}") for i in range(3)]

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    assert len(runs.started) == 1

    view = scheduler.stop()
    assert view["queued"] == []
    assert view["running"] == runs.started  # what was already going keeps going

    runs.finish(runs.started[0])
    assert len(runs.started) == 1  # nothing new started - the queue was cleared


# -- the registry ---------------------------------------------------------------------


def test_the_registry_hands_back_the_same_scheduler_for_a_board(store, board_and_repo):
    board_id, _ = board_and_repo
    registry = SchedulerRegistry(store.path, FakeRuns())
    assert registry.get(board_id) is registry.get(board_id)


def test_two_boards_get_independent_schedulers(store, board_and_repo):
    board_id, repo_id = board_and_repo
    other_board = store.create_board("other")["id"]
    registry = SchedulerRegistry(store.path, FakeRuns())
    assert registry.get(board_id) is not registry.get(other_board)


# -- over http ------------------------------------------------------------------------


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runs_module,
        "run_card_lifecycle",
        lambda store, cid, on_phase=None, **k: SimpleNamespace(
            phase="opened", pr_url=None, blocked_reason_code=None, refusal=None
        ),
    )
    ready = threading.Event()
    holder = {}

    def _serve():
        store = Store(tmp_path / "board.db")
        board = store.create_board("b")
        repo = store.create_repo(board["id"], "r", "/tmp/r", "main")
        holder["board_id"] = board["id"]
        holder["card_id"] = store.create_card(board["id"], repo["id"], "a card")["id"]
        srv = build_server(store, port=0, host="127.0.0.1")
        holder["server"] = srv
        ready.set()
        srv.serve_forever()
        store.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    ready.wait()
    port = holder["server"].server_address[1]
    yield f"http://127.0.0.1:{port}", holder["board_id"], holder["card_id"]
    holder["server"].shutdown()
    thread.join()
    holder["server"].server_close()


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


def test_run_all_starts_the_boards_todo_cards_over_http(server):
    base, board_id, card_id = server
    status, body = _call(f"{base}/api/boards/{board_id}/run-all", method="POST")
    assert status == 202
    assert card_id in body["running"] or card_id in body["queued"]
    assert _wait(lambda: _call(f"{base}/api/boards/{board_id}/schedule")[1]["running"] == [])


def test_schedule_endpoint_reports_an_empty_board_cleanly(server):
    base, board_id, _ = server
    status, body = _call(f"{base}/api/boards/{board_id}/schedule")
    assert status == 200
    assert body == {"running": [], "queued": [], "waiting": {}, "paused_until": None}


def test_run_all_stop_clears_the_queue_over_http(server):
    base, board_id, _ = server
    _call(f"{base}/api/boards/{board_id}/run-all", method="POST")
    status, body = _call(f"{base}/api/boards/{board_id}/run-all/stop", method="POST")
    assert status == 200
    assert body["queued"] == []


def test_run_all_on_an_unknown_board_is_a_404(server):
    base, _, _ = server
    status, _ = _call(f"{base}/api/boards/nope/run-all", method="POST")
    assert status == 404
