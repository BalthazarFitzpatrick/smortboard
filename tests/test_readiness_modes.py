"""what a board needs to run depends on its mode: sealed needs docker and the card image, open
needs the agent's cli. both /api/runtime and the pre-flight rows follow the board's own mode."""

import subprocess

import pytest

from smortboard import preflight
from smortboard.exec import backends
from smortboard.server import runs
from smortboard.store.api import Store


def _runner(cmd, timeout=10, cwd=None):
    return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")


def _store(tmp_path, mode):
    store = Store(tmp_path / "board.db")
    board = store.create_board("b")
    store.create_repo(board["id"], "r", str(tmp_path / "r"), "main", test_command="true")
    if mode == "open":
        store.set_setting("allow_open_mode", "on")
        store.set_board_run_mode(board["id"], "open")
    return store


def _rows(store):
    return {check["id"]: check for check in preflight.run_preflight(store, runner=_runner)}


def test_an_open_board_needs_no_docker_or_repo_image(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "docker_available", lambda: False)
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/claude")
    with _store(tmp_path, "open") as store:
        rows = _rows(store)
    assert rows["docker"]["status"] == "ok" and "not needed" in rows["docker"]["detail"]
    assert rows["card-image"]["status"] == "ok" and "not needed" in rows["card-image"]["detail"]
    assert rows["claude-cli"]["status"] == "ok"
    assert not [key for key in rows if key.endswith("-image")]


def test_an_open_board_names_a_missing_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "docker_available", lambda: False)
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    with _store(tmp_path, "open") as store:
        rows = _rows(store)
    assert rows["claude-cli"]["status"] == "fail"
    assert "install claude code" in rows["claude-cli"]["fix"]


def test_a_sealed_board_still_needs_docker(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "docker_available", lambda: False)
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    with _store(tmp_path, "sealed") as store:
        rows = _rows(store)
    assert rows["docker"]["status"] == "fail"
    assert "claude-cli" not in rows, "no board is open, so the cli is not asked about"
    assert [key for key in rows if key.endswith("-image") and key.startswith("repo-")]


def test_runtime_answers_both_modes_and_keeps_the_sealed_answer_on_top(tmp_path, monkeypatch):
    monkeypatch.setattr(backends, "docker_available", lambda: False)
    monkeypatch.setattr(backends, "card_token_available", lambda token_path=None: True)
    monkeypatch.setattr(runs.shutil, "which", lambda name: "/usr/bin/claude")
    answer = runs.Readiness(token_path=tmp_path / "token").check()
    assert answer["ready"] is False
    assert any("docker is not running" in line for line in answer["missing"])
    assert answer["modes"]["sealed"]["ready"] is False
    assert answer["modes"]["open"] == {"ready": True, "missing": []}


def test_runtime_names_what_an_open_board_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(backends, "docker_available", lambda: True)
    monkeypatch.setattr(backends, "card_image_available", lambda: True)
    monkeypatch.setattr(backends, "card_token_available", lambda token_path=None: False)
    monkeypatch.setattr(runs.shutil, "which", lambda name: None)
    answer = runs.Readiness(token_path=tmp_path / "token").check()
    missing = answer["modes"]["open"]["missing"]
    assert any("claude cli is not on PATH" in line for line in missing)
    assert any("no card credential" in line for line in missing)
    assert answer["modes"]["sealed"]["ready"] is False


@pytest.mark.parametrize("mode", ["sealed", "open"])
def test_each_mode_is_decided_by_the_board_not_the_machine(tmp_path, mode):
    with _store(tmp_path, mode) as store:
        board_id = store.list_boards()[0]["id"]
        assert store.run_mode(board_id) == mode
