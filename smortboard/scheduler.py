"""bounded, per-board parallelism: several cards drained from `todo` at once.

`BoardScheduler` picks up where a single `r` press leaves off (see lifecycle.py, server/runs.py).
It does not run a card itself - it decides WHEN a card may start and hands it to RunRegistry.start,
the same entry point a manual run uses. Three rules gate a start, each documented at its check:

DEPENDENCIES - a card starts only once every card it depends on has a pull request MERGED on
  GitHub, not merely `accepted` on the board.
LEASES - two cards in the same repo whose lease globs could touch the same file never run together.
USAGE_LIMIT - a run that blocks on it marks the active credential profile limited. Rotation to the
  next configured profile is opt-in (auto_switch_profiles == "on"); by default the board just
  parks new starts until the earliest reset, same as before profiles existed. Already-running
  cards are left alone either way. See smortboard/profiles.py.
BUDGET - a board with a daily_budget_usd set and today's (UTC) spend at or past it starts no new
  cards; already-running cards finish. See _board_daily_budget and telemetry.board_spend_today.

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

from smortboard import profiles, telemetry
from smortboard.actions import with_next
from smortboard.exec.runner import _api_unreachable_signal, _session_limit_text_signal
from smortboard.exec.worktrees import (
    branch_name,
    default_branch,
    fetch_base,
    has_remote,
    worktree_path,
)
from smortboard.review.merge_request import PullRequestState, pr_view
from smortboard.review.mergeable import check_mergeable, merge_branch, push_branch
from smortboard.store.api import Store
from smortboard.store.errors import NotFoundError

# the same author every other board-written comment carries - see lifecycle.BOARD_AUTHOR
_BOARD_AUTHOR = "smortboard"

# unset means this - the operator's own value in settings always wins, see store.api._SETTING_KEYS
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

# API_UNREACHABLE backoff: three automatic retries, then it stays blocked for the operator
API_UNREACHABLE_BACKOFF_MINUTES = (2, 10, 30)
API_UNREACHABLE_MAX_RETRIES = len(API_UNREACHABLE_BACKOFF_MINUTES)


def relabel_stale_crashes(store: Store) -> list[dict[str, str]]:
    """one-time fix for cards blocked CRASH before the classifier learned session-limit and
    api-unreachable wording: relabels them by re-reading their last `result` event's own text,
    the same signals classify_result checks on a fresh run. Returns each relabel for logging.
    """
    relabeled = []
    for board in store.list_boards():
        for card in store.list_cards(board["id"]):
            if card.get("blocked_reason_code") != "CRASH":
                continue
            results = [e for e in store.list_events(card["id"]) if e["kind"] == "result"]
            if not results:
                continue
            payload = results[-1]["payload"]
            if _session_limit_text_signal(payload):
                new_code = "USAGE_LIMIT"
            elif _api_unreachable_signal(payload):
                new_code = "API_UNREACHABLE"
            else:
                continue
            store.update_card(card["id"], blocked_reason_code=new_code)
            store.append_event(card["id"], "relabeled", {"from": "CRASH", "to": new_code})
            relabeled.append({"card_id": card["id"], "from": "CRASH", "to": new_code})
    return relabeled


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


# a checking card's branch is re-synced with its base no more often than this, per repo - the
# board has no push signal for "someone merged a PR on this repo" (only for a specific PR's own
# state, see _dependency_wait above), so this is the timer fallback the spec allows
_SWEEP_INTERVAL_SECONDS = 5 * 60
_last_sweep: dict[str, float] = {}


def _sweep_checking_prs(store: Store, board_id: str) -> None:
    """keeps every checking card's branch mergeable with its base while its pull request waits.

    Measured 2026-09-14 (card 59727ba3, PR #112): a branch cut once at the start of a run and
    never updated drifted 34 commits behind main while its PR waited, and conflicted in four files
    other PRs had since touched. Behind but still mergeable: merges base in and pushes, so the PR
    stays current. Behind and conflicting: blocks the card MERGE_CONFLICT with the files, same as
    lifecycle.py does at hand-over - never touches the pull request either way.
    """
    now = time.time()
    for card in store.list_cards(board_id):
        if card["status"] != "checking" or card.get("blocked_reason_code"):
            continue
        if not _latest_merge_request_url(store, card["id"]):
            continue  # opening never finished - nothing open to keep current
        try:
            repo = store.get_repo(card["repo_id"])
        except NotFoundError:
            continue
        repo_path = repo["path"]
        last = _last_sweep.get(repo_path, 0.0)
        if now - last < _SWEEP_INTERVAL_SECONDS:
            continue
        _last_sweep[repo_path] = now
        if not has_remote(repo_path):
            continue
        base = default_branch(repo)
        if not fetch_base(repo_path, base):
            continue
        tree = worktree_path(repo_path, card["id"])
        if not tree.exists():
            continue  # worktree cleaned up - nothing here to sync
        branch, base_ref = branch_name(card["id"]), f"origin/{base}"
        check = check_mergeable(tree, branch, base_ref)
        if not check.behind:
            continue  # already current
        if not check.clean:
            _flag_merge_conflict(store, card["id"], branch, base_ref, check.conflicting_files)
            continue
        merged = merge_branch(tree, base_ref)
        if not merged.clean:
            _flag_merge_conflict(store, card["id"], branch, base_ref, merged.conflicting_files)
        elif merged.merged:
            push_branch(tree, branch)


def _flag_merge_conflict(
    store: Store, card_id: str, branch: str, base_ref: str, files: list[str]
) -> None:
    store.append_event(card_id, "merge_conflict", {"base_ref": base_ref, "files": files})
    store.update_card(card_id, blocked_reason_code="MERGE_CONFLICT", review_flag=True)
    note = with_next(
        f"Merging {base_ref} into {branch} now conflicts in: {', '.join(files) or 'unknown files'}."
        "\n\nResuming this card lets the worker merge the base branch and resolve them.",
        "MERGE_CONFLICT",
    )
    store.add_comment(card_id, author=_BOARD_AUTHOR, body=note)


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


def _board_max_parallel(store: Store, board_id: str) -> int | None:
    """this board's own cap, or None for no board-specific limit - only the global one applies"""
    try:
        raw = store.get_board(board_id).get("max_parallel")
    except NotFoundError:
        return None
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _board_daily_budget(store: Store, board_id: str) -> float | None:
    """this board's own daily usd spend cap, or None for no cap at all"""
    try:
        raw = store.get_board(board_id).get("daily_budget_usd")
    except NotFoundError:
        return None
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _budget_exhausted(store: Store, board_id: str) -> bool:
    """True once today's (UTC) spend on this board is at or past its own daily budget - unset
    budget means never exhausted, so a board with no cap never pays for this check's own cost"""
    budget = _board_daily_budget(store, board_id)
    if budget is None:
        return False
    return telemetry.board_spend_today(store, board_id) >= budget


def _dependency_wait(store: Store, card: dict[str, Any], repo_path: str | Path) -> str | None:
    """None once every dependency's pull request is actually MERGED on GitHub.

    `accepted` alone used to be enough - it no longer is. THE BOARD NEVER MERGES, so `accepted`
    only means the operator signed off and a PR is open; a dependent's worktree is cut fresh from the
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
        # per-card timers for a solo, automatic retry - API_UNREACHABLE backoff and a resumed
        # MERGE_CONFLICT's own once-only attempt, both keyed by card id
        self._card_retry_at: dict[str, float] = {}

    # -- reads --------------------------------------------------------------------

    def schedule_view(self) -> dict[str, Any]:
        """running / queued / waiting / paused_until - resumes a lapsed pause first, so polling
        this is enough to drain the queue again once a USAGE_LIMIT window rolls over. Also requeues
        any card whose own retry timer (API_UNREACHABLE backoff, a MERGE_CONFLICT auto-resume) has
        come due - those are per-card, not the board-wide pause, so nothing else drains them."""
        now = time.time()
        with self._lock:
            expired = self._paused_until is not None and now >= self._paused_until
            due = [card_id for card_id, at in self._card_retry_at.items() if now >= at]
        if due:
            with self._lock:
                for card_id in due:
                    self._card_retry_at.pop(card_id, None)
                    if card_id not in self._queue and card_id not in self._running:
                        self._queue.append(card_id)
        if expired:
            with self._lock:
                self._paused_until = None
        if expired or due:
            self._tick()
        store = Store(self._db_path)
        try:
            budget_paused = _budget_exhausted(store, self.board_id)
        finally:
            store.close()
        with self._lock:
            return {
                "running": sorted(self._running),
                "queued": list(self._queue),
                "waiting": dict(self._waiting),
                "paused_until": self._paused_until,
                "card_retry_at": dict(self._card_retry_at),
                "budget_paused": budget_paused,
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
            # no push signal for "a PR merged on this repo" exists board-wide, so every tick is
            # the opportunity to notice one - _sweep_checking_prs throttles itself per repo
            _sweep_checking_prs(store, self.board_id)
            running_cards = []
            for card_id in running_ids:
                with contextlib.suppress(NotFoundError):
                    running_cards.append(store.get_card(card_id))
            # THE GLOBAL CAP IS ONE SEAT COUNT SHARED ACROSS EVERY BOARD - runs.active() is the one
            # RunRegistry every BoardScheduler shares, so it is the only place that count can come
            # from. a fake with no .active() (most scheduler tests) falls back to this board's own
            # running set, exactly the old single-board behaviour.
            active = getattr(self._runs, "active", None)
            active_states = list(active()) if active else None
            global_running = len(active_states) if active_states is not None else len(running_ids)
            slots = _max_parallel(store) - global_running
            board_cap = _board_max_parallel(store, self.board_id)
            if board_cap is not None:
                slots = min(slots, board_cap - len(running_ids))
            if _budget_exhausted(store, self.board_id):
                # today's spend on this board already hit its cap - no new starts, but a card
                # already running keeps its slot and finishes
                slots = 0
            # a card started by hand holds its lease too, but not one of this board's slots
            for state in active_states or []:
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
            reason = getattr(state, "blocked_reason_code", None)
            if reason == "USAGE_LIMIT":
                self._handle_usage_limit(card_id)
            elif reason == "API_UNREACHABLE":
                self._handle_api_unreachable(card_id)
            elif reason == "MERGE_CONFLICT":
                self._handle_merge_conflict(card_id)
            else:
                self._tick()

        return _on_finish

    def _handle_merge_conflict(self, card_id: str) -> None:
        """resumes a MERGE_CONFLICT card exactly once, automatically, through the same path an
        inbox answer uses (attention.answer_card) - the note tells the worker which base to merge
        and which files it conflicts in. A second MERGE_CONFLICT on the same card (the resume's own
        attempt failed to resolve it) is left for the operator - never retried twice."""
        # imported here, not at module level: attention.py imports conflicting_run from this
        # module, so a top-level import here would be circular
        from smortboard.attention import AnswerRefused, answer_card

        store = Store(self._db_path)
        try:
            already_tried = any(
                e["kind"] == "merge_conflict_auto_resume" for e in store.list_events(card_id)
            )
            if already_tried:
                store.add_comment(
                    card_id,
                    author=_BOARD_AUTHOR,
                    body="merge conflict again after the automatic resume - left for the operator.",
                )
                return
            merge_events = [e for e in store.list_events(card_id) if e["kind"] == "merge_conflict"]
            payload = merge_events[-1]["payload"] if merge_events else {}
            base_ref = payload.get("base_ref", "the base branch")
            files = payload.get("files") or []
            store.append_event(card_id, "merge_conflict_auto_resume", {"files": files})
            note = (
                f"Resuming automatically: merge {base_ref} into this branch and resolve the "
                f"conflicts in: {', '.join(files) or 'the listed files'}. Then commit the result."
            )
            # still blocked - the operator sees it in the inbox like any other refusal
            with contextlib.suppress(AnswerRefused):
                answer_card(store, self._runs, card_id, note)
        finally:
            store.close()
        self._tick()

    def _handle_api_unreachable(self, card_id: str) -> None:
        """retries an API_UNREACHABLE card itself, with backoff 2/10/30 minutes. After
        API_UNREACHABLE_MAX_RETRIES automatic attempts it is left blocked for the operator, same
        as any other block - the card's own blocked_reason_code and board note already say why."""
        store = Store(self._db_path)
        try:
            attempts = [
                e for e in store.list_events(card_id) if e["kind"] == "api_unreachable_retry"
            ]
            attempt = len(attempts) + 1
            if attempt > API_UNREACHABLE_MAX_RETRIES:
                store.add_comment(
                    card_id,
                    author=_BOARD_AUTHOR,
                    body=f"api unreachable after {len(attempts)} automatic retries - "
                    "left for the operator.",
                )
                return
            delay_minutes = API_UNREACHABLE_BACKOFF_MINUTES[attempt - 1]
            retry_at = time.time() + delay_minutes * 60
            store.append_event(
                card_id, "api_unreachable_retry", {"attempt": attempt, "retry_at": retry_at}
            )
            store.add_comment(
                card_id,
                author=_BOARD_AUTHOR,
                body=f"api unreachable - retrying automatically in {delay_minutes} minute(s) "
                f"(attempt {attempt}/{API_UNREACHABLE_MAX_RETRIES}).",
            )
        finally:
            store.close()
        with self._lock:
            self._card_retry_at[card_id] = retry_at
        self._tick()

    def _handle_usage_limit(self, card_id: str) -> None:
        """the credential in use just got refused. Marks it limited and, if another configured
                profile is not, switches to it and resumes `card_id` itself - USAGE_LIMIT blocks a manual
                run for a human, but here there is another credential to try before giving up like that.

                profiles.handle_usage_limit does the mark-and-rotate as one load-mutate-save transaction
                (see its docstring) rather than this method making several separate profiles calls that
                each hit disk - with only the implicit "default" profile configured it stays a pure read,
                so a single-credential board never grows a profiles.json.

        rotation is opt-in: auto_switch_profiles must be "on". Anything else (unset, or a stored "off"
                from before this flipped) skips the rotation half - the profile is still marked limited
                (so it is skipped once switching is turned on later), but the board parks until the reset
                exactly as it did before profiles existed, instead of rotating credentials on the
                operator's behalf without being asked.
        """
        store = Store(self._db_path)
        try:
            resets_at = _latest_reset(store)
            auto_switch = store.get_settings().get("auto_switch_profiles") == "on"
            if auto_switch:
                result = profiles.handle_usage_limit(resets_at)
            else:
                # a single-profile board must never write a state file just because a run hit
                # its limit alone - same invariant handle_usage_limit itself keeps
                if profiles.has_multiple_profiles():
                    profiles.mark_limited(profiles.active_profile(), resets_at)
                result = {"rotated": False, "next_profile": None, "earliest_reset": None}
        finally:
            store.close()

        if result["next_profile"] is not None:
            with self._lock:
                if card_id not in self._queue and card_id not in self._running:
                    self._queue.insert(0, card_id)
        else:
            fallback = result["earliest_reset"] if result["rotated"] else None
            # the card itself has to rejoin the queue too, not only the board-wide pause clear -
            # left out of the queue, schedule_view's lapsed-pause check had nothing to start once
            # the window rolled over, and this card sat blocked past its own reset forever
            with self._lock:
                self._paused_until = fallback or resets_at or (time.time() + _FALLBACK_PARK_SECONDS)
                if card_id not in self._queue and card_id not in self._running:
                    self._queue.append(card_id)
        self._tick()


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
