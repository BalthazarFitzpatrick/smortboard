"""an approval on a protected base does not prove the work has landed"""

import pytest

from smortboard import scheduler
from smortboard.review.merge_request import PullRequestState
from smortboard.review.stacks import stacking_parent
from smortboard.store import Store


@pytest.mark.parametrize(
    ("state", "stacked"),
    [
        (PullRequestState(merged=False, state="OPEN", base="main"), True),
        (PullRequestState(merged=False, error="unreachable"), True),
        (PullRequestState(merged=True, base="card/grandparent"), True),
        (PullRequestState(merged=True, base="main"), False),
    ],
)
def test_accepted_protected_parent_stays_stacked_until_merged(
    tmp_path, monkeypatch, state, stacked
):
    with Store(tmp_path / "board.db") as store:
        board = store.create_board("board")
        repo = store.create_repo(board["id"], "repo", str(tmp_path), "main")
        parent = store.create_card(board["id"], repo["id"], "parent")
        child = store.create_card(board["id"], repo["id"], "child")
        store.add_dependency(child["id"], parent["id"])
        store.update_card(parent["id"], status="accepted")
        store.append_event(parent["id"], "merge_request", {"url": "https://example.test/pr/1"})
        monkeypatch.setattr(scheduler, "_cached_pr_view", lambda *args: state)
        result = stacking_parent(store, store.get_card(child["id"]))
        assert (result is not None) == stacked
        if stacked:
            assert result["id"] == parent["id"]


def test_two_approved_but_unmerged_parents_still_wait(tmp_path, monkeypatch):
    with Store(tmp_path / "board.db") as store:
        board = store.create_board("board")
        repo = store.create_repo(board["id"], "repo", str(tmp_path), "main")
        child = store.create_card(board["id"], repo["id"], "child")
        for name in ("first", "second"):
            parent = store.create_card(board["id"], repo["id"], name)
            store.add_dependency(child["id"], parent["id"])
            store.update_card(parent["id"], status="accepted")
        with pytest.raises(ValueError, match="all but one"):
            stacking_parent(store, store.get_card(child["id"]))
