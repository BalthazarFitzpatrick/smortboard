"""merge mode persistence, legacy defaults and HTTP validation"""

import sqlite3

import pytest

from smortboard.store import Store, schema
from tests import test_server

running_server = test_server.running_server
request = test_server._request


def test_existing_boards_require_review_after_migration(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        for version, script in enumerate(schema._MIGRATIONS[:20], 1):
            conn.executescript(script)
            conn.execute(f"PRAGMA user_version = {version}")
        conn.execute(
            "INSERT INTO boards (id, name, position, created_at) VALUES (?, ?, ?, ?)",
            ("legacy", "existing", 0, "2026-09-18"),
        )
    with Store(path) as store:
        assert store.get_board("legacy")["merge_mode"] is None
        assert store.board_merges_freely("legacy") is False
    with Store(path) as store:
        assert store.board_merges_freely("legacy") is False


@pytest.mark.parametrize("value", [None, "review", "free"])
def test_modes_round_trip_and_stay_per_board(tmp_path, value):
    path = tmp_path / "board.db"
    with Store(path) as store:
        first = store.create_board("first")["id"]
        second = store.create_board("second")["id"]
        assert store.set_board_merge_mode(first, value)["merge_mode"] == value
        assert store.board_merges_freely(first) == (value == "free")
        assert store.board_merges_freely(second) is False
    with Store(path) as store:
        assert store.get_board(first)["merge_mode"] == value
        assert store.list_boards()[0]["merge_mode"] == value


@pytest.mark.parametrize("value", ["auto", "FREE", "", True, 1, [], {}])
def test_invalid_modes_do_not_change_board(tmp_path, value):
    with Store(tmp_path / "board.db") as store:
        board = store.create_board("board")["id"]
        with pytest.raises(ValueError):
            store.set_board_merge_mode(board, value)
        assert store.board_merges_freely(board) is False


def test_http_mode_patch_and_validation(running_server):
    status, board = request(f"{running_server}/api/boards", "POST", {"name": "board"})
    assert status == 201
    endpoint = f"{running_server}/api/boards/{board['id']}"
    for value in ("free", "review", None):
        status, changed = request(endpoint, "PATCH", {"merge_mode": value})
        assert status == 200
        assert changed["merge_mode"] == value
    for value in ("auto", True, [], {}):
        status, error = request(endpoint, "PATCH", {"merge_mode": value})
        assert status == 400
        assert "merge_mode" in error["error"]
    status, boards = request(f"{running_server}/api/boards")
    assert status == 200
    assert boards[0]["merge_mode"] is None


def test_http_lease_mode_patch_and_validation(running_server):
    status, board = request(f"{running_server}/api/boards", "POST", {"name": "board"})
    assert status == 201
    endpoint = f"{running_server}/api/boards/{board['id']}"
    for value in ("soft", "strict", None):
        status, changed = request(endpoint, "PATCH", {"lease_mode": value})
        assert status == 200
        assert changed["lease_mode"] == value
    status, error = request(endpoint, "PATCH", {"lease_mode": "loose"})
    assert status == 400
    assert "lease_mode" in error["error"]
