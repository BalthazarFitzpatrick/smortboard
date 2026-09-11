"""a flagged card with no reason code is not always a decision: its latest run may have been stopped
by fabian or refused by the board, and the inbox should say which"""

from smortboard.attention import attention_rows
from tests.test_attention import _board_and_card, repo, store  # noqa: F401 (fixtures)


def test_a_stopped_run_reads_as_stopped(store, repo):  # noqa: F811
    _, card = _board_and_card(store, repo)
    store.append_event(card["id"], "lifecycle_started", {})
    store.append_event(card["id"], "run_stopped", {})
    store.update_card(card["id"], status="doing", review_flag=True)

    row = attention_rows(store)[0]
    assert row["reason"] == "stopped"
    assert row["answerable"] is False
    assert "press r" in row["hint"]


def test_a_refused_run_reads_as_refused(store, repo):  # noqa: F811
    _, card = _board_and_card(store, repo)
    store.append_event(card["id"], "lifecycle_started", {})
    store.append_event(card["id"], "run_refused", {"note": "no git remote"})
    store.update_card(card["id"], review_flag=True)

    row = attention_rows(store)[0]
    assert row["reason"] == "refused"
    assert "fix what the note says" in row["hint"]


def test_a_stop_from_an_earlier_run_does_not_leak_into_a_later_one(store, repo):  # noqa: F811
    _, card = _board_and_card(store, repo)
    store.append_event(card["id"], "lifecycle_started", {})
    store.append_event(card["id"], "run_stopped", {})
    store.append_event(card["id"], "lifecycle_started", {})
    store.append_event(card["id"], "merge_request", {"url": "https://example.test/pull/1"})
    store.update_card(card["id"], status="checking", review_flag=True)

    assert attention_rows(store)[0]["reason"] == "review"
