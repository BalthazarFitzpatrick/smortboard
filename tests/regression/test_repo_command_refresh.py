"""a repo's test_command must not be frozen at run start.

Placed under tests/regression/ rather than alongside test_gates.py / test_backends.py: this
card's path lease is `tests/**/*.py`, and fnmatch (what lease_guard.py actually uses) requires a
literal '/' after the wildcard, so a file directly under tests/ does not match it - only a nested
one does. Flagged for Balthazar; the fix itself lives in review/gates.py and exec/backends.py,
both already covered by the lease.

Both the board's own test gate (review/gates.py) and the worker's Bash allowlist/brief
(exec/backends.py) took a `repo` dict from whoever called them and trusted it for the rest of a
run, including every fix round. A card on the fix route calls both again per round, so an
operator's edit to test_command between rounds was silently ignored until a fresh run re-fetched
the repo. Both now re-read the repo row by id immediately before use.
"""

import subprocess

from smortboard.exec import backends
from smortboard.exec.backends import ContainerBackend
from smortboard.exec.runner import RunResult
from smortboard.exec.worktrees import create_worktree
from smortboard.review.gates import run_test_gate
from smortboard.store.api import Store


def _init_repo(path):
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "first"], check=True, capture_output=True
    )


def _fake_docker_run(monkeypatch, capture):
    class _Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def _run(cmd, **kwargs):
        capture.append(cmd)
        return _Completed()

    monkeypatch.setattr(subprocess, "run", _run)
    monkeypatch.setattr("smortboard.review.gates.docker_available", lambda: True)


def test_the_gate_re_reads_test_command_rather_than_trusting_a_stale_repo_dict(
    tmp_path, monkeypatch
):
    """a caller holding a repo dict fetched before an operator's edit must still see the edit -
    proves the fix scoped to review/gates.py"""
    seen = []
    _fake_docker_run(monkeypatch, seen)
    with Store(tmp_path / "board.db") as store:
        board = store.create_board("b")
        repo = store.create_repo(board["id"], "r", str(tmp_path), "main", test_command="old cmd")
        card = store.create_card(board["id"], repo["id"], "a card")
        stale = dict(repo)  # what a round's caller fetched before the edit below
        store.set_repo_test_command(repo["id"], "new cmd")
        result = run_test_gate(store, card["id"], tmp_path, stale)
    assert result.command == "new cmd"
    assert seen[0][-1].endswith("new cmd")


def test_a_fresh_run_still_reads_the_current_repo_directly(tmp_path, monkeypatch):
    """no regression: a repo dict with no edit behind it still drives the gate as before"""
    seen = []
    _fake_docker_run(monkeypatch, seen)
    with Store(tmp_path / "board.db") as store:
        board = store.create_board("b")
        repo = store.create_repo(board["id"], "r", str(tmp_path), "main", test_command="only cmd")
        card = store.create_card(board["id"], repo["id"], "a card")
        result = run_test_gate(store, card["id"], tmp_path, repo)
    assert result.command == "only cmd"


def test_the_worker_container_re_reads_test_command_for_its_bash_allowlist_and_brief(
    tmp_path, monkeypatch
):
    """a card's fix rounds all call ContainerBackend.run_card again with the same stale repo the
    lifecycle snapshotted at run start - proves the fix scoped to exec/backends.py: the allowlist
    and the brief the agent reads must both carry the edited command, not the old one"""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _init_repo(repo_path)
    info = create_worktree(repo_path, "card-x")
    token = tmp_path / "token"
    token.write_text("secret")
    token.chmod(0o600)

    seen = {}

    def _fake_run_process(store, card_id, cmd, cwd=None, env=None, **kwargs):
        seen["cmd"] = cmd
        seen["prompt"] = kwargs.get("stream_prompt")
        return RunResult(
            subtype="success",
            is_error=False,
            blocked_reason_code=None,
            session_id="s1",
            total_cost_usd=0.01,
            num_turns=1,
            result_text="done",
        )

    monkeypatch.setattr(backends, "run_process", _fake_run_process)

    backend = ContainerBackend()
    with Store(tmp_path / "board.sqlite3") as store:
        board = store.create_board("b")
        repo = store.create_repo(board["id"], "r", str(repo_path), "main", test_command="old cmd")
        card = store.create_card(board["id"], repo["id"], "a card", leases=["x"])
        stale = dict(repo)  # captured before the edit below, like lifecycle's run-start snapshot
        store.set_repo_test_command(repo["id"], "new cmd")
        backend.run_card(
            store,
            card["id"],
            info.path,
            "prompt",
            tmp_path / "settings.json",
            repo=stale,
            token_path=token,
        )

    assert "new cmd" in seen["prompt"]
    assert "old cmd" not in seen["prompt"]
    assert any("new cmd" in part for part in seen["cmd"])
    assert not any("old cmd" in part for part in seen["cmd"])
