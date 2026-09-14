"""the card lifecycle: the chain, and what a refusal at each link does to the card.

Nothing real runs here - no container, no model, no gh, no push. Each step is replaced with a fake
that returns the shape the real one returns, because what this suite is about is the ORDER and the
consequences, not the steps themselves. Those have their own suites.
"""

import subprocess

import pytest

from smortboard import lifecycle
from smortboard.exec.runner import RunResult
from smortboard.exec.worktrees import WorktreeInfo
from smortboard.operator import OPERATOR_NAME
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
    # the fake backend commits nothing, so it stands in for a run that did
    monkeypatch.setattr(lifecycle, "branch_has_commits", lambda *a, **k: True)
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


def test_an_accepted_card_is_refused_without_touching_its_outcome(board, monkeypatch):
    from smortboard.review.decide import accept_card
    from smortboard.review.outcome import card_outcome

    store, card_id = board
    _stub_gates(monkeypatch)
    lifecycle.run_card_lifecycle(store, card_id, backend=_Backend())
    store.update_card(card_id, status="checking")
    accept_card(store, card_id)
    before = card_outcome(store, card_id)

    events_before = len(store.list_events(card_id))
    cut = []
    monkeypatch.setattr(lifecycle, "create_worktree", lambda *a, **k: cut.append(1))
    result = lifecycle.run_card_lifecycle(store, card_id, backend=_Backend())

    assert result.phase == "refused"
    assert "accepted" in result.refusal
    assert cut == []  # no worktree was cut for the refused rerun
    assert card_outcome(store, card_id) == before
    # the refusal must not have recorded a new attempt that would bury the real one
    kinds = [e["kind"] for e in store.list_events(card_id)[events_before:]]
    assert "lifecycle_started" not in kinds


def test_a_rejected_card_can_be_run_again(board, monkeypatch):
    from smortboard.review.decide import reject_card

    store, card_id = board
    _stub_gates(monkeypatch)
    lifecycle.run_card_lifecycle(store, card_id, backend=_Backend())
    store.update_card(card_id, status="checking")
    reject_card(store, card_id, close_pr=lambda *a, **k: None)
    assert store.get_card(card_id)["status"] == "rejected"

    result = lifecycle.run_card_lifecycle(store, card_id, backend=_Backend())
    assert result.phase == "opened"
    assert result.pr_url == "https://x/pull/1"


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


def test_fabian_notes_reach_the_prompt_since_a_running_agent_cannot_take_input(board):
    store, card_id = board
    store.add_comment(card_id, "fabian", "use the other endpoint instead")
    store.add_comment(card_id, "someone-else", "not from fabian")
    prompt = lifecycle.build_card_prompt(store.get_card(card_id))
    assert f"Notes from {OPERATOR_NAME}, oldest first:" in prompt
    assert "- use the other endpoint instead" in prompt
    assert "not from fabian" not in prompt


def test_the_lease_reaches_the_run_as_globs_not_as_rows(board):
    """get_card returns lease rows; handing those straight on listed dicts at the agent"""
    store, card_id = board
    globs = lifecycle._lease_globs(store.get_card(card_id))
    assert globs == ["thing.py"]
    assert all(isinstance(g, str) for g in globs)


# -- fresh worktrees for dependent cards fetch origin first --------------------------------------


def _with_origin(repo, tmp_path):
    """gives `repo` a real origin remote - a bare clone of itself - so fetch_base has somewhere
    to fetch from without reaching the network."""
    bare = tmp_path / "origin.git"
    _git(repo, "clone", "-q", "--bare", str(repo), str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    return bare


def _spy_create_worktree(monkeypatch, tree_path):
    seen = {}

    def _create(repo_path, card_id, base="main"):
        seen["base"] = base
        return WorktreeInfo(card_id=card_id, path=tree_path, branch=f"card/{card_id}")

    monkeypatch.setattr(lifecycle, "create_worktree", _create)
    return seen


def test_a_card_with_no_dependencies_is_cut_from_the_local_base_as_before(
    board, tmp_path, monkeypatch
):
    """no depends_on - _base_for_fresh_cut is never reached, so behaviour is unchanged"""
    store, card_id = board
    _stub_gates(monkeypatch)
    fetched = []
    monkeypatch.setattr(lifecycle, "fetch_base", lambda *a, **k: fetched.append(1) or True)
    seen = _spy_create_worktree(monkeypatch, tmp_path / "tree")
    lifecycle.run_card_lifecycle(store, card_id, backend=_Backend())
    assert fetched == []
    assert seen["base"] == "main"


def test_a_dependent_cards_worktree_is_cut_from_a_freshly_fetched_origin(
    board, tmp_path, monkeypatch
):
    store, card_id = board
    repo_id = store.get_card(card_id)["repo_id"]
    board_id = store.get_card(card_id)["board_id"]
    repo_path = store.get_repo(repo_id)["path"]
    _with_origin(repo_path, tmp_path)
    dep = store.create_card(board_id, None, "dep card")
    store.add_dependency(card_id, dep["id"])
    _stub_gates(monkeypatch)
    seen = _spy_create_worktree(monkeypatch, tmp_path / "tree")
    lifecycle.run_card_lifecycle(store, card_id, backend=_Backend())
    assert seen["base"] == "origin/main"


def test_a_dependent_card_falls_back_to_the_local_base_with_no_origin(board, tmp_path, monkeypatch):
    store, card_id = board
    board_id = store.get_card(card_id)["board_id"]
    dep = store.create_card(board_id, None, "dep card")
    store.add_dependency(card_id, dep["id"])
    _stub_gates(monkeypatch)
    seen = _spy_create_worktree(monkeypatch, tmp_path / "tree")
    lifecycle.run_card_lifecycle(store, card_id, backend=_Backend())
    assert seen["base"] == "main"
    assert not any(
        "could not fetch" in c["body"] for c in store.list_comments(card_id)
    )  # no remote at all is not a failure worth a comment


def test_a_failed_fetch_falls_back_and_leaves_a_comment(board, tmp_path, monkeypatch):
    store, card_id = board
    repo_id = store.get_card(card_id)["repo_id"]
    board_id = store.get_card(card_id)["board_id"]
    repo_path = store.get_repo(repo_id)["path"]
    _with_origin(repo_path, tmp_path)
    dep = store.create_card(board_id, None, "dep card")
    store.add_dependency(card_id, dep["id"])
    _stub_gates(monkeypatch)
    monkeypatch.setattr(lifecycle, "fetch_base", lambda *a, **k: False)
    seen = _spy_create_worktree(monkeypatch, tmp_path / "tree")
    lifecycle.run_card_lifecycle(store, card_id, backend=_Backend())
    assert seen["base"] == "main"
    assert any("could not fetch" in c["body"] for c in store.list_comments(card_id))


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


class _ModelBackend(_Backend):
    """records which model each worker run was started on"""

    def __init__(self):
        super().__init__()
        self.models = []

    def run_card(self, store, card_id, worktree_path, prompt, settings_path, **kwargs):
        self.models.append(kwargs.get("model"))
        return self.result


def _capture_reviewer_model(monkeypatch):
    reviewed = []

    def review(*args, **kwargs):
        reviewed.append(kwargs.get("model"))
        return ReviewResult(approved=True, findings=[])

    monkeypatch.setattr(lifecycle, "run_review", review)
    return reviewed


def test_the_card_model_wins_and_the_reviewer_keeps_its_own(board, monkeypatch):
    store, card_id = board
    _stub_gates(monkeypatch)
    reviewed = _capture_reviewer_model(monkeypatch)
    store.set_setting("worker_model", "haiku")
    store.set_setting("reviewer_model", "opus")
    store.update_card(card_id, model="sonnet")
    backend = _ModelBackend()
    lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    assert backend.models == ["sonnet"]
    assert reviewed == ["opus"]


# -- stopping a run (see smortboard/server/runs.py RunRegistry.stop) ----------


def test_a_stop_during_the_worker_run_never_reaches_the_gates(board, monkeypatch):
    """a killed process reports some ordinary-looking blocked_reason_code (CRASH, most likely) -
    stop_requested is checked BEFORE that is interpreted, so a deliberate stop is never read as
    the work having gone wrong, and the chain never continues to gates/reviewer/pull request"""
    store, card_id = board
    gated, reviewed, opened = [], [], []
    monkeypatch.setattr(lifecycle, "run_test_gate", lambda *a, **k: gated.append(1))
    monkeypatch.setattr(lifecycle, "run_review", lambda *a, **k: reviewed.append(1))
    monkeypatch.setattr(lifecycle, "open_merge_request", lambda *a, **k: opened.append(1))
    backend = _Backend(
        RunResult(
            subtype=None,
            is_error=True,
            blocked_reason_code="CRASH",
            session_id=None,
            total_cost_usd=None,
            num_turns=None,
            result_text=None,
        )
    )
    result = lifecycle.run_card_lifecycle(
        store, card_id, backend=backend, stop_requested=lambda: True
    )
    assert result.phase == "stopped"
    assert result.blocked_reason_code is None  # not a claim about the work - not a CHECK value
    assert gated == [] and reviewed == [] and opened == []


def test_a_stop_leaves_the_card_flagged_with_a_comment_and_an_event(board, monkeypatch):
    store, card_id = board
    lifecycle.run_card_lifecycle(store, card_id, backend=_Backend(), stop_requested=lambda: True)
    card = store.get_card(card_id)
    assert card["review_flag"] == 1
    assert card["blocked_reason_code"] is None  # never a CHECK-constraint value
    bodies = [c["body"] for c in store.list_comments(card_id)]
    assert any(f"Stopped by {OPERATOR_NAME}." in b for b in bodies)
    kinds = [e["kind"] for e in store.list_events(card_id)]
    assert "run_stopped" in kinds


def test_a_stop_after_the_worker_but_before_the_gate_also_stops_the_chain(board, monkeypatch):
    """stop_requested is polled between phases too, not only around the worker call"""
    store, card_id = board
    calls = {"n": 0}

    def stop_requested():
        calls["n"] += 1
        # the worker's own check and the one before the commit check pass; the one after the gate
        # stops
        return calls["n"] > 2

    monkeypatch.setattr(lifecycle, "branch_has_commits", lambda *a, **k: True)
    gated, reviewed = [], []

    def _gate(*a, **k):
        gated.append(1)
        return GateResult(passed=True, command="true", exit_code=0, output="ok")

    monkeypatch.setattr(lifecycle, "run_test_gate", _gate)
    monkeypatch.setattr(lifecycle, "run_review", lambda *a, **k: reviewed.append(1))
    result = lifecycle.run_card_lifecycle(
        store, card_id, backend=_Backend(), stop_requested=stop_requested
    )
    assert result.phase == "stopped"
    assert gated == [1]  # the gate that was already running is left to finish
    assert reviewed == []  # but the reviewer, the next step, never starts


def test_a_stopped_card_keeps_its_worktree_and_branch_for_a_later_run(board, monkeypatch):
    store, card_id = board
    result = lifecycle.run_card_lifecycle(
        store, card_id, backend=_Backend(), stop_requested=lambda: True
    )
    assert result.worktree is not None
    from pathlib import Path

    assert Path(result.worktree).exists()


def test_without_a_card_model_the_board_setting_then_sonnet_apply(board, monkeypatch):
    store, card_id = board
    _stub_gates(monkeypatch)
    reviewed = _capture_reviewer_model(monkeypatch)
    backend = _ModelBackend()
    lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    assert backend.models == [lifecycle.DEFAULT_WORKER_MODEL]
    assert reviewed == [lifecycle.DEFAULT_REVIEWER_MODEL]


def test_a_card_with_no_lease_is_refused_before_a_worktree_is_cut(tmp_path, repo):
    """an empty lease lets the agent write nowhere - a run that can only fail is not started"""
    store = Store(tmp_path / "b.db")
    b = store.create_board("b")
    r = store.create_repo(b["id"], "repo", str(repo), "main", test_command="true", image="i")
    card = store.create_card(b["id"], r["id"], "no lease")
    backend = _Backend()
    result = lifecycle.run_card_lifecycle(store, card["id"], backend=backend)
    assert result.phase == "refused"
    assert "no lease" in result.refusal
    assert result.blocked_reason_code is None
    assert backend.calls == []
    assert result.worktree is None
    store.close()


def test_a_clean_merge_at_handover_reruns_the_gate_then_opens_the_pr(board, monkeypatch):
    """the branch was behind but merged clean - the gate is re-run against the merged result
    before the pull request opens, per lifecycle's opening-phase sync"""
    from smortboard.review.mergeable import MergeSyncResult

    store, card_id = board
    _stub_gates(monkeypatch)
    monkeypatch.setattr(
        lifecycle,
        "sync_with_base",
        lambda *a, **k: MergeSyncResult(clean=True, behind=True, merged=True),
    )
    gate_calls = []
    real_gate = lifecycle.run_test_gate

    def _counting_gate(*a, **k):
        gate_calls.append(1)
        return real_gate(*a, **k)

    monkeypatch.setattr(lifecycle, "run_test_gate", _counting_gate)

    result = lifecycle.run_card_lifecycle(store, card_id, backend=_Backend())
    assert result.phase == "opened"
    # the normal testing-phase gate, plus one more re-run against the merged result
    assert gate_calls == [1, 1]


def test_a_conflicting_merge_at_handover_blocks_and_opens_no_pr(board, monkeypatch):
    from smortboard.review.mergeable import MergeSyncResult

    store, card_id = board
    _stub_gates(monkeypatch)
    monkeypatch.setattr(
        lifecycle,
        "sync_with_base",
        lambda *a, **k: MergeSyncResult(
            clean=False,
            behind=True,
            conflicting_files=["smortboard/store/api.py", "ui/settings.js"],
        ),
    )
    opened = []
    monkeypatch.setattr(
        lifecycle,
        "open_merge_request",
        lambda *a, **k: (
            opened.append(1)
            or (_ for _ in ()).throw(
                AssertionError("open_merge_request must not be called on a conflict")
            )
        ),
    )

    result = lifecycle.run_card_lifecycle(store, card_id, backend=_Backend())
    assert result.phase == "blocked"
    assert result.blocked_reason_code == "MERGE_CONFLICT"
    assert opened == []
    card = store.get_card(card_id)
    assert card["blocked_reason_code"] == "MERGE_CONFLICT"
    note = card["comments"][-1]["body"]
    assert "smortboard/store/api.py" in note
    assert "ui/settings.js" in note
    events = [e for e in store.list_events(card_id) if e["kind"] == "merge_conflict"]
    assert events and events[-1]["payload"]["files"] == [
        "smortboard/store/api.py",
        "ui/settings.js",
    ]


def test_a_clean_but_not_behind_merge_skips_the_extra_gate(board, monkeypatch):
    """clean and not behind (never merged) - nothing to re-test, the normal gate result stands"""
    from smortboard.review.mergeable import MergeSyncResult

    store, card_id = board
    _stub_gates(monkeypatch)
    monkeypatch.setattr(
        lifecycle, "sync_with_base", lambda *a, **k: MergeSyncResult(clean=True, behind=False)
    )
    gate_calls = []
    monkeypatch.setattr(
        lifecycle,
        "run_test_gate",
        lambda *a, **k: (
            gate_calls.append(1)
            or GateResult(passed=True, command="true", exit_code=0, output="ok")
        ),
    )
    result = lifecycle.run_card_lifecycle(store, card_id, backend=_Backend())
    assert result.phase == "opened"
    assert gate_calls == [1]  # only the normal testing-phase gate, no extra one after merge
