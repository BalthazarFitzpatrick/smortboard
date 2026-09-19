"""landing proof, ordering and refusal boundaries"""

import threading
from types import SimpleNamespace

import pytest

from smortboard.review import land_card as landing
from smortboard.review.decide import DecisionRefused, accept_card, reject_card
from smortboard.review.merge_request import PullRequestState, retarget_merge_request
from smortboard.store.api import Store
from tests import test_server

running_server = test_server.running_server


@pytest.fixture
def setup(tmp_path):
    with Store(tmp_path / "b.db") as store:
        board = store.create_board("b")
        repo = store.create_repo(board["id"], "r", str(tmp_path), "development")
        card = store.create_card(board["id"], repo["id"], "c")
        store.update_card(card["id"], status="checking")
        yield store, store.get_card(card["id"]), repo


def test_github_merge_accepts_without_integrating(setup, monkeypatch):
    from smortboard.server import landings

    store, card, repo = setup
    store.append_event(card["id"], "merge_request", {"url": "https://x/pull/1"})
    monkeypatch.setattr(landing, "pr_view", lambda *a: PullRequestState(True, base="development"))
    monkeypatch.setattr(landings, "open_release_request", lambda *a: None)
    monkeypatch.setattr(landings, "land_card", lambda *a: pytest.fail("integrated twice"))
    assert landings.perform_landing(store, card["id"])["status"] == "accepted"
    assert landing.already_landed(store, card, repo, "development", None)


def test_github_failure_falls_back_to_git(setup, monkeypatch):
    store, card, repo = setup
    monkeypatch.setattr(landing, "pr_view", lambda *a: PullRequestState(False, error="offline"))
    monkeypatch.setattr(landing, "fetch_base", lambda *a: True)
    monkeypatch.setattr(landing, "_git", lambda *a: SimpleNamespace(returncode=0))
    assert landing.already_landed(store, card, repo, "development", "url")
    assert store.list_events(card["id"])[-1]["payload"]["via"] == "git"


def test_previous_attempt_integration_is_not_proof_for_new_work(setup, monkeypatch):
    store, card, repo = setup
    store.append_event(card["id"], "integrated", {"base": "development"})
    store.append_event(card["id"], "lifecycle_started", {})
    monkeypatch.setattr(landing, "fetch_base", lambda *a: False)
    assert not landing.already_landed(store, card, repo, "development", None)


def test_operator_accept_retests_a_branch_already_synced_by_sweep(setup, monkeypatch):
    from smortboard import lifecycle
    from smortboard.review.mergeable import MergeSyncResult
    from smortboard.server import landings

    store, card, repo = setup
    monkeypatch.setattr(landings, "already_landed", lambda *a: False)
    monkeypatch.setattr(
        landings,
        "existing_worktree",
        lambda *a: SimpleNamespace(path=repo["path"], branch="card/test"),
    )
    monkeypatch.setattr(lifecycle, "sync_with_base", lambda *a: MergeSyncResult(clean=True))
    monkeypatch.setattr(
        lifecycle,
        "run_test_gate",
        lambda *a: SimpleNamespace(
            passed=False,
            command="tests",
            exit_code=1,
            output="FAILED tests/test_changed.py::test_changed",
        ),
    )
    monkeypatch.setattr(landing, "integrate", lambda *a: pytest.fail("landed untested changes"))
    with pytest.raises(DecisionRefused):
        landings.perform_landing(store, card["id"])
    fresh = store.get_card(card["id"])
    assert fresh["status"] == "checking"
    assert fresh["blocked_reason_code"] == "TESTS_FAILED"


def test_a_retargeted_but_merged_pr_is_proof_once_its_base_reaches_our_base(setup, monkeypatch):
    """the exact bug: a card's PR was retargeted to main and merged there before the board saw it.
    github's own record of that PR's base is fixed at main forever - if main has since reached
    development on its own, that is just as good a proof as a base string match"""
    store, card, repo = setup
    monkeypatch.setattr(landing, "pr_view", lambda *a: PullRequestState(True, base="main"))
    monkeypatch.setattr(landing, "fetch_base", lambda *a: True)
    monkeypatch.setattr(landing, "_git", lambda *a: SimpleNamespace(returncode=0))
    assert landing.already_landed(store, card, repo, "development", "url")
    assert store.list_events(card["id"])[-1]["payload"]["via"] == "github"


def test_a_retargeted_pr_whose_base_never_reached_ours_is_not_proof(setup, monkeypatch):
    store, card, repo = setup
    monkeypatch.setattr(landing, "pr_view", lambda *a: PullRequestState(True, base="main"))
    monkeypatch.setattr(landing, "fetch_base", lambda *a: True)
    monkeypatch.setattr(landing, "_git", lambda *a: SimpleNamespace(returncode=1))
    assert not landing.already_landed(store, card, repo, "development", "url")


def test_a_retargeted_pr_is_not_proof_when_the_base_cannot_be_fetched(setup, monkeypatch):
    store, card, repo = setup
    monkeypatch.setattr(landing, "pr_view", lambda *a: PullRequestState(True, base="main"))
    monkeypatch.setattr(landing, "fetch_base", lambda *a: False)
    monkeypatch.setattr(
        landing, "_git", lambda *a: pytest.fail("must not check ancestry without both fetches")
    )
    assert not landing.already_landed(store, card, repo, "development", "url")


def test_a_stacked_cards_retargeted_pr_still_does_not_widen(setup, monkeypatch):
    """the widened check must not swallow the existing stacked-card boundary: a card stacked on an
    unmerged parent's branch is not landed just because that branch happens to reach base"""
    store, card, repo = setup
    store.append_event(card["id"], "stacked_on", {"parent_id": "parent", "branch": "card/parent"})
    monkeypatch.setattr(landing, "pr_view", lambda *a: PullRequestState(True, base="card/parent"))
    monkeypatch.setattr(landing, "fetch_base", lambda *a: True)
    seen = []

    def _git(_repo, *args):
        seen.append(args)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(landing, "_git", _git)
    assert not landing.already_landed(store, card, repo, "development", "url")
    widened = ("merge-base", "--is-ancestor", "origin/card/parent", "origin/development")
    assert widened not in seen, "a stacked card must not use the widened github check"


def test_merged_parent_target_pr_is_not_default_base_proof(setup, monkeypatch):
    store, card, repo = setup
    store.append_event(card["id"], "stacked_on", {"parent_id": "parent", "branch": "card/parent"})
    monkeypatch.setattr(landing, "pr_view", lambda *a: PullRequestState(True, base="card/parent"))
    monkeypatch.setattr(landing, "fetch_base", lambda *a: False)
    assert not landing.already_landed(store, card, repo, "development", "url")


def test_accept_lands_before_releasing_checkout(setup, monkeypatch):
    from smortboard.review import decide

    store, card, _ = setup
    calls = []
    monkeypatch.setattr(decide, "_release_worktree", lambda *a: calls.append("released"))
    accept_card(store, card["id"], land=lambda: calls.append("landed"))
    assert calls == ["landed", "released"]


def test_refused_landing_does_not_accept_or_release(setup, monkeypatch):
    from smortboard.review import decide

    store, card, _ = setup
    monkeypatch.setattr(decide, "_release_worktree", lambda *a: pytest.fail("released"))

    def refuse():
        raise DecisionRefused("conflict")

    with pytest.raises(DecisionRefused):
        accept_card(store, card["id"], land=refuse)
    assert store.get_card(card["id"])["status"] == "checking"


def test_retarget_refuses_nondefault_base(setup):
    _, _, repo = setup
    with pytest.raises(ValueError):
        retarget_merge_request(repo, "https://x/pull/1", "other")


def test_integrated_accept_is_idempotent(setup):
    store, card, _ = setup
    store.append_event(card["id"], "integrated", {"base": "development"})
    accept_card(store, card["id"])
    count = len(store.list_events(card["id"]))
    assert accept_card(store, card["id"])["status"] == "accepted"
    assert len(store.list_events(card["id"])) == count


def test_reject_keeps_parent_branch(setup, monkeypatch):
    from smortboard.review import decide

    store, card, repo = setup
    child = store.create_card(card["board_id"], repo["id"], "child")
    store.add_dependency(child["id"], card["id"])
    store.append_event(child["id"], "stacked_on", {"parent_id": card["id"], "branch": "parent"})
    monkeypatch.setattr(decide, "branch_exists", lambda *a: True)
    monkeypatch.setattr(decide, "branch_diff", lambda *a: "")
    monkeypatch.setattr(decide, "delete_branch", lambda *a: pytest.fail("deleted parent"))
    reject_card(store, card["id"])
    assert "local branch is kept" in store.get_card(card["id"])["comments"][-1]["body"]


def test_accept_request_returns_while_landing_and_guards_decisions(running_server, monkeypatch):
    from smortboard.server import app, landings

    monkeypatch.setattr(app, "validate_repo", lambda name, path, base: path)

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def wait_for_release(store, card_id):
        entered.set()
        try:
            assert release.wait(5)
            return accept_card(store, card_id)
        finally:
            finished.set()

    monkeypatch.setattr(landings, "perform_landing", wait_for_release)
    request = test_server._request
    _, board = request(f"{running_server}/api/boards", "POST", {"name": "b"})
    _, repo = request(
        f"{running_server}/api/boards/{board['id']}/repos",
        "POST",
        {
            "board_id": board["id"],
            "name": "r",
            "path": "/not/a/repo",
            "default_branch": "development",
        },
    )
    _, card = request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": repo["id"], "title": "c"},
    )
    url = f"{running_server}/api/cards/{card['id']}"
    request(url, "PATCH", {"status": "checking"})
    try:
        assert request(url + "/accept", "POST") == (202, {"state": "landing"})
        assert entered.wait(2)
        assert request(f"{running_server}/health")[0] == 200
        assert request(url + "/accept", "POST")[0] == 409
        assert request(url + "/reject", "POST")[0] == 409
        assert request(url + "/run", "POST")[0] == 409
        assert request(url, "DELETE")[0] == 409
        assert request(url, "PATCH", {"status": "rejected"})[0] == 409
        assert request(f"{running_server}/api/boards/{board['id']}", "DELETE")[0] == 409
    finally:
        release.set()
        assert finished.wait(2)


def test_rejected_dependency_refuses_manual_run_before_checkout(setup):
    from smortboard.lifecycle import run_card_lifecycle

    store, card, repo = setup
    parent = store.create_card(card["board_id"], repo["id"], "parent", status="rejected")
    store.add_dependency(card["id"], parent["id"])
    result = run_card_lifecycle(store, card["id"])
    assert result.phase == "refused"
    assert "dependency was rejected" in result.refusal


def test_rejected_parent_with_stack_refuses_branch_reuse(setup):
    from smortboard.lifecycle import run_card_lifecycle

    store, card, repo = setup
    child = store.create_card(card["board_id"], repo["id"], "child")
    store.add_dependency(child["id"], card["id"])
    store.append_event(child["id"], "stacked_on", {"parent_id": card["id"], "branch": "parent"})
    store.update_card(card["id"], status="rejected")
    result = run_card_lifecycle(store, card["id"])
    assert result.phase == "refused"
    assert "still the base" in result.refusal
