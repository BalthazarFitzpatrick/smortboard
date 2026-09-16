"""the demo dataset: what it contains, and that it can never reach the operator's own board."""

from __future__ import annotations

from collections import Counter

import pytest

from smortboard import cli, demo
from smortboard.attention import with_actions
from smortboard.server.runs import recover_orphaned_runs
from smortboard.store.api import Store
from smortboard.store.schema import BLOCKED_REASON_CODES, STATUSES


@pytest.fixture
def demo_db(tmp_path):
    return demo.build_demo_db(tmp_path / "demo.db")


def _cards(store, board):
    """as the board sees them: with_actions is what the cards route runs before the ui gets them"""
    return with_actions(store, store.list_cards(board["id"]))


def _drawn_column(card):
    """the column board.js draws the card in - attention wins over the card's own status"""
    if card.get("handled_by_board"):
        return card["status"]
    if card["blocked_reason_code"] or card["review_flag"]:
        return "attention"
    return card["status"]


def test_three_boards_each_with_a_repo_and_cards(demo_db):
    with Store(demo_db) as store:
        boards = store.list_boards()
        assert [b["name"] for b in boards] == ["orchard-pay", "lantern-cms", "tideline-etl"]
        for board in boards:
            assert store.list_repos(board["id"]), f"{board['name']} has no repo"
            assert len(_cards(store, board)) >= 20


def test_every_status_and_every_blocked_reason_is_represented(demo_db):
    with Store(demo_db) as store:
        statuses, reasons = set(), set()
        for board in store.list_boards():
            for card in _cards(store, board):
                statuses.add(card["status"])
                if card["blocked_reason_code"]:
                    reasons.add(card["blocked_reason_code"])
        assert statuses == set(STATUSES)
        assert reasons == set(BLOCKED_REASON_CODES)


def test_the_drawn_columns_match_what_the_demo_declares(demo_db):
    """column_counts is what tests/test_demo_regimes.py asserts the regimes against, so it has to
    be what the seeded board actually draws - including attention, which is not a stored status"""
    declared = {spec["board"]: spec["columns"] for spec in demo.column_counts()}
    with Store(demo_db) as store:
        for board in store.list_boards():
            drawn = Counter(_drawn_column(c) for c in _cards(store, board))
            assert dict(drawn) == declared[board["name"]], board["name"]


def test_no_doing_card_is_orphaned_into_crash_when_a_board_opens(demo_db):
    """a doing card whose attempt never ended is blocked as CRASH on the next open - which would
    empty the doing column of the demo every time it started"""
    with Store(demo_db) as store:
        assert recover_orphaned_runs(store) == []


def test_the_demo_has_the_extras_that_make_it_read_as_real_work(demo_db):
    with Store(demo_db) as store:
        cards = [c for board in store.list_boards() for c in _cards(store, board)]
        assert any(c["depends_on"] for c in cards), "no card depends on another"
        assert any(c["comments"] for c in cards), "no comments anywhere"
        assert any(c["attachments"] for c in cards), "no attachments anywhere"
        findings = [
            f
            for c in cards
            for e in store.list_events(c["id"])
            if e["kind"] == "review_gate"
            for f in e["payload"]["findings"]
        ]
        assert findings, "no reviewer finding anywhere"
        assert any(
            e["kind"] == "rate_limit_event" for c in cards for e in store.list_events(c["id"])
        )
        for board in store.list_boards():
            assert store.get_plan(board["id"])
            assert len(store.list_orchestrator_messages(board["id"])) >= 2


def test_nothing_in_the_demo_points_at_a_real_path(demo_db):
    with Store(demo_db) as store:
        for board in store.list_boards():
            for repo in store.list_repos(board["id"]):
                assert repo["path"].startswith("/home/demo/projects/")


def test_the_demo_flag_never_opens_the_configured_db(monkeypatch, tmp_path):
    """--demo bypasses _resolve_db entirely - not --db, not SMORTBOARD_DB, not the data dir"""
    real_db = tmp_path / "real.db"
    monkeypatch.setenv("SMORTBOARD_DB", str(real_db))

    opened = []
    monkeypatch.setattr(cli, "_resolve_db", lambda value: opened.append(value) or real_db)
    monkeypatch.setattr(cli, "make_demo_db", lambda: tmp_path / "demo.db")
    monkeypatch.setattr(cli, "build_server", lambda *a, **k: (_ for _ in ()).throw(SystemExit))

    with pytest.raises(SystemExit):
        cli.main(["--demo", "--no-browser"])
    assert opened == []
    assert not real_db.exists()


def test_make_demo_db_writes_only_inside_its_own_throwaway_directory():
    path = demo.make_demo_db()
    assert path.exists()
    assert path.parent.name.startswith("smortboard-demo-")
    assert [p.name for p in path.parent.iterdir()] == ["demo.db"]
