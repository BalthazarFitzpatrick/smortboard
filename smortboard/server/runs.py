"""the board's handle on running cards: start one, ask where it is, and nothing else.

A card takes minutes and the http server is single-threaded on purpose (the store's sqlite
connection belongs to the thread that opened it), so a run cannot happen inside the request that
asks for it - the whole board would freeze for the length of the card. Each run gets its own thread
AND ITS OWN STORE, opened on the same database file. Sharing the server's connection across threads
is exactly what sqlite refuses, and doing it anyway is the kind of bug that only appears under two
cards at once.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from smortboard import profiles
from smortboard.actions import with_next
from smortboard.exec.runner import ProcessHandle
from smortboard.lifecycle import BOARD_AUTHOR, LifecycleResult, _stopped, run_card_lifecycle
from smortboard.store.api import Store
from smortboard.store.errors import NotFoundError

# how long a readiness answer is trusted before it is measured again. `docker info` takes seconds
# to answer, and the board asks on every render
READINESS_TTL_SECONDS = 30

# what a card says when the board process died under its run
ORPHANED_NOTE = (
    "the board stopped while this card was running, so the run never finished: no gate ran and no "
    "pull request was opened. its worktree and any commits already fetched back are kept. if "
    "`docker ps` still lists a smortboard container for it, remove that first."
)


# enough of an unexpected error to say what broke, short enough for a card's note
CRASH_ERROR_CHARS = 500


def _block_crashed(store: Store, card_id: str, error: str) -> None:
    """the run raised - the card is blocked CRASH with the error, rather than left showing doing
    with nobody running it (run_ended is still written, so recover_orphaned_runs never sees it)"""
    short = error if len(error) <= CRASH_ERROR_CHARS else error[:CRASH_ERROR_CHARS] + "..."
    store.append_event(card_id, "run_crashed", {"error": short})
    store.update_card(card_id, blocked_reason_code="CRASH", review_flag=True)
    store.add_comment(
        card_id,
        author=BOARD_AUTHOR,
        body=with_next(f"the run stopped on an unexpected error: {short}", "CRASH"),
    )


def _left_mid_run(events: list[dict[str, Any]]) -> bool:
    """whether the card's latest attempt started and never wrote run_ended"""
    starts = [i for i, event in enumerate(events) if event["kind"] == "lifecycle_started"]
    if not starts:
        return False
    return not any(event["kind"] == "run_ended" for event in events[starts[-1] :])


def recover_orphaned_runs(store: Store) -> list[str]:
    """blocks every card a previous board process left mid-run as CRASH, and returns their ids.

    A fresh registry holds no runs, so a card the store still shows working - doing, unflagged, no
    reason code, no run_ended since its last start - lost its board. Left alone it sits in doing
    forever, out of the inbox; CRASH puts it there, and an answer resumes it in its own worktree.
    """
    recovered = []
    for board in store.list_boards():
        for card in store.list_cards(board["id"]):
            if card["status"] != "doing" or card["blocked_reason_code"] or card["review_flag"]:
                continue
            if not _left_mid_run(store.list_events(card["id"])):
                continue
            store.append_event(card["id"], "run_orphaned", {})
            store.update_card(card["id"], blocked_reason_code="CRASH", review_flag=True)
            store.add_comment(
                card["id"], author=BOARD_AUTHOR, body=with_next(ORPHANED_NOTE, "CRASH")
            )
            recovered.append(card["id"])
    return recovered


class RunNotActiveError(RuntimeError):
    """stop() was asked for a card with no run going - nothing to terminate"""


@dataclass
class RunState:
    card_id: str
    phase: str = "preparing"
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    pr_url: str | None = None
    blocked_reason_code: str | None = None
    refusal: str | None = None
    error: str | None = None
    live_steering: bool = True

    @property
    def running(self) -> bool:
        return self.finished_at is None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["running"] = self.running
        data["elapsed_seconds"] = round((self.finished_at or time.time()) - self.started_at, 1)
        return data


class RunRegistry:
    """every card run this board has started, and which of them is still going.

    In memory rather than in the store: a run does not survive the board being restarted, and
    pretending otherwise would leave cards showing "running" forever with no process behind them.
    What the run DID is durable - it is all in the event log.
    """

    def __init__(self, db_path: str | Path, token_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path)
        self._token_path = token_path
        self._runs: dict[str, RunState] = {}
        # notes queued for LIVE delivery: a card's run pops these itself, between turns, once the
        # agent it belongs to finishes its current turn - see runner.run_process
        self._pending: dict[str, list[dict[str, Any]]] = {}
        # the process/container a card's run is inside right now, and which cards a stop was asked
        # for - both keyed by card_id, both read and written from the request thread AND the run's
        # own thread, so both live under the same lock as everything else here
        self._handles: dict[str, ProcessHandle] = {}
        self._stopping: set[str] = set()
        self._lock = threading.Lock()
        # card_id -> on_finish, for every start that brings none - see set_finish_hook
        self._finish_hook: Callable[[str], Callable[[RunState], None] | None] | None = None

    def set_finish_hook(self, hook: Callable[[str], Callable[[RunState], None] | None]) -> None:
        """gives every run started without an on_finish (a manual r, an inbox answer) the one
        the scheduler would have passed, so its USAGE_LIMIT, API_UNREACHABLE or MERGE_CONFLICT is
        handled the same as a queued run's - see SchedulerRegistry.finish_hook"""
        self._finish_hook = hook

    def get(self, card_id: str) -> RunState | None:
        with self._lock:
            return self._runs.get(card_id)

    def active(self) -> list[RunState]:
        with self._lock:
            return [state for state in self._runs.values() if state.running]

    def queue_note(self, card_id: str, comment: dict[str, Any]) -> bool:
        """queues a comment for live delivery if this card's run is currently going.

        returns whether it was queued - the conversation endpoint uses this to answer `delivery`
        as "live" or "next_run". a card with no run, or a run that has already finished, cannot
        accept it live even if the comment was written a second before the run ended.
        """
        with self._lock:
            state = self._runs.get(card_id)
            if state is None or not state.running or not state.live_steering:
                return False
            self._pending.setdefault(card_id, []).append(comment)
            return True

    def pop_pending(self, card_id: str) -> list[dict[str, Any]]:
        """drains the notes queued for one card - called by the runner between turns"""
        with self._lock:
            return self._pending.pop(card_id, [])

    def start(
        self,
        card_id: str,
        runner: Callable[..., Any] | None = None,
        on_finish: Callable[[RunState], None] | None = None,
    ) -> RunState:
        """starts the card, or returns the run already going for it.

        ONE RUN PER CARD. Two agents on one card would race each other in the same worktree, and
        the second `git worktree add` would fail anyway - so this refuses to start a second one
        rather than producing a confusing error a minute later.

        `on_finish`, if given, is called with the finished RunState from the run's own thread once
        it is done - additive for smortboard.scheduler, which uses it to learn a slot freed up
        without polling every card.
        """
        with self._lock:
            existing = self._runs.get(card_id)
            if existing is not None and existing.running:
                return existing
            state = RunState(card_id=card_id)
            self._runs[card_id] = state

        # asked before the thread starts, so the hook sees where this run's events begin. a hook
        # that fails still lets the run start - the state above is already registered as running
        if on_finish is None and self._finish_hook is not None:
            try:
                on_finish = self._finish_hook(card_id)
            except (sqlite3.Error, profiles.ProfileError):
                on_finish = None
        thread = threading.Thread(
            target=self._run, args=(state, runner or run_card_lifecycle, on_finish), daemon=True
        )
        thread.start()
        return state

    def stop(self, card_id: str) -> RunState:
        """terminates whatever this card's run is inside right now: SIGTERM, wait up to 10s, then
        kill - and `docker rm -f` the container too, since killing the docker client does not
        reliably stop the container underneath it.

        Marks the card as stopping BEFORE terminating, so the run's own thread (reading
        `stop_requested` in lifecycle.py) knows a killed process was a deliberate stop rather than a
        crash, and ends the chain at "stopped" instead of interpreting whatever the dead process
        reports. Terminating blocks the request thread up to the 10s ceiling - deliberately: the
        caller gets to know the process is actually gone, not just that a flag was set.
        """
        with self._lock:
            state = self._runs.get(card_id)
            if state is None or not state.running:
                raise RunNotActiveError(f"card {card_id} has no run going")
            self._stopping.add(card_id)
            handle = self._handles.get(card_id)
        if handle is not None:
            handle.terminate()
        return state

    def _is_stopping(self, card_id: str) -> bool:
        with self._lock:
            return card_id in self._stopping

    def _register_handle(self, card_id: str, handle: ProcessHandle) -> None:
        with self._lock:
            self._handles[card_id] = handle

    def _run(
        self,
        state: RunState,
        runner: Callable[..., Any],
        on_finish: Callable[[RunState], None] | None = None,
    ) -> None:
        store = Store(self._db_path)  # this thread's own connection, never the server's
        try:
            from smortboard.labs.registry import get_adapter
            from smortboard.labs.routing import run_ref

            lab, _ = run_ref(store, "worker", store.get_card(state.card_id))
            state.live_steering = get_adapter(lab).capabilities.live_steering
            # keep only the explicit override here; each role resolves its own lab's profile
            result = runner(
                store,
                state.card_id,
                token_path=self._token_path,
                on_phase=lambda phase: setattr(state, "phase", phase),
                pending_notes=lambda: self.pop_pending(state.card_id),
                stop_requested=lambda: self._is_stopping(state.card_id),
                on_process=lambda handle: self._register_handle(state.card_id, handle),
            )
            state.phase = result.phase
            state.pr_url = result.pr_url
            state.blocked_reason_code = result.blocked_reason_code
            state.refusal = result.refusal
        except Exception as exc:  # noqa: BLE001
            # a thread that dies silently leaves the card showing a phase it left long ago, so
            # every failure becomes visible state - on the card too, not only in memory
            state.error = f"{type(exc).__name__}: {exc}"
            # a deliberate stop that broke a step on its way down is still a stop
            stopping = self._is_stopping(state.card_id)
            state.phase = "stopped" if stopping else "blocked"
            state.blocked_reason_code = None if stopping else "CRASH"
            with contextlib.suppress(sqlite3.Error, NotFoundError):
                if stopping:
                    _stopped(store, LifecycleResult(card_id=state.card_id, phase="stopped"))
                else:
                    _block_crashed(store, state.card_id, state.error)
        finally:
            # the mark a restarted board reads to tell a finished run from one it orphaned - written
            # BEFORE finished_at flips running off, so nothing ever sees a finished run without it
            with contextlib.suppress(sqlite3.Error):
                store.append_event(state.card_id, "run_ended", {"phase": state.phase})
            state.finished_at = time.time()
            store.close()
            with self._lock:
                self._handles.pop(state.card_id, None)
                self._stopping.discard(state.card_id)
        if on_finish is not None:
            on_finish(state)


class Readiness:
    """whether this board can run a card at all, answered before anyone presses the button.

    The three things a run needs are all outside the board: Docker, a card credential, and gh.
    Discovering one is missing after a card has been queued is a worse experience than being told
    up front, so the interface asks this and says what to fix.
    """

    def __init__(self, token_path: str | Path | None = None) -> None:
        self._token_path = token_path
        self._answer: dict[str, Any] | None = None
        self._measured_at = 0.0

    def check(self) -> dict[str, Any]:
        if self._answer is not None and time.time() - self._measured_at < READINESS_TTL_SECONDS:
            return self._answer

        from smortboard.exec.backends import (
            card_image,
            card_image_available,
            card_token_available,
            docker_available,
        )

        docker = docker_available()
        # the active profile's credential, the one a run will use - not the legacy card_token,
        # which is gone once the default profile is removed
        token = (
            card_token_available(profiles.token_path_for_run(self._token_path))
            if profiles.active_profile() is not None or self._token_path is not None
            else False
        )
        if not token:
            for lab in profiles.configured_labs():
                if lab == "anthropic":
                    continue
                try:
                    profiles.read_active_token(lab)
                except profiles.ProfileError:
                    continue
                token = True
                break
        # only checked when docker answers - "the image is missing" is not useful news when the
        # thing that would hold the image is not running
        image = docker and card_image_available()
        missing = []
        if not docker:
            missing.append("docker is not running. every card runs in its own container.")
        elif not image:
            missing.append(
                f"no card image. build it once: "
                f"docker build -f docker/card.Dockerfile -t {card_image()} ."
            )
        if not token:
            missing.append(
                "no card credential. add a credential file in the profiles panel. "
                "for claude, generate the token with `claude setup-token`."
            )
        self._answer = {
            "ready": not missing,
            "docker": docker,
            "image": image,
            "token": token,
            "missing": missing,
        }
        self._measured_at = time.time()
        return self._answer
