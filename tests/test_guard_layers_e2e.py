"""a card through the whole guard stack: lifecycle, worktree sync, container clone, fetch-back, the
post-run lease check and the gates - with real git, a real store and the real backend.

Only the outside world is faked: the agent (a function that edits the clone the container would
mount), the test gate's docker call, the reviewer and the pull request. Measured 2026-09-19: the
worktree sync, the lease check and the base merge each broke on the other on the live board, and
every unit test passed, because each layer's own tests fake the layers around it.
"""

import json
import subprocess
from pathlib import Path

import pytest

from smortboard import lifecycle
from smortboard.exec import backends
from smortboard.exec.backends import ContainerBackend
from smortboard.exec.runner import RunResult
from smortboard.exec.worktrees import create_worktree, worktree_path
from smortboard.review.gates import GateResult
from smortboard.review.merge_request import MergeRequestResult
from smortboard.review.reviewer import ReviewResult
from smortboard.store.api import Store


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _identity(path):
    git(path, "config", "user.name", "test")
    git(path, "config", "user.email", "test@example.test")


class World:
    def __init__(self, tmp_path, store, card_id, repo_path, origin, token):
        self.tmp_path, self.store, self.card_id = tmp_path, store, card_id
        self.repo_path, self.origin, self.token = repo_path, origin, token
        self.seen: dict = {}

    def move_base(self, files: dict[str, str]) -> None:
        """someone else lands commits on the remote base. the repo's LOCAL main stays where it was,
        as on a real machine where nobody pulled"""
        other = self.tmp_path / f"other-{len(list(self.tmp_path.iterdir()))}"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(other)], check=True)
        _identity(other)
        for name, text in files.items():
            (other / name).parent.mkdir(parents=True, exist_ok=True)
            (other / name).write_text(text)
        git(other, "add", "-A")
        git(other, "commit", "-qm", "base moves")
        git(other, "push", "-q", "origin", "main")

    def run(self) -> lifecycle.LifecycleResult:
        return lifecycle.run_card_lifecycle(
            self.store, self.card_id, backend=ContainerBackend(), token_path=self.token
        )

    def notes(self) -> str:
        return "\n---\n".join(c["body"][:600] for c in self.store.list_comments(self.card_id))

    def lease_events(self) -> list[dict]:
        return [
            e["payload"]
            for e in self.store.list_events(self.card_id)
            if e["kind"] == "lease_check_finished"
        ]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    git(repo_path, "init", "-q", "-b", "main")
    _identity(repo_path)
    (repo_path / "src").mkdir()
    (repo_path / "src/a.py").write_text("a = 1\n")
    (repo_path / "src/shared.py").write_text("s = 1\n")
    git(repo_path, "add", "-A")
    git(repo_path, "commit", "-qm", "initial")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(repo_path), str(origin)], check=True)
    git(repo_path, "remote", "add", "origin", str(origin))
    git(repo_path, "fetch", "-q", "origin")

    token = tmp_path / "token"
    token.write_text("secret")
    token.chmod(0o600)

    store = Store(tmp_path / "board.db")
    board = store.create_board("b")
    repo = store.create_repo(
        board["id"], "r", str(repo_path), "main", test_command="true", image="img:latest"
    )
    card = store.create_card(
        board["id"],
        repo["id"],
        "change a",
        description="a should be bigger",
        criteria=["a is bigger"],
        tasks=["change it"],
        leases=["src/**"],
    )
    # the card's branch, cut from the OLD base, already holding one commit of its own in the lease
    tree = create_worktree(repo_path, card["id"], base="main")
    (tree.path / "src/a.py").write_text("a = 2\n")
    git(tree.path, "commit", "-qam", "card work")

    w = World(tmp_path, store, card["id"], repo_path, origin, token)

    def _gate(store_, card_id, work_path, repo_):
        w.seen["gate_saw_claude_dir"] = (Path(work_path) / ".claude").exists()
        w.seen["gate_tree_files"] = sorted(
            str(p.relative_to(work_path))
            for p in Path(work_path).rglob("*")
            if p.is_file() and not {".git", ".claude"} & set(p.relative_to(work_path).parts)
        )
        return GateResult(passed=True, command="true", exit_code=0, output="ok")

    monkeypatch.setattr(lifecycle, "run_test_gate", _gate)
    monkeypatch.setattr(
        lifecycle, "run_review", lambda *a, **k: ReviewResult(approved=True, findings=[])
    )
    monkeypatch.setattr(
        lifecycle,
        "open_merge_request",
        lambda *a, **k: MergeRequestResult(opened=True, branch="card/x", url="https://x/pull/1"),
    )
    yield w
    store.close()


def _agent(monkeypatch, world, actions):
    """the container's agent: `actions(clone_path)` edits the clone the container would mount"""

    def _run_process(store, card_id, cmd, cwd=None, **kwargs):
        world.seen["prompt"] = kwargs.get("stream_prompt") or ""
        world.seen["cmd"] = cmd
        actions(Path(cwd))
        return RunResult(
            subtype="success",
            is_error=False,
            blocked_reason_code=None,
            session_id="s",
            total_cost_usd=0.01,
            num_turns=1,
            result_text="done",
        )

    monkeypatch.setattr(backends, "run_process", _run_process)


def _edit_own_file(clone):
    (clone / "src/a.py").write_text("a = 3\n")
    git(clone, "commit", "-qam", "more card work")


def test_a_stale_branch_is_current_with_the_base_before_the_worker_and_the_gate(world, monkeypatch):
    world.move_base({"README.md": "base moved on\n"})
    _agent(monkeypatch, world, _edit_own_file)

    result = world.run()

    assert result.phase == "opened", world.notes()
    assert "README.md" in world.seen["gate_tree_files"]  # the gate saw the base's commit
    assert world.lease_events()[-1]["passed"] is True


def test_a_conflicting_base_is_handed_to_the_worker_and_its_merge_is_not_a_lease_violation(
    world, monkeypatch
):
    """the live-board failure: the base and the card both changed src/a.py, so the worker was told
    to merge it, and the lease check then blamed the card for every file the base brought"""
    world.move_base({"src/a.py": "a = 9\n", "README.md": "base moved on\n", "docs/x.md": "x\n"})

    def _merge_and_resolve(clone):
        merged = subprocess.run(
            ["git", "-C", str(clone), "merge", "--no-edit", "origin/main"],
            capture_output=True,
            text=True,
            check=False,
        )
        world.seen["agent_merge_output"] = merged.stdout + merged.stderr
        if merged.returncode:
            (clone / "src/a.py").write_text("a = 10\n")
            git(clone, "add", "src/a.py")
            git(clone, "commit", "-q", "--no-edit")

    _agent(monkeypatch, world, _merge_and_resolve)

    result = world.run()

    assert "src/a.py" in world.seen["prompt"]  # the conflict reached the worker as a note
    assert result.phase == "opened", world.notes()
    assert world.lease_events()[-1]["passed"] is True, world.lease_events()[-1]
    assert "README.md" in world.seen["gate_tree_files"]


def test_a_genuine_edit_outside_the_lease_is_still_blocked(world, monkeypatch):
    world.move_base({"README.md": "base moved on\n"})

    def _write_outside(clone):
        (clone / "docs").mkdir()
        (clone / "docs/own.md").write_text("the card wrote this\n")
        git(clone, "add", "docs")
        git(clone, "commit", "-qm", "outside the lease")

    _agent(monkeypatch, world, _write_outside)

    result = world.run()

    assert result.blocked_reason_code == "LEASE_CONFLICT"
    assert world.lease_events()[-1]["outside"] == ["docs/own.md"]


def _guard_mount(cmd) -> Path:
    """the host dir the worker's docker command mounts read-only at /smortboard"""
    mounts = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-v"]
    return next(Path(m.rsplit(":", 2)[0]) for m in mounts if m.endswith(":/smortboard:ro"))


def test_the_guards_live_outside_the_worktree_for_exactly_one_attempt(world, monkeypatch):
    """card 05de2d52 blocked TESTS_FAILED: the gate mounts the worktree, and the repo's own ruff
    linted the board's .claude/bash_guard.py. the guards now sit in a dir of their own, carry the
    repo's remembered globs, and a reused worktree loses the files an older board left in it"""
    worktree = worktree_path(world.repo_path, world.card_id)
    legacy = worktree / ".claude"
    legacy.mkdir()
    (legacy / "bash_guard.py").write_text('print(f"BASH_ESCAPE: stale")\n')
    (legacy / "lease.json").write_text("{}")
    repo_id = world.store.get_card(world.card_id)["repo_id"]
    world.store.remember_lease_paths(repo_id, ["docs/**"])

    def _look_then_edit(clone):
        guards = _guard_mount(world.seen["cmd"])
        world.seen["guards"] = guards
        world.seen["guard_files"] = {p.name for p in guards.iterdir()}
        world.seen["lease"] = json.loads((guards / "lease.json").read_text())
        world.seen["worktree_claude_during_run"] = legacy.exists()
        _edit_own_file(clone)

    _agent(monkeypatch, world, _look_then_edit)

    result = world.run()

    assert result.phase == "opened", world.notes()
    guards = world.seen["guards"]
    assert world.repo_path.resolve() not in guards.resolve().parents
    assert {"settings.json", "lease.json", "lease_guard.py", "bash_guard.py"} <= world.seen[
        "guard_files"
    ]
    assert world.seen["lease"]["path_globs"] == ["src/**"]
    assert world.seen["lease"]["remembered_globs"] == ["docs/**"]
    assert world.seen["worktree_claude_during_run"] is False
    assert world.seen["gate_saw_claude_dir"] is False
    assert not guards.exists()  # removed once the attempt ended
