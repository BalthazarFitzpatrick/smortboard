"""the card lifecycle: the chain, and what a refusal at each link does to the card.

Nothing real runs here - no container, no model, no gh, no push. Each step is replaced with a fake
that returns the shape the real one returns, because what this suite is about is the ORDER and the
consequences, not the steps themselves. Those have their own suites.
"""

import subprocess

import pytest

from smortboard import lifecycle
from smortboard.exec.runner import RunResult
from smortboard.review.gates import GateResult, GateUnavailable
from smortboard.review.merge_request import MergeRequestResult
from smortboard.review.reviewer import ReviewFinding, ReviewResult
from smortboard.store.api import Store


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path):
    """a real git repo, because create_worktree really cuts a worktree in it"""
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "thing.py").write_text("x = 1\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "first")
    return path


@pytest.fixture
def board(tmp_path, repo):
    store = Store(tmp_path / "board.db")
    b = store.create_board("b")
    r = store.create_repo(
        b["id"], "repo", str(repo), "main", test_command="true", image="img:latest"
    )
    card = store.create_card(
        b["id"],
        r["id"],
        "add the thing",
        description="the thing is missing",
        criteria=["the thing exists"],
        tasks=["add it", "commit it"],
        leases=["thing.py"],
    )
    yield store, card["id"]
    store.close()


class _Backend:
    """stands in for the container runtime: records that it was called, returns a clean run"""

    name = "fake"

    def __init__(self, result=None):
        self.result = result or RunResult(
            subtype="success",
            is_error=False,
            blocked_reason_code=None,
            session_id="s",
            total_cost_usd=0.1,
            num_turns=3,
            result_text="done",
        )
        self.calls = []

    def run_card(self, store, card_id, worktree_path, prompt, settings_path, **kwargs):
        self.calls.append({"card_id": card_id, "prompt": prompt, "path": str(worktree_path)})
        return self.result


def _stub_gates(monkeypatch, *, passed=True, approved=True, pr_url="https://x/pull/1", findings=()):
    monkeypatch.setattr(
        lifecycle,
        "run_test_gate",
        lambda *a, **k: GateResult(passed=passed, command="true", exit_code=0, output="ok"),
    )
    monkeypatch.setattr(
        lifecycle,
        "run_review",
        lambda *a, **k: ReviewResult(approved=approved, findings=list(findings)),
    )
    monkeypatch.setattr(
        lifecycle,
        "open_merge_request",
        lambda *a, **k: MergeRequestResult(opened=bool(pr_url), branch="card/x", url=pr_url),
    )


def test_a_clean_card_reaches_an_open_pull_request(board, monkeypatch):
    store, card_id = board
    _stub_gates(monkeypatch)
    result = lifecycle.run_card_lifecycle(store, card_id, backend=_Backend())
    assert result.phase == "opened"
    assert result.pr_url == "https://x/pull/1"
    assert result.blocked_reason_code is None
    assert store.get_card(card_id)["status"] == "checking"


def test_the_phases_are_reported_in_order(board, monkeypatch):
    store, card_id = board
    _stub_gates(monkeypatch)
    seen = []
    lifecycle.run_card_lifecycle(store, card_id, backend=_Backend(), on_phase=seen.append)
    assert seen == ["preparing", "running", "testing", "reviewing", "opening"]


def test_the_pull_request_url_lands_where_a_human_will_see_it(board, monkeypatch):
    store, card_id = board
    _stub_gates(monkeypatch)
    lifecycle.run_card_lifecycle(store, card_id, backend=_Backend())
    bodies = [c["body"] for c in store.list_comments(card_id)]
    assert any("https://x/pull/1" in b for b in bodies)
    assert any("Merging is yours" in b for b in bodies)
    assert store.get_card(card_id)["review_flag"] == 1


def test_a_failing_test_gate_stops_the_card_before_the_reviewer(board, monkeypatch):
    store, card_id = board
    _stub_gates(monkeypatch, passed=False)
    reviewed = []
    monkeypatch.setattr(lifecycle, "run_review", lambda *a, **k: reviewed.append(1))
    result = lifecycle.run_card_lifecycle(store, card_id, backend=_Backend())
    assert result.phase == "blocked"
    assert result.blocked_reason_code == "TESTS_FAILED"
    assert reviewed == []  # the reviewer is never asked about work the tests rejected


def test_a_rejecting_reviewer_stops_the_card_before_the_pull_request(board, monkeypatch):
    store, card_id = board
    opened = []
    _stub_gates(
        monkeypatch,
        approved=False,
        findings=[ReviewFinding("leaked_credential", "critical", "thing.py", "an api key")],
    )
    monkeypatch.setattr(lifecycle, "open_merge_request", lambda *a, **k: opened.append(1))
    result = lifecycle.run_card_lifecycle(store, card_id, backend=_Backend())
    assert result.phase == "blocked"
    assert result.blocked_reason_code == "REVIEW_REJECTED"
    assert opened == []
    assert any("an api key" in c["body"] for c in store.list_comments(card_id))


def test_a_blocked_run_never_reaches_a_gate(board, monkeypatch):
    """the agent stopped to ask something; there is nothing finished to judge yet"""
    store, card_id = board
    gated = []
    _stub_gates(monkeypatch)
    monkeypatch.setattr(lifecycle, "run_test_gate", lambda *a, **k: gated.append(1))
    backend = _Backend(
        RunResult(
            subtype="success",
            is_error=False,
            blocked_reason_code="AGENT_QUESTION",
            session_id="s",
            total_cost_usd=0.1,
            num_turns=2,
            result_text="which one?",
        )
    )
    result = lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    assert result.blocked_reason_code == "AGENT_QUESTION"
    assert gated == []
    assert store.get_card(card_id)["blocked_reason_code"] == "AGENT_QUESTION"


def test_a_blocked_card_keeps_the_column_it_was_in(board, monkeypatch):
    """blocked is not a sixth status - the card stays where it was and raises a reason code"""
    store, card_id = board
    _stub_gates(monkeypatch, passed=False)
    lifecycle.run_card_lifecycle(store, card_id, backend=_Backend())
    card = store.get_card(card_id)
    assert card["status"] in lifecycle_statuses()
    assert card["blocked_reason_code"] == "TESTS_FAILED"


def lifecycle_statuses():
    from smortboard.store.schema import STATUSES

    return STATUSES


def test_a_card_with_no_repo_is_refused_with_a_reason(tmp_path, monkeypatch):
    store = Store(tmp_path / "b.db")
    b = store.create_board("b")
    card = store.create_card(b["id"], None, "nowhere to work")
    result = lifecycle.run_card_lifecycle(store, card["id"], backend=_Backend())
    assert result.phase == "refused"
    assert "no repo" in result.refusal
    assert result.blocked_reason_code is None  # nothing went wrong with the work
    store.close()


def test_a_repo_with_no_test_command_is_refused_rather_than_passed(board, monkeypatch):
    """a card that cannot be tested must not be treated as a card that passed its tests"""
    store, card_id = board
    _stub_gates(monkeypatch)

    def _unavailable(*a, **k):
        raise GateUnavailable("This repo declares no test_command")

    monkeypatch.setattr(lifecycle, "run_test_gate", _unavailable)
    opened = []
    monkeypatch.setattr(lifecycle, "open_merge_request", lambda *a, **k: opened.append(1))
    result = lifecycle.run_card_lifecycle(store, card_id, backend=_Backend())
    assert result.phase == "refused"
    assert "test gate could not run" in result.refusal
    assert opened == []


def test_the_prompt_carries_the_card_and_only_the_card(board, monkeypatch):
    store, card_id = board
    _stub_gates(monkeypatch)
    backend = _Backend()
    lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    prompt = backend.calls[0]["prompt"]
    assert "CARD: add the thing" in prompt
    assert "the thing is missing" in prompt
    assert "the thing exists" in prompt  # the criteria
    assert "- add it" in prompt and "- commit it" in prompt
    assert "already on the correct branch" in prompt


def test_a_done_task_is_not_asked_for_again(board):
    store, card_id = board
    card = store.get_card(card_id)
    store.set_task_done(card["tasks"][0]["id"], done=True)
    prompt = lifecycle.build_card_prompt(store.get_card(card_id))
    assert "- add it" not in prompt
    assert "- commit it" in prompt


def test_operator_notes_reach_the_prompt_since_a_running_agent_cannot_take_input(board):
    store, card_id = board
    store.add_comment(card_id, "operator", "use the other endpoint instead")
    store.add_comment(card_id, "someone-else", "not from operator")
    prompt = lifecycle.build_card_prompt(store.get_card(card_id))
    assert "Notes from operator, oldest first:" in prompt
    assert "- use the other endpoint instead" in prompt
    assert "not from operator" not in prompt


def test_the_lease_reaches_the_run_as_globs_not_as_rows(board):
    """get_card returns lease rows; handing those straight on listed dicts at the agent"""
    store, card_id = board
    globs = lifecycle._lease_globs(store.get_card(card_id))
    assert globs == ["thing.py"]
    assert all(isinstance(g, str) for g in globs)


def test_the_card_runs_on_its_own_branch_never_on_main(board, monkeypatch):
    store, card_id = board
    _stub_gates(monkeypatch)
    backend = _Backend()
    result = lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    assert result.branch == f"card/{card_id}"
    on = subprocess.run(
        ["git", "-C", backend.calls[0]["path"], "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert on == f"card/{card_id}"
    assert on != "main"
