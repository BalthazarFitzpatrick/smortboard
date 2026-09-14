"""bounded, per-board parallelism: several cards drained from `todo` at once.

`BoardScheduler` picks up where a single `r` press leaves off (see lifecycle.py, server/runs.py).
It does not run a card itself - it decides WHEN a card may start and hands it to RunRegistry.start,
the same entry point a manual run uses. Three rules gate a start, each documented at its check:

DEPENDENCIES - a card starts only once every card it depends on has a pull request MERGED on
  GitHub, not merely `accepted` on the board.
LEASES - two cards in the same repo whose lease globs could touch the same file never run together.
USAGE_LIMIT - a run that blocks on it pauses new starts until the window resets; already-running
  cards are left alone, and the blocked card itself stays blocked for a human, not retried here.

TESTABLE WITHOUT THREADS. `_tick` is a plain method: given a store and a fake `runs` object (one
whose `.start` calls the runner synchronously and fires `on_finish` inline, as RunRegistry itself
would eventually do on its own thread) a whole run-all can be driven from a single-threaded test,
deterministically, one tick at a time.
"""

from __future__ import annotations

import contextlib
import fnmatch
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from smortboard.review.merge_request import PullRequestState, pr_view
from smortboard.store.api import Store
from smortboard.store.errors import NotFoundError

# unset means this - fabian's own value in settings always wins, see store.api._SETTING_KEYS
DEFAULT_MAX_PARALLEL = 2

# statuses a blocked card can never be requeued from - the schema has no "blocked" status of its
# own (a blocked card keeps whatever status it was in and raises blocked_reason_code instead, see
# store/schema.py), so queueability is decided on that flag, not on status alone
_NEVER_QUEUEABLE = ("accepted", "rejected")


def _is_queueable(card: dict[str, Any]) -> bool:
    """whether `w` may draw this card into the queue: a fresh todo card, or one a run left
    blocked - a bad lease widened or a test command fixed should let it re-run on its own, not
    wait for a human to drag it back to todo first."""
    if card["status"] in _NEVER_QUEUEABLE:
        return False
    return card["status"] == "todo" or bool(card.get("blocked_reason_code"))


# a run that blocks USAGE_LIMIT but carries no readable resetsAt (never happened in the spikes,
# but a stream is someone else's format) parks for this long rather than never resuming
_FALLBACK_PARK_SECONDS = 5 * 60

# schedule_view is polled by the UI every couple seconds - asking GitHub every poll would hammer
# it for no benefit, so one answer per PR url is good for this long
_PR_STATE_TTL_SECONDS = 60
_pr_state_cache: dict[str, tuple[float, PullRequestState]] = {}


def _cached_pr_view(repo_path: str | Path, url: str) -> PullRequestState:
    now = time.time()
    cached = _pr_state_cache.get(url)
    if cached is not None and now - cached[0] < _PR_STATE_TTL_SECONDS:
        return cached[1]
    state = pr_view(repo_path, url)
    _pr_state_cache[url] = (now, state)
    return state


def _latest_merge_request_url(store: Store, card_id: str) -> str | None:
    events = [e for e in store.list_events(card_id) if e["kind"] == "merge_request"]
    if not events:
        return None
    url = events[-1]["payload"].get("url")
    return url or None


def _lease_globs(card: dict[str, Any]) -> list[str]:
    return [row["path_glob"] for row in card.get("leases") or []]


def _segment_may_match(a: str, b: str) -> bool:
    """one path segment against another, conservatively. equal, or either could be a glob that
    matches the other - fnmatch both directions since a `*.py` and a literal `routes.py` need it
    read from each side."""
    if a == b:
        return True
    if any(ch in a for ch in "*?[") or any(ch in b for ch in "*?["):
        return fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a)
    return False


def globs_may_overlap(a: str, b: str) -> bool:
    """conservative lease overlap: True unless the two globs are PROVABLY disjoint.

    Compared segment by segment (split on "/"). A "**" on either side ends the comparison in an
    overlap - it can reach anything below it. Two segments that are both plain literals and differ
    end it in no overlap. Anything else (a "*"/"?"/"[...]" segment against a literal, or the globs
    running out at different depths) is treated as a possible touch, on purpose: a false "they
    might conflict" costs a card a few minutes of queueing, a false "they cannot conflict" costs a
    stepped-on file.
    """
    segs_a, segs_b = a.strip("/").split("/"), b.strip("/").split("/")
    # deliberately not strict: a glob running out first is a directory prefix, handled below
    for sa, sb in zip(segs_a, segs_b, strict=False):
        if sa == "**" or sb == "**":
            return True
        if not _segment_may_match(sa, sb):
            return False
    return True  # one ran out first - it is a prefix (a directory glob) of the other


def _leases_conflict(globs_a: list[str], globs_b: list[str]) -> bool:
    # AN EMPTY LEASE TOUCHES NOTHING, NOT EVERYTHING. the hook the lease compiles to
    # (exec/leases.py lease_allows) is False for every path over an empty list. the lifecycle
    # refuses such a card before its agent runs, so this only keeps it from holding anyone else up
    if not globs_a or not globs_b:
        return False
    return any(globs_may_overlap(a, b) for a in globs_a for b in globs_b)


def _max_parallel(store: Store) -> int:
    raw = store.get_settings().get("max_parallel")
    if raw is None:
        return DEFAULT_MAX_PARALLEL
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_PARALLEL
    return value if value > 0 else DEFAULT_MAX_PARALLEL


def _dependency_wait(store: Store, card: dict[str, Any], repo_path: str | Path) -> str | None:
    """None once every dependency's pull request is actually MERGED on GitHub.

    `accepted` alone used to be enough - it no longer is. THE BOARD NEVER MERGES, so `accepted`
    only means Fabian signed off and a PR is open; a dependent's worktree is cut fresh from the
    repo's base branch, so it sees the dependency's code only once that PR landed there. `repo_path`
    is any local checkout with `gh` available - `gh pr view <url>` resolves from the url itself, so
    it does not need to be the dependency's own repo.
    """
    for dep_id in card.get("depends_on") or []:
        try:
            dep = store.get_card(dep_id)
        except NotFoundError:
            continue  # a deleted dependency blocks nothing that still exists
        if dep["status"] != "accepted":
            return f"waiting on dependency ‘{dep['title']}’ - not accepted yet ({dep['status']})"
        url = _latest_merge_request_url(store, dep_id)
        if url is None:
            return f"dependency ‘{dep['title']}’ is accepted but has no pull request recorded"
        state = _cached_pr_view(repo_path, url)
        if state.error:
            return (
                f"could not ask GitHub about dependency ‘{dep['title']}’s pull request: "
                f"{state.error}"
            )
        if state.merged:
            continue
        if state.state == "OPEN":
            return f"dependency ‘{dep['title']}’s pull request is still open"
        return f"dependency ‘{dep['title']}’s pull request was closed without merging"
    return None


def _lease_wait(card: dict[str, Any], pending: list[tuple[str, list[str], str]]) -> str | None:
    my_globs = _lease_globs(card)
    for repo_id, globs, other_id in pending:
        if repo_id == card["repo_id"] and _leases_conflict(my_globs, globs):
            return f"lease conflict with card {other_id}"
    return None


def conflicting_run(store: Store, card: dict[str, Any], running_ids: Iterable[str]) -> str | None:
    """why `card` may not start beside the runs going now, or None - the scheduler's lease rule for
    the starts that never pass through _tick: a manual run and an inbox answer"""
    for run_id in running_ids:
        if run_id == card["id"]:
            continue
        try:
            other = store.get_card(run_id)
        except NotFoundError:
            continue
        same_repo = other["repo_id"] == card["repo_id"]
        if same_repo and _leases_conflict(_lease_globs(card), _lease_globs(other)):
            return f"its lease overlaps the running card ‘{other['title']}’"
    return None


class BoardScheduler:
    """one board's queue. `runs` is anything shaped like server.runs.RunRegistry - only `.start`
    is called, so a test can pass a fake that runs synchronously."""

    def __init__(self, board_id: str, db_path: str | Path, runs: Any) -> None:
        self.board_id = board_id
        self._db_path = Path(db_path)
        self._runs = runs
        self._lock = threading.Lock()
        # held for a whole tick and for every queue write: a tick snapshots the queue, starts cards
        # outside _lock, then writes its leftovers back - unguarded, a stop() in between came back
        # to life. reentrant because a test's fake runner fires on_finish, and so _tick, inline
        self._tick_lock = threading.RLock()
        self._queue: list[str] = []
        self._running: set[str] = set()
        self._waiting: dict[str, str] = {}
        self._paused_until: float | None = None

    # -- reads --------------------------------------------------------------------

    def schedule_view(self) -> dict[str, Any]:
        """running / queued / waiting / paused_until - resumes a lapsed pause first, so polling
        this is enough to drain the queue again once a USAGE_LIMIT window rolls over; nothing
        else needs its own timer."""
        with self._lock:
            expired = self._paused_until is not None and time.time() >= self._paused_until
        if expired:
            with self._lock:
                self._paused_until = None
            self._tick()
        with self._lock:
            return {
                "running": sorted(self._running),
                "queued": list(self._queue),
                "waiting": dict(self._waiting),
                "paused_until": self._paused_until,
            }

    # -- writes --------------------------------------------------------------------

    def start_all(self) -> dict[str, Any]:
        """queues every `todo` card on this board that has a repo, then starts what it can."""
        store = Store(self._db_path)
        try:
            todo = [
                c["id"]
                for c in store.list_cards(self.board_id)
                if _is_queueable(c) and c.get("repo_id")
            ]
        finally:
            store.close()
        with self._tick_lock:
            with self._lock:
                for card_id in todo:
                    if card_id not in self._queue and card_id not in self._running:
                        self._queue.append(card_id)
            self._tick()
        return self.schedule_view()

    def stop(self) -> dict[str, Any]:
        """clears the queue. running cards are left to finish - stopping mid-worktree would lose
        their commit, not just their turn."""
        with self._tick_lock, self._lock:
            self._queue = []
            self._waiting = {}
        return self.schedule_view()

    # -- the loop --------------------------------------------------------------------

    def _tick(self) -> None:
        with self._tick_lock:
            self._tick_locked()

    def _tick_locked(self) -> None:
        with self._lock:
            if self._paused_until is not None and time.time() < self._paused_until:
                return  # parked for USAGE_LIMIT; schedule_view resumes this once the window rolls
            queue_snapshot = list(self._queue)
            running_ids = set(self._running)

        store = Store(self._db_path)
        try:
            running_cards = []
            for card_id in running_ids:
                with contextlib.suppress(NotFoundError):
                    running_cards.append(store.get_card(card_id))
            slots = _max_parallel(store) - len(running_ids)
            # a card started by hand holds its lease too, but not one of this board's slots
            active = getattr(self._runs, "active", None)
            for state in active() if active else []:
                if state.card_id not in running_ids:
                    with contextlib.suppress(NotFoundError):
                        running_cards.append(store.get_card(state.card_id))
            pending = [(c["repo_id"], _lease_globs(c), c["id"]) for c in running_cards]

            started: list[str] = []
            waiting: dict[str, str] = {}
            remaining: list[str] = []
            for card_id in queue_snapshot:
                try:
                    card = store.get_card(card_id)
                except NotFoundError:
                    continue  # gone - drop it, nothing to wait for
                if card_id in running_ids or not _is_queueable(card):
                    continue  # started by hand, accepted, rejected - either way not ours to queue
                if slots <= 0:
                    remaining.append(card_id)
                    continue
                reason = None
                if card.get("depends_on"):
                    try:
                        repo_path = store.get_repo(card["repo_id"])["path"]
                    except NotFoundError:
                        reason = "this card's repo is gone, so its dependencies cannot be checked"
                    else:
                        reason = _dependency_wait(store, card, repo_path)
                reason = reason or _lease_wait(card, pending)
                if reason:
                    waiting[card_id] = reason
                    remaining.append(card_id)
                    continue
                self._runs.start(card_id, on_finish=self._make_on_finish(card_id))
                pending.append((card["repo_id"], _lease_globs(card), card["id"]))
                started.append(card_id)
                slots -= 1
        finally:
            store.close()

        with self._lock:
            self._running |= set(started)
            self._queue = remaining
            self._waiting = waiting

    def _make_on_finish(self, card_id: str):
        def _on_finish(state: Any) -> None:
            with self._lock:
                self._running.discard(card_id)
            if getattr(state, "blocked_reason_code", None) == "USAGE_LIMIT":
                self._pause_for_usage_limit()
            self._tick()

        return _on_finish

    def _pause_for_usage_limit(self) -> None:
        """stop starting new cards until the latest rate_limit_event's resetsAt. THE CARD THAT HIT
        IT IS NOT RETRIED HERE - it is blocked USAGE_LIMIT like a manual run would leave it, for a
        human to look at or re-run; this only holds back cards that have not started yet."""
        store = Store(self._db_path)
        try:
            resets_at = _latest_reset(store)
        finally:
            store.close()
        with self._lock:
            self._paused_until = resets_at or (time.time() + _FALLBACK_PARK_SECONDS)


def _latest_reset(store: Store) -> float | None:
    """the most recent rate_limit_event, board-wide (a seat is shared across every card the
    operator runs, not per-repo) - see telemetry.usage_projection, which reads the same events."""
    events = store.list_events_by_kind(["rate_limit_event"])
    if not events:
        return None
    payload = events[-1]["payload"]
    info = payload.get("rate_limit_info") or {}
    unified = info.get("unifiedWindows")
    if isinstance(unified, dict):
        resets = [w.get("resetsAt") for w in unified.values() if w.get("resetsAt")]
        return max(resets) if resets else None
    return info.get("resetsAt")


class SchedulerRegistry:
    """one BoardScheduler per board, created on first use - mirrors RunRegistry's and
    OrchestratorRegistry's shape (one dict, one lock, lazily populated per board id)."""

    def __init__(self, db_path: str | Path, runs: Any) -> None:
        self._db_path = Path(db_path)
        self._runs = runs
        self._schedulers: dict[str, BoardScheduler] = {}
        self._lock = threading.Lock()

    def get(self, board_id: str) -> BoardScheduler:
        with self._lock:
            scheduler = self._schedulers.get(board_id)
            if scheduler is None:
                scheduler = BoardScheduler(board_id, self._db_path, self._runs)
                self._schedulers[board_id] = scheduler
            return scheduler
