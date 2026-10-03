"""mission control publishes a valid dependency graph as one change"""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from smortboard.lifecycle import build_card_prompt
from smortboard.orchestrator import (
    _PLANNING_MODE_RULES,
    CARD_TEXT_RULES,
    build_board_snapshot,
    run_orchestrator_turn,
)
from smortboard.store.api import Store


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "board.db") as value:
        yield value


def _propose(store, board, specs):
    return run_orchestrator_turn(
        store,
        board["id"],
        "create agreed cards",
        runner=lambda *args: json.dumps({"reply": "done", "plan": "new plan", "cards": specs}),
        mode="manage",
    )


@pytest.mark.parametrize("reference_kind", ["id", "short_id", "title"])
def test_existing_dependencies_resolve_without_losing_edges(store, reference_kind):
    board = store.create_board("board")
    parent = store.create_card(board["id"], None, "existing")
    reference = {"id": parent["id"], "short_id": parent["id"][:8], "title": parent["title"]}[
        reference_kind
    ]
    result = _propose(store, board, [{"title": "child", "depends_on": [reference]}])
    assert result.error is None
    child = next(card for card in store.list_cards(board["id"]) if card["title"] == "child")
    assert child["depends_on"] == [parent["id"]]


def test_forward_references_resolve_in_one_batch(store):
    board = store.create_board("board")
    result = _propose(
        store,
        board,
        [{"title": "child", "depends_on": ["parent"]}, {"title": "parent"}],
    )
    assert result.error is None
    cards = {card["title"]: card for card in store.list_cards(board["id"])}
    assert cards["child"]["depends_on"] == [cards["parent"]["id"]]


@pytest.mark.parametrize(
    "specs,error",
    [
        (
            [{"title": "valid"}, {"title": "invalid", "depends_on": ["missing"]}],
            "missing dependency",
        ),
        (
            [{"title": "a", "depends_on": ["b"]}, {"title": "b", "depends_on": ["a"]}],
            "dependency cycle",
        ),
        ([{"title": "self", "depends_on": ["self"]}], "dependency cycle"),
        (
            [{"title": "same"}, {"title": "same"}, {"title": "child", "depends_on": ["same"]}],
            "ambiguous dependency",
        ),
    ],
)
def test_invalid_graph_creates_nothing_and_preserves_plan(store, specs, error):
    board = store.create_board("board")
    store.set_plan(board["id"], "previous decisions")
    result = _propose(store, board, specs)
    assert error in result.error
    assert store.list_cards(board["id"]) == []
    assert store.get_plan(board["id"]) == "previous decisions"
    assert error in store.list_orchestrator_messages(board["id"])[-1]["body"]


def test_existing_and_new_same_title_are_ambiguous(store):
    board = store.create_board("board")
    parent = store.create_card(board["id"], None, "same")
    result = _propose(store, board, [{"title": "same"}, {"title": "child", "depends_on": ["same"]}])
    assert "ambiguous dependency" in result.error
    assert [card["id"] for card in store.list_cards(board["id"])] == [parent["id"]]


def test_colliding_short_ids_are_refused_but_exact_ids_work(store, monkeypatch):
    board = store.create_board("board")
    ids = iter(["12345678-1111-1111-1111-111111111111", "12345678-2222-2222-2222-222222222222"])
    with monkeypatch.context() as patch:
        patch.setattr("smortboard.store.api._new_id", lambda: next(ids))
        first = store.create_card(board["id"], None, "one")
        store.create_card(board["id"], None, "two")
    result = _propose(store, board, [{"title": "child", "depends_on": ["12345678"]}])
    assert "ambiguous dependency" in result.error
    assert len(store.list_cards(board["id"])) == 2
    assert _propose(store, board, [{"title": "child", "depends_on": [first["id"]]}]).error is None


def test_direct_dependency_insert_rejects_cycle_without_changing_edges(store):
    board = store.create_board("board")
    a, b, c = [store.create_card(board["id"], None, title) for title in ("a", "b", "c")]
    store.add_dependency(a["id"], b["id"])
    store.add_dependency(b["id"], c["id"])
    with pytest.raises(ValueError, match="dependency cycle"):
        store.add_dependency(c["id"], a["id"])
    assert store.get_dependencies(c["id"]) == []
    assert store.get_dependencies(a["id"]) == [b["id"]]


def test_failed_second_insert_rolls_back_first_card_and_edge(store):
    board = store.create_board("board")
    parent = store.create_card(board["id"], None, "existing")
    with pytest.raises(ValueError):
        store.create_cards_with_dependencies(
            board["id"],
            [
                {"repo_id": None, "title": "valid", "depends_on": [parent["id"]]},
                {"repo_id": None, "title": "invalid", "leases": ["/outside"]},
            ],
        )
    assert [card["id"] for card in store.list_cards(board["id"])] == [parent["id"]]
    assert store.get_dependents(parent["id"]) == []


def test_concurrent_dependency_updates_cannot_publish_a_cycle(store, tmp_path):
    board = store.create_board("board")
    a = store.create_card(board["id"], None, "a")
    b = store.create_card(board["id"], None, "b")
    ready = Barrier(2)

    def add_edge(card_id, dependency_id):
        with Store(tmp_path / "board.db") as writer:
            ready.wait(timeout=10)
            try:
                writer.add_dependency(card_id, dependency_id)
            except ValueError:
                return False
            return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(add_edge, a["id"], b["id"])
        second = executor.submit(add_edge, b["id"], a["id"])
        assert sorted([first.result(timeout=15), second.result(timeout=15)]) == [False, True]
    assert len(store.get_dependencies(a["id"])) + len(store.get_dependencies(b["id"])) == 1


def test_other_connection_cannot_observe_partial_batch(store, tmp_path, monkeypatch):
    board = store.create_board("board")
    insert = store._insert_card
    observed = []
    with Store(tmp_path / "board.db") as observer:

        def inspect_insert(*args, **kwargs):
            card = insert(*args, **kwargs)
            observed.append(observer.list_cards(board["id"]))
            return card

        monkeypatch.setattr(store, "_insert_card", inspect_insert)
        created = store.create_cards_with_dependencies(
            board["id"],
            [
                {"repo_id": None, "title": "child", "depends_on": ["parent"]},
                {"repo_id": None, "title": "parent"},
            ],
        )
        assert observed == [[], []]
        assert [card["title"] for card in created] == ["child", "parent"]
        child = next(card for card in observer.list_cards(board["id"]) if card["title"] == "child")
        assert child["depends_on"] == [created[1]["id"]]


def test_plan_decisions_survive_card_creation_and_worker_prompt(store):
    board = store.create_board("board")
    criteria = "retry only 429 responses; preserve idempotency keys"
    task = "use existing client; never introduce a second transport"
    result = _propose(
        store, board, [{"title": "retry calls", "criteria": [criteria], "tasks": [task]}]
    )
    assert result.error is None
    prompt = build_card_prompt(store.list_cards(board["id"])[0])
    assert criteria in prompt and task in prompt
    assert "self-contained" in CARD_TEXT_RULES
    assert "workers do not receive this conversation or board plan" in CARD_TEXT_RULES
    assert '"create: <title>"' in _PLANNING_MODE_RULES
    assert '"update:' not in _PLANNING_MODE_RULES and '"delete:' not in _PLANNING_MODE_RULES


def test_snapshot_bounds_history_but_preserves_active_dependency_chain_and_plan(store):
    board = store.create_board("board")
    older = store.create_card(board["id"], None, "old root", status="accepted")
    parent = store.create_card(
        board["id"], None, "old parent", status="accepted", depends_on=[older["id"]]
    )
    for i in range(45):
        store.create_card(board["id"], None, f"finished {i}", status="accepted")
    active = store.create_card(board["id"], None, "active", depends_on=[parent["id"]])
    plan = "essential decision; " * 1000
    store.set_plan(board["id"], plan)
    snapshot = build_board_snapshot(store, board["id"])
    assert len(snapshot["cards"]) == 43
    assert snapshot["omitted_terminal_cards"] == 5
    assert {older["id"][:8], parent["id"][:8], active["id"][:8]} <= {
        card["id"] for card in snapshot["cards"]
    }
    assert snapshot["plan"] == plan
    assert snapshot["plan_needs_compaction"] is True
