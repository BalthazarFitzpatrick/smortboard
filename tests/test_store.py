import sqlite3

import pytest

from smortboard.store import BlockedReasonInvalidError, NotFoundError, Store, UnknownFieldError
from smortboard.store.schema import STATUSES


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "board.sqlite3") as s:
        yield s


def _make_board_and_card(store, **card_kwargs):
    board = store.create_board("Phase 1")
    card = store.create_card(board["id"], None, "write the store", **card_kwargs)
    return board, card


def test_migrate_is_idempotent(tmp_path):
    db_path = tmp_path / "board.sqlite3"
    with Store(db_path):
        pass
    # opening an up-to-date database a second time must be a no-op, not an error
    with Store(db_path) as store:
        assert store.list_boards() == []


def test_create_and_read_back_card(store):
    board, card = _make_board_and_card(
        store, tasks=["write schema"], criteria=["round trips"], leases=["smortboard/store/*"]
    )
    fetched = store.get_card(card["id"])
    assert fetched["title"] == "write the store"
    assert fetched["board_id"] == board["id"]
    assert fetched["status"] == "todo"
    assert fetched["blocked_reason_code"] is None
    assert [t["text"] for t in fetched["tasks"]] == ["write schema"]
    assert [c["text"] for c in fetched["criteria"]] == ["round trips"]
    assert [lease["path_glob"] for lease in fetched["leases"]] == ["smortboard/store/*"]


def test_card_moves_through_every_status(store):
    _, card = _make_board_and_card(store)
    for status in STATUSES:
        updated = store.update_card(card["id"], status=status)
        assert updated["status"] == status


def test_a_blocked_card_keeps_the_status_it_was_in(store):
    """blocked is an overlay, not a column. a card blocked while working is still 'doing', which is
    what lets it render in place with the gold outline and what the resume briefing reads back."""
    _, card = _make_board_and_card(store)
    store.update_card(card["id"], status="doing")
    blocked = store.update_card(card["id"], blocked_reason_code="USAGE_LIMIT")
    assert blocked["status"] == "doing"
    assert blocked["blocked_reason_code"] == "USAGE_LIMIT"
    cleared = store.update_card(card["id"], blocked_reason_code=None)
    assert cleared["status"] == "doing"
    assert cleared["blocked_reason_code"] is None


def test_a_queued_card_can_carry_a_reason_code(store):
    """a card can block before it ever runs - a rejected dependency blocks a card still in todo"""
    _, card = _make_board_and_card(store)
    blocked = store.update_card(card["id"], blocked_reason_code="DEPENDENCY_REJECTED")
    assert blocked["status"] == "todo"
    assert blocked["blocked_reason_code"] == "DEPENDENCY_REJECTED"


def test_unknown_status_is_refused(store):
    _, card = _make_board_and_card(store)
    with pytest.raises(BlockedReasonInvalidError):
        store.update_card(card["id"], status="blocked")


def test_unknown_reason_code_is_refused(store):
    _, card = _make_board_and_card(store)
    with pytest.raises(BlockedReasonInvalidError):
        store.update_card(card["id"], blocked_reason_code="NOT_A_REAL_CODE")


def test_card_deps_both_directions(store):
    board = store.create_board("Phase 1")
    upstream = store.create_card(board["id"], None, "upstream")
    downstream = store.create_card(board["id"], None, "downstream")
    store.add_dependency(downstream["id"], upstream["id"])

    assert store.get_dependencies(downstream["id"]) == [upstream["id"]]
    assert store.get_dependents(upstream["id"]) == [downstream["id"]]
    # not a second table: querying the other card's own dep lists is empty
    assert store.get_dependents(downstream["id"]) == []
    assert store.get_dependencies(upstream["id"]) == []


def test_events_are_append_only_and_seq_is_per_card_monotonic(store):
    _, card_a = _make_board_and_card(store)
    board = store.get_board(card_a["board_id"])
    card_b = store.create_card(board["id"], None, "second card")

    store.append_event(card_a["id"], "created", {"by": "test"})
    store.append_event(card_a["id"], "moved", {"to": "doing"})
    store.append_event(card_b["id"], "created", {"by": "test"})

    events_a = store.list_events(card_a["id"])
    events_b = store.list_events(card_b["id"])
    assert [e["seq"] for e in events_a] == [1, 2]
    assert [e["seq"] for e in events_b] == [1]
    assert events_a[1]["payload"] == {"to": "doing"}

    # append is the only write path exposed — there is no update/delete on events
    assert not hasattr(store, "update_event")
    assert not hasattr(store, "delete_event")


def test_attachments_round_trip_bytes(store):
    _, card = _make_board_and_card(store)
    meta = store.add_attachment(card["id"], "notes.txt", "text/plain", b"hello world")
    assert "blob" not in meta
    assert store.get_attachment_blob(meta["id"]) == b"hello world"


def test_comments(store):
    _, card = _make_board_and_card(store)
    store.add_comment(card["id"], "operator", "looks good")
    comments = store.list_comments(card["id"])
    assert len(comments) == 1
    assert comments[0]["body"] == "looks good"


def test_get_card_not_found(store):
    with pytest.raises(NotFoundError):
        store.get_card("does-not-exist")


def test_export_round_trips_to_an_identical_database(store, tmp_path):
    board = store.create_board("Phase 1")
    repo = store.create_repo(board["id"], "smortboard", "/repo", "main")
    upstream = store.create_card(
        board["id"], repo["id"], "upstream", tasks=["t1"], criteria=["c1"], leases=["a/*"]
    )
    downstream = store.create_card(board["id"], repo["id"], "downstream")
    store.add_dependency(downstream["id"], upstream["id"])
    store.add_comment(upstream["id"], "operator", "hi")
    store.append_event(upstream["id"], "created", {"n": 1})
    store.add_attachment(upstream["id"], "a.txt", "text/plain", b"binary\x00data")

    bundle_path = tmp_path / "bundle.json"
    store.export(bundle_path)

    fresh_path = tmp_path / "fresh.sqlite3"
    with Store(fresh_path) as fresh:
        fresh.import_bundle(bundle_path)

    _assert_databases_identical(store, fresh_path)


def _assert_databases_identical(store, fresh_path):
    original_conn = store._conn  # noqa: SLF001 -- test-only introspection to prove the round trip
    fresh_conn = sqlite3.connect(str(fresh_path))
    fresh_conn.row_factory = sqlite3.Row

    tables = [
        "boards",
        "repos",
        "cards",
        "card_tasks",
        "card_criteria",
        "card_leases",
        "card_deps",
        "comments",
        "events",
        "attachments",
    ]
    for table in tables:
        original_rows = [
            dict(r) for r in original_conn.execute(f"SELECT * FROM {table}").fetchall()
        ]
        fresh_rows = [dict(r) for r in fresh_conn.execute(f"SELECT * FROM {table}").fetchall()]
        assert sorted(original_rows, key=lambda r: r.get("id", "")) == sorted(
            fresh_rows, key=lambda r: r.get("id", "")
        ), f"table {table} differs after round trip"
    fresh_conn.close()


def test_deleting_a_card_takes_its_children_with_it(store):
    """the old delete was a single DELETE against cards, so it passed for a bare card and raised
    FOREIGN KEY constraint failed for any card that had ever been used."""
    board, card = _make_board_and_card(store)
    other = store.create_card(board["id"], None, "the other one")
    store.add_comment(card["id"], "someone", "a comment")
    store.append_event(card["id"], "run_started", {"pid": 1})
    store.add_attachment(card["id"], "note.txt", "text/plain", b"bytes")
    store.add_dependency(card["id"], other["id"])
    store.add_dependency(other["id"], card["id"])

    store.delete_card(card["id"])

    with pytest.raises(NotFoundError):
        store.get_card(card["id"])
    # the dependency edges named it at both ends, and both had to go
    assert store.get_dependencies(other["id"]) == []
    assert store.get_dependents(other["id"]) == []


def test_deleting_a_board_takes_its_cards_with_it(store):
    board, card = _make_board_and_card(store)
    store.add_comment(card["id"], "someone", "a comment")
    store.delete_board(board["id"])
    with pytest.raises(NotFoundError):
        store.get_board(board["id"])
    with pytest.raises(NotFoundError):
        store.get_card(card["id"])


# -- repos: test_command -----------------------------------------------------


def test_migration_2_applies_to_an_existing_database_without_loss(tmp_path):
    """a repo written under migration 1 keeps its data after opening the db again applies
    migration 2 - the new column must be additive, not destructive"""
    db_path = tmp_path / "board.sqlite3"
    with Store(db_path) as store:
        board = store.create_board("Phase 1")
        repo = store.create_repo(board["id"], "smortboard", "/repo", "main")
        card = store.create_card(board["id"], repo["id"], "a card")

    # reopening re-runs migrate(); it must be a no-op on the parts already applied
    with Store(db_path) as reopened:
        assert reopened.get_repo(repo["id"])["name"] == "smortboard"
        assert reopened.get_repo(repo["id"])["test_command"] is None
        assert reopened.get_card(card["id"])["title"] == "a card"


def test_create_repo_with_test_command(store):
    board = store.create_board("Phase 1")
    repo = store.create_repo(board["id"], "smortboard", "/repo", "main", test_command="pytest")
    assert store.get_repo(repo["id"])["test_command"] == "pytest"


def test_set_repo_test_command_round_trips(store):
    board = store.create_board("Phase 1")
    repo = store.create_repo(board["id"], "smortboard", "/repo", "main")
    assert store.get_repo(repo["id"])["test_command"] is None
    updated = store.set_repo_test_command(repo["id"], "uv run pytest")
    assert updated["test_command"] == "uv run pytest"
    cleared = store.set_repo_test_command(repo["id"], None)
    assert cleared["test_command"] is None


# -- tasks: tick, don't rewrite ------------------------------------------------


def test_set_task_done_round_trips(store):
    _, card = _make_board_and_card(store, tasks=["write the guard"])
    task_id = card["tasks"][0]["id"]
    assert card["tasks"][0]["done"] == 0

    done = store.set_task_done(task_id, True)
    assert done["done"] == 1
    assert store.get_card(card["id"])["tasks"][0]["done"] == 1

    undone = store.set_task_done(task_id, False)
    assert undone["done"] == 0


def test_add_and_remove_task(store):
    _, card = _make_board_and_card(store)
    task = store.add_task(card["id"], "a new task")
    assert task["text"] == "a new task"
    assert store.get_card(card["id"])["tasks"][0]["id"] == task["id"]

    store.remove_task(task["id"])
    assert store.get_card(card["id"])["tasks"] == []


def test_criteria_have_no_update_path(store):
    """acceptance criteria are the contract a card is judged against - there is no method on
    Store that can change card_criteria.text once a card exists"""
    _, card = _make_board_and_card(store, criteria=["must pass review"])
    criterion_id = card["criteria"][0]["id"]

    store_methods = [name for name in dir(Store) if not name.startswith("_")]
    for name in store_methods:
        assert "criteri" not in name.lower() or name == "create_card"

    # the row is unreachable through update_card too - criteria isn't a writable card field
    with pytest.raises(UnknownFieldError):
        store.update_card(card["id"], criteria=["rewritten"])

    unchanged = store.get_card(card["id"])
    assert unchanged["criteria"][0]["id"] == criterion_id
    assert unchanged["criteria"][0]["text"] == "must pass review"
