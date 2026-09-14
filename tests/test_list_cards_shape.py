"""list_cards batches its child queries per board - it must still return exactly what get_card
returns for each card, key for key and in the same order"""

import pytest

from smortboard.store.api import Store
from smortboard.store.schema import current_version


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "board.db") as s:
        yield s


def test_list_cards_matches_get_card_for_every_card(store):
    board = store.create_board("b")
    other = store.create_board("other")
    first = store.create_card(
        board["id"],
        None,
        "first",
        tasks=["t1", "t2", "t3"],
        criteria=["c1", "c2"],
        leases=["src/*", "tests/*"],
        position=1,
    )
    second = store.create_card(board["id"], None, "second", position=0)
    empty = store.create_card(board["id"], None, "no children at all", position=2)
    elsewhere = store.create_card(other["id"], None, "on another board")

    store.add_dependency(first["id"], second["id"])
    store.add_dependency(first["id"], empty["id"])
    store.add_dependency(elsewhere["id"], first["id"])  # a dependent on a different board
    for body in ("one", "two", "three"):
        store.add_comment(first["id"], author="fabian", body=body)
    store.add_comment(second["id"], author="smortboard", body="a note")
    store.add_attachment(first["id"], "a.txt", "text/plain", b"a")
    store.add_attachment(first["id"], "b.txt", "text/plain", b"b")

    listed = store.list_cards(board["id"])
    assert [c["title"] for c in listed] == ["second", "first", "no children at all"]
    assert listed == [store.get_card(c["id"]) for c in listed]
    # key order too, since it is the api's json
    assert [list(c) for c in listed] == [list(store.get_card(c["id"])) for c in listed]
    assert store.list_cards(other["id"]) == [store.get_card(elsewhere["id"])]


def test_list_cards_on_an_empty_board(store):
    board = store.create_board("empty")
    assert store.list_cards(board["id"]) == []


def test_indexes_migration_applies(store):
    names = {
        row[0] for row in store._conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    assert {"idx_cards_board", "idx_comments_card", "idx_card_deps_dependent"} <= names
    # at least the version this test was written against - not pinned exactly, so a later
    # migration does not need to keep editing a number that was never this test's point
    assert current_version(store._conn) >= 10
