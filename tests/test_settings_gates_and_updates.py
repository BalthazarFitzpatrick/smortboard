"""stored gated choices survive a gate going off, the three formerly unchecked settings are
validated, and a board's settings are applied whole or not at all"""

import pytest

from smortboard.store import Store
from tests import test_server

running_server = test_server.running_server
request = test_server._request


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "board.db") as value:
        yield value


@pytest.mark.parametrize(
    "key,good,bad",
    [
        ("resume_briefing", "off", "on"),
        ("enable_mouse", "on", "yes"),
        ("gate_timeout_seconds", 900, 0),
    ],
)
def test_the_formerly_unchecked_settings_are_validated(store, key, good, bad):
    assert store.set_setting(key, good)[key] in (good, str(good))
    with pytest.raises(ValueError, match=key):
        store.set_setting(key, bad)
    assert store.set_setting(key, None)[key] is None


def test_a_board_update_applies_every_field_together(store):
    board = store.create_board("b")["id"]
    store.set_setting("allow_free_merge", "on")
    store.set_setting("allow_soft_leases", "on")
    changed = store.update_board(
        board, {"merge_mode": "free", "lease_mode": "soft", "max_parallel": 3}
    )
    assert (changed["merge_mode"], changed["lease_mode"], changed["max_parallel"]) == (
        "free",
        "soft",
        3,
    )


def test_a_refused_field_leaves_the_whole_board_untouched(store):
    board = store.create_board("b")["id"]
    store.set_setting("allow_free_merge", "on")
    store.set_board_merge_mode(board, "free")
    # soft leases are off, so the open preset is refused as a whole
    with pytest.raises(ValueError, match="soft leases are off"):
        store.update_board(
            board, {"merge_mode": "review", "lease_mode": "soft", "run_mode": "open"}
        )
    after = store.get_board(board)
    assert (after["merge_mode"], after["lease_mode"], after["run_mode"]) == ("free", None, None)


def test_http_board_patch_is_all_or_nothing(running_server):
    _, board = request(f"{running_server}/api/boards", "POST", {"name": "board"})
    endpoint = f"{running_server}/api/boards/{board['id']}"
    request(f"{running_server}/api/settings", "PATCH", {"allow_free_merge": "on"})
    request(endpoint, "PATCH", {"merge_mode": "free"})
    status, error = request(
        endpoint, "PATCH", {"merge_mode": "review", "lease_mode": "soft", "max_parallel": 2}
    )
    assert status == 400
    assert "soft leases are off" in error["error"]
    _, boards = request(f"{running_server}/api/boards")
    stored = next(b for b in boards if b["id"] == board["id"])
    assert (stored["merge_mode"], stored["lease_mode"], stored["max_parallel"]) == (
        "free",
        None,
        None,
    )


def test_a_stored_free_choice_does_not_land_freely_while_the_gate_is_off(store):
    board = store.create_board("b")["id"]
    store.set_setting("allow_free_merge", "on")
    store.set_board_merge_mode(board, "free")
    store.set_setting("allow_free_merge", None)
    assert store.get_board(board)["merge_mode"] == "free"
    assert store.board_merges_freely(board) is False
