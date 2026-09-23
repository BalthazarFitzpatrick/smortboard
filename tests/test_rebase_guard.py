"""the rebase guard: a card whose base moved is rebased in a fresh tree, or blocked OUTDATED.

Real git throughout - a repo whose origin is a bare clone of itself, and a second clone standing in
for the person working outside the board. The run-start path and the checking-PR sweep both go
through rebase_guard.rebase_onto_base; each is driven from its own entry point here.
"""

import sqlite3
import subprocess

import pytest

from smortboard import lifecycle
from smortboard import scheduler as scheduler_module
from smortboard.attention import answer_card
from smortboard.exec.runner import RunResult
from smortboard.exec.worktrees import create_worktree, worktree_path
from smortboard.review import rebase_guard
from smortboard.review.gates import GateResult
from smortboard.review.merge_request import MergeRequestResult
from smortboard.review.reviewer import ReviewResult
from smortboard.store.api import Store
from smortboard.store.errors import BlockedReasonInvalidError
from smortboard.store.schema import _MIGRATIONS


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _identity(path):
    git(path, "config", "user.email", "t@t")
    git(path, "config", "user.name", "t")


@pytest.fixture(autouse=True)
def _clear_sweep_throttle():
    scheduler_module._last_sweep.clear()
    scheduler_module._last_base_sha.clear()
    yield
    scheduler_module._last_sweep.clear()
    scheduler_module._last_base_sha.clear()


class Setup:
    def __init__(self, tmp_path, store, repo_path, origin, board_id, repo_id):
        self.tmp_path, self.store = tmp_path, store
        self.repo_path, self.origin = repo_path, origin
        self.board_id, self.repo_id = board_id, repo_id
        self._clones = 0

    def card(self, title="card", leases=("*.py",)):
        return self.store.create_card(self.board_id, self.repo_id, title, leases=list(leases))["id"]

    def clone(self):
        self._clones += 1
        other = self.tmp_path / f"other-{self._clones}"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(other)], check=True)
        _identity(other)
        return other

    def move_base(self, files):
        """someone outside the board lands a commit on origin/main; local main stays put"""
        other = self.clone()
        for name, text in files.items():
            (other / name).write_text(text)
        git(other, "add", "-A")
        git(other, "commit", "-qm", f"outside: {', '.join(files)}")
        git(other, "push", "-q", "origin", "main")
        return git(other, "rev-parse", "HEAD")

    def cut(self, card_id, files, base="main"):
        """the card's branch with one commit of its own, as a finished run leaves it"""
        tree = create_worktree(self.repo_path, card_id, base=base)
        for name, text in files.items():
            (tree.path / name).write_text(text)
        git(tree.path, "add", "-A")
        git(tree.path, "commit", "-qm", "card work")
        return tree

    def waiting_pr(self, card_id):
        """pushed and waiting in checking, as the board leaves a card whose PR is open"""
        branch = f"card/{card_id}"
        git(self.repo_path, "push", "-q", "origin", f"{branch}:refs/heads/{branch}")
        self.store.update_card(card_id, status="checking", review_flag=True)
        self.store.append_event(
            card_id, "merge_request", {"opened": True, "branch": branch, "url": "https://x/pull/1"}
        )

    def tip(self, ref, where=None):
        return git(where or self.repo_path, "rev-parse", ref)

    def origin_refs(self):
        return dict(
            line.split(" ", 1)[::-1]
            for line in git(
                self.origin, "for-each-ref", "--format=%(objectname) %(refname)"
            ).splitlines()
        )

    def kinds(self, card_id):
        return [e["kind"] for e in self.store.list_events(card_id)]

    def event(self, card_id, kind):
        return [e["payload"] for e in self.store.list_events(card_id) if e["kind"] == kind][-1]


@pytest.fixture
def setup(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    git(repo_path, "init", "-q", "-b", "main")
    _identity(repo_path)
    (repo_path / "thing.py").write_text("x = 1\n")
    (repo_path / "other.py").write_text("y = 1\n")
    git(repo_path, "add", "-A")
    git(repo_path, "commit", "-qm", "first")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(repo_path), str(origin)], check=True)
    git(repo_path, "remote", "add", "origin", str(origin))
    git(repo_path, "fetch", "-q", "origin")
    store = Store(tmp_path / "board.db")
    board = store.create_board("b")
    repo = store.create_repo(
        board["id"], "repo", str(repo_path), "main", test_command="true", image="img:latest"
    )
    yield Setup(tmp_path, store, repo_path, origin, board["id"], repo["id"])
    store.close()


class _Worker:
    """a worker that really commits in the card's worktree, and records what it started from"""

    name = "fake"

    def __init__(self, edit):
        self.edit, self.calls = edit, []

    def run_card(self, store, card_id, worktree_path, prompt, settings_path, **kwargs):
        self.calls.append({"prompt": prompt, "thing": (worktree_path / "thing.py").read_text()})
        self.edit(worktree_path)
        return RunResult(
            subtype="success",
            is_error=False,
            blocked_reason_code=None,
            session_id="s",
            total_cost_usd=0.1,
            num_turns=1,
            result_text="done",
        )


def _commit(files):
    def edit(tree):
        for name, text in files.items():
            (tree / name).write_text(text)
        git(tree, "add", "-A")
        git(tree, "commit", "-qm", "worker commit")

    return edit


@pytest.fixture
def gates(monkeypatch):
    """both gates pass and the pull request 'opens' - the push is what these tests watch"""
    monkeypatch.setattr(
        lifecycle,
        "run_test_gate",
        lambda *a, **k: GateResult(passed=True, command="true", exit_code=0, output="ok"),
    )
    monkeypatch.setattr(
        lifecycle, "run_review", lambda *a, **k: ReviewResult(approved=True, findings=[])
    )
    monkeypatch.setattr(
        lifecycle,
        "open_merge_request",
        lambda *a, **k: MergeRequestResult(opened=True, branch="card/x", url="https://x/pull/1"),
    )


def _worktrees(repo_path):
    return [
        line
        for line in git(repo_path, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    ]


# -- run start ----------------------------------------------------------------------------------


def test_run_start_rebases_onto_an_outside_commit_that_leaves_the_cards_files_alone(setup, gates):
    card_id = setup.card()
    setup.cut(card_id, {"thing.py": "x = 2\n"})
    old_tip = setup.tip(f"card/{card_id}")
    base_sha = setup.move_base({"other.py": "y = 2\n"})
    worker = _Worker(_commit({"thing.py": "x = 3\n"}))

    result = lifecycle.run_card_lifecycle(setup.store, card_id, backend=worker)

    assert result.phase == "opened", setup.store.list_comments(card_id)[-1]["body"]
    assert worker.calls[0]["thing"] == "x = 2\n"  # the card's own commit survived the rebase
    rebased = setup.event(card_id, "rebased_onto_base")
    assert rebased["old_tip"] == old_tip and rebased["base_sha"] == base_sha
    assert rebased["pushed"] is False  # never pushed, so nothing on origin to replace
    new_tip = rebased["new_tip"]
    assert setup.tip(f"{new_tip}^") == base_sha  # replayed on top, not merged
    branch = f"card/{card_id}"
    assert git(setup.repo_path, "rev-list", "--merges", f"origin/main..{branch}") == ""
    assert (worktree_path(setup.repo_path, card_id) / "other.py").read_text() == "y = 2\n"
    assert len(_worktrees(setup.repo_path)) == 2  # the main checkout and the card's, no scratch


def test_run_start_blocks_outdated_when_an_outside_commit_conflicts(setup, gates):
    card_id = setup.card()
    setup.cut(card_id, {"thing.py": "x = 2\n"})
    branch = f"card/{card_id}"
    old_tip = setup.tip(branch)
    setup.move_base({"thing.py": "x = 9\n"})
    worker = _Worker(_commit({"thing.py": "x = 3\n"}))

    result = lifecycle.run_card_lifecycle(setup.store, card_id, backend=worker)

    assert result.blocked_reason_code == "OUTDATED"
    assert worker.calls == []
    assert setup.tip(branch) == old_tip  # exactly as it was
    assert setup.tip("HEAD", worktree_path(setup.repo_path, card_id)) == old_tip
    assert git(worktree_path(setup.repo_path, card_id), "status", "--porcelain") == ""
    assert setup.event(card_id, "rebase_conflict")["files"] == ["thing.py"]
    note = setup.store.list_comments(card_id)[-1]["body"]
    assert "thing.py" in note and "OUTDATED" in note
    assert "rebased_onto_base" not in setup.kinds(card_id)
    assert len(_worktrees(setup.repo_path)) == 2


def test_a_branch_with_no_commits_of_its_own_starts_from_the_current_base(setup):
    card_id = setup.card()
    create_worktree(setup.repo_path, card_id, base="main")
    base_sha = setup.move_base({"thing.py": "x = 9\n"})

    result = rebase_guard.rebase_onto_base(setup.store, card_id, setup.repo_path, "main")

    assert result.outcome == "rebased" and result.new_tip == base_sha
    assert (worktree_path(setup.repo_path, card_id) / "thing.py").read_text() == "x = 9\n"


def test_a_worktree_with_uncommitted_changes_is_left_alone(setup):
    card_id = setup.card()
    tree = setup.cut(card_id, {"thing.py": "x = 2\n"})
    old_tip = setup.tip(f"card/{card_id}")
    setup.move_base({"other.py": "y = 2\n"})
    (tree.path / "thing.py").write_text("x = half done\n")

    result = rebase_guard.rebase_onto_base(setup.store, card_id, setup.repo_path, "main")

    assert result.outcome == "skipped"
    assert setup.tip(f"card/{card_id}") == old_tip
    assert (tree.path / "thing.py").read_text() == "x = half done\n"
    assert "uncommitted" in setup.event(card_id, "rebase_skipped")["reason"]


# -- the checking-pr sweep ------------------------------------------------------------------------


def test_the_sweep_rebases_a_waiting_pr_and_force_pushes_only_its_branch(setup):
    card_id = setup.card()
    setup.cut(card_id, {"thing.py": "x = 2\n"})
    setup.waiting_pr(card_id)
    branch = f"card/{card_id}"
    base_sha = setup.move_base({"other.py": "y = 2\n"})
    before = setup.origin_refs()

    scheduler_module._sweep_checking_prs(setup.store, setup.board_id)

    rebased = setup.event(card_id, "rebased_onto_base")
    assert rebased["pushed"] is True
    after = setup.origin_refs()
    changed = {ref for ref in after if after[ref] != before.get(ref)}
    assert changed == {f"refs/heads/{branch}"}  # main and everything else untouched
    assert after[f"refs/heads/{branch}"] == rebased["new_tip"] == setup.tip(branch)
    assert after["refs/heads/main"] == base_sha
    assert setup.tip(f"{rebased['new_tip']}^") == base_sha
    card = setup.store.get_card(card_id)
    assert card["blocked_reason_code"] is None and card["status"] == "checking"


def test_the_sweep_blocks_a_waiting_pr_outdated_and_moves_nothing(setup):
    card_id = setup.card()
    setup.cut(card_id, {"thing.py": "x = 2\n"})
    setup.waiting_pr(card_id)
    branch = f"card/{card_id}"
    old_tip = setup.tip(branch)
    setup.move_base({"thing.py": "x = 9\n"})
    before = setup.origin_refs()

    scheduler_module._sweep_checking_prs(setup.store, setup.board_id)

    card = setup.store.get_card(card_id)
    assert card["blocked_reason_code"] == "OUTDATED" and card["status"] == "checking"
    assert "thing.py" in card["comments"][-1]["body"]
    assert setup.tip(branch) == old_tip
    assert setup.origin_refs() == before


def test_a_lease_refused_push_clobbers_nothing_and_is_retried_by_the_next_sweep(setup, monkeypatch):
    """someone pushes to the card's branch between the board's fetch and its push. the card is
    retried, not blocked: the next sweep sees origin holds a commit the branch lacks, and skips"""
    card_id = setup.card()
    setup.cut(card_id, {"thing.py": "x = 2\n"})
    setup.waiting_pr(card_id)
    branch = f"card/{card_id}"
    old_tip = setup.tip(branch)
    setup.move_base({"other.py": "y = 2\n"})
    real_replay = rebase_guard._replay
    theirs = {}

    def _replay_while_someone_pushes(*args, **kwargs):
        replayed = real_replay(*args, **kwargs)
        other = setup.clone()
        git(other, "checkout", "-q", branch)
        (other / "theirs.py").write_text("theirs = 1\n")
        git(other, "add", "-A")
        git(other, "commit", "-qm", "someone else's fix")
        git(other, "push", "-q", "origin", branch)
        theirs["sha"] = git(other, "rev-parse", "HEAD")
        return replayed

    monkeypatch.setattr(rebase_guard, "_replay", _replay_while_someone_pushes)

    scheduler_module._sweep_checking_prs(setup.store, setup.board_id)

    assert setup.origin_refs()[f"refs/heads/{branch}"] == theirs["sha"]  # not clobbered
    assert setup.tip(branch) == old_tip  # the local move was put back
    assert setup.tip("HEAD", worktree_path(setup.repo_path, card_id)) == old_tip
    refused = setup.event(card_id, "rebase_push_refused")
    assert refused["expected_remote_tip"] == old_tip and refused["local_restored"] is True
    card = setup.store.get_card(card_id)
    assert card["blocked_reason_code"] is None

    monkeypatch.setattr(rebase_guard, "_replay", real_replay)
    scheduler_module._last_base_sha.clear()
    scheduler_module._sweep_checking_prs(setup.store, setup.board_id)

    assert setup.origin_refs()[f"refs/heads/{branch}"] == theirs["sha"]
    assert "someone else pushed" in setup.event(card_id, "rebase_skipped")["reason"]
    assert setup.store.get_card(card_id)["blocked_reason_code"] is None


def test_a_stacked_child_is_skipped_while_its_parent_is_unlanded(setup):
    """the rule: a stacked child's base is its parent's branch - the parent is rebased as an
    ordinary card, the child is left exactly as it was until the parent lands"""
    parent_id = setup.card("parent")
    setup.cut(parent_id, {"thing.py": "x = 2\n"})
    setup.waiting_pr(parent_id)
    child_id = setup.card("child")
    setup.store.add_dependency(child_id, parent_id)
    setup.cut(child_id, {"other.py": "y = 5\n"}, base=f"card/{parent_id}")
    setup.waiting_pr(child_id)
    setup.store.append_event(
        child_id, "stacked_on", {"parent_id": parent_id, "branch": f"card/{parent_id}"}
    )
    child_tip = setup.tip(f"card/{child_id}")
    setup.move_base({"new.py": "n = 1\n"})

    scheduler_module._sweep_checking_prs(setup.store, setup.board_id)

    assert "rebased_onto_base" in setup.kinds(parent_id)
    assert setup.tip(f"card/{child_id}") == child_tip
    assert setup.origin_refs()[f"refs/heads/card/{child_id}"] == child_tip
    assert not any(kind.startswith("rebase") for kind in setup.kinds(child_id))


# -- answering an outdated card -------------------------------------------------------------------


class _SyncRuns:
    """RunRegistry's get/start, running the card inline so the answer is followed to its end"""

    def __init__(self, store, backend):
        self.store, self.backend, self.result = store, backend, None

    def get(self, card_id):
        return None

    def start(self, card_id):
        self.result = lifecycle.run_card_lifecycle(self.store, card_id, backend=self.backend)
        return type("State", (), {"as_dict": lambda _self: {"card_id": card_id}})()


def test_answering_outdated_redoes_the_card_on_a_fresh_tree_and_keeps_the_old_tip(setup, gates):
    card_id = setup.card()
    setup.cut(card_id, {"thing.py": "x = 2\n"})
    setup.waiting_pr(card_id)
    branch = f"card/{card_id}"
    old_tip = setup.tip(branch)
    base_sha = setup.move_base({"thing.py": "x = 9\n"})
    scheduler_module._sweep_checking_prs(setup.store, setup.board_id)
    assert setup.store.get_card(card_id)["blocked_reason_code"] == "OUTDATED"
    worker = _Worker(_commit({"thing.py": "x = 10\n"}))
    runs = _SyncRuns(setup.store, worker)

    answer_card(setup.store, runs, card_id, "keep x above nine")

    assert runs.result.phase == "opened", setup.store.list_comments(card_id)[-1]["body"]
    (call,) = worker.calls
    assert call["thing"] == "x = 9\n"  # a fresh tree at the moved base
    assert "no longer rebased" in call["prompt"] and "thing.py" in call["prompt"]
    assert "keep x above nine" in call["prompt"]
    backups = git(
        setup.repo_path,
        "for-each-ref",
        "--format=%(objectname)",
        f"refs/smortboard/outdated/{card_id}/",
    )
    assert backups.split() == [old_tip]  # the old commits are still reachable
    reset = setup.event(card_id, "outdated_reset")
    assert reset["old_tip"] == old_tip and reset["base_sha"] == base_sha
    new_tip = setup.tip(branch)
    assert setup.tip(f"{new_tip}^") == base_sha
    # origin held only the card's own pre-reset history, so the hand-over replaced it
    assert setup.origin_refs()[f"refs/heads/{branch}"] == new_tip
    assert "superseded_branch_replaced" in setup.kinds(card_id)
    assert setup.store.get_card(card_id)["blocked_reason_code"] is None


def test_the_reset_refuses_a_worktree_with_uncommitted_changes(setup):
    from smortboard.attention import AnswerRefused

    card_id = setup.card()
    tree = setup.cut(card_id, {"thing.py": "x = 2\n"})
    old_tip = setup.tip(f"card/{card_id}")
    setup.store.update_card(card_id, blocked_reason_code="OUTDATED", review_flag=True)
    (tree.path / "thing.py").write_text("x = not committed\n")

    with pytest.raises(AnswerRefused):
        answer_card(setup.store, _SyncRuns(setup.store, None), card_id, "redo it")

    assert setup.tip(f"card/{card_id}") == old_tip
    assert setup.store.get_card(card_id)["blocked_reason_code"] == "OUTDATED"
    assert (tree.path / "thing.py").read_text() == "x = not committed\n"


# -- the schema ---------------------------------------------------------------------------------


def test_the_outdated_migration_keeps_every_existing_card(tmp_path):
    """a board.db at migration 24 gets OUTDATED in its CHECK - sqlite cannot ALTER one, so the
    table is rebuilt; every column and every row that points at a card has to survive"""
    db_path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    for script in _MIGRATIONS[:24]:
        conn.executescript(script)
    conn.execute("PRAGMA user_version = 24")
    conn.execute("INSERT INTO boards (id, name, position, created_at) VALUES ('b1','b',0,'t')")
    conn.execute(
        "INSERT INTO repos (id, board_id, name, path, default_branch) "
        "VALUES ('r1','b1','repo','/repo','main')"
    )
    conn.execute(
        "INSERT INTO cards (id, board_id, repo_id, title, status, blocked_reason_code, position, "
        "created_at, updated_at, complexity, lab, model, ledger_task, findings_route) "
        "VALUES ('c1','b1','r1','old card','checking','BASE_RED',0,'t','t',2,'codex','m','7','fix')"
    )
    conn.execute(
        "INSERT INTO card_tasks (id, card_id, position, text) VALUES ('t1','c1',0,'do it')"
    )
    conn.execute("INSERT INTO card_leases (id, card_id, path_glob) VALUES ('l1','c1','src/**')")
    conn.execute(
        "INSERT INTO events (id, card_id, seq, kind, payload_json, created_at) "
        "VALUES ('e1','c1',1,'result','{}','t')"
    )
    conn.commit()
    conn.close()

    with Store(db_path) as store:
        card = store.get_card("c1")
        assert (card["status"], card["blocked_reason_code"]) == ("checking", "BASE_RED")
        assert (card["complexity"], card["lab"], card["model"]) == (2, "codex", "m")
        assert store.findings_route("c1") == "fix"
        assert card["tasks"][0]["text"] == "do it"
        assert card["leases"][0]["path_glob"] == "src/**"
        assert [e["kind"] for e in store.list_events("c1")] == ["result"]
        assert store._conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert store._conn.execute("PRAGMA user_version").fetchone()[0] == len(_MIGRATIONS)

        store.update_card("c1", blocked_reason_code="OUTDATED", review_flag=True)
        assert store.get_card("c1")["blocked_reason_code"] == "OUTDATED"
        with pytest.raises(BlockedReasonInvalidError):
            store.update_card("c1", blocked_reason_code="NOT_A_REAL_REASON")
