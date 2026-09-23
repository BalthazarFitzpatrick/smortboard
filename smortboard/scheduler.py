"""bounded, per-board parallelism: several cards drained from `todo` at once.

`BoardScheduler` picks up where a single `r` press leaves off (see lifecycle.py, server/runs.py).
It does not run a card itself - it decides WHEN a card may start and hands it to RunRegistry.start,
the same entry point a manual run uses. Three rules gate a start, each documented at its check:

DEPENDENCIES - free mode waits for merged pull requests; review mode allows one checking parent
  and a stack of at most three cards.
LEASES - two cards in the same repo whose lease globs could touch the same file never run together.
USAGE_LIMIT - a run that blocks on it marks the active credential profile limited. Rotation to the
  next configured profile is opt-in (auto_switch_profiles == "on"); by default the board just
  parks new starts until the earliest reset, same as before profiles existed. Already-running
  cards are left alone either way. See smortboard/profiles.py. A cross-lab fallback model is
  switched to unasked unless usage_limit_route is "attention", which asks in the inbox instead.
BUDGET - a board with a daily_budget_usd set and today's (UTC) spend at or past it starts no new
  cards; already-running cards finish. See _board_daily_budget and telemetry.board_spend_today.

TESTABLE WITHOUT THREADS. `_tick` is a plain method: given a store and a fake `runs` object (one
whose `.start` calls the runner synchronously and fires `on_finish` inline, as RunRegistry itself
would eventually do on its own thread) a whole run-all can be driven from a single-threaded test,
deterministically, one tick at a time. The one thread here, SchedulerTicker, only calls plain
methods (process_due, sweep_all) that a test can call itself.
"""

from __future__ import annotations

import contextlib
import fnmatch
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from smortboard import profiles, telemetry
from smortboard.actions import with_next
from smortboard.budgets import spend_refusal
from smortboard.exec.worktrees import (
    branch_name,
    default_branch,
    fetch_base,
    has_remote,
    worktree_path,
)
from smortboard.labs.events import neutral_events
from smortboard.labs.routing import role_ref, run_ref
from smortboard.lifecycle import RUNTIME_NOT_READY
from smortboard.review.integrate import integration_lock
from smortboard.review.merge_request import PullRequestState, pr_view
from smortboard.review.mergeable import check_mergeable, merge_branch, push_branch
from smortboard.store.api import Store
from smortboard.store.errors import NotFoundError
from smortboard.store.schema import DEFAULT_USAGE_LIMIT_ROUTE

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

# a refusal over a runtime that is not ready (Docker down) retries on its own before it reaches the
# inbox - measured 2026-09-18, five cards were refused within 20s while Docker was starting
RUNTIME_BACKOFF_MINUTES = (1, 2, 5)
RUNTIME_MAX_RETRIES = len(RUNTIME_BACKOFF_MINUTES)

# every run writes a few rate_limit_event rows, so the refused run's own window sits well inside
# this many newest rows - reading all of them parsed the whole 74 MB events table per call
_RESET_LOOKBACK_EVENTS = 200


def usage_limit_route(settings: dict[str, Any]) -> str:
    """ "attention" asks in the inbox before a cross-lab model switch; anything else switches"""
    route = settings.get("usage_limit_route")
    return "attention" if route == "attention" else DEFAULT_USAGE_LIMIT_ROUTE


def latest_limit(store: Store, card_id: str) -> dict[str, Any] | None:
    """the card's newest profile_limited payload - which role, lab and model hit the limit"""
    for event in reversed(store.list_events(card_id)):
        if event["kind"] == "profile_limited":
            return event["payload"]
    return None


def card_reset(store: Store, card_id: str) -> float | None:
    """when this card's usage limit resets: its own profile_limited record, else the latest window
    its last attempt saw - a card relabeled from CRASH never had a profile_limited written"""
    events = store.list_events(card_id)
    for event in reversed(events):
        if event["kind"] == "profile_limited":
            return event["payload"].get("resets_at")
        if event["kind"] == "lifecycle_started":
            break
    resets = []
    for event in reversed(events):
        if event["kind"] == "lifecycle_started":
            break
        for row in neutral_events(event):
            block = row.get("rate_limit") or row.get("result") or {}
            if block.get("resets_at") is not None:
                resets.append(block["resets_at"])
    return max(resets) if resets else None


def record_fallback(
    store: Store,
    card_id: str,
    role: str,
    from_lab: str,
    target: tuple[str, str, str],
    limited_until: float | None,
) -> None:
    """queues a one-shot switch of `role` to `target` for the card's next run (run_ref consumes it)
    and says so on the card - the board's own fallback and the inbox's retry both land here"""
    target_lab, target_model, target_profile = target
    profiles.set_active(target_profile, lab=target_lab)
    store.append_event(
        card_id,
        "lab_fallback",
        {
            "role": role,
            "lab": target_lab,
            "model": target_model,
            "profile": target_profile,
            "from_lab": from_lab,
            "limited_until": limited_until,
        },
    )
    until = (
        f" until {time.strftime('%H:%M', time.localtime(limited_until))}" if limited_until else ""
    )
    store.add_comment(
        card_id,
        author=_BOARD_AUTHOR,
        body=f"Retrying {role} on {target_lab}/{target_model}; {from_lab} was limited{until}.",
    )


def _refused_for_runtime(state: Any) -> bool:
    refusal = getattr(state, "refusal", None) or ""
    return getattr(state, "phase", None) == "refused" and refusal.startswith(RUNTIME_NOT_READY)


def _lapsed_retry(store: Store, card: dict[str, Any], now: float) -> bool:
    """whether a retry this card was owed has come due with nothing left to fire it"""
    reason = card.get("blocked_reason_code")
    if reason == "USAGE_LIMIT":
        reset = card_reset(store, card["id"])
        return reset is None or reset <= now
    if reason == "API_UNREACHABLE":
        attempts = [
            e for e in store.list_events(card["id"]) if e["kind"] == "api_unreachable_retry"
        ]
        if len(attempts) >= API_UNREACHABLE_MAX_RETRIES:
            return False
        retry_at = attempts[-1]["payload"].get("retry_at") if attempts else None
        return retry_at is None or retry_at <= now
    return False


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
            result = next(
                (
                    row.get("result")
                    for row in neutral_events({"payload": payload, "kind": "result"})
                    if row["kind"] == "result"
                ),
                {},
            )
            new_code = result.get("blocked_reason_code")
            if new_code not in {"USAGE_LIMIT", "API_UNREACHABLE"}:
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


# base changes trigger sweeps; the timer retries unavailable worktrees
_SWEEP_INTERVAL_SECONDS = 5 * 60
_last_sweep: dict[tuple[str, str, str], float] = {}
_last_base_sha: dict[tuple[str, str, str], str] = {}


def _read_base_sha(repo_path: str, base: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "rev-parse", "--verify", f"origin/{base}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _is_stacked(store: Store, card_id: str) -> bool:
    for event in reversed(store.list_events(card_id)):
        if event["kind"] == "stacked_retargeted":
            return False
        if event["kind"] == "stacked_on":
            return True
    return False


def sweep_checking_prs(store: Store, board_id: str, *, repo_path: str | Path | None = None) -> None:
    """check the fetched base immediately after a board landing"""
    _sweep_checking_prs(store, board_id, repo_path=repo_path)


def _sweep_checking_prs(
    store: Store, board_id: str, *, repo_path: str | Path | None = None
) -> None:
    """sweep every waiting branch when its fetched base changes, with a slow retry backstop.

    Measured 2026-09-14 (card 59727ba3, PR #112): a branch cut once at the start of a run and
    never updated drifted 34 commits behind main while its PR waited, and conflicted in four files
    other PRs had since touched. Behind but still mergeable: merges base in and pushes, so the PR
    stays current. Behind and conflicting: blocks the card MERGE_CONFLICT with the files, same as
    lifecycle.py does at hand-over - never touches the pull request either way.
    """
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for card in store.list_cards(board_id):
        if card["status"] != "checking" or card.get("blocked_reason_code"):
            continue
        if not _latest_merge_request_url(store, card["id"]):
            continue  # opening never finished - nothing open to keep current
        if _is_stacked(store, card["id"]):
            from smortboard.review.land_card import retry_retarget

            if retry_retarget(store, card):
                continue
        try:
            repo = store.get_repo(card["repo_id"])
        except NotFoundError:
            continue
        if repo_path is not None and str(repo_path) != repo["path"]:
            continue
        key = (board_id, repo["path"], default_branch(repo))
        groups.setdefault(key, []).append(card)

    now = time.time()
    for key, cards in groups.items():
        _, path, base = key
        # landing tests must observe the same tree that gets integrated
        with integration_lock(path, base):
            _sweep_repo(store, key, cards, now)


def _sweep_repo(store, key, cards, now):
    _, path, base = key
    if not has_remote(path) or not fetch_base(path, base):
        return
    sha = _read_base_sha(path, base)
    if not sha:
        return
    if sha == _last_base_sha.get(key) and now - _last_sweep[key] < _SWEEP_INTERVAL_SECONDS:
        return
    base_ref = f"origin/{base}"
    for card in cards:
        card = store.get_card(card["id"])
        if card["status"] != "checking" or card.get("blocked_reason_code"):
            continue
        tree = worktree_path(path, card["id"])
        if not tree.exists():
            continue
        branch = branch_name(card["id"])
        check = check_mergeable(tree, branch, base_ref)
        if not check.behind:
            continue
        if not check.clean:
            _flag_merge_conflict(store, card["id"], branch, base_ref, check.conflicting_files)
            continue
        merged = merge_branch(tree, base_ref)
        if not merged.clean:
            _flag_merge_conflict(store, card["id"], branch, base_ref, merged.conflicting_files)
        elif merged.merged:
            push_branch(tree, branch)
    _last_base_sha[key] = sha
    _last_sweep[key] = now


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
    spend = telemetry.board_spend_today(store, board_id)
    return spend is None or spend >= budget


def _dependency_wait(store: Store, card: dict[str, Any], repo_path: str | Path) -> str | None:
    """review mode can stack on ready work; free mode requires proof every dependency landed"""
    if not store.board_merges_freely(card["board_id"]):
        from smortboard.review.stacks import stacking_parent

        try:
            stacking_parent(store, card)
        except ValueError as exc:
            return str(exc)
        return None
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
        self._lab_paused_until: dict[str, float] = {}
        self._run_profiles: dict[str, tuple[str, str]] = {}
        self._run_start_seq: dict[str, int] = {}
        self._limited_roles: dict[str, str] = {}
        # per-card timers for a solo, automatic retry - API_UNREACHABLE backoff and a resumed
        # MERGE_CONFLICT's own once-only attempt, both keyed by card id
        self._card_retry_at: dict[str, float] = {}
        # a runtime refusal leaves no reason code, so a doing card would fail _is_queueable - the
        # board's own retry of it is let through once
        self._retry_ok: set[str] = set()
        self._runtime_attempts: dict[str, int] = {}

    @property
    def _paused_until(self) -> float | None:
        """legacy callers see the anthropic pause; new clients use paused_labs"""
        return self._lab_paused_until.get("anthropic")

    @_paused_until.setter
    def _paused_until(self, value: float | None) -> None:
        if value is None:
            self._lab_paused_until.pop("anthropic", None)
        else:
            self._lab_paused_until["anthropic"] = value

    # -- reads --------------------------------------------------------------------

    def scheduled_retry(self, store: Store, card: dict[str, Any]) -> float | None:
        """when this board runs `card` again by itself, or None when nothing is scheduled. a
        queued card waits out any pause over its labs; with none it is due now"""
        card_id = card["id"]
        with self._lock:
            timer = self._card_retry_at.get(card_id)
            queued = card_id in self._queue
            pauses = dict(self._lab_paused_until)
            limited_role = self._limited_roles.get(card_id)
        if timer is not None:
            return timer
        if not queued:
            return None
        labs = {run_ref(store, "worker", card)[0]}
        if limited_role:
            labs.add(run_ref(store, limited_role, card)[0])
        return max([time.time(), *(pauses.get(lab, 0) for lab in labs)])

    def paused_labs(self) -> dict[str, float]:
        with self._lock:
            return dict(self._lab_paused_until)

    def _lab_paused(self, lab: str) -> bool:
        with self._lock:
            return self._lab_paused_until.get(lab, 0) > time.time()

    def process_due(self, now: float | None = None) -> None:
        """requeues every card whose own retry timer came due (API_UNREACHABLE backoff, a runtime
        refusal) and lifts every lapsed lab pause, then ticks once if either happened - the
        ticker thread calls this so a retry fires without anyone polling this board"""
        now = time.time() if now is None else now
        with self._lock:
            expired = [lab for lab, at in self._lab_paused_until.items() if now >= at]
            due = [card_id for card_id, at in self._card_retry_at.items() if now >= at]
            for card_id in due:
                self._card_retry_at.pop(card_id, None)
                if card_id not in self._queue and card_id not in self._running:
                    self._queue.append(card_id)
            for lab in expired:
                self._lab_paused_until.pop(lab, None)
        if expired or due:
            self._tick()

    def schedule_view(self) -> dict[str, Any]:
        """running / queued / waiting / paused_until - processes due timers and lapsed pauses
        first, so a view is never staler than the ticker's last pass"""
        self.process_due()
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
                "paused_labs": dict(self._lab_paused_until),
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

    def requeue(self, card_ids: list[str]) -> None:
        """puts cards back in this board's queue and ticks - what a restart owes a card whose
        retry timer died with the last process"""
        with self._tick_lock:
            with self._lock:
                for card_id in card_ids:
                    if card_id not in self._queue and card_id not in self._running:
                        self._queue.append(card_id)
            self._tick()

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
            queue_snapshot = list(self._queue)
            running_ids = set(self._running)
            retry_ok = set(self._retry_ok)

        store = Store(self._db_path)
        try:
            # no git here: the checking-PR sweep fetches, and a tick also runs on the request
            # thread (start_all, schedule_view) - SchedulerTicker sweeps on its own thread instead
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
            # a card started by hand holds its lease too, but not one of this board's slots
            for state in active_states or []:
                if state.card_id not in running_ids:
                    with contextlib.suppress(NotFoundError):
                        running_cards.append(store.get_card(state.card_id))
            pending = [(c["repo_id"], _lease_globs(c), c["id"]) for c in running_cards]

            started: list[str] = []
            waiting: dict[str, str] = {}
            # cards this tick decided no longer belong in the queue at all (gone, started by
            # hand, or landed); see the writeback below for why only removals get tracked
            drop: list[str] = []
            for card_id in queue_snapshot:
                try:
                    card = store.get_card(card_id)
                except NotFoundError:
                    drop.append(card_id)  # gone - drop it, nothing to wait for
                    continue
                own_retry = card_id in retry_ok and card["status"] not in _NEVER_QUEUEABLE
                if card_id in running_ids or not (_is_queueable(card) or own_retry):
                    drop.append(card_id)  # started by hand, accepted, rejected - not ours anymore
                    continue
                if slots <= 0:
                    continue
                reason = None
                worker_lab, _ = run_ref(store, "worker", card)
                limited_lab = worker_lab
                if card_id in self._limited_roles:
                    limited_lab, _ = run_ref(store, self._limited_roles[card_id], card)
                with self._lock:
                    pauses = [
                        self._lab_paused_until.get(lab, 0) for lab in {worker_lab, limited_lab}
                    ]
                    paused = max(pauses)
                if paused is not None and paused > time.time():
                    waiting[card_id] = f"{worker_lab} usage limited until {paused}"
                    continue
                if card.get("depends_on"):
                    try:
                        repo_path = store.get_repo(card["repo_id"])["path"]
                    except NotFoundError:
                        reason = "this card's repo is gone, so its dependencies cannot be checked"
                    else:
                        reason = _dependency_wait(store, card, repo_path)
                reason = reason or _lease_wait(card, pending)
                reason = reason or spend_refusal(store, card)
                if reason:
                    waiting[card_id] = reason
                    continue
                self._note_start(store, card_id, worker_lab)
                self._runs.start(card_id, on_finish=self._make_on_finish(card_id))
                pending.append((card["repo_id"], _lease_globs(card), card["id"]))
                started.append(card_id)
                slots -= 1
        finally:
            store.close()

        with self._lock:
            self._running |= set(started)
            # this tick read the queue at line ~522 and then did slow, unlocked I/O (a network PR
            # sweep, per-card store reads) - another thread (a usage-limit retry, a due-retry
            # requeue) may have inserted into the LIVE self._queue since. filtering the live queue
            # for what this tick decided to remove, instead of replacing it with the stale
            # snapshot-derived `remaining`, keeps that insertion instead of silently clobbering it
            gone = set(started) | set(drop)
            self._queue = [card_id for card_id in self._queue if card_id not in gone]
            self._retry_ok -= gone
            self._waiting = waiting

    def _note_start(self, store: Store, card_id: str, worker_lab: str) -> None:
        """what _handle_usage_limit reads back about the run about to start: the credential it
        runs on, and where its own events begin"""
        self._run_profiles[card_id] = (worker_lab, profiles.active_profile(worker_lab) or "default")
        events = store.list_events(card_id)
        self._run_start_seq[card_id] = events[-1]["seq"] if events else -1

    def prepare_run(self, store: Store, card: dict[str, Any]) -> Callable[[Any], None]:
        """the on_finish for a run this board did not start - a manual r, an inbox answer, a
        fallback retry - so a limit, an unreachable api or a conflict there is handled the same
        way. the card leaves this board's queue and timers: it is running now"""
        card_id = card["id"]
        self._note_start(store, card_id, run_ref(store, "worker", card)[0])
        with self._lock:
            self._queue = [queued for queued in self._queue if queued != card_id]
            self._card_retry_at.pop(card_id, None)
            self._waiting.pop(card_id, None)
            self._retry_ok.discard(card_id)
        return self._make_on_finish(card_id)

    def _make_on_finish(self, card_id: str):
        def _on_finish(state: Any) -> None:
            with self._lock:
                self._running.discard(card_id)
            if _refused_for_runtime(state):
                self._handle_runtime_not_ready(card_id)
                return
            self._runtime_attempts.pop(card_id, None)
            reason = getattr(state, "blocked_reason_code", None)
            if reason == "USAGE_LIMIT":
                self._handle_usage_limit(card_id)
            elif reason == "API_UNREACHABLE":
                self._handle_api_unreachable(card_id)
            elif reason == "MERGE_CONFLICT":
                self._handle_merge_conflict(card_id)
            else:
                self._limited_roles.pop(card_id, None)
                self._tick()

        return _on_finish

    def _handle_runtime_not_ready(self, card_id: str) -> None:
        """retries a card refused because Docker or its credential was not ready, after 1, 2 and 5
        minutes. a fourth refusal stays with the operator, whose refusal note says what is missing"""
        attempt = self._runtime_attempts.get(card_id, 0) + 1
        retry_at = None
        store = Store(self._db_path)
        try:
            if attempt > RUNTIME_MAX_RETRIES:
                store.add_comment(
                    card_id,
                    author=_BOARD_AUTHOR,
                    body=f"runtime still not ready after {RUNTIME_MAX_RETRIES} automatic retries "
                    "- left for the operator.",
                )
            else:
                delay_minutes = RUNTIME_BACKOFF_MINUTES[attempt - 1]
                retry_at = time.time() + delay_minutes * 60
                store.append_event(
                    card_id, "runtime_retry", {"attempt": attempt, "retry_at": retry_at}
                )
                store.add_comment(
                    card_id,
                    author=_BOARD_AUTHOR,
                    body=f"runtime not ready - retrying automatically in {delay_minutes} "
                    f"minute(s) (attempt {attempt}/{RUNTIME_MAX_RETRIES}).",
                )
        finally:
            store.close()
        with self._lock:
            if retry_at is None:
                self._runtime_attempts.pop(card_id, None)
            else:
                self._runtime_attempts[card_id] = attempt
                self._card_retry_at[card_id] = retry_at
                self._retry_ok.add(card_id)
        self._tick()

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

        the cross-lab fallback walk runs only while usage_limit_route is "fallback" (the default).
        "attention" leaves the card blocked for the inbox to ask about, and the board still re-runs
        it on its own model once the pause lapses - that is a wait, not a model switch.
        """
        store = Store(self._db_path)
        try:
            card = store.get_card(card_id)
            settings = store.get_settings()
            lab, _ = role_ref(settings, "worker", card)
            lab, profile = self._run_profiles.get(
                card_id, (lab, profiles.active_profile(lab) or "default")
            )
            role = "worker"
            model = None
            current_events = [
                event
                for event in store.list_events(card_id)
                if event["seq"] > self._run_start_seq.get(card_id, -1)
            ]
            for event in reversed(current_events):
                payload = event["payload"]
                if payload.get("lab") and payload.get("profile"):
                    lab, profile = payload["lab"], payload["profile"]
                    model = payload.get("model")
                    role = payload.get("role") or (
                        "reviewer" if event["kind"].startswith("reviewer_") else "worker"
                    )
                    break
            resets_at = _latest_reset(store, lab, profile)
            if resets_at is None:
                for event in reversed(current_events):
                    if event["payload"].get("lab") or event["payload"].get("profile"):
                        continue
                    resets = [
                        (row.get("rate_limit") or row.get("result") or {}).get("resets_at")
                        for row in neutral_events(event)
                    ]
                    resets = [value for value in resets if value is not None]
                    if resets:
                        resets_at = max(resets)
                        break
            resets_at = resets_at or (time.time() + _FALLBACK_PARK_SECONDS)
            auto_switch = store.get_settings().get("auto_switch_profiles") == "on"
            if auto_switch:
                result = profiles.handle_usage_limit(resets_at, lab=lab, name=profile)
            else:
                # a single-profile board must never write a state file just because a run hit
                # its limit alone - same invariant handle_usage_limit itself keeps
                if profiles.has_multiple_profiles(lab) or profiles.state_path().exists():
                    profiles.mark_limited(profile, resets_at, lab=lab)
                result = {"rotated": False, "next_profile": None, "earliest_reset": None}
            store.append_event(
                card_id,
                "profile_limited",
                {
                    "lab": lab,
                    "profile": profile,
                    "role": role,
                    "model": model,
                    "resets_at": resets_at,
                },
            )
            self._limited_roles[card_id] = role
            fallback_selected = False
            pause_until = (
                result["earliest_reset"] or resets_at or (time.time() + _FALLBACK_PARK_SECONDS)
            )
            if result["next_profile"] is None:
                with self._lock:
                    self._lab_paused_until[lab] = pause_until
                # another credential in the same lab is a cheaper way out than another model
                own_lab_free = profiles.has_multiple_profiles(lab) and profiles.next_available(
                    lab=lab
                )
                if usage_limit_route(settings) == "fallback" and not own_lab_free:
                    target = profiles.usable_fallback(
                        settings.get(f"{role}_cross_lab_fallback") or [],
                        lab,
                        skip=lambda target_lab, _profile: self._lab_paused(target_lab),
                    )
                    if target is not None:
                        record_fallback(store, card_id, role, lab, target, pause_until)
                        fallback_selected = True
        finally:
            store.close()

        if result["next_profile"] is not None or fallback_selected:
            with self._lock:
                if card_id not in self._queue and card_id not in self._running:
                    self._queue.insert(0, card_id)
        else:
            fallback = result["earliest_reset"] if result["rotated"] else None
            # the card itself has to rejoin the queue too, not only the board-wide pause clear -
            # left out of the queue, schedule_view's lapsed-pause check had nothing to start once
            # the window rolled over, and this card sat blocked past its own reset forever
            with self._lock:
                self._lab_paused_until[lab] = (
                    fallback or resets_at or (time.time() + _FALLBACK_PARK_SECONDS)
                )
                if card_id not in self._queue and card_id not in self._running:
                    self._queue.append(card_id)
        self._tick()


def _latest_reset(store: Store, lab: str = "anthropic", profile: str | None = None) -> float | None:
    """the latest window belongs to the refused run's lab and credential"""
    for event in store.list_recent_events("rate_limit_event", _RESET_LOOKBACK_EVENTS):
        payload = event["payload"]
        if (payload.get("lab") or "anthropic") != lab:
            continue
        if profile is not None and (payload.get("profile") or "default") != profile:
            continue
        resets = []
        for row in neutral_events(event):
            block = row.get("rate_limit") or row.get("result") or {}
            reset = block.get("resets_at")
            if reset is not None:
                resets.append(reset)
        if resets:
            return max(resets)
    return None


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

    def _existing(self) -> list[BoardScheduler]:
        with self._lock:
            return list(self._schedulers.values())

    def finish_hook(self, card_id: str) -> Callable[[Any], None] | None:
        """RunRegistry calls this for every start that brings no on_finish of its own, so a manual
        run, an inbox answer and a fallback retry get the same block handling as a queued run"""
        store = Store(self._db_path)
        try:
            try:
                card = store.get_card(card_id)
            except NotFoundError:
                return None
            return self.get(card["board_id"]).prepare_run(store, card)
        finally:
            store.close()

    def retry_at(self, store: Store, card: dict[str, Any]) -> float | None:
        """when the board runs `card` again by itself, or None when nothing is scheduled"""
        with self._lock:
            scheduler = self._schedulers.get(card["board_id"])
        return scheduler.scheduled_retry(store, card) if scheduler is not None else None

    def paused_labs(self) -> dict[str, float]:
        """every lab some board has paused, until the latest of those pauses - a limit is
        account-wide, so one board's pause holds for all of them"""
        merged: dict[str, float] = {}
        for scheduler in self._existing():
            for lab, until in scheduler.paused_labs().items():
                merged[lab] = max(until, merged.get(lab, 0))
        return merged

    def process_due(self, now: float | None = None) -> None:
        for scheduler in self._existing():
            scheduler.process_due(now)

    def sweep_all(self) -> None:
        """the checking-PR sweep for every board, off the request thread - see SchedulerTicker"""
        store = Store(self._db_path)
        try:
            for board in store.list_boards():
                _sweep_checking_prs(store, board["id"])
        finally:
            store.close()

    def requeue_lapsed(self, now: float | None = None) -> list[str]:
        """on startup: requeues every USAGE_LIMIT and API_UNREACHABLE card whose reset or retry
        time has passed. the timers that would have fired lived in the last process - measured,
        wowtomate cards f0d85b17 and 14740f6e sat blocked from 09-14 on with nothing scheduled"""
        now = time.time() if now is None else now
        due: dict[str, list[str]] = {}
        store = Store(self._db_path)
        try:
            for board in store.list_boards():
                for card in store.list_cards(board["id"]):
                    if card["status"] in _NEVER_QUEUEABLE or not card.get("repo_id"):
                        continue
                    if _lapsed_retry(store, card, now):
                        due.setdefault(board["id"], []).append(card["id"])
        finally:
            store.close()
        for board_id, card_ids in due.items():
            self.get(board_id).requeue(card_ids)
        return [card_id for card_ids in due.values() for card_id in card_ids]


class SchedulerTicker:
    """one background thread that fires due retries and lapsed pauses, and sweeps checking pull
    requests. before it, both happened only inside schedule_view, which the ui polls for the open
    board alone - a retry on any other board waited for someone to open it"""

    def __init__(
        self,
        registry: SchedulerRegistry,
        *,
        interval: float = 15.0,
        sweep_interval: float = 120.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._registry = registry
        self._interval = interval
        self._sweep_interval = sweep_interval
        self._clock = clock
        self._last_sweep: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None

    def run_once(self) -> None:
        now = self._clock()
        self._registry.process_due(now)
        if self._last_sweep is None or now - self._last_sweep >= self._sweep_interval:
            self._last_sweep = now
            self._registry.sweep_all()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.run_once()
            # a dead ticker silently ends every automatic retry, so one failed pass is kept for
            # a look and the next pass still runs
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"{type(exc).__name__}: {exc}"

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="scheduler-ticker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
