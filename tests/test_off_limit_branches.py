"""which branches a board may never land on: a per-board list, main/master/trunk by default, and a
global switch that lifts every list at once"""

import json

import pytest

from smortboard.store import Store
from tests import test_server

running_server = test_server.running_server
request = test_server._request


def test_a_new_board_keeps_main_master_and_trunk(tmp_path):
    with Store(tmp_path / "b.db") as store:
        board = store.create_board("b")
        assert board["off_limit_branches"] == ["main", "master", "trunk"]
        assert store.off_limit_branches(board["id"]) == {"main", "master", "trunk"}


def test_a_board_list_replaces_the_default_and_null_restores_it(tmp_path):
    with Store(tmp_path / "b.db") as store:
        board = store.create_board("b")
        changed = store.set_board_off_limit_branches(board["id"], ["release", "release", "main"])
        assert changed["off_limit_branches"] == ["release", "main"], "deduped, order kept"
        assert store.off_limit_branches(board["id"]) == {"release", "main"}
        assert store.set_board_off_limit_branches(board["id"], [])["off_limit_branches"] == []
        assert store.off_limit_branches(board["id"]) == frozenset()
        restored = store.set_board_off_limit_branches(board["id"], None)
        assert restored["off_limit_branches"] == ["main", "master", "trunk"]


def test_the_global_switch_lifts_every_list(tmp_path):
    with Store(tmp_path / "b.db") as store:
        board = store.create_board("b")
        store.set_setting("off_limit_branches", "off")
        assert store.off_limit_branches(board["id"]) == frozenset()
        assert store.get_board(board["id"])["off_limit_branches"] == ["main", "master", "trunk"], (
            "the board's own list is kept for when the switch goes back on"
        )
        store.set_setting("off_limit_branches", None)
        assert store.off_limit_branches(board["id"]) == {"main", "master", "trunk"}
        with pytest.raises(ValueError, match="off_limit_branches"):
            store.set_setting("off_limit_branches", "on")


@pytest.mark.parametrize("bad", ["", "has space", "-f", "a..b", "x.lock", "dir/", 3, None])
def test_a_list_holds_branch_names_only(tmp_path, bad):
    with Store(tmp_path / "b.db") as store:
        board = store.create_board("b")
        with pytest.raises(ValueError):
            store.set_board_off_limit_branches(board["id"], [bad])
        with pytest.raises(ValueError):
            store.set_board_off_limit_branches(board["id"], "main")


def test_export_and_import_keep_a_board_list(tmp_path):
    with Store(tmp_path / "a.db") as store:
        board = store.create_board("b")
        store.set_board_off_limit_branches(board["id"], ["release"])
        store.export(tmp_path / "bundle.json")
    assert json.loads((tmp_path / "bundle.json").read_text())["boards"][0]["off_limit_branches"]
    with Store(tmp_path / "b.db") as target:
        target.import_bundle(tmp_path / "bundle.json")
        assert target.get_board(board["id"])["off_limit_branches"] == ["release"]


def test_http_board_list_patch_and_validation(running_server):
    status, board = request(f"{running_server}/api/boards", "POST", {"name": "board"})
    endpoint = f"{running_server}/api/boards/{board['id']}"
    status, changed = request(endpoint, "PATCH", {"off_limit_branches": ["master", "trunk"]})
    assert (status, changed["off_limit_branches"]) == (200, ["master", "trunk"])
    status, error = request(endpoint, "PATCH", {"off_limit_branches": ["no spaces"]})
    assert status == 400 and "branch" in error["error"]
    status, _ = request(f"{running_server}/api/settings", "PATCH", {"off_limit_branches": "off"})
    assert status == 200
