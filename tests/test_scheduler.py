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
from datetime import UTC, datetime, timedelta
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
        state = SimpleNamespace(card_id=card_id, running=True)
        state.as_dict = lambda: {"card_id": card_id, "running": True}
        return state

    def get(self, card_id):
        # matches RunRegistry.get()'s shape for attention.answer_card - nothing is ever mid-run
        # from this fake's own perspective once .start() has returned
        return None

    def active(self):
        # matches RunRegistry.active()'s shape: every card started but not yet .finish()ed. one
        # FakeRuns shared by several BoardSchedulers (as the real RunRegistry is) is what lets a
        # test prove the global cap is counted across boards, not per board
        return [SimpleNamespace(card_id=cid, running=True) for cid in self._callbacks]

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
    store.set_board_merge_mode(board["id"], "free")
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


def test_a_board_limit_holds_a_board_back_while_another_board_still_starts(store, board_and_repo):
    """global 3, board a limit 1: a starts one at a time while board b, sharing the same global
    seat count through one RunRegistry, still gets its own card in"""
    board_a, repo_a = board_and_repo
    board_b = store.create_board("b2")
    repo_b = store.create_repo(board_b["id"], "r2", "/tmp/r2", "main")

    store.set_setting("max_parallel", "3")
    store.set_board_max_parallel(board_a, 1)
    [store.create_card(board_a, repo_a, f"a{i}") for i in range(2)]
    card_b = store.create_card(board_b["id"], repo_b["id"], "b0")

    runs = FakeRuns()  # one shared registry, exactly as the real server wires every board's own
    scheduler_a = BoardScheduler(board_a, store.path, runs)
    scheduler_b = BoardScheduler(board_b["id"], store.path, runs)

    scheduler_a.start_all()
    assert len(scheduler_a.schedule_view()["running"]) == 1, "board a's own cap holds it to one"
    assert len(scheduler_a.schedule_view()["queued"]) == 1

    scheduler_b.start_all()
    assert scheduler_b.schedule_view()["running"] == [card_b["id"]], (
        "board b is unaffected by a's cap"
    )


def test_a_global_cap_of_two_holds_a_third_card_back_on_any_board(store, board_and_repo):
    board_a, repo_a = board_and_repo
    board_b = store.create_board("b2")
    repo_b = store.create_repo(board_b["id"], "r2", "/tmp/r2", "main")

    store.set_setting("max_parallel", "2")
    store.create_card(board_a, repo_a, "a0")
    store.create_card(board_a, repo_a, "a1")
    card_b = store.create_card(board_b["id"], repo_b["id"], "b0")

    runs = FakeRuns()
    scheduler_a = BoardScheduler(board_a, store.path, runs)
    scheduler_b = BoardScheduler(board_b["id"], store.path, runs)

    scheduler_a.start_all()
    assert len(runs.started) == 2  # the global cap, not board a's own (unset) limit

    scheduler_b.start_all()
    assert card_b["id"] not in runs.started, "the third card waits on the shared global cap"
    assert scheduler_b.schedule_view()["queued"] == [card_b["id"]]

    runs.finish(runs.started[0])
    scheduler_b._tick()
    assert card_b["id"] in runs.started, "a freed global seat lets board b's card start"


def test_an_unset_board_limit_behaves_exactly_like_today(store, board_and_repo):
    board_id, repo_id = board_and_repo
    [store.create_card(board_id, repo_id, f"c{i}") for i in range(3)]

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    assert len(runs.started) == DEFAULT_MAX_PARALLEL == 2


def test_a_lowered_board_limit_never_stops_an_already_running_card(store, board_and_repo):
    board_id, repo_id = board_and_repo
    ids = [store.create_card(board_id, repo_id, f"c{i}")["id"] for i in range(2)]

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    assert len(runs.started) == 2

    store.set_board_max_parallel(board_id, 1)  # lowered while both cards are running
    scheduler._tick()
    assert set(runs.started) == set(ids), "a lowered cap only holds back the NEXT card, not these"


# -- daily budget: a board-level spend ceiling ---------------------------------------


def test_a_budget_already_spent_today_blocks_new_starts(store, board_and_repo):
    board_id, repo_id = board_and_repo
    store.set_setting("max_parallel", "5")
    store.set_board_daily_budget(board_id, 1.0)
    a = store.create_card(board_id, repo_id, "a")
    b = store.create_card(board_id, repo_id, "b")
    store.append_event(a["id"], "result", {"total_cost_usd": 0.7})
    store.append_event(a["id"], "result", {"total_cost_usd": 0.5})  # today's spend: 1.2, over 1.0

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()

    assert runs.started == [], "the board is at its daily budget, so nothing new starts"
    assert b["id"] not in runs.started
    assert scheduler.schedule_view()["budget_paused"] is True


def test_an_unset_budget_never_blocks_a_start(store, board_and_repo):
    board_id, repo_id = board_and_repo
    a = store.create_card(board_id, repo_id, "a")
    store.append_event(a["id"], "result", {"total_cost_usd": 500.0})  # huge spend, no cap set

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()

    assert runs.started == [a["id"]]
    assert scheduler.schedule_view()["budget_paused"] is False


def test_yesterdays_spend_does_not_count_against_todays_budget(store, board_and_repo):
    board_id, repo_id = board_and_repo
    store.set_board_daily_budget(board_id, 1.0)
    a = store.create_card(board_id, repo_id, "a")
    yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    store.append_event(a["id"], "result", {"total_cost_usd": 5.0})
    with store._conn:
        store._conn.execute(
            "UPDATE events SET created_at = ? WHERE card_id = ? AND kind = 'result'",
            (yesterday, a["id"]),
        )

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()

    assert runs.started == [a["id"]], "yesterday's spend does not count against today's budget"
    assert scheduler.schedule_view()["budget_paused"] is False


def test_a_running_card_finishes_once_the_boards_budget_is_hit_mid_run(store, board_and_repo):
    board_id, repo_id = board_and_repo
    store.set_setting("max_parallel", "5")
    store.set_board_daily_budget(board_id, 1.0)
    a = store.create_card(board_id, repo_id, "a")
    b = store.create_card(board_id, repo_id, "b")

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    assert set(runs.started) == {a["id"], b["id"]}, "budget not spent yet - both start"

    store.append_event(a["id"], "result", {"total_cost_usd": 1.5})  # a's own run pushes past cap
    runs.finish(a["id"])  # a finishes; b is left running untouched, no new card starts

    assert runs.started == [a["id"], b["id"]]
    assert scheduler.schedule_view()["budget_paused"] is True


def test_a_card_past_its_own_total_cap_never_starts_even_with_budget_left(store, board_and_repo):
    board_id, repo_id = board_and_repo
    store.set_setting("card_total_budget_usd", "1.0")
    a = store.create_card(board_id, repo_id, "capped")
    b = store.create_card(board_id, repo_id, "fine")
    store.append_event(a["id"], "result", {"total_cost_usd": 1.5})

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()

    assert a["id"] not in runs.started
    assert b["id"] in runs.started
    assert scheduler.schedule_view()["budget_paused"] is False  # board cap is untouched


# -- blocked cards rejoin the queue --------------------------------------------------


def test_a_blocked_card_is_requeued_by_start_all(store, board_and_repo):
    # a run leaves a blocked card in "doing" with a reason code, not in some sixth "blocked"
    # status - schema.py's STATUSES has no such value
    board_id, repo_id = board_and_repo
    card = store.create_card(board_id, repo_id, "blocked card")
    store.update_card(card["id"], status="doing", blocked_reason_code="TESTS_FAILED")

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()

    assert card["id"] in runs.started


def test_a_blocked_card_with_any_reason_code_is_requeued(store, board_and_repo):
    board_id, repo_id = board_and_repo
    codes = ["CRASH", "USAGE_LIMIT", "LEASE_CONFLICT", "AGENT_QUESTION", "REVIEW_REJECTED"]
    ids = []
    for i, code in enumerate(codes):
        card = store.create_card(board_id, repo_id, f"c{i}")
        store.update_card(card["id"], status="doing", blocked_reason_code=code)
        ids.append(card["id"])
    store.set_setting("max_parallel", str(len(codes)))

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()

    assert set(runs.started) == set(ids)


def test_a_blocked_cards_lease_still_conflicts_with_a_running_card(store, board_and_repo):
    # requeuing does not bypass the lease rule - a blocked card sits waiting exactly like a
    # fresh todo one would
    board_id, repo_id = board_and_repo
    store.set_setting("max_parallel", "5")
    a = store.create_card(board_id, repo_id, "a", leases=["src/api/*.py"])
    blocked = store.create_card(board_id, repo_id, "b", leases=["src/api/routes.py"])
    store.update_card(blocked["id"], status="doing", blocked_reason_code="TESTS_FAILED")

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()

    assert a["id"] in runs.started
    assert blocked["id"] not in runs.started
    assert "lease" in scheduler.schedule_view()["waiting"][blocked["id"]]


def test_accepted_and_rejected_cards_are_never_requeued(store, board_and_repo):
    # the store lets a reason code ride alongside any status (store/api.py's
    # _check_blocked_invariant) - accepted/rejected must stay excluded regardless
    board_id, repo_id = board_and_repo
    accepted = store.create_card(board_id, repo_id, "accepted")
    store.update_card(accepted["id"], status="accepted", blocked_reason_code=None)
    rejected = store.create_card(board_id, repo_id, "rejected")
    store.update_card(rejected["id"], status="rejected", blocked_reason_code=None)

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()

    assert runs.started == []


def test_a_todo_card_is_still_selected_alongside_a_blocked_one(store, board_and_repo):
    # existing to-do selection is unchanged by the blocked-card addition
    board_id, repo_id = board_and_repo
    store.set_setting("max_parallel", "5")
    todo = store.create_card(board_id, repo_id, "todo")
    blocked = store.create_card(board_id, repo_id, "blocked")
    store.update_card(blocked["id"], status="doing", blocked_reason_code="CRASH")

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()

    assert set(runs.started) == {todo["id"], blocked["id"]}


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


def test_a_usage_limit_card_itself_rejoins_the_queue_once_the_pause_expires(store, board_and_repo):
    """the gap the queue-only fix left: the card that hit USAGE_LIMIT must rerun once its own
    reset passes, not only unblock cards behind it."""
    board_id, repo_id = board_and_repo
    store.set_setting("max_parallel", "1")
    a = store.create_card(board_id, repo_id, "a")

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    runs.finish(a["id"], blocked_reason_code="USAGE_LIMIT")
    runs.started.clear()

    scheduler._paused_until = time.time() - 1
    scheduler.schedule_view()
    assert a["id"] in runs.started


# -- API_UNREACHABLE backoff ---------------------------------------------------------


def test_api_unreachable_retries_with_backoff_then_stops_at_three(store, board_and_repo):
    board_id, repo_id = board_and_repo
    a = store.create_card(board_id, repo_id, "a")

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    assert runs.started == [a["id"]]

    for expected_minutes in scheduler_module.API_UNREACHABLE_BACKOFF_MINUTES:
        runs.started.clear()
        runs.finish(a["id"], blocked_reason_code="API_UNREACHABLE")
        view = scheduler.schedule_view()
        retry_at = view["card_retry_at"][a["id"]]
        assert retry_at == pytest.approx(time.time() + expected_minutes * 60, abs=5)
        # fast-forward: the retry is due, so polling starts it again
        scheduler._card_retry_at[a["id"]] = time.time() - 1
        scheduler.schedule_view()
        assert runs.started == [a["id"]]

    # a fourth failure exhausts the automatic retries and stays blocked for the operator
    runs.started.clear()
    runs.finish(a["id"], blocked_reason_code="API_UNREACHABLE")
    view = scheduler.schedule_view()
    assert a["id"] not in view["card_retry_at"]
    assert runs.started == []

    retries = [e for e in store.list_events(a["id"]) if e["kind"] == "api_unreachable_retry"]
    assert len(retries) == len(scheduler_module.API_UNREACHABLE_BACKOFF_MINUTES)


def test_api_unreachable_retry_posts_a_board_comment_with_the_next_retry_time(
    store, board_and_repo
):
    board_id, repo_id = board_and_repo
    a = store.create_card(board_id, repo_id, "a")
    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    runs.finish(a["id"], blocked_reason_code="API_UNREACHABLE")
    comments = store.get_card(a["id"])["comments"]
    assert any("retrying automatically" in c["body"] for c in comments)


# -- stale CRASH relabel --------------------------------------------------------------


def test_relabel_stale_crashes_fixes_a_mislabeled_session_limit(store, board_and_repo):
    board_id, repo_id = board_and_repo
    a = store.create_card(board_id, repo_id, "a")
    store.update_card(a["id"], blocked_reason_code="CRASH", review_flag=True)
    store.append_event(
        a["id"],
        "result",
        {
            "type": "result",
            "is_error": True,
            "result": "You've hit your session limit · resets 3:40pm (UTC)",
        },
    )

    relabeled = scheduler_module.relabel_stale_crashes(store)
    assert relabeled == [{"card_id": a["id"], "from": "CRASH", "to": "USAGE_LIMIT"}]
    assert store.get_card(a["id"])["blocked_reason_code"] == "USAGE_LIMIT"
    kinds = [e["kind"] for e in store.list_events(a["id"])]
    assert "relabeled" in kinds


def test_relabel_stale_crashes_fixes_a_mislabeled_api_unreachable(store, board_and_repo):
    board_id, repo_id = board_and_repo
    a = store.create_card(board_id, repo_id, "a")
    store.update_card(a["id"], blocked_reason_code="CRASH", review_flag=True)
    store.append_event(
        a["id"],
        "result",
        {"type": "result", "is_error": True, "result": "Unable to connect to API"},
    )

    relabeled = scheduler_module.relabel_stale_crashes(store)
    assert relabeled == [{"card_id": a["id"], "from": "CRASH", "to": "API_UNREACHABLE"}]
    assert store.get_card(a["id"])["blocked_reason_code"] == "API_UNREACHABLE"


def test_relabel_stale_crashes_leaves_a_genuine_crash_alone(store, board_and_repo):
    board_id, repo_id = board_and_repo
    a = store.create_card(board_id, repo_id, "a")
    store.update_card(a["id"], blocked_reason_code="CRASH", review_flag=True)
    store.append_event(
        a["id"], "result", {"type": "result", "is_error": True, "result": "TypeError: boom"}
    )

    assert scheduler_module.relabel_stale_crashes(store) == []
    assert store.get_card(a["id"])["blocked_reason_code"] == "CRASH"


# -- MERGE_CONFLICT auto-resume -------------------------------------------------------


def test_a_merge_conflict_is_resumed_automatically_once(store, board_and_repo):
    board_id, repo_id = board_and_repo
    a = store.create_card(board_id, repo_id, "a")
    store.append_event(
        a["id"], "merge_conflict", {"base_ref": "origin/development", "files": ["x.py"]}
    )

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    # mirrors what lifecycle._block already wrote before this card's run finished
    store.update_card(a["id"], blocked_reason_code="MERGE_CONFLICT", review_flag=True)
    runs.finish(a["id"], blocked_reason_code="MERGE_CONFLICT")

    # answer_card resumed it through runs.start - the same path an inbox answer uses
    assert a["id"] in runs.started
    assert store.get_card(a["id"])["blocked_reason_code"] is None
    kinds = [e["kind"] for e in store.list_events(a["id"])]
    assert "merge_conflict_auto_resume" in kinds
    comments = store.get_card(a["id"])["comments"]
    assert any("Resuming automatically" in c["body"] for c in comments)


def test_a_second_merge_conflict_on_the_same_card_is_left_for_the_operator(store, board_and_repo):
    board_id, repo_id = board_and_repo
    a = store.create_card(board_id, repo_id, "a")
    store.append_event(
        a["id"], "merge_conflict", {"base_ref": "origin/development", "files": ["x.py"]}
    )

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    runs.finish(a["id"], blocked_reason_code="MERGE_CONFLICT")
    runs.started.clear()

    # the resumed run conflicts again
    store.update_card(a["id"], blocked_reason_code="MERGE_CONFLICT", review_flag=True)
    scheduler._handle_merge_conflict(a["id"])

    assert a["id"] not in runs.started  # never retried twice for the same conflict
    retries = [e for e in store.list_events(a["id"]) if e["kind"] == "merge_conflict_auto_resume"]
    assert len(retries) == 1
    comments = store.get_card(a["id"])["comments"]
    assert any("left for the operator" in c["body"] for c in comments)


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
    # the fake run holds until the test lets it finish, so a response is never raced by a run that
    # already ended - an instant fake made run-all's reply sometimes list the card nowhere
    release = threading.Event()

    def _held_run(store, cid, on_phase=None, **k):
        release.wait(5)
        return SimpleNamespace(phase="opened", pr_url=None, blocked_reason_code=None, refusal=None)

    monkeypatch.setattr(runs_module, "run_card_lifecycle", _held_run)
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
    yield f"http://127.0.0.1:{port}", holder["board_id"], holder["card_id"], release
    release.set()
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
    base, board_id, card_id, release = server
    status, body = _call(f"{base}/api/boards/{board_id}/run-all", method="POST")
    assert status == 202
    assert card_id in body["running"] or card_id in body["queued"]
    release.set()
    assert _wait(lambda: _call(f"{base}/api/boards/{board_id}/schedule")[1]["running"] == [])


def test_schedule_endpoint_reports_an_empty_board_cleanly(server):
    base, board_id, _, _ = server
    status, body = _call(f"{base}/api/boards/{board_id}/schedule")
    assert status == 200
    assert body == {
        "running": [],
        "queued": [],
        "waiting": {},
        "paused_until": None,
        "paused_labs": {},
        "card_retry_at": {},
        "budget_paused": False,
    }


def test_run_all_stop_clears_the_queue_over_http(server):
    base, board_id, _, _ = server
    _call(f"{base}/api/boards/{board_id}/run-all", method="POST")
    status, body = _call(f"{base}/api/boards/{board_id}/run-all/stop", method="POST")
    assert status == 200
    assert body["queued"] == []


def test_run_all_on_an_unknown_board_is_a_404(server):
    base, _, _, _ = server
    status, _ = _call(f"{base}/api/boards/nope/run-all", method="POST")
    assert status == 404


def _patch_json(url, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="PATCH", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else None)


def test_patch_board_max_parallel_over_http(server):
    base, board_id, _, _ = server
    status, body = _patch_json(f"{base}/api/boards/{board_id}", {"max_parallel": 2})
    assert status == 200
    assert body["max_parallel"] == 2

    status, body = _patch_json(f"{base}/api/boards/{board_id}", {"max_parallel": 0})
    assert status == 400

    status, body = _patch_json(f"{base}/api/boards/{board_id}", {"max_parallel": None})
    assert status == 200
    assert body["max_parallel"] is None


# -- keeping a checking card's pr mergeable ------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_sweep_throttle():
    scheduler_module._last_sweep.clear()
    scheduler_module._last_base_sha.clear()
    yield
    scheduler_module._last_sweep.clear()
    scheduler_module._last_base_sha.clear()


def _checking_card(store, board_id, repo_path, tmp_path):
    """a card in checking, with an open pull request recorded and a worktree on disk - what the
    sweep needs to have anything to act on. its own repo row, so its path is real and per-test"""
    repo_path.mkdir(parents=True, exist_ok=True)
    repo = store.create_repo(board_id, "r", str(repo_path), "main")
    repo_id = repo["id"]
    card = store.create_card(board_id, repo_id, "waiting card", leases=["x"])
    store.update_card(card["id"], status="checking")
    _merge(store, card["id"], "https://x/pull/9")
    from smortboard.exec.worktrees import branch_name, worktree_path

    tree = worktree_path(repo_path, card["id"])
    tree.mkdir(parents=True)
    return card["id"], tree, branch_name(card["id"])


def _stub_git_plumbing(monkeypatch, *, has_remote=True, fetch_ok=True):
    monkeypatch.setattr(scheduler_module, "_read_base_sha", lambda *a: "base-sha")
    monkeypatch.setattr(scheduler_module, "has_remote", lambda *a, **k: has_remote)
    monkeypatch.setattr(scheduler_module, "fetch_base", lambda *a, **k: fetch_ok)


def test_a_clean_behind_branch_is_merged_and_pushed(store, board_and_repo, tmp_path, monkeypatch):
    from smortboard.review.mergeable import MergeSyncResult

    board_id, _ = board_and_repo
    card_id, tree, branch = _checking_card(store, board_id, tmp_path / "repo", tmp_path)
    _stub_git_plumbing(monkeypatch)
    monkeypatch.setattr(
        scheduler_module,
        "check_mergeable",
        lambda *a, **k: MergeSyncResult(clean=True, behind=True),
    )
    pushed = []
    monkeypatch.setattr(
        scheduler_module,
        "merge_branch",
        lambda *a, **k: MergeSyncResult(clean=True, behind=True, merged=True),
    )
    monkeypatch.setattr(
        scheduler_module, "push_branch", lambda repo_path, br, **k: pushed.append(br) or True
    )

    scheduler_module._sweep_checking_prs(store, board_id)

    assert pushed == [branch]
    card = store.get_card(card_id)
    assert card["blocked_reason_code"] is None


def test_a_conflicting_behind_branch_is_flagged_merge_conflict(
    store, board_and_repo, tmp_path, monkeypatch
):
    from smortboard.review.mergeable import MergeSyncResult

    board_id, _ = board_and_repo
    card_id, tree, branch = _checking_card(store, board_id, tmp_path / "repo", tmp_path)
    _stub_git_plumbing(monkeypatch)
    monkeypatch.setattr(
        scheduler_module,
        "check_mergeable",
        lambda *a, **k: MergeSyncResult(
            clean=False, behind=True, conflicting_files=["a.py", "b.py"]
        ),
    )
    monkeypatch.setattr(
        scheduler_module, "merge_branch", lambda *a, **k: pytest.fail("must not merge for real")
    )
    monkeypatch.setattr(
        scheduler_module, "push_branch", lambda *a, **k: pytest.fail("must not push a conflict")
    )

    scheduler_module._sweep_checking_prs(store, board_id)

    card = store.get_card(card_id)
    assert card["blocked_reason_code"] == "MERGE_CONFLICT"
    note = card["comments"][-1]["body"]
    assert "a.py" in note and "b.py" in note
    events = [e for e in store.list_events(card_id) if e["kind"] == "merge_conflict"]
    assert events and events[-1]["payload"]["files"] == ["a.py", "b.py"]


def test_a_not_behind_branch_is_left_alone(store, board_and_repo, tmp_path, monkeypatch):
    from smortboard.review.mergeable import MergeSyncResult

    board_id, _ = board_and_repo
    card_id, tree, branch = _checking_card(store, board_id, tmp_path / "repo", tmp_path)
    _stub_git_plumbing(monkeypatch)
    monkeypatch.setattr(
        scheduler_module,
        "check_mergeable",
        lambda *a, **k: MergeSyncResult(clean=True, behind=False),
    )
    monkeypatch.setattr(
        scheduler_module, "merge_branch", lambda *a, **k: pytest.fail("nothing to merge")
    )

    scheduler_module._sweep_checking_prs(store, board_id)

    card = store.get_card(card_id)
    assert card["blocked_reason_code"] is None


def test_an_unchanged_base_does_not_sweep_again(store, board_and_repo, tmp_path, monkeypatch):
    from smortboard.review.mergeable import MergeSyncResult

    board_id, _ = board_and_repo
    card_id, tree, branch = _checking_card(store, board_id, tmp_path / "repo", tmp_path)
    _stub_git_plumbing(monkeypatch)
    calls = []
    monkeypatch.setattr(
        scheduler_module,
        "check_mergeable",
        lambda *a, **k: calls.append(1) or MergeSyncResult(clean=True, behind=False),
    )

    scheduler_module._sweep_checking_prs(store, board_id)
    scheduler_module._sweep_checking_prs(store, board_id)

    assert calls == [1]  # the second fetch found the same base


def test_the_sweep_reads_a_non_main_default_branch(store, board_and_repo, tmp_path, monkeypatch):
    """a repo pointed at "development" is synced against origin/development, never origin/main"""
    from smortboard.review.mergeable import MergeSyncResult

    board_id, _ = board_and_repo
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo = store.create_repo(board_id, "r2", str(repo_path), "development")
    card = store.create_card(board_id, repo["id"], "waiting card", leases=["x"])
    store.update_card(card["id"], status="checking")
    _merge(store, card["id"], "https://x/pull/10")
    from smortboard.exec.worktrees import worktree_path

    worktree_path(repo_path, card["id"]).mkdir(parents=True)

    _stub_git_plumbing(monkeypatch)
    fetched = []
    monkeypatch.setattr(
        scheduler_module, "fetch_base", lambda path, base, **k: fetched.append(base) or True
    )
    checked = []
    monkeypatch.setattr(
        scheduler_module,
        "check_mergeable",
        lambda tree, branch, base_ref: (
            checked.append(base_ref) or MergeSyncResult(clean=True, behind=False)
        ),
    )

    scheduler_module._sweep_checking_prs(store, board_id)

    assert fetched == ["development"]
    assert checked == ["origin/development"]


def test_changed_base_sweeps_every_checking_card_in_one_repo(
    store, board_and_repo, tmp_path, monkeypatch
):
    from smortboard.review.mergeable import MergeSyncResult

    board_id, _ = board_and_repo
    repo_path = tmp_path / "repo"
    first, _, branch = _checking_card(store, board_id, repo_path, tmp_path)
    repo_id = store.get_card(first)["repo_id"]
    branches = [branch]
    for number in range(2):
        card = store.create_card(board_id, repo_id, f"waiting {number}")
        store.update_card(card["id"], status="checking")
        _merge(store, card["id"], f"https://x/pull/{number}")
        scheduler_module.worktree_path(repo_path, card["id"]).mkdir(parents=True)
        branches.append(scheduler_module.branch_name(card["id"]))
    _stub_git_plumbing(monkeypatch)
    fetched, checked, pushed = [], [], []
    sha = ["first"]
    monkeypatch.setattr(scheduler_module, "_read_base_sha", lambda *a: sha[0])
    monkeypatch.setattr(scheduler_module, "fetch_base", lambda *a: fetched.append(1) or True)
    monkeypatch.setattr(
        scheduler_module,
        "check_mergeable",
        lambda tree, branch, base: checked.append(branch) or MergeSyncResult(True, behind=True),
    )
    monkeypatch.setattr(
        scheduler_module, "merge_branch", lambda *a: MergeSyncResult(True, merged=True)
    )
    monkeypatch.setattr(
        scheduler_module, "push_branch", lambda tree, branch: pushed.append(branch) or True
    )
    scheduler_module._sweep_checking_prs(store, board_id)
    assert set(checked) == set(branches)
    assert set(pushed) == set(branches)
    assert len(fetched) == 1
    scheduler_module._sweep_checking_prs(store, board_id)
    assert len(checked) == 3
    assert len(fetched) == 2
    sha[0] = "changed"
    scheduler_module.sweep_checking_prs(store, board_id, repo_path=repo_path)
    assert len(checked) == 6
    assert len(pushed) == 6


def test_failed_fetch_does_not_consume_base_change(store, board_and_repo, tmp_path, monkeypatch):
    from smortboard.review.mergeable import MergeSyncResult

    board_id, _ = board_and_repo
    _checking_card(store, board_id, tmp_path / "repo", tmp_path)
    _stub_git_plumbing(monkeypatch, fetch_ok=False)
    checked = []
    monkeypatch.setattr(
        scheduler_module,
        "check_mergeable",
        lambda *a: checked.append(1) or MergeSyncResult(True),
    )
    scheduler_module._sweep_checking_prs(store, board_id)
    assert not scheduler_module._last_base_sha
    assert not scheduler_module._last_sweep
    monkeypatch.setattr(scheduler_module, "fetch_base", lambda *a: True)
    scheduler_module._sweep_checking_prs(store, board_id)
    assert checked == [1]
    assert list(scheduler_module._last_base_sha.values()) == ["base-sha"]


def test_stacked_card_waits_for_retarget_before_sweeping(
    store, board_and_repo, tmp_path, monkeypatch
):
    from smortboard.review.mergeable import MergeSyncResult

    board_id, _ = board_and_repo
    card_id, _, _ = _checking_card(store, board_id, tmp_path / "repo", tmp_path)
    parent = store.create_card(board_id, store.get_card(card_id)["repo_id"], "parent")
    store.update_card(parent["id"], status="checking")
    store.append_event(card_id, "stacked_on", {"parent_id": parent["id"], "branch": "card/parent"})
    _stub_git_plumbing(monkeypatch)
    checked = []
    monkeypatch.setattr(
        scheduler_module,
        "check_mergeable",
        lambda *a: checked.append(1) or MergeSyncResult(True),
    )
    scheduler_module._sweep_checking_prs(store, board_id)
    assert checked == []
    store.append_event(card_id, "stacked_retargeted", {"parent_id": "parent"})
    scheduler_module._sweep_checking_prs(store, board_id)
    assert checked == [1]


def test_slow_backstop_rechecks_unchanged_base(store, board_and_repo, tmp_path, monkeypatch):
    from smortboard.review.mergeable import MergeSyncResult

    board_id, _ = board_and_repo
    _checking_card(store, board_id, tmp_path / "repo", tmp_path)
    _stub_git_plumbing(monkeypatch)
    checked = []
    now = [1000.0]
    monkeypatch.setattr(scheduler_module.time, "time", lambda: now[0])
    monkeypatch.setattr(
        scheduler_module,
        "check_mergeable",
        lambda *a: checked.append(1) or MergeSyncResult(True),
    )
    scheduler_module._sweep_checking_prs(store, board_id)
    now[0] += scheduler_module._SWEEP_INTERVAL_SECONDS
    scheduler_module._sweep_checking_prs(store, board_id)
    assert checked == [1, 1]


@pytest.mark.parametrize(
    ("status", "blocked", "ready"),
    [
        ("todo", None, False),
        ("doing", None, False),
        ("checking", None, True),
        ("checking", "MERGE_CONFLICT", False),
        ("rejected", None, False),
        ("accepted", None, True),
    ],
)
def test_review_dependencies_start_only_when_ready(
    store, board_and_repo, monkeypatch, status, blocked, ready
):
    board_id, repo_id = board_and_repo
    store.set_repo_default_branch(repo_id, "development")
    store.set_board_merge_mode(board_id, "review")
    parent = store.create_card(board_id, repo_id, "parent")
    child = store.create_card(board_id, repo_id, "child")
    store.add_dependency(child["id"], parent["id"])
    store.update_card(parent["id"], status=status, blocked_reason_code=blocked)
    _merge(store, parent["id"], "https://x/pull/parent")
    monkeypatch.setattr(
        scheduler_module, "pr_view", lambda *a: pytest.fail("review mode need not ask GitHub")
    )
    wait = scheduler_module._dependency_wait(store, store.get_card(child["id"]), "/tmp/r")
    assert (wait is None) is ready


@pytest.mark.parametrize("pr_state", [None, "OPEN", "CLOSED", "ERROR", "MERGED"])
def test_review_accepted_dependency_does_not_wait_for_github(
    store, board_and_repo, monkeypatch, pr_state
):
    board_id, repo_id = board_and_repo
    store.set_repo_default_branch(repo_id, "development")
    store.set_board_merge_mode(board_id, "review")
    parent = store.create_card(board_id, repo_id, "parent")
    child = store.create_card(board_id, repo_id, "child")
    store.add_dependency(child["id"], parent["id"])
    store.update_card(parent["id"], status="accepted")
    if pr_state:
        _merge(store, parent["id"], "https://x/pull/parent")
    monkeypatch.setattr(
        scheduler_module, "pr_view", lambda *a: pytest.fail("accepted review dependency is ready")
    )
    assert scheduler_module._dependency_wait(store, store.get_card(child["id"]), "/tmp/r") is None


def test_review_waits_for_two_unaccepted_parents(store, board_and_repo):
    board_id, repo_id = board_and_repo
    store.set_board_merge_mode(board_id, "review")
    child = store.create_card(board_id, repo_id, "child")
    for number in range(2):
        parent = store.create_card(board_id, repo_id, f"parent {number}")
        store.update_card(parent["id"], status="checking")
        _merge(store, parent["id"], f"https://x/pull/{number}")
        store.add_dependency(child["id"], parent["id"])
    assert scheduler_module._dependency_wait(store, store.get_card(child["id"]), "/tmp/r")


def test_review_stack_stops_at_three_cards(store, board_and_repo):
    board_id, repo_id = board_and_repo
    store.set_board_merge_mode(board_id, "review")
    parent = None
    for depth in range(1, 5):
        card = store.create_card(board_id, repo_id, f"level {depth}")
        if parent:
            store.add_dependency(card["id"], parent["id"])
        wait = scheduler_module._dependency_wait(store, store.get_card(card["id"]), "/tmp/r")
        assert (wait is None) is (depth <= 3)
        if parent:
            store.append_event(
                card["id"],
                "stacked_on",
                {"parent_id": parent["id"], "branch": scheduler_module.branch_name(parent["id"])},
            )
        store.update_card(card["id"], status="checking")
        _merge(store, card["id"], f"https://x/pull/{depth}")
        parent = card


def test_sweep_waits_until_landing_releases_its_tested_tree(
    store, board_and_repo, tmp_path, monkeypatch
):
    from contextlib import contextmanager

    from smortboard.review.integrate import integration_lock
    from smortboard.review.mergeable import MergeSyncResult

    board_id, _ = board_and_repo
    repo_path = tmp_path / "repo"
    _checking_card(store, board_id, repo_path, tmp_path)
    _stub_git_plumbing(monkeypatch)
    attempting, swept = threading.Event(), threading.Event()
    errors = []

    @contextmanager
    def observe_lock(path, base):
        attempting.set()
        with integration_lock(path, base):
            yield

    monkeypatch.setattr(scheduler_module, "integration_lock", observe_lock)
    monkeypatch.setattr(
        scheduler_module, "check_mergeable", lambda *a: swept.set() or MergeSyncResult(True)
    )

    def sweep():
        try:
            with Store(store.path) as connection:
                scheduler_module._sweep_checking_prs(connection, board_id)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=sweep)
    with integration_lock(repo_path, "main"):
        worker.start()
        assert attempting.wait(5)
        assert not swept.is_set()
    worker.join(5)
    assert not worker.is_alive()
    assert errors == []
    assert swept.is_set()


def test_sweep_retries_retarget_after_parent_lands(store, board_and_repo, tmp_path, monkeypatch):
    from smortboard.review import land_card as landing_module
    from smortboard.review.mergeable import MergeSyncResult

    board_id, _ = board_and_repo
    child_id, _, _ = _checking_card(store, board_id, tmp_path / "repo", tmp_path)
    parent = store.create_card(board_id, store.get_card(child_id)["repo_id"], "parent")
    store.add_dependency(child_id, parent["id"])
    store.update_card(parent["id"], status="accepted")
    store.append_event(parent["id"], "integrated", {"base": "main"})
    store.append_event(child_id, "stacked_on", {"parent_id": parent["id"], "branch": "card/parent"})
    _stub_git_plumbing(monkeypatch)
    retargeted, swept = [], []

    def retarget(connection, card, repo, base):
        retargeted.append(card["id"])
        connection.append_event(child_id, "stacked_retargeted", {"base": base})

    monkeypatch.setattr(landing_module, "retarget_children", retarget)
    monkeypatch.setattr(
        scheduler_module, "check_mergeable", lambda *a: swept.append(1) or MergeSyncResult(True)
    )
    scheduler_module._sweep_checking_prs(store, board_id)
    assert retargeted == [parent["id"]]
    assert swept == [1]
