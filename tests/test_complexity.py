import json
import sqlite3

import pytest

from smortboard.consolidate import _fold
from smortboard.orchestrator import _clean_complexity, run_orchestrator_turn
from smortboard.store import Store
from smortboard.store.schema import _MIGRATIONS
from smortboard.telemetry import estimate_complexity


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "board.sqlite3") as s:
        yield s


# -- migration 19 -----------------------------------------------------------


def test_migration_19_applies_on_a_fresh_db(store):
    board = store.create_board("Phase 1")
    card = store.create_card(board["id"], None, "a card")
    assert card["complexity"] is None


def test_migration_19_applies_from_a_db_without_it(tmp_path):
    db_path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    for script in _MIGRATIONS[:18]:
        conn.executescript(script)
    conn.execute("PRAGMA user_version = 18")
    conn.execute(
        "INSERT INTO boards (id, name, position, created_at) VALUES ('b1','Phase 1',0,'t')"
    )
    conn.execute(
        "INSERT INTO cards (id, board_id, title, status, position, created_at, updated_at) "
        "VALUES ('c1','b1','old card','todo',0,'t','t')"
    )
    conn.commit()
    conn.close()

    with Store(db_path) as store:
        card = store.get_card("c1")
        assert card["title"] == "old card"
        assert card["complexity"] is None
        updated = store.update_card("c1", complexity=2)
        assert updated["complexity"] == 2


# -- validation ---------------------------------------------------------


def test_create_card_accepts_valid_complexity(store):
    board = store.create_board("Phase 1")
    for level in (1, 2, 3, None):
        card = store.create_card(board["id"], None, "card", complexity=level)
        assert card["complexity"] == level


def test_create_card_rejects_invalid_complexity(store):
    board = store.create_board("Phase 1")
    with pytest.raises(ValueError):
        store.create_card(board["id"], None, "card", complexity=4)


def test_update_card_rejects_invalid_complexity(store):
    board = store.create_board("Phase 1")
    card = store.create_card(board["id"], None, "card")
    with pytest.raises(ValueError):
        store.update_card(card["id"], complexity=0)


def test_update_card_accepts_valid_complexity(store):
    board = store.create_board("Phase 1")
    card = store.create_card(board["id"], None, "card")
    updated = store.update_card(card["id"], complexity=3)
    assert updated["complexity"] == 3
    cleared = store.update_card(card["id"], complexity=None)
    assert cleared["complexity"] is None


# -- orchestrator mapping -------------------------------------------------


@pytest.mark.parametrize(
    "word,level", [("low", 1), ("medium", 2), ("high", 3), ("nonsense", None), (None, None)]
)
def test_clean_complexity_maps_words_to_levels(word, level):
    assert _clean_complexity(word) == level


def test_fold_keeps_the_highest_member_complexity(store):
    board = store.create_board("Phase 1")
    repo = store.create_repo(board["id"], "repo", "/repo", "main")
    low = store.create_card(board["id"], repo["id"], "low card", complexity=1)
    high = store.create_card(board["id"], repo["id"], "high card", complexity=3)
    merged_line = _fold(store, board["id"], [low, high], {}, "merged")
    assert "merged" in merged_line
    merged = next(c for c in store.list_cards(board["id"]) if c["title"] == "merged")
    assert merged["complexity"] == 3


# -- estimate_complexity ---------------------------------------------------


def test_estimate_complexity_low_for_a_tiny_card():
    assert estimate_complexity(1, 1, 0, False, "sonnet") == 1
    assert estimate_complexity(2, 2, 1, False, "sonnet") == 1


def test_estimate_complexity_medium_for_a_moderate_card():
    # three criteria, three tasks, a narrow lease - the typical terse card
    assert estimate_complexity(3, 3, 1, False, "sonnet") == 2


def test_estimate_complexity_a_broad_lease_pushes_a_bigger_card_to_high():
    assert estimate_complexity(3, 3, 1, True, "sonnet") == 2
    assert estimate_complexity(4, 4, 1, True, "sonnet") == 3


def test_estimate_complexity_opus_raises_the_estimate():
    assert estimate_complexity(2, 2, 1, False, "sonnet") == 1
    assert estimate_complexity(2, 2, 1, False, "opus") == 2


def test_estimate_complexity_opus_plus_broad_lease_is_high():
    assert estimate_complexity(3, 3, 1, True, "opus") == 3


def test_estimate_complexity_no_model_no_leases():
    assert estimate_complexity(0, 0, 0, False, None) == 1


# -- orchestrator turn -> stored complexity -------------------------------


def test_orchestrator_turn_maps_complexity_words_to_levels(store):
    board = store.create_board("dev")
    store.create_repo(board["id"], "repo", "/repo", "main")
    payload = {
        "reply": "ok",
        "plan": "p",
        "cards": [
            {
                "title": t,
                "description": "",
                "repo": "repo",
                "criteria": [],
                "tasks": [],
                "leases": [f"{t}.py"],
                "depends_on": [],
                "complexity": word,
            }
            for t, word in (("a", "low"), ("b", "medium"), ("c", "high"))
        ],
    }

    def run(prompt, model, budget_usd):
        return json.dumps(payload)

    run_orchestrator_turn(store, board["id"], "go", runner=run)
    cards = {c["title"]: c["complexity"] for c in store.list_cards(board["id"])}
    assert cards == {"a": 1, "b": 2, "c": 3}
