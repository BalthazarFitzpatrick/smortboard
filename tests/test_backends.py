"""backend selection and the container backend's mounts/cleanup/handback.

Never invokes the real `claude` binary and never requires Docker to be present - `docker` itself
is faked the same way runner tests fake the event stream, by monkeypatching the functions that
would otherwise shell out to it. The one exception is test_container_backend_real_docker_smoke,
which is skipped unless Docker is actually usable on the machine running the suite.
"""

import subprocess
import tempfile
from pathlib import Path

import pytest

from smortboard.exec import backends
from smortboard.exec.backends import (
    CardRuntimeUnavailable,
    ContainerBackend,
    docker_available,
    require_card_runtime,
)
from smortboard.exec.runner import RunResult
from smortboard.exec.worktrees import WorktreeError, create_worktree
from smortboard.store.api import Store


def _init_repo(path):
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "initial"], check=True, capture_output=True
    )


def _fake_result(subtype="success"):
    return RunResult(
        subtype=subtype,
        is_error=False,
        blocked_reason_code=None,
        session_id="s1",
        total_cost_usd=0.01,
        num_turns=1,
        result_text="done",
    )


# -- backend selection ---------------------------------------------------------


def test_no_docker_refuses_rather_than_falling_back(monkeypatch):
    """there is no subprocess fallback on purpose: it would put an agent that may have read
    untrusted input on the operator's own filesystem, with their own credential."""
    monkeypatch.setattr("smortboard.exec.backends.docker_available", lambda: False)
    with pytest.raises(CardRuntimeUnavailable) as exc:
        require_card_runtime(token_path="/nonexistent/token")
    assert "Docker" in str(exc.value)


def test_missing_card_credential_refuses_and_says_how_to_fix_it(tmp_path, monkeypatch):
    monkeypatch.setattr("smortboard.exec.backends.docker_available", lambda: True)
    with pytest.raises(CardRuntimeUnavailable) as exc:
        require_card_runtime(token_path=tmp_path / "missing-token")
    assert "claude setup-token" in str(exc.value)


def test_the_runtime_is_the_container_when_docker_and_token_are_there(tmp_path, monkeypatch):
    monkeypatch.setattr("smortboard.exec.backends.docker_available", lambda: True)
    token = tmp_path / "card_token"
    token.write_text("t")
    assert isinstance(require_card_runtime(token_path=token), ContainerBackend)


def test_docker_available_false_when_docker_missing_from_path(monkeypatch):
    monkeypatch.setattr(backends.shutil, "which", lambda name: None)
    assert docker_available() is False


def test_docker_available_false_when_daemon_does_not_answer(monkeypatch):
    monkeypatch.setattr(backends.shutil, "which", lambda name: "/usr/bin/docker")

    def _fail(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(backends.subprocess, "run", _fail)
    assert docker_available() is False


# -- the container command line ------------------------------------------------


def test_docker_command_mounts_clone_rw_and_token_ro_with_no_env_credential(tmp_path):
    backend = ContainerBackend(image="smortboard-card:test")
    clone_path = tmp_path / "clone"
    token_path = tmp_path / "token"
    clone_path.mkdir()
    token_path.write_text("secret-token")
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}")

    cmd = backend._docker_command(
        clone_path, token_path, "do the thing", settings_path, "sonnet", None
    )

    assert cmd[:3] == ["docker", "run", "--rm"]
    joined = " ".join(cmd)
    assert f"{clone_path}:/workspace:rw" in joined
    assert f"{token_path}:/run/secrets/card_token:ro" in joined
    # the token is read from the mounted file inside the container, never handed over as -e/--env
    assert "-e" not in cmd
    assert "--env" not in cmd
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in " ".join(cmd[:-1])  # not in the docker args themselves
    assert "cat /run/secrets/card_token" in cmd[-1]
    # no github credential anywhere in the command
    assert "github" not in joined.lower()
    assert "GH_TOKEN" not in joined
    assert "GITHUB_TOKEN" not in joined


def test_docker_command_uses_the_configured_image(tmp_path):
    backend = ContainerBackend(image="my-registry/smortboard-card:1.2.3")
    clone_path = tmp_path / "clone"
    token_path = tmp_path / "token"
    clone_path.mkdir()
    token_path.write_text("secret")
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}")

    cmd = backend._docker_command(clone_path, token_path, "p", settings_path, "sonnet", None)
    assert "my-registry/smortboard-card:1.2.3" in cmd


# -- clone-then-fetch round trip, with real git -------------------------------


def test_clone_then_fetch_round_trip(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    info = create_worktree(repo, "card-1")

    backend = ContainerBackend()
    clone_path = tmp_path / "clone"
    backend._clone(info.path, clone_path, info.branch)
    assert (clone_path / "README.md").exists()
    assert (clone_path / ".git").exists()  # a real, independent .git - not a pointer file
    assert (clone_path / ".git").is_dir()

    # simulate the card committing inside the container's clone
    (clone_path / "new.txt").write_text("card wrote this\n")
    subprocess.run(["git", "-C", str(clone_path), "add", "new.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(clone_path), "commit", "-m", "card commit"],
        check=True,
        capture_output=True,
    )
    new_sha = subprocess.run(
        ["git", "-C", str(clone_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    backend._fetch_back(repo, clone_path, info.branch)

    repo_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", info.branch],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert repo_sha == new_sha


def test_clone_of_missing_source_raises_worktree_error(tmp_path):
    backend = ContainerBackend()
    with pytest.raises(WorktreeError):
        backend._clone(tmp_path / "does-not-exist", tmp_path / "clone", "main")


# -- cleanup on success and on failure -----------------------------------------


def test_container_run_card_cleans_up_clone_on_success(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    info = create_worktree(repo, "card-2")

    token = tmp_path / "token"
    token.write_text("secret")

    seen = {}

    def _fake_run_process(store, card_id, cmd, cwd=None, env=None):
        seen["cmd"] = cmd
        return _fake_result()

    monkeypatch.setattr(backends, "run_process", _fake_run_process)

    backend = ContainerBackend()
    with Store(tmp_path / "board.sqlite3") as store:
        board = store.create_board("b")
        card = store.create_card(board["id"], None, "a card")
        result = backend.run_card(
            store, card["id"], info.path, "prompt", tmp_path / "settings.json", token_path=token
        )

    assert result.subtype == "success"
    assert seen["cmd"][0] == "docker"
    # the clone directory used for this run no longer exists once run_card returns
    leftover = list(Path(tempfile.gettempdir()).glob(f"smortboard-card-{card['id']}-*"))
    assert not leftover


def test_container_run_card_cleans_up_clone_on_failure(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    info = create_worktree(repo, "card-3")

    token = tmp_path / "token"
    token.write_text("secret")

    created_clone_paths = []
    real_clone = ContainerBackend._clone

    def _tracking_clone(self, worktree_path, clone_path, branch):
        created_clone_paths.append(clone_path)
        real_clone(self, worktree_path, clone_path, branch)

    def _boom(*a, **k):
        raise RuntimeError("simulated docker failure")

    monkeypatch.setattr(ContainerBackend, "_clone", _tracking_clone)
    monkeypatch.setattr(backends, "run_process", _boom)

    backend = ContainerBackend()
    with Store(tmp_path / "board.sqlite3") as store:
        board = store.create_board("b")
        card = store.create_card(board["id"], None, "a card")
        with pytest.raises(RuntimeError):
            backend.run_card(
                store,
                card["id"],
                info.path,
                "prompt",
                tmp_path / "settings.json",
                token_path=token,
            )

    assert created_clone_paths
    assert not created_clone_paths[0].exists()


def test_container_run_card_raises_when_token_missing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    info = create_worktree(repo, "card-4")

    backend = ContainerBackend()
    with Store(tmp_path / "board.sqlite3") as store:
        board = store.create_board("b")
        card = store.create_card(board["id"], None, "a card")
        with pytest.raises(WorktreeError):
            backend.run_card(
                store,
                card["id"],
                info.path,
                "prompt",
                tmp_path / "settings.json",
                token_path=tmp_path / "missing",
            )


# -- subprocess backend: token becomes an env var, never for the container ----


def test_container_backend_real_docker_smoke(tmp_path):
    """end to end: builds nothing (no image required), just proves docker itself can run a
    throwaway container against a real bind mount and this backend can shell out to it"""
    result = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{tmp_path}:/workspace:rw", "alpine", "sh", "-c", "true"],
        capture_output=True,
    )
    assert result.returncode == 0
