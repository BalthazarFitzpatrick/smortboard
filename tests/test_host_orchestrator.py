"""mission control and fold in open mode: a host process on the read-only snapshot, told the real
paths, with no docker. run_process is replaced by a recorder; the clones are real git clones."""

import json
import subprocess

import pytest

from smortboard import orchestrator
from smortboard.exec import repo_snapshot
from smortboard.exec.repo_snapshot import build_repo_snapshot, to_host_paths
from smortboard.exec.runner import RunResult
from smortboard.store.api import Store


def _repo(path):
    path.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@t"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", "-C", str(path), *args], check=True)
    (path / "a.txt").write_text("x")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "c"], check=True, capture_output=True)
    return path


def test_the_snapshot_remembers_where_each_mount_really_is(tmp_path):
    extra = tmp_path / "extra-data"
    extra.mkdir()
    snapshot = build_repo_snapshot(
        [{"name": "r", "path": str(_repo(tmp_path / "r")), "default_branch": "main"}],
        [str(extra)],
        refresher=lambda *a: None,
    )
    try:
        assert set(snapshot.host_paths) == {"/repos/r", "/extra/extra-data"}
        assert snapshot.host_paths["/extra/extra-data"] == str(extra)
        assert (tmp_path / "r").exists()
        assert snapshot.host_paths["/repos/r"].startswith(str(snapshot._tmp_dir))
    finally:
        snapshot.cleanup()


def test_to_host_paths_rewrites_longest_first():
    mapping = {
        "/extra/data": "/real/data",
        "/extra/shots": "/real/shots",
        "/repos/r": "/tmp/clone/r",
    }
    text = "read /repos/r/a.py, /extra/shots/s.png and /extra/data/x"
    assert to_host_paths(text, mapping) == (
        "read /tmp/clone/r/a.py, /real/shots/s.png and /real/data/x"
    )


@pytest.fixture
def open_board(tmp_path):
    store = Store(tmp_path / "board.db")
    board = store.create_board("b")
    store.create_repo(board["id"], "proj", str(_repo(tmp_path / "proj")), "main")
    store.set_setting("allow_open_mode", "on")
    store.set_board_run_mode(board["id"], "open")
    token = tmp_path / "token"
    token.write_text("test-token")
    token.chmod(0o600)
    yield store, board["id"], token
    store.close()


def _no_docker(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("an open turn must not reach for docker")

    monkeypatch.setattr(orchestrator, "docker_available", boom)


def test_an_open_turn_runs_on_the_host_with_real_paths(open_board, monkeypatch):
    store, board_id, token = open_board
    _no_docker(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "gh-secret")
    seen = {}

    def fake(store_, card_id, cmd, **kwargs):
        seen.update(cmd=cmd, **kwargs)
        return RunResult(
            subtype="success",
            is_error=False,
            blocked_reason_code=None,
            session_id="s",
            total_cost_usd=0.02,
            num_turns=1,
            result_text=None,
            structured_output={"reply": "ok"},
        )

    monkeypatch.setattr(orchestrator, "run_process", fake)
    run = orchestrator._real_runner(store, board_id, token, "system: repos under /repos/proj", [])
    out = run("plan against /repos/proj/a.txt", "sonnet", 1.0)
    assert json.loads(out) == {"reply": "ok"}
    assert seen["cmd"][:2] == ["sh", "-c"]
    assert "/repos/proj" not in seen["cmd"][2], "the container path is rewritten to the real clone"
    assert "smortboard-mc-" in seen["cmd"][2]
    assert seen["new_session"] is True
    assert "GH_TOKEN" not in seen["env"]
    assert seen["stdin_text"] == "test-token\n"
    assert "test-token" not in seen["cmd"][2]
    assert "< /dev/null" in seen["cmd"][2]


def test_a_codex_turn_runs_on_the_host_with_its_own_codex_home(open_board, monkeypatch):
    from pathlib import Path

    from smortboard import profiles

    store, board_id, token = open_board
    _no_docker(monkeypatch)
    monkeypatch.setattr(profiles, "active_profile", lambda lab="anthropic": "work")
    monkeypatch.setattr(profiles, "profile_kind", lambda name, lab="anthropic": "auth_json")
    monkeypatch.setattr(profiles, "read_profile_token", lambda lab, name: '{"tokens": "x"}')
    seen = {}

    def fake(store_, card_id, cmd, **kwargs):
        home = Path(kwargs["env"]["CODEX_HOME"])
        seen.update(cmd=cmd, hooks=(home / "hooks.json").is_file(), **kwargs)
        return RunResult(
            subtype="success",
            is_error=False,
            blocked_reason_code=None,
            session_id="s",
            total_cost_usd=0.0,
            num_turns=1,
            result_text=None,
            structured_output={"reply": "ok"},
        )

    monkeypatch.setattr(orchestrator, "run_process", fake)
    run = orchestrator._real_runner(store, board_id, token, "system", [])
    assert json.loads(run("plan", "openai/gpt-5.6-luna", 1.0)) == {"reply": "ok"}
    script = seen["cmd"][2]
    assert "codex exec" in script and "--sandbox read-only" in script
    assert seen["hooks"] is True and seen["new_session"] is True


def test_a_sealed_turn_still_needs_docker(open_board, monkeypatch):
    store, board_id, token = open_board
    store.set_board_run_mode(board_id, "sealed")
    monkeypatch.setattr(orchestrator, "docker_available", lambda: False)
    run = orchestrator._real_runner(store, board_id, token, "system", [])
    with pytest.raises(RuntimeError, match="docker is not running"):
        run("plan", "sonnet", 1.0)


def test_the_module_still_exports_the_mount_names():
    assert repo_snapshot.REPOS_MOUNT == "/repos" and repo_snapshot.EXTRA_MOUNT == "/extra"
