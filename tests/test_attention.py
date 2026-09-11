"""the attention inbox: which cards surface, what question text they carry, and answering one.

Nothing real runs here - no container, no model, no docker. answer_card's `runs` collaborator is a
small fake matching RunRegistry's get()/start() shape (see server/runs.py), and worktree reuse is
proven against a real git repo, the same way test_lifecycle.py proves the rest of the chain.
"""

import subprocess

import pytest

from smortboard import lifecycle
from smortboard.attention import AnswerRefused, answer_card, attention_rows
from smortboard.exec.worktrees import branch_name, worktree_path
from smortboard.store.api import Store


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path):
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
def store(tmp_path):
    s = Store(tmp_path / "board.db")
    yield s
    s.close()


class _FakeState:
    def __init__(self, card_id, running=False):
        self.card_id = card_id
        self.running = running

    def as_dict(self):
        return {"card_id": self.card_id, "running": self.running, "phase": "running"}


class _FakeRuns:
    """stands in for RunRegistry: get() reports whether a card is running, start() records it"""

    def __init__(self, running_card_id=None):
        self.running_card_id = running_card_id
        self.started = []

    def get(self, card_id):
        if card_id == self.running_card_id:
            return _FakeState(card_id, running=True)
        return None

    def start(self, card_id):
        self.started.append(card_id)
        return _FakeState(card_id, running=True)


def _board_and_card(store, repo, **card_kwargs):
    board = store.create_board("b")
    r = store.create_repo(board["id"], "repo", str(repo), "main")
    card = store.create_card(board["id"], r["id"], "a card", **card_kwargs)
    return board, card


# ---- attention_rows ---------------------------------------------------------------------------


def test_attention_rows_empty_with_nothing_blocked(store, repo):
    _board_and_card(store, repo)
    assert attention_rows(store) == []


def test_agent_question_pulls_the_latest_result_event(store, repo):
    board, card = _board_and_card(store, repo)
    store.update_card(card["id"], blocked_reason_code="AGENT_QUESTION", review_flag=True)
    store.append_event(card["id"], "result", {"result": "first attempt's question"})
    store.append_event(card["id"], "result", {"result": "should I widen the lease?"})

    rows = attention_rows(store)
    assert len(rows) == 1
    row = rows[0]
    assert row["card_id"] == card["id"]
    assert row["board_id"] == board["id"]
    assert row["board_name"] == "b"
    assert row["reason"] == "AGENT_QUESTION"
    assert row["question"] == "should I widen the lease?"


def test_other_reasons_pull_the_latest_board_comment(store, repo):
    _, card = _board_and_card(store, repo)
    store.update_card(card["id"], blocked_reason_code="TESTS_FAILED", review_flag=True)
    store.add_comment(card["id"], author="smortboard", body="`pytest` exited 1.\n\n```\nboom\n```")
    store.add_comment(card["id"], author="operator", body="a note that is not the block reason")

    rows = attention_rows(store)
    assert rows[0]["question"] == "`pytest` exited 1.\n\n```\nboom\n```"
    assert rows[0]["answerable"] is True
    assert rows[0]["hint"] == ""


def test_accepted_and_rejected_cards_never_surface(store, repo):
    _, card = _board_and_card(store, repo)
    store.update_card(card["id"], status="accepted", blocked_reason_code=None, review_flag=True)
    assert attention_rows(store) == []


def test_a_checking_card_awaiting_decision_surfaces_too(store, repo):
    """review_flag alone, no reason code - a pull request open and waiting on accept/reject"""
    _, card = _board_and_card(store, repo)
    store.update_card(card["id"], status="checking", review_flag=True)
    rows = attention_rows(store)
    assert len(rows) == 1
    assert rows[0]["reason"] == "review"
    # a decision, so the inbox shows where to make it instead of an answer box
    assert rows[0]["answerable"] is False
    assert "press y to accept or x to reject" in rows[0]["hint"]


def test_oldest_first(store, repo):
    _, older = _board_and_card(store, repo)
    store.update_card(older["id"], blocked_reason_code="CRASH", review_flag=True)
    _, newer = _board_and_card(store, repo)
    store.update_card(newer["id"], blocked_reason_code="CRASH", review_flag=True)

    rows = attention_rows(store)
    assert [r["card_id"] for r in rows] == [older["id"], newer["id"]]


# ---- answer_card --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason", ["AGENT_QUESTION", "TESTS_FAILED", "REVIEW_REJECTED", "CRASH", "LEASE_CONFLICT"]
)
def test_answer_resumes_a_resumable_reason(store, repo, reason):
    _, card = _board_and_card(store, repo)
    store.update_card(card["id"], blocked_reason_code=reason, review_flag=True)
    runs = _FakeRuns()

    result = answer_card(store, runs, card["id"], "go ahead, widen the lease")

    assert result["running"] is True
    assert runs.started == [card["id"]]
    updated = store.get_card(card["id"])
    assert updated["blocked_reason_code"] is None
    assert updated["review_flag"] == 0
    operator_comments = [c for c in updated["comments"] if c["author"] == "operator"]
    assert operator_comments[-1]["body"] == "go ahead, widen the lease"


@pytest.mark.parametrize("reason", ["USAGE_LIMIT", "DEPENDENCY_REJECTED"])
def test_answer_refuses_a_non_resumable_reason(store, repo, reason):
    _, card = _board_and_card(store, repo)
    store.update_card(card["id"], blocked_reason_code=reason, review_flag=True)
    runs = _FakeRuns()

    with pytest.raises(AnswerRefused):
        answer_card(store, runs, card["id"], "won't help")
    assert runs.started == []
    # nothing about the block changed
    updated = store.get_card(card["id"])
    assert updated["blocked_reason_code"] == reason


def test_answer_refuses_a_running_card(store, repo):
    _, card = _board_and_card(store, repo)
    store.update_card(card["id"], blocked_reason_code="CRASH", review_flag=True)
    runs = _FakeRuns(running_card_id=card["id"])

    with pytest.raises(AnswerRefused):
        answer_card(store, runs, card["id"], "hi")
    assert runs.started == []


def test_answer_refuses_a_card_with_nothing_to_answer(store, repo):
    _, card = _board_and_card(store, repo)
    runs = _FakeRuns()
    with pytest.raises(AnswerRefused):
        answer_card(store, runs, card["id"], "hi")


def test_answer_refuses_a_checking_card_with_no_reason_code(store, repo):
    """review_flag alone (checking, awaiting accept/reject) is a decision, not a question"""
    _, card = _board_and_card(store, repo)
    store.update_card(card["id"], status="checking", review_flag=True)
    runs = _FakeRuns()
    with pytest.raises(AnswerRefused):
        answer_card(store, runs, card["id"], "hi")


# ---- worktree reuse on resume (lifecycle.py's worktree-cutting step only) -----------------------


class _CrashingBackend:
    """a fake runtime whose run_card reports a crash straight away, so the lifecycle stops right
    after cutting the worktree - what this test is about, without needing the gates or a reviewer"""

    def run_card(self, *a, **k):
        from smortboard.exec.runner import RunResult

        return RunResult(
            subtype="error",
            is_error=True,
            blocked_reason_code="CRASH",
            session_id=None,
            total_cost_usd=None,
            num_turns=1,
            result_text="crashed",
        )


def test_resume_reuses_an_existing_worktree_and_its_commits(store, repo):
    """a blocked card's worktree from the first run is still on disk - resuming must reuse it,
    prior commits and all, rather than raising 'worktree already exists' like create_worktree does"""
    board = store.create_board("b")
    r = store.create_repo(
        board["id"], "repo", str(repo), "main", test_command="true", image="img:latest"
    )
    card = store.create_card(board["id"], r["id"], "a card")

    from smortboard.exec.worktrees import create_worktree

    tree = create_worktree(str(repo), card["id"], base="main")
    (tree.path / "progress.txt").write_text("partial work\n")
    _git(tree.path, "add", "-A")
    _git(tree.path, "commit", "-q", "-m", "partial")
    marker_commit = subprocess.run(
        ["git", "-C", str(tree.path), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()

    store.update_card(card["id"], status="doing", blocked_reason_code="CRASH", review_flag=True)

    result = lifecycle.run_card_lifecycle(store, card["id"], backend=_CrashingBackend())

    assert result.phase == "blocked"  # the crash, not a worktree refusal
    assert result.blocked_reason_code == "CRASH"
    assert result.worktree == str(worktree_path(repo, card["id"]))
    assert result.branch == branch_name(card["id"])

    head = subprocess.run(
        ["git", "-C", str(tree.path), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    assert head == marker_commit
    assert (tree.path / "progress.txt").exists()


def test_resume_adds_a_worktree_for_a_branch_with_none_checked_out(store, repo):
    """the worktree directory was cleaned up but the branch survived - resuming should add a
    fresh worktree onto that branch rather than cutting a new one from base"""
    from smortboard.exec.worktrees import (
        branch_name as _branch_name,
    )
    from smortboard.exec.worktrees import (
        create_worktree,
        destroy_worktree,
    )

    board = store.create_board("b")
    r = store.create_repo(
        board["id"], "repo", str(repo), "main", test_command="true", image="img:latest"
    )
    card = store.create_card(board["id"], r["id"], "a card")

    tree = create_worktree(str(repo), card["id"], base="main")
    (tree.path / "progress.txt").write_text("partial work\n")
    _git(tree.path, "add", "-A")
    _git(tree.path, "commit", "-q", "-m", "partial")
    destroy_worktree(str(repo), card["id"])
    assert not worktree_path(repo, card["id"]).exists()

    store.update_card(card["id"], status="doing", blocked_reason_code="CRASH", review_flag=True)

    from smortboard.exec.worktrees import add_worktree

    result = add_worktree(str(repo), card["id"])
    assert result.branch == _branch_name(card["id"])
    assert (result.path / "progress.txt").exists()
