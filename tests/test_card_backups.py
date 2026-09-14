from datetime import UTC, datetime, timedelta

import pytest

from smortboard.store import NotFoundError, Store
from smortboard.store.schema import BACKUP_RETENTION_DAYS


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "board.sqlite3") as s:
        yield s


def test_deleting_a_card_writes_a_backup_row_and_removes_the_card(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "a card")

    store.delete_card(card["id"])

    with pytest.raises(NotFoundError):
        store.get_card(card["id"])
    backups = store.list_card_backups(board["id"])
    assert len(backups) == 1
    assert backups[0]["card_id"] == card["id"]
    assert backups[0]["board_id"] == board["id"]


def test_restore_card_brings_back_id_fields_status_model_tasks_criteria_leases_deps(store):
    board = store.create_board("b")
    other = store.create_card(board["id"], None, "the dependency")
    card = store.create_card(
        board["id"],
        None,
        "write the store",
        status="doing",
        model="opus",
        tasks=["a task"],
        criteria=["a criterion"],
        leases=["smortboard/store/*"],
    )
    store.add_dependency(card["id"], other["id"])
    store.add_comment(card["id"], "fabian", "a note")
    before = store.get_card(card["id"])

    store.delete_card(card["id"])
    with pytest.raises(NotFoundError):
        store.get_card(card["id"])

    backup_id = store.list_card_backups(board["id"])[0]["id"]
    restored = store.restore_card(backup_id)

    assert restored == before
    assert restored["status"] == "doing"
    assert restored["model"] == "opus"
    assert [t["text"] for t in restored["tasks"]] == ["a task"]
    assert [c["text"] for c in restored["criteria"]] == ["a criterion"]
    assert [lease["path_glob"] for lease in restored["leases"]] == ["smortboard/store/*"]
    assert restored["depends_on"] == [other["id"]]
    assert restored["comments"][0]["body"] == "a note"

    # the backup is consumed on restore - a card can't be restored twice from the same backup
    with pytest.raises(NotFoundError):
        store.restore_card(backup_id)


def test_a_backup_older_than_retention_is_purged_a_newer_one_kept(tmp_path):
    db_path = tmp_path / "board.sqlite3"
    with Store(db_path) as store:
        board = store.create_board("b")
        old_card = store.create_card(board["id"], None, "old")
        new_card = store.create_card(board["id"], None, "new")
        store.delete_card(old_card["id"])
        store.delete_card(new_card["id"])

        backups = store.list_card_backups(board["id"])
        old_backup_id = next(b["id"] for b in backups if b["card_id"] == old_card["id"])
        new_backup_id = next(b["id"] for b in backups if b["card_id"] == new_card["id"])

        # backdate the old backup past the retention window - test-only introspection, purge
        # itself only ever runs off deleted_at set by delete_card
        stale = (datetime.now(UTC) - timedelta(days=BACKUP_RETENTION_DAYS, hours=1)).isoformat()
        store._conn.execute(  # noqa: SLF001
            "UPDATE card_backups SET deleted_at = ? WHERE id = ?", (stale, old_backup_id)
        )
        store._conn.commit()  # noqa: SLF001

    # purge runs on store open
    with Store(db_path) as reopened:
        remaining = {b["id"] for b in reopened.list_card_backups(board["id"])}
        assert old_backup_id not in remaining
        assert new_backup_id in remaining


def test_deleting_a_board_backs_up_its_cards_too(store):
    board = store.create_board("b")
    first = store.create_card(board["id"], None, "first")
    second = store.create_card(board["id"], None, "second")

    store.delete_board(board["id"])

    backups = store.list_card_backups(board["id"])
    assert {b["card_id"] for b in backups} == {first["id"], second["id"]}


def test_export_then_import_keeps_card_backups(store, tmp_path):
    board = store.create_board("b")
    card = store.create_card(
        board["id"], None, "a card", tasks=["t"], criteria=["c"], leases=["a/*"]
    )
    store.add_comment(card["id"], "someone", "hi")
    store.delete_card(card["id"])
    backups = store.list_card_backups(board["id"])
    assert len(backups) == 1

    bundle_path = tmp_path / "bundle.json"
    store.export(bundle_path)

    fresh_path = tmp_path / "fresh.sqlite3"
    with Store(fresh_path) as fresh:
        fresh.import_bundle(bundle_path)
        fresh_backups = fresh.list_card_backups(board["id"])
        assert len(fresh_backups) == 1
        assert fresh_backups[0]["id"] == backups[0]["id"]

        restored = fresh.restore_card(fresh_backups[0]["id"])
        assert restored["id"] == card["id"]
        assert restored["title"] == "a card"
        assert [t["text"] for t in restored["tasks"]] == ["t"]
        assert restored["comments"][0]["body"] == "hi"
