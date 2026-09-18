import json
import sqlite3
import stat

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


def test_a_fresh_db_is_created_0600(tmp_path):
    db_path = tmp_path / "board.sqlite3"
    with Store(db_path):
        pass
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600


def test_an_existing_loose_db_is_tightened_on_open(tmp_path):
    db_path = tmp_path / "board.sqlite3"
    with Store(db_path):
        pass
    db_path.chmod(0o644)
    with Store(db_path):
        pass
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600


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


def test_import_bundle_refuses_a_bundle_row_with_a_non_column_key(store, tmp_path):
    """a bundle key becomes a raw sql column name on import - a crafted key must be refused,
    not interpolated, so the table it targets survives"""
    board = store.create_board("Phase 1")
    bundle_path = tmp_path / "bundle.json"
    store.export(bundle_path)
    bundle = json.loads(bundle_path.read_text())
    bundle["boards"][0]["id) VALUES (1); DROP TABLE cards; --"] = "x"
    bundle_path.write_text(json.dumps(bundle))

    fresh_path = tmp_path / "fresh.sqlite3"
    with Store(fresh_path) as fresh:
        with pytest.raises(UnknownFieldError):
            fresh.import_bundle(bundle_path)
        # the table this key targeted must still exist and be usable
        fresh.create_board("still works")
    assert board["id"]


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


def test_a_repo_can_declare_its_lint_command(store):
    board = store.create_board("Phase 1")
    repo = store.create_repo(board["id"], "smortboard", "/repo", "main", lint_command="ruff check")
    assert store.get_repo(repo["id"])["lint_command"] == "ruff check"


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


def test_set_repo_default_branch_round_trips(store):
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "r", "/tmp/r", "feature/review-tool")
    updated = store.set_repo_default_branch(repo["id"], "main")
    assert updated["default_branch"] == "main"
    assert store.get_repo(repo["id"])["default_branch"] == "main"


def test_a_repo_can_declare_its_image(store):
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "r", "/tmp/r", "main", image="card-python:latest")
    assert repo["image"] == "card-python:latest"
    cleared = store.set_repo_image(repo["id"], None)
    assert cleared["image"] is None


# -- migration 6: prompts, orchestrator messages and plan --------------------


def test_migration_6_applies_to_an_existing_v5_database(tmp_path):
    """a board written before phase 4 keeps its data after migration 6 adds the new tables"""
    db_path = tmp_path / "board.sqlite3"
    with Store(db_path) as store:
        board = store.create_board("Phase 1")
        card = store.create_card(board["id"], None, "a card")

    with Store(db_path) as reopened:
        assert reopened.get_card(card["id"])["title"] == "a card"
        assert reopened.get_prompt("worker") is None
        assert reopened.list_orchestrator_messages(board["id"]) == []
        assert reopened.get_plan(board["id"]) is None


def test_prompt_versioning_keeps_history(store):
    first = store.set_prompt("worker", "be careful")
    assert first["version"] == 1
    second = store.set_prompt("worker", "be more careful")
    assert second["version"] == 2

    assert store.get_prompt("worker")["body"] == "be more careful"
    history = store.list_prompt_versions("worker")
    assert [h["version"] for h in history] == [1, 2]
    assert [h["body"] for h in history] == ["be careful", "be more careful"]


def test_a_prompt_with_no_saves_is_unset(store):
    assert store.get_prompt("orchestrator") is None


def test_orchestrator_messages_and_plan_round_trip(store):
    board = store.create_board("b")
    operator_msg = store.add_orchestrator_message(board["id"], "operator", "build the thing")
    assert operator_msg["cards"] == []
    reply = store.add_orchestrator_message(
        board["id"], "orchestrator", "on it", cards=[{"id": "c1", "title": "the thing"}]
    )
    assert reply["cards"] == [{"id": "c1", "title": "the thing"}]

    messages = store.list_orchestrator_messages(board["id"])
    assert [m["author"] for m in messages] == ["operator", "orchestrator"]

    assert store.get_plan(board["id"]) is None
    store.set_plan(board["id"], "phase 1 first")
    assert store.get_plan(board["id"]) == "phase 1 first"
    store.set_plan(board["id"], "phase 2 next")  # overwrites, one plan per board
    assert store.get_plan(board["id"]) == "phase 2 next"


def test_orchestrator_messages_limit_keeps_the_tail(store):
    board = store.create_board("b")
    for i in range(5):
        store.add_orchestrator_message(board["id"], "operator", f"message {i}")
    tail = store.list_orchestrator_messages(board["id"], limit=2)
    assert [m["body"] for m in tail] == ["message 3", "message 4"]


def test_export_round_trips_the_phase_4_tables(store, tmp_path):
    board = store.create_board("b")
    store.set_prompt("worker", "custom worker prompt")
    store.add_orchestrator_message(
        board["id"],
        "operator",
        "hi",
    )
    store.set_plan(board["id"], "the plan")

    bundle_path = tmp_path / "bundle.json"
    store.export(bundle_path)

    fresh_path = tmp_path / "fresh.sqlite3"
    with Store(fresh_path) as fresh:
        fresh.import_bundle(bundle_path)
        assert fresh.get_prompt("worker")["body"] == "custom worker prompt"
        assert fresh.get_plan(board["id"]) == "the plan"
        assert len(fresh.list_orchestrator_messages(board["id"])) == 1


def test_list_events_by_kind_spans_every_card(store):
    board = store.create_board("b")
    a = store.create_card(board["id"], None, "a")
    b = store.create_card(board["id"], None, "b")
    store.append_event(a["id"], "result", {"total_cost_usd": 0.1})
    store.append_event(a["id"], "assistant", {})
    store.append_event(b["id"], "result", {"total_cost_usd": 0.2})

    results = store.list_events_by_kind(["result"])
    assert len(results) == 2
    assert {e["card_id"] for e in results} == {a["id"], b["id"]}


def test_list_doing_cards_blocked_ignores_status_and_reason(store):
    board = store.create_board("b")
    doing_blocked = store.create_card(board["id"], None, "blocked while doing")
    store.update_card(doing_blocked["id"], status="doing", blocked_reason_code="USAGE_LIMIT")
    doing_clean = store.create_card(board["id"], None, "doing but fine")
    store.update_card(doing_clean["id"], status="doing")
    todo_blocked = store.create_card(board["id"], None, "blocked before starting")
    store.update_card(todo_blocked["id"], blocked_reason_code="DEPENDENCY_REJECTED")

    ids = {c["id"] for c in store.list_doing_cards_blocked()}
    assert ids == {doing_blocked["id"]}


def test_setting_orchestrator_model_round_trips(store):
    assert store.get_settings()["orchestrator_model"] is None
    updated = store.set_setting("orchestrator_model", "sonnet")
    assert updated["orchestrator_model"] == "sonnet"


def test_a_card_carries_a_model_that_can_be_changed_or_cleared(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "c", model="opus")
    assert card["model"] == "opus"
    assert store.update_card(card["id"], model="haiku")["model"] == "haiku"
    assert store.update_card(card["id"], model=None)["model"] is None


def test_worker_and_reviewer_models_are_board_settings(store):
    settings = store.get_settings()
    assert settings["worker_model"] is None and settings["reviewer_model"] is None
    store.set_setting("worker_model", "haiku")
    assert store.set_setting("reviewer_model", "opus")["worker_model"] == "haiku"
    assert store.get_settings()["reviewer_model"] == "opus"


def test_a_card_lists_its_attachments(store):
    # the panel read card.attachments, which get_card never returned, so the section said "none"
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "c")
    store.add_attachment(card["id"], "attempt-1.diff", "text/x-diff", b"+x\n")
    assert [a["filename"] for a in store.get_card(card["id"])["attachments"]] == ["attempt-1.diff"]


def test_set_leases_replaces_the_whole_lease(store):
    _, card = _make_board_and_card(store, leases=["old/*"])
    updated = store.set_leases(card["id"], ["src/**", " tests/** "])
    assert [row["path_glob"] for row in updated["leases"]] == ["src/**", "tests/**"]
    assert store.set_leases(card["id"], [])["leases"] == []


@pytest.mark.parametrize("glob", ["", "/abs/path.py", "../other/**", "src/../../x", 7])
def test_set_leases_refuses_a_glob_the_guard_could_never_match(store, glob):
    _, card = _make_board_and_card(store, leases=["keep/*"])
    with pytest.raises(ValueError):
        store.set_leases(card["id"], [glob])
    assert [row["path_glob"] for row in store.get_card(card["id"])["leases"]] == ["keep/*"]


def test_set_leases_on_a_missing_card_is_not_found(store):
    with pytest.raises(NotFoundError):
        store.set_leases("nope", ["src/**"])


def test_set_dependencies_replaces_the_whole_list(store):
    board = store.create_board("b")
    a = store.create_card(board["id"], None, "a")
    b = store.create_card(board["id"], None, "b")
    c = store.create_card(board["id"], None, "c")
    store.set_dependencies(c["id"], [a["id"]])
    assert store.get_card(c["id"])["depends_on"] == [a["id"]]
    updated = store.set_dependencies(c["id"], [b["id"]])
    assert updated["depends_on"] == [b["id"]]
    assert store.set_dependencies(c["id"], [])["depends_on"] == []


def test_create_card_with_depends_on_links_them(store):
    board = store.create_board("b")
    upstream = store.create_card(board["id"], None, "upstream")
    downstream = store.create_card(board["id"], None, "downstream", depends_on=[upstream["id"]])
    assert downstream["depends_on"] == [upstream["id"]]
    assert store.get_dependents(upstream["id"]) == [downstream["id"]]


def test_create_card_with_unknown_depends_on_is_refused(store):
    board = store.create_board("b")
    with pytest.raises(ValueError):
        store.create_card(board["id"], None, "downstream", depends_on=["nope"])
    assert store.list_cards(board["id"]) == []


def test_set_dependencies_refuses_an_unknown_card_id(store):
    _, card = _make_board_and_card(store)
    with pytest.raises(ValueError):
        store.set_dependencies(card["id"], ["nope"])
    assert store.get_card(card["id"])["depends_on"] == []


def test_set_dependencies_refuses_self_dependency(store):
    _, card = _make_board_and_card(store)
    with pytest.raises(ValueError):
        store.set_dependencies(card["id"], [card["id"]])
    assert store.get_card(card["id"])["depends_on"] == []


def test_set_dependencies_refuses_a_cycle(store):
    board = store.create_board("b")
    a = store.create_card(board["id"], None, "a")
    b = store.create_card(board["id"], None, "b")
    store.set_dependencies(b["id"], [a["id"]])
    with pytest.raises(ValueError):
        store.set_dependencies(a["id"], [b["id"]])
    assert store.get_card(a["id"])["depends_on"] == []
    assert store.get_card(b["id"])["depends_on"] == [a["id"]]


def test_set_dependencies_on_a_missing_card_is_not_found(store):
    with pytest.raises(NotFoundError):
        store.set_dependencies("nope", [])


def test_an_existing_database_migrates_to_merge_conflict(tmp_path):
    """a board.db stuck at migration 10 (no MERGE_CONFLICT yet) gets the wider CHECK constraint
    without losing any of its cards, tasks, criteria or leases - the CHECK can't be ALTERed in
    sqlite, so migration 11 recreates the table and this proves the copy is lossless"""
    import sqlite3

    from smortboard.store.schema import _MIGRATIONS

    db_path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    for script in _MIGRATIONS[:10]:
        conn.executescript(script)
    conn.execute("PRAGMA user_version = 10")
    conn.execute(
        "INSERT INTO boards (id, name, position, created_at) VALUES ('b1','Phase 1',0,'t')"
    )
    conn.execute(
        "INSERT INTO repos (id, board_id, name, path, default_branch) "
        "VALUES ('r1','b1','repo','/repo','main')"
    )
    conn.execute(
        "INSERT INTO cards (id, board_id, repo_id, title, status, position, created_at, updated_at) "
        "VALUES ('c1','b1','r1','old card','checking',0,'t','t')"
    )
    conn.execute(
        "INSERT INTO card_tasks (id, card_id, position, text) VALUES ('t1','c1',0,'do it')"
    )
    conn.execute("INSERT INTO card_leases (id, card_id, path_glob) VALUES ('l1','c1','src/**')")
    conn.commit()
    conn.close()

    with Store(db_path) as store:
        card = store.get_card("c1")
        assert card["title"] == "old card"
        assert card["status"] == "checking"
        assert card["tasks"][0]["text"] == "do it"
        assert card["leases"][0]["path_glob"] == "src/**"

        # the new reason code the migration exists for actually works now
        store.update_card("c1", blocked_reason_code="MERGE_CONFLICT", review_flag=True)
        assert store.get_card("c1")["blocked_reason_code"] == "MERGE_CONFLICT"

        with pytest.raises(BlockedReasonInvalidError):
            store.update_card("c1", blocked_reason_code="NOT_A_REAL_REASON")


def test_an_existing_database_migrates_the_old_author_key_to_operator(tmp_path):
    """a board.db stuck at migration 15 still has the old personal author key on its
    orchestrator_messages (CHECK constraint) and comments (no CHECK) rows - migration 16 rebuilds
    the former and updates the latter in place, and "operator" must work for new rows after"""
    from smortboard.store.schema import _MIGRATIONS

    db_path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    for script in _MIGRATIONS[:15]:
        conn.executescript(script)
    conn.execute("PRAGMA user_version = 15")
    conn.execute(
        "INSERT INTO boards (id, name, position, created_at) VALUES ('b1','Phase 1',0,'t')"
    )
    conn.execute(
        "INSERT INTO repos (id, board_id, name, path, default_branch) "
        "VALUES ('r1','b1','repo','/repo','main')"
    )
    conn.execute(
        "INSERT INTO cards (id, board_id, repo_id, title, status, position, created_at, updated_at) "
        "VALUES ('c1','b1','r1','old card','doing',0,'t','t')"
    )
    conn.execute(
        "INSERT INTO comments (id, card_id, author, body, created_at) "
        "VALUES ('cm1','c1','fab' || 'ian','an old note','t')"
    )
    # migration 6 now creates orchestrator_messages with the CHECK already fixed, so an old row
    # under the pre-fix CHECK has to be faked by recreating the table the way it looked before
    conn.execute("DROP TABLE orchestrator_messages")
    conn.execute(
        """
        CREATE TABLE orchestrator_messages (
            id TEXT PRIMARY KEY,
            board_id TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
            author TEXT NOT NULL CHECK (author IN ('fab' || 'ian', 'orchestrator', 'board')),
            body TEXT NOT NULL,
            cards_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO orchestrator_messages (id, board_id, author, body, created_at) "
        "VALUES ('m1','b1','fab' || 'ian','build the thing','t')"
    )
    conn.commit()
    conn.close()

    with Store(db_path) as store:
        card = store.get_card("c1")
        assert card["comments"][0]["author"] == "operator"
        assert card["comments"][0]["body"] == "an old note"

        messages = store.list_orchestrator_messages("b1")
        assert messages[0]["author"] == "operator"
        assert messages[0]["body"] == "build the thing"

        # inserting new rows under the new CHECK still works
        store.add_comment("c1", "operator", "a fresh note")
        added = store.add_orchestrator_message("b1", "operator", "a fresh message")
        assert added["author"] == "operator"


@pytest.mark.parametrize("glob", ["/abs/path.py", "../other/**"])
def test_create_card_refuses_a_glob_the_guard_could_never_match(store, glob):
    with pytest.raises(ValueError):
        _make_board_and_card(store, leases=[glob])


@pytest.mark.parametrize("image", ["--privileged", "-v/:/host", "img name", "", 7])
def test_repo_image_that_could_read_as_a_docker_flag_is_refused(store, image):
    board = store.create_board("b")
    with pytest.raises(ValueError):
        store.create_repo(board["id"], "r", "/tmp/r", "main", image=image)
    repo = store.create_repo(board["id"], "r", "/tmp/r", "main", image="ghcr.io/o/card:1.2")
    with pytest.raises(ValueError):
        store.set_repo_image(repo["id"], image)


@pytest.mark.parametrize("model", ["--dangerously-skip-permissions", "x; y", "a b", ""])
def test_card_model_that_could_read_as_a_claude_flag_is_refused(store, model):
    with pytest.raises(ValueError):
        _make_board_and_card(store, model=model)
    _, card = _make_board_and_card(store, model="claude-opus-5[1m]")
    with pytest.raises(ValueError):
        store.update_card(card["id"], model=model)


@pytest.mark.parametrize(
    "key",
    ["worker_budget_usd", "reviewer_budget_usd", "orchestrator_budget_usd", "fold_budget_usd"],
)
def test_a_spend_cap_is_a_positive_dollar_amount_or_unset(store, key):
    assert store.spend_cap(key, 4.0) == 4.0
    store.set_setting(key, "2.5")
    assert store.spend_cap(key, 4.0) == 2.5
    for bad in (0, -1, "free", True):
        with pytest.raises(ValueError):
            store.set_setting(key, bad)
    store.set_setting(key, None)
    assert store.spend_cap(key, 4.0) == 4.0


def test_migration_18_applies_on_a_fresh_db_and_on_a_v17_database(tmp_path):
    """board_spend exists fresh, and an old db stuck at 17 gets it without losing a board"""
    fresh = tmp_path / "fresh.sqlite3"
    with Store(fresh) as store:
        board = store.create_board("b")
        row = store.add_board_spend(board["id"], "orchestrator", 0.42)
        assert row["cost_usd"] == 0.42
        assert store.list_board_spend(board["id"])[0]["role"] == "orchestrator"

    from smortboard.store.schema import _MIGRATIONS

    old_db = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(str(old_db))
    conn.execute("PRAGMA foreign_keys = ON")
    for script in _MIGRATIONS[:17]:
        conn.executescript(script)
    conn.execute("PRAGMA user_version = 17")
    conn.execute(
        "INSERT INTO boards (id, name, position, created_at) VALUES ('b1','Phase 1',0,'t')"
    )
    conn.commit()
    conn.close()

    with Store(old_db) as reopened:
        assert reopened.get_board("b1")["name"] == "Phase 1"
        assert reopened.list_board_spend("b1") == []
        row = reopened.add_board_spend("b1", "fold", 1.1)
        assert row["board_id"] == "b1"


def test_board_spend_today_includes_mission_control_and_fold_rows(store):
    from smortboard.telemetry import board_spend_today

    board = store.create_board("b")
    card = store.create_card(board["id"], None, "c")
    store.append_event(card["id"], "result", {"total_cost_usd": 0.3})
    store.add_board_spend(board["id"], "orchestrator", 0.2)
    store.add_board_spend(board["id"], "fold", 0.1)
    assert board_spend_today(store, board["id"]) == pytest.approx(0.6)
