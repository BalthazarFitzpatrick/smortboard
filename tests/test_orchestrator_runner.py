"""the production orchestrator runner's docker command. Docker and the model are faked - only the
functions that would otherwise shell out - but the read-only clones are built for real."""

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from smortboard.exec.runner import RunResult
from smortboard.orchestrator import (
    ORCHESTRATOR_ALLOWED_TOOLS,
    run_orchestrator_turn,
)
from smortboard.store.api import Store

GOOD_JSON = json.dumps({"reply": "ok", "plan": "p", "cards": []})


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)


def _make_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    _git(path, "config", "user.email", "test@test.com")
    _git(path, "config", "user.name", "test")
    (path / "README.md").write_text("hello\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "initial")


def _wire(monkeypatch, calls, result_text=GOOD_JSON, token="t0k3n"):
    monkeypatch.setattr("smortboard.orchestrator.docker_available", lambda: True)
    monkeypatch.setattr("smortboard.orchestrator.read_card_token", lambda p=None: token)

    def _fake_run_process(store, card_id, cmd, stdin_text=None, container_name=None, **kwargs):
        calls.append({"cmd": cmd, "stdin": stdin_text, "container_name": container_name})
        return RunResult(
            subtype="success",
            is_error=False,
            blocked_reason_code=None,
            session_id="s1",
            total_cost_usd=0.01,
            num_turns=1,
            result_text=result_text,
        )

    monkeypatch.setattr("smortboard.orchestrator.run_process", _fake_run_process)


def test_the_command_mounts_clones_and_extra_paths_read_only(tmp_path, monkeypatch):
    repo = tmp_path / "src"
    _make_repo(repo)
    shots = tmp_path / "screens"
    shots.mkdir()
    missing = tmp_path / "gone"
    calls = []
    _wire(monkeypatch, calls)

    with Store(tmp_path / "b.db") as store:
        board = store.create_board("dev")
        store.create_repo(board["id"], "src", str(repo), "main", test_command="uv run pytest")
        store.set_setting("mission_control_read_paths", json.dumps([str(shots), str(missing)]))
        result = run_orchestrator_turn(store, board["id"], "plan it")

    assert result.error is None
    cmd = calls[0]["cmd"]
    joined = shlex.join(cmd)

    # a read-only clone mounted under /repos, and the existing extra path under /extra
    assert any(":/repos/src:ro" in c for c in cmd)
    assert f"{shots}:/extra/screens:ro" in cmd
    # the missing path was skipped, not mounted
    assert all("/extra/gone" not in c for c in cmd)

    # every -v mount is read-only
    vmounts = [cmd[i + 1] for i, c in enumerate(cmd) if c == "-v"]
    assert vmounts and all(m.endswith(":ro") for m in vmounts)

    # working dir is the mount parent, never a clone (a repo's own settings must not apply)
    assert cmd[cmd.index("-w") + 1] == "/"

    # read-only tools allowed, everything that writes or shells out refused
    for tool in ORCHESTRATOR_ALLOWED_TOOLS:
        assert f"--allowedTools {tool}" in joined
    for tool in ("Edit", "Write", "Bash"):
        assert f"--disallowedTools {tool}" in joined

    # named so the turn can be stopped
    assert "--name" in cmd
    assert calls[0]["container_name"] == cmd[cmd.index("--name") + 1]


def test_a_missing_read_path_becomes_a_board_message_and_the_turn_still_runs(tmp_path, monkeypatch):
    missing = tmp_path / "nope"
    calls = []
    _wire(monkeypatch, calls)

    with Store(tmp_path / "b.db") as store:
        board = store.create_board("dev")
        store.set_setting("mission_control_read_paths", json.dumps([str(missing)]))
        result = run_orchestrator_turn(store, board["id"], "plan it")
        notes = [
            m["body"]
            for m in store.list_orchestrator_messages(board["id"])
            if m["author"] == "board"
        ]

    assert result.error is None
    assert calls  # the turn still ran
    assert any(str(missing) in note for note in notes)


def test_the_credential_travels_on_stdin_not_argv(tmp_path, monkeypatch):
    calls = []
    _wire(monkeypatch, calls, token="s3cr3t-token")

    with Store(tmp_path / "b.db") as store:
        board = store.create_board("dev")
        run_orchestrator_turn(store, board["id"], "plan it")

    call = calls[0]
    joined = shlex.join(call["cmd"])
    assert "-e" not in call["cmd"] and "--env" not in call["cmd"]
    assert "s3cr3t-token" not in joined
    assert call["stdin"] == "s3cr3t-token\n"


def test_no_docker_fails_the_turn_without_crashing(tmp_path, monkeypatch):
    monkeypatch.setattr("smortboard.orchestrator.docker_available", lambda: False)
    with Store(tmp_path / "b.db") as store:
        board = store.create_board("dev")
        result = run_orchestrator_turn(store, board["id"], "plan it")
        assert result.error is not None
        authors = [m["author"] for m in store.list_orchestrator_messages(board["id"])]
        assert "board" in authors


@pytest.mark.parametrize("raw", ["not json", json.dumps({"a": 1}), ""])
def test_a_malformed_read_paths_setting_is_no_paths_not_a_crash(tmp_path, raw):
    with Store(tmp_path / "b.db") as store:
        store.set_setting("mission_control_read_paths", raw)
        assert store.mission_control_read_paths() == []
