"""re-run economy: what a card re-run costs when nothing the worker could change has changed.

Measured on the live board: 38 failed test gates, none caused by card code - a missing tool in the
image, a read-only cache, a board-written lint command. Four cards burned ~$50 over 7-11 attempts
each, mostly re-running the worker into the same environment failure. Real git; the worker, gate,
reviewer and pull request are fakes.
"""

import pytest

from smortboard import lifecycle
from smortboard.review.gates import GateResult
from smortboard.review.merge_request import MergeRequestResult
from smortboard.review.reviewer import ReviewResult
from smortboard.store.api import Store
from tests.test_reviewer_path import _CommittingBackend, _git, _identity


@pytest.fixture
def card(tmp_path, monkeypatch):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _identity(path)
    (path / "thing.py").write_text("x = 1\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "first")
    store = Store(tmp_path / "board.db")
    board = store.create_board("b")
    repo = store.create_repo(
        board["id"], "repo", str(path), "main", test_command="uv run pytest", image="img:latest"
    )
    card = store.create_card(board["id"], repo["id"], "add card.py", leases=["card.py"])
    env = {"image_id": "sha256:old", "gate_passes": False}
    monkeypatch.setattr(lifecycle, "image_id", lambda image: env["image_id"])

    def _gate(store, card_id, *a, **k):
        passed = env["gate_passes"]
        store.append_event(card_id, "test_gate", {"passed": passed, "command": "x"})
        return GateResult(passed=passed, command="x", exit_code=0 if passed else 1, output="")

    monkeypatch.setattr(lifecycle, "run_test_gate", _gate)
    monkeypatch.setattr(lifecycle, "check_base_red", lambda *a, **k: None)
    reviews = []

    def _review(store, card_id, diff, *a, **k):
        reviews.append(k.get("head"))
        store.append_event(
            card_id, "review_gate", {"approved": True, "error": None, "head": k.get("head")}
        )
        return ReviewResult(approved=True)

    monkeypatch.setattr(lifecycle, "run_review", _review)
    monkeypatch.setattr(
        lifecycle,
        "open_merge_request",
        lambda *a, **k: MergeRequestResult(opened=True, branch="card/x", url="https://x/pull/1"),
    )
    yield store, card["id"], repo["id"], env, reviews
    store.close()


def _kinds(store, card_id):
    return [e["kind"] for e in store.list_events(card_id)]


def test_a_rebuilt_image_reruns_the_gate_but_not_the_worker(card):
    store, card_id, _, env, reviews = card
    backend = _CommittingBackend()
    first = lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    assert first.blocked_reason_code == "TESTS_FAILED"

    # the operator rebuilt the image: same tag, new content id, and now the suite passes
    env["image_id"], env["gate_passes"] = "sha256:new", True
    seen = []
    second = lifecycle.run_card_lifecycle(store, card_id, backend=backend, on_phase=seen.append)
    assert second.phase == "opened"
    assert backend.calls == 1  # no second worker run
    assert _kinds(store, card_id).count("test_gate") == 2
    assert len(reviews) == 1  # no verdict existed for this head yet, so it was reviewed
    assert "running" not in seen
    assert "worker_skipped" in _kinds(store, card_id)
    assert any("image changed" in c["body"] for c in store.list_comments(card_id))


def test_an_edited_test_command_reruns_the_gate_but_not_the_worker(card):
    store, card_id, repo_id, env, _ = card
    backend = _CommittingBackend()
    lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    store.set_repo_test_command(repo_id, "uv run pytest -p no:xdist")
    env["gate_passes"] = True
    result = lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    assert result.phase == "opened"
    assert backend.calls == 1


def test_a_gate_only_rerun_that_fails_again_is_refused_next_time(card):
    """same head, same image, same test command, no note - the refusal holds for a gate-only
    attempt too, so the board does not cycle gate runs either"""
    store, card_id, _, env, _ = card
    backend = _CommittingBackend()
    lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    env["image_id"] = "sha256:new"  # a rebuild that did not fix it
    second = lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    assert second.blocked_reason_code == "TESTS_FAILED"
    third = lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    assert third.blocked_reason_code == "TESTS_FAILED"
    assert backend.calls == 1
    assert _kinds(store, card_id).count("test_gate") == 2  # the third attempt ran nothing
    assert "Nothing has changed" in store.list_comments(card_id)[-1]["body"]


def test_the_same_environment_after_a_failed_gate_runs_the_worker_as_before(card):
    """the worker may fix what the gate names - only a changed environment skips it"""
    store, card_id, _, _, _ = card
    backend = _CommittingBackend()
    lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    assert backend.calls == 2


def test_an_approved_head_is_not_reviewed_again(card, monkeypatch):
    """the pull request could not open after both gates passed; the re-run re-tests the head and
    goes straight to the pull request, reusing the verdict the reviewer already gave it"""
    store, card_id, _, env, reviews = card
    env["gate_passes"] = True
    urls = iter([None, "https://x/pull/1"])
    monkeypatch.setattr(
        lifecycle,
        "open_merge_request",
        lambda *a, **k: MergeRequestResult(
            opened=True, branch="card/x", url=next(urls), refusal="gh is not authenticated"
        ),
    )
    backend = _CommittingBackend()
    first = lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    assert first.phase == "refused"
    second = lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    assert second.phase == "opened"
    assert backend.calls == 1
    assert len(reviews) == 1
    assert "review_reused" in _kinds(store, card_id)
