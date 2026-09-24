"""operator-triggered landings, each on its own thread and sqlite connection"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from smortboard.exec.worktrees import default_branch, existing_worktree
from smortboard.lifecycle import LifecycleResult, _sync_and_retest
from smortboard.review.decide import DecisionRefused, accept_card
from smortboard.review.integrate import open_release_request
from smortboard.review.land_card import already_landed, land_card, retarget_children
from smortboard.review.merge_request import PROTECTED_BRANCHES
from smortboard.review.outcome import card_outcome
from smortboard.review.stacks import integrated_event
from smortboard.store.api import Store


def needs_landing(store, card):
    if card["status"] != "checking" or not card.get("repo_id"):
        return False
    repo = store.get_repo(card["repo_id"])
    base = default_branch(repo)
    return base not in PROTECTED_BRANCHES and not integrated_event(store, card["id"], base)


def perform_landing(store, card_id):
    card = store.get_card(card_id)
    if card["status"] != "checking" or card["blocked_reason_code"]:
        raise DecisionRefused("only an unblocked checking card can land")
    repo = store.get_repo(card["repo_id"])
    base = default_branch(repo)
    url = card_outcome(store, card_id)["pr_url"]
    if already_landed(store, card, repo, base, url):
        accepted = accept_card(store, card_id)
        store.add_comment(
            card_id,
            author="smortboard",
            body=f"already merged into {base}; accepted without another merge.",
        )
        if base not in PROTECTED_BRANCHES:
            open_release_request(repo["path"], base)
        retarget_children(store, card, repo, base)
        return accepted
    tree = existing_worktree(repo["path"], card_id)
    state = LifecycleResult(card_id=card_id, phase="opening")

    def sync_and_test(*args):
        # a sweep may have changed the branch since its last test gate
        return _sync_and_retest(*args, always_test=True)

    land_card(store, state, card, tree, repo, base, url, sync=sync_and_test)
    card = store.get_card(card_id)
    if card["status"] != "accepted":
        raise DecisionRefused("landing failed; the card and its pull request remain in checking.")
    return card


@dataclass
class LandingState:
    card_id: str
    running: bool = True
    error: str | None = None


class LandingRegistry:
    def __init__(self, db_path):
        self._db_path = db_path
        self._states = {}
        self._lock = threading.Lock()

    def get(self, card_id):
        with self._lock:
            return self._states.get(card_id)

    def start(self, card_id):
        with self._lock:
            previous = self._states.get(card_id)
            if previous and previous.running:
                raise DecisionRefused("this card is already landing")
            state = LandingState(card_id)
            self._states[card_id] = state
        threading.Thread(target=self._run, args=(state,), daemon=True).start()
        return state

    def _run(self, state):
        try:
            with Store(self._db_path) as store:
                try:
                    perform_landing(store, state.card_id)
                except Exception as exc:
                    state.error = str(exc)
                    store.update_card(state.card_id, review_flag=True)
                    store.add_comment(
                        state.card_id,
                        author="smortboard",
                        body=f"could not complete acceptance: {exc}",
                    )
        finally:
            with self._lock:
                state.running = False
