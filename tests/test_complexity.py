import json
import sqlite3

import pytest

from smortboard.consolidate import _fold
from smortboard.orchestrator import run_orchestrator_turn
from smortboard.store import Store
from smortboard.store.errors import UnknownFieldError
from smortboard.store.schema import _MIGRATIONS
from smortboard.telemetry import estimate_complexity


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "board.sqlite3") as s:
        yield s


# -- a card carries no complexity of its own (migration 33) ------------------


def test_a_card_has_no_complexity_field(store):
    board = store.create_board("Phase 1")
    card = store.create_card(board["id"], None, "a card")
    assert "complexity" not in card
    columns = [row[1] for row in store._conn.execute("PRAGMA table_info(cards)")]
    assert "complexity" not in columns


def test_complexity_is_neither_createable_nor_writable(store):
    board = store.create_board("Phase 1")
    with pytest.raises(TypeError):
        store.create_card(board["id"], None, "card", complexity=2)
    card = store.create_card(board["id"], None, "card")
    with pytest.raises(UnknownFieldError):
        store.update_card(card["id"], complexity=2)


def test_migration_33_drops_the_column_and_keeps_every_card(tmp_path):
    db_path = tmp_path / "v32.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    for script in _MIGRATIONS[:32]:
        conn.executescript(script)
    conn.execute("PRAGMA user_version = 32")
    conn.execute(
        "INSERT INTO boards (id, name, position, created_at) VALUES ('b1','Phase 1',0,'t')"
    )
    conn.execute(
        "INSERT INTO cards (id, board_id, title, status, position, created_at, updated_at, "
        "complexity) VALUES ('c1','b1','rated card','todo',0,'t','t',3)"
    )
    conn.commit()
    conn.close()

    with Store(db_path) as store:
        card = store.get_card("c1")
        assert card["title"] == "rated card"
        assert "complexity" not in card


def test_an_old_export_that_carries_complexity_still_restores(store):
    board = store.create_board("Phase 1")
    card = store.create_card(board["id"], None, "card")
    store.delete_card(card["id"])
    backup = store.list_card_backups(board["id"])[0]
    payload = json.loads(
        store._conn.execute(
            "SELECT payload_json FROM card_backups WHERE id = ?", (backup["id"],)
        ).fetchone()[0]
    )
    payload["card"]["complexity"] = 2
    store._conn.execute(
        "UPDATE card_backups SET payload_json = ? WHERE id = ?", (json.dumps(payload), backup["id"])
    )
    store._conn.commit()
    restored = store.restore_card(backup["id"])
    assert restored["title"] == "card"
    assert "complexity" not in restored


# -- consolidation and mission control no longer carry it --------------------


def test_a_folded_card_has_no_complexity(store):
    board = store.create_board("Phase 1")
    repo = store.create_repo(board["id"], "repo", "/repo", "main")
    first = store.create_card(board["id"], repo["id"], "first card")
    second = store.create_card(board["id"], repo["id"], "second card")
    merged_line = _fold(store, board["id"], [first, second], {}, "merged")
    assert "merged" in merged_line
    merged = next(c for c in store.list_cards(board["id"]) if c["title"] == "merged")
    assert "complexity" not in merged


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


def test_estimate_complexity_is_independent_of_model():
    assert estimate_complexity(2, 2, 1, False, "sonnet") == 1
    assert estimate_complexity(2, 2, 1, False, "opus") == 1


def test_estimate_complexity_broad_lease_does_not_add_a_model_penalty():
    assert estimate_complexity(3, 3, 1, True, "opus") == 2


def test_estimate_complexity_no_model_no_leases():
    assert estimate_complexity(0, 0, 0, False, None) == 1


# -- orchestrator turn -----------------------------------------------------


def test_orchestrator_turn_ignores_a_stray_complexity_from_the_model(store):
    board = store.create_board("dev")
    store.create_repo(board["id"], "repo", "/repo", "main")
    payload = {
        "reply": "ok",
        "plan": "p",
        "cards": [
            {
                "title": "a",
                "description": "",
                "repo": "repo",
                "criteria": [],
                "tasks": [],
                "leases": ["a.py"],
                "depends_on": [],
                "complexity": "high",
            }
        ],
    }

    def run(prompt, model, budget_usd):
        return json.dumps(payload)

    run_orchestrator_turn(store, board["id"], "go", runner=run)
    (card,) = store.list_cards(board["id"])
    assert card["title"] == "a"
    assert "complexity" not in card
