"""a card on a development base is landed by the board; a card on main still waits for a human.

The git side of landing is tested for real in test_integrate.py; here `integrate` is faked, because
what matters is the chain around it - when it runs, what a failure does, what the card ends as.
"""

import subprocess

from smortboard import lifecycle
from smortboard.review.integrate import IntegrateResult
from tests import test_lifecycle as chain

# the chain suite's own fixtures, bound here so pytest finds them for this module too
repo = chain.repo
board = chain.board


def _on_development(store, card_id, repo_path):
    store.set_board_merge_mode(store.get_card(card_id)["board_id"], "free")
    subprocess.run(["git", "-C", str(repo_path), "branch", "development"], check=True)
    store.set_repo_default_branch(store.get_card(card_id)["repo_id"], "development")


def _fake_integrate(monkeypatch, *results):
    calls = []
    answers = iter(results)

    def fake(tree_path, branch, base, title, remote="origin"):
        calls.append(base)
        return next(answers)

    monkeypatch.setattr(lifecycle, "integrate", fake)
    monkeypatch.setattr(lifecycle, "open_release_request", lambda *a, **k: "https://x/pull/9")
    return calls


def test_a_development_card_is_merged_by_the_board_and_accepted(board, repo, monkeypatch):
    store, card_id = board
    _on_development(store, card_id, repo)
    chain._stub_gates(monkeypatch)
    calls = _fake_integrate(monkeypatch, IntegrateResult("abcdef1234567"))

    result = lifecycle.run_card_lifecycle(store, card_id, backend=chain._Backend())
    assert result.phase == "opened"
    assert calls == ["development"]
    card = store.get_card(card_id)
    assert card["status"] == "accepted"
    events = [e for e in store.list_events(card_id) if e["kind"] == "integrated"]
    assert events[-1]["payload"]["sha"] == "abcdef1234567"
    note = card["comments"][-1]["body"]
    assert "merged it into development as abcdef1234" in note
    assert "https://x/pull/9" in note


def test_a_main_card_is_never_merged_by_the_board(board, monkeypatch):
    store, card_id = board
    chain._stub_gates(monkeypatch)
    calls = _fake_integrate(monkeypatch)

    result = lifecycle.run_card_lifecycle(store, card_id, backend=chain._Backend())
    assert result.phase == "opened"
    assert calls == []
    assert store.get_card(card_id)["status"] == "checking"


def test_a_moved_base_is_synced_and_tried_again(board, repo, monkeypatch):
    store, card_id = board
    _on_development(store, card_id, repo)
    chain._stub_gates(monkeypatch)
    calls = _fake_integrate(
        monkeypatch, IntegrateResult(None, "rejected", moved=True), IntegrateResult("feed")
    )
    synced = []
    monkeypatch.setattr(lifecycle, "sync_with_base", lambda *a, **k: synced.append(1))

    lifecycle.run_card_lifecycle(store, card_id, backend=chain._Backend())
    assert calls == ["development", "development"]
    # the hand-over sync, then one before each landing attempt
    assert len(synced) == 3
    assert store.get_card(card_id)["status"] == "accepted"


def test_a_card_that_cannot_land_keeps_its_pull_request_for_the_operator(board, repo, monkeypatch):
    store, card_id = board
    _on_development(store, card_id, repo)
    chain._stub_gates(monkeypatch)
    _fake_integrate(monkeypatch, IntegrateResult(None, "pushing card/x failed"))

    result = lifecycle.run_card_lifecycle(store, card_id, backend=chain._Backend())
    assert result.phase == "opened"
    card = store.get_card(card_id)
    assert card["status"] == "checking"
    assert card["review_flag"]
    assert (
        "could not merge it into development: pushing card/x failed" in card["comments"][-1]["body"]
    )
