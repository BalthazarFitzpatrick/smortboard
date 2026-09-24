"""every way a card can wait on a person has one call to action, and every surface says it.

Asked for by the operator: needed actions should be identifiable from anywhere - the inbox, the
card strip, the open card, and the card's last comment.
"""

import pytest

from smortboard import lifecycle
from smortboard.actions import next_action, short_action, with_next
from smortboard.attention import attention_rows, with_actions
from smortboard.store.api import Store
from smortboard.store.schema import BLOCKED_REASON_CODES

FLAG_REASONS = ("refused", "stopped", "review")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "board.db")
    yield s
    s.close()


@pytest.mark.parametrize("reason", [*BLOCKED_REASON_CODES, *FLAG_REASONS])
def test_every_waiting_reason_has_a_short_and_a_full_action(reason):
    assert short_action(reason)
    assert len(short_action(reason)) <= 20, "the short form has to fit a strip footer"
    assert next_action(reason).endswith(".")


def test_no_reason_has_no_action():
    assert next_action(None) is None
    assert short_action("not-a-reason") is None
    assert with_next("a note", "not-a-reason") == "a note"


def test_with_next_puts_the_action_last():
    note = with_next("the run stopped.\n", "stopped")
    assert note == f"the run stopped.\n\nnext: {next_action('stopped')}"


def test_a_blocked_cards_last_comment_ends_with_its_action(store):
    card = store.create_card(store.create_board("b")["id"], None, "c", leases=["x"])
    state = lifecycle.LifecycleResult(card_id=card["id"], phase="testing")
    lifecycle._block(store, state, "TESTS_FAILED", "`pytest` exited 1")
    last = store.get_card(card["id"])["comments"][-1]["body"]
    assert last.endswith(f"next: {next_action('TESTS_FAILED')}")


def test_the_inbox_row_carries_the_action_first(store):
    board_id = store.create_board("b")["id"]
    card = store.create_card(board_id, None, "c")
    # DEPENDENCY_REJECTED, not USAGE_LIMIT - USAGE_LIMIT is retried by the board itself
    # (Fix A/D) and excluded from the inbox while that retry is pending; see test_attention.py's
    # handled_by_board coverage for that behavior
    store.update_card(card["id"], blocked_reason_code="DEPENDENCY_REJECTED", review_flag=True)
    (row,) = attention_rows(store)
    assert row["action"] == next_action("DEPENDENCY_REJECTED")
    assert row["hint"] == row["action"]  # no answer box, so the hint says the same thing


def test_the_board_cards_carry_their_action_and_quiet_ones_none(store):
    board_id = store.create_board("b")["id"]
    waiting = store.create_card(board_id, None, "waiting")
    store.update_card(waiting["id"], blocked_reason_code="AGENT_QUESTION", review_flag=True)
    store.create_card(board_id, None, "quiet")
    cards = {c["title"]: c for c in with_actions(store, store.list_cards(board_id))}
    assert cards["waiting"]["next_action_short"] == short_action("AGENT_QUESTION")
    assert cards["waiting"]["next_action"] == next_action("AGENT_QUESTION")
    assert cards["quiet"]["next_action"] is None
    assert cards["quiet"]["next_action_short"] is None
