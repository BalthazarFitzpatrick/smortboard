"""run mode: a global switch, a per-board choice and the one resolver that combines them"""

import pytest

from smortboard.store.api import Store


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "board.db") as opened:
        yield opened


def _board(store):
    return store.create_board("b")["id"]


def test_defaults_are_sealed_and_off(store):
    board_id = _board(store)
    assert store.get_settings()["allow_open_mode"] is None
    assert store.get_board(board_id)["run_mode"] is None
    assert store.run_mode(board_id) == "sealed"


@pytest.mark.parametrize(
    ("switch", "stored", "expected"),
    [
        (None, "sealed", "sealed"),
        (None, "open", "sealed"),
        ("on", "sealed", "sealed"),
        ("on", "open", "open"),
    ],
)
def test_run_mode_truth_table(store, switch, stored, expected):
    board_id = _board(store)
    # open can only be stored while the switch is on, so set it first then apply the case
    store.set_setting("allow_open_mode", "on")
    store.set_board_run_mode(board_id, stored)
    store.set_setting("allow_open_mode", switch)
    assert store.run_mode(board_id) == expected


def test_open_is_refused_while_the_switch_is_off(store):
    board_id = _board(store)
    with pytest.raises(ValueError, match="open mode is off"):
        store.set_board_run_mode(board_id, "open")
    assert store.get_board(board_id)["run_mode"] is None


def test_values_are_validated(store):
    board_id = _board(store)
    with pytest.raises(ValueError, match="run_mode must be"):
        store.set_board_run_mode(board_id, "loose")
    with pytest.raises(ValueError, match="allow_open_mode"):
        store.set_setting("allow_open_mode", "off")


def test_switch_off_then_on_restores_the_stored_open(store):
    board_id = _board(store)
    store.set_setting("allow_open_mode", "on")
    store.set_board_run_mode(board_id, "open")
    store.set_setting("allow_open_mode", None)
    assert store.get_board(board_id)["run_mode"] == "open"
    assert store.run_mode(board_id) == "sealed"
    store.set_setting("allow_open_mode", "on")
    assert store.run_mode(board_id) == "open"


def test_a_board_can_go_back_to_sealed(store):
    board_id = _board(store)
    store.set_setting("allow_open_mode", "on")
    store.set_board_run_mode(board_id, "open")
    store.set_board_run_mode(board_id, "sealed")
    assert store.run_mode(board_id) == "sealed"
