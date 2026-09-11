"""the board's handle on running cards: start one, ask where it is, and nothing else.

A card takes minutes and the http server is single-threaded on purpose (the store's sqlite
connection belongs to the thread that opened it), so a run cannot happen inside the request that
asks for it - the whole board would freeze for the length of the card. Each run gets its own thread
AND ITS OWN STORE, opened on the same database file. Sharing the server's connection across threads
is exactly what sqlite refuses, and doing it anyway is the kind of bug that only appears under two
cards at once.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from smortboard.lifecycle import run_card_lifecycle
from smortboard.store.api import Store

# how long a readiness answer is trusted before it is measured again. `docker info` takes seconds
# to answer, and the board asks on every render
READINESS_TTL_SECONDS = 30


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
        self._lock = threading.Lock()

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
            if state is None or not state.running:
                return False
            self._pending.setdefault(card_id, []).append(comment)
            return True

    def pop_pending(self, card_id: str) -> list[dict[str, Any]]:
        """drains the notes queued for one card - called by the runner between turns"""
        with self._lock:
            return self._pending.pop(card_id, [])

    def start(self, card_id: str, runner: Callable[..., Any] | None = None) -> RunState:
        """starts the card, or returns the run already going for it.

        ONE RUN PER CARD. Two agents on one card would race each other in the same worktree, and
        the second `git worktree add` would fail anyway - so this refuses to start a second one
        rather than producing a confusing error a minute later.
        """
        with self._lock:
            existing = self._runs.get(card_id)
            if existing is not None and existing.running:
                return existing
            state = RunState(card_id=card_id)
            self._runs[card_id] = state

        thread = threading.Thread(
            target=self._run, args=(state, runner or run_card_lifecycle), daemon=True
        )
        thread.start()
        return state

    def _run(self, state: RunState, runner: Callable[..., Any]) -> None:
        store = Store(self._db_path)  # this thread's own connection, never the server's
        try:
            result = runner(
                store,
                state.card_id,
                token_path=self._token_path,
                on_phase=lambda phase: setattr(state, "phase", phase),
                pending_notes=lambda: self.pop_pending(state.card_id),
            )
            state.phase = result.phase
            state.pr_url = result.pr_url
            state.blocked_reason_code = result.blocked_reason_code
            state.refusal = result.refusal
        except Exception as exc:  # noqa: BLE001
            # a thread that dies silently leaves the card showing a phase it left long ago, so
            # every failure becomes visible state rather than a traceback nobody reads
            state.phase = "refused"
            state.error = f"{type(exc).__name__}: {exc}"
        finally:
            state.finished_at = time.time()
            store.close()


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
        token = card_token_available(self._token_path)
        # only checked when docker answers - "the image is missing" is not useful news when the
        # thing that would hold the image is not running
        image = docker and card_image_available()
        missing = []
        if not docker:
            missing.append("Docker is not running. Every card runs in its own container.")
        elif not image:
            missing.append(
                f"No card image. Build it once: "
                f"docker build -f docker/card.Dockerfile -t {card_image()} ."
            )
        if not token:
            missing.append(
                "No card credential. Run `claude setup-token` and store it as "
                "smortboard-card-token."
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
