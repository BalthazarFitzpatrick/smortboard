"""every open pull request across every board, in merge order - the inbox for what is left to land.

GET /api/pulls (server/app.py) is the only caller. Reuses digest._pull_requests for the gathering
and the dependencies-first sort, dropping its `since` window: every open pull request matters here,
not just ones opened in a recent window. Live state comes from GitHub through
review.merge_request._gh, the same allowlisted entry point every other gh call in this codebase
goes through - `pr view` is already on that allowlist, so nothing here widens it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from smortboard.digest import _pull_requests
from smortboard.review.merge_request import _gh
from smortboard.store.api import Store

# GET /api/pulls is polled by the panel; the same ttl scheduler.py's own gh cache uses - one
# `gh pr view` per open pull request per minute, not per panel refresh
_PR_STATE_TTL_SECONDS = 60
_pr_state_cache: dict[str, tuple[float, dict[str, Any]]] = {}

# a card counts as carrying an open pull request from checking onward - accepted keeps it on the
# list too, since protected-base cards may be accepted before their pull request is merged
_OPEN_STATUSES = ("checking", "accepted")


def _live_state(repo_path: str | Path, url: str) -> dict[str, Any]:
    """gh's live view of one pull request, cached by url - never raises, an unreachable gh just
    reports unknown state rather than losing the row"""
    now = time.time()
    cached = _pr_state_cache.get(url)
    if cached is not None and now - cached[0] < _PR_STATE_TTL_SECONDS:
        return cached[1]
    if shutil.which("gh") is None:
        return {"state": None, "mergeable": None}
    # a repo folder that is gone, or a gh that hangs, raises before any exit code exists
    try:
        result = _gh(["pr", "view", url, "--json", "state,mergeable,url"], cwd=repo_path)
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is None or result.returncode != 0:
        state = {"state": None, "mergeable": None}
    else:
        try:
            data = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            data = {}
        state = {
            "state": (data.get("state") or "").lower() or None,
            "mergeable": (data.get("mergeable") or "").lower() or None,
        }
    _pr_state_cache[url] = (now, state)
    return state


def _dependency_unmerged(
    cards_by_id: dict[str, dict[str, Any]],
    live_by_card: dict[str, dict[str, Any]],
    card: dict[str, Any],
) -> bool:
    """whether any dependency of this card has not merged yet on GitHub - a deleted dependency
    blocks nothing that still exists, the same rule scheduler._dependency_wait applies"""
    for dep_id in card.get("depends_on") or []:
        dep = cards_by_id.get(dep_id)
        if dep is None:
            continue
        if dep["status"] != "accepted":
            return True
        dep_state = live_by_card.get(dep_id)
        if dep_state is None or dep_state.get("state") != "merged":
            return True
    return False


def open_pull_requests(store: Store) -> list[dict[str, Any]]:
    """one row per open pull request across every board, in merge order. a PR gh reports merged is
    left out - the board's part in it is done once that is true"""
    boards = {b["id"]: b["name"] for b in store.list_boards()}
    all_cards: list[dict[str, Any]] = []
    for board_id in boards:
        all_cards.extend(store.list_cards(board_id))
    cards_by_id = {c["id"]: c for c in all_cards}

    open_cards = [c for c in all_cards if c["status"] in _OPEN_STATUSES]
    ordered = _pull_requests(store, open_cards, since=0.0)  # gathering and sort, reused as-is

    # one gh call per pull request, however many rows or dependency checks reference its url
    live_by_card: dict[str, dict[str, Any]] = {}
    for row in ordered:
        card = cards_by_id[row["card_id"]]
        repo = store.get_repo(card["repo_id"]) if card.get("repo_id") else None
        live_by_card[row["card_id"]] = (
            _live_state(repo["path"], row["url"]) if repo else {"state": None, "mergeable": None}
        )

    rows = []
    for row in ordered:
        card = cards_by_id[row["card_id"]]
        state = live_by_card[row["card_id"]]
        if state.get("state") == "merged":
            continue
        rows.append(
            {
                "card_id": card["id"],
                "board_id": card["board_id"],
                "board_name": boards.get(card["board_id"], ""),
                "title": card["title"],
                "url": row["url"],
                "state": state.get("state"),
                "mergeable": state.get("mergeable"),
                "depends_on": list(card.get("depends_on") or []),
                "conflicting": state.get("mergeable") == "conflicting",
                "dependency_unmerged": _dependency_unmerged(cards_by_id, live_by_card, card),
            }
        )
    for index, row in enumerate(rows, start=1):
        row["position"] = index
    return rows
