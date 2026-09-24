"""merge mode persistence, legacy defaults, the global gates and HTTP validation"""

import json
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
        store.set_setting("allow_free_merge", "on")
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


FREE_OFF = "free merge is off - turn it on in settings (o) first"
SOFT_OFF = "soft leases are off - turn them on in settings (o) first"


def test_free_is_refused_while_the_gate_is_off(tmp_path):
    with Store(tmp_path / "board.db") as store:
        board = store.create_board("board")["id"]
        with pytest.raises(ValueError) as refused:
            store.set_board_merge_mode(board, "free")
        assert str(refused.value) == FREE_OFF
        assert store.get_board(board)["merge_mode"] is None
        # review and null never need the gate
        assert store.set_board_merge_mode(board, "review")["merge_mode"] == "review"
        assert store.set_board_merge_mode(board, None)["merge_mode"] is None
        with pytest.raises(ValueError, match="allow_free_merge"):
            store.set_setting("allow_free_merge", "yes")


def test_turning_free_merge_off_resets_every_board(tmp_path):
    with Store(tmp_path / "board.db") as store:
        free, review = store.create_board("free")["id"], store.create_board("review")["id"]
        store.set_setting("allow_free_merge", "on")
        store.set_board_merge_mode(free, "free")
        store.set_board_merge_mode(review, "review")

        assert store.set_setting("allow_free_merge", None)["allow_free_merge"] is None

        assert [b["merge_mode"] for b in store.list_boards()] == [None, None]
        assert store.board_merges_freely(free) is False
        with pytest.raises(ValueError):
            store.set_board_merge_mode(free, "free")


def _db_at(path, version):
    conn = sqlite3.connect(path)
    for index, script in enumerate(schema._MIGRATIONS[:version], 1):
        conn.executescript(script)
        conn.execute(f"PRAGMA user_version = {index}")
    return conn


def test_the_migration_turns_the_gate_on_for_a_board_already_free(tmp_path):
    conn = _db_at(tmp_path / "old.db", 26)
    for board_id, mode in (("free", "free"), ("review", "review")):
        conn.execute(
            "INSERT INTO boards (id, name, position, created_at, merge_mode) VALUES (?, ?, 0, ?, ?)",
            (board_id, board_id, "2026-09-18", mode),
        )
    conn.commit()
    conn.close()
    with Store(tmp_path / "old.db") as store:
        assert store.get_settings()["allow_free_merge"] == "on"
        assert store.get_settings()["allow_soft_leases"] is None
        assert store.board_merges_freely("free") is True


def test_the_migration_leaves_free_merge_off_with_no_free_board(tmp_path):
    conn = _db_at(tmp_path / "old.db", 26)
    conn.execute(
        "INSERT INTO boards (id, name, position, created_at, merge_mode) VALUES (?, ?, 0, ?, ?)",
        ("review", "review", "2026-09-18", "review"),
    )
    conn.commit()
    conn.close()
    with Store(tmp_path / "old.db") as store:
        assert store.get_settings()["allow_free_merge"] is None


def test_a_copied_free_board_drops_to_review_while_the_gate_is_off(tmp_path):
    with Store(tmp_path / "a.db") as source:
        source.set_setting("allow_free_merge", "on")
        source.set_setting("allow_soft_leases", "on")
        board = source.create_board("free")["id"]
        source.set_board_merge_mode(board, "free")
        source.set_board_lease_mode(board, "soft")
        bundle = json.loads(source.export_text())
        [kept] = source.import_as_new(bundle)
        assert source.get_board(kept)["merge_mode"] == "free"
    with Store(tmp_path / "b.db") as target:
        [copy] = target.import_as_new(bundle)
        assert target.get_board(copy)["merge_mode"] is None
        assert target.get_board(copy)["lease_mode"] is None
        assert target.get_settings()["allow_free_merge"] is None


def test_a_full_restore_of_a_free_board_turns_its_gate_on(tmp_path):
    with Store(tmp_path / "a.db") as source:
        source.set_setting("allow_free_merge", "on")
        board = source.create_board("free")["id"]
        source.set_board_merge_mode(board, "free")
        bundle = json.loads(source.export_text())
    # a bundle from before the gates carries the board but no gate row
    bundle["settings"] = []
    (tmp_path / "bundle.json").write_text(json.dumps(bundle))
    with Store(tmp_path / "b.db") as target:
        target.import_bundle(tmp_path / "bundle.json")
        assert target.get_settings()["allow_free_merge"] == "on"
        assert target.board_merges_freely(board) is True


def test_http_board_modes_answer_400_while_their_gate_is_off(running_server):
    status, board = request(f"{running_server}/api/boards", "POST", {"name": "board"})
    endpoint = f"{running_server}/api/boards/{board['id']}"
    status, error = request(endpoint, "PATCH", {"merge_mode": "free"})
    assert (status, error["error"]) == (400, FREE_OFF)
    status, error = request(endpoint, "PATCH", {"lease_mode": "soft"})
    assert (status, error["error"]) == (400, SOFT_OFF)
    status, _ = request(f"{running_server}/api/settings", "PATCH", {"allow_free_merge": "on"})
    assert status == 200
    status, changed = request(endpoint, "PATCH", {"merge_mode": "free"})
    assert (status, changed["merge_mode"]) == (200, "free")


def test_http_mode_patch_and_validation(running_server):
    status, board = request(f"{running_server}/api/boards", "POST", {"name": "board"})
    assert status == 201
    endpoint = f"{running_server}/api/boards/{board['id']}"
    request(f"{running_server}/api/settings", "PATCH", {"allow_free_merge": "on"})
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
    request(f"{running_server}/api/settings", "PATCH", {"allow_soft_leases": "on"})
    for value in ("soft", "strict", None):
        status, changed = request(endpoint, "PATCH", {"lease_mode": value})
        assert status == 200
        assert changed["lease_mode"] == value
    status, error = request(endpoint, "PATCH", {"lease_mode": "loose"})
    assert status == 400
    assert "lease_mode" in error["error"]
