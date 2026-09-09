"""backend selection and the container backend's mounts/cleanup/handback.

Never invokes the real `claude` binary and never requires Docker to be present - `docker` itself
is faked the same way runner tests fake the event stream, by monkeypatching the functions that
would otherwise shell out to it. The one exception is test_container_backend_real_docker_smoke,
which is skipped unless Docker is actually usable on the machine running the suite.
"""

import shlex
import subprocess
import tempfile
from pathlib import Path

import pytest

from smortboard.exec import backends
from smortboard.exec.backends import (
    CardRuntimeUnavailable,
    CardTokenMissing,
    ContainerBackend,
    card_token_path,
    docker_available,
    read_card_token,
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


def test_the_token_is_never_on_the_command_line_or_a_mount(tmp_path):
    """it arrives on stdin instead. a mount meant a plaintext credential had to exist on the
    operator's disk; an env var would show up in `docker inspect`."""
    backend = ContainerBackend(image="img")
    cmd = backend._docker_command(tmp_path / "clone", "prompt", tmp_path / "s.json", "sonnet", None)
    joined = shlex.join(cmd)
    assert "-e" not in cmd and "--env" not in cmd
    assert "/run/secrets" not in joined
    assert "CLAUDE_CODE_OAUTH_TOKEN" in joined  # read from stdin, never assigned a literal
    assert "read -r CLAUDE_CODE_OAUTH_TOKEN" in joined
    # only the clone is mounted
    assert cmd.count("-v") == 1  # the list, not the string: a temp path can contain "-v"
    assert "-i" in cmd  # stdin has to stay open long enough to hand it over


def test_the_credential_reaches_the_container_only_through_stdin(tmp_path, monkeypatch):
    seen = {}

    def _fake_run_process(store, card_id, cmd, cwd=None, env=None, stdin_text=None):
        seen["stdin"] = stdin_text
        seen["cmd"] = shlex.join(cmd)
        return "result"

    monkeypatch.setattr("smortboard.exec.backends.run_process", _fake_run_process)
    monkeypatch.setattr("smortboard.exec.backends.read_card_token", lambda p=None: "s3cret")
    monkeypatch.setattr("smortboard.exec.backends.repo_root_of_worktree", lambda p: tmp_path)
    monkeypatch.setattr("smortboard.exec.backends.current_branch", lambda p: "card/x")
    monkeypatch.setattr(ContainerBackend, "_clone", lambda self, w, c, b: c.mkdir(exist_ok=True))
    monkeypatch.setattr(ContainerBackend, "_fetch_back", lambda self, r, c, b: None)

    ContainerBackend(image="img").run_card(None, "card", tmp_path, "prompt", tmp_path / "s.json")

    assert seen["stdin"] == "s3cret\n"
    assert "s3cret" not in seen["cmd"]


def test_docker_command_uses_the_configured_image(tmp_path):
    backend = ContainerBackend(image="my-registry/smortboard-card:1.2.3")
    clone_path = tmp_path / "clone"
    token_path = tmp_path / "token"
    clone_path.mkdir()
    token_path.write_text("secret")
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}")

    cmd = backend._docker_command(clone_path, "p", settings_path, "sonnet", None)
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

    def _fake_run_process(store, card_id, cmd, cwd=None, env=None, stdin_text=None):
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
        with pytest.raises(CardTokenMissing):
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


def test_the_default_token_path_is_honoured(tmp_path, monkeypatch):
    """exercises card_token_path()'s default branch, which nothing reached before - ruff caught an
    undefined constant on that line that the whole suite had walked straight past."""
    monkeypatch.setattr("smortboard.exec.backends._credential_store_token", lambda: None)
    token = tmp_path / "card_token"
    token.write_text("from-the-file\n")
    monkeypatch.setenv("SMORTBOARD_CARD_TOKEN_PATH", str(token))
    assert card_token_path() == token
    assert read_card_token() == "from-the-file"


def test_the_credential_store_wins_over_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "smortboard.exec.backends._credential_store_token", lambda: "from-the-keychain"
    )
    token = tmp_path / "card_token"
    token.write_text("from-the-file\n")
    monkeypatch.setenv("SMORTBOARD_CARD_TOKEN_PATH", str(token))
    assert read_card_token() == "from-the-keychain"


def test_the_instructions_name_the_platform_actually_in_use(tmp_path, monkeypatch):
    """the message used to tell a Windows user to run a macOS command."""
    monkeypatch.setattr("smortboard.exec.backends._credential_store_token", lambda: None)
    monkeypatch.setenv("SMORTBOARD_CARD_TOKEN_PATH", str(tmp_path / "absent"))

    monkeypatch.setattr("smortboard.exec.backends.os.name", "nt")
    with pytest.raises(CardTokenMissing) as win:
        read_card_token()
    assert "Credential Manager" in str(win.value)
    assert "security add-generic-password" not in str(win.value)

    monkeypatch.setattr("smortboard.exec.backends.os.name", "posix")
    monkeypatch.setattr("smortboard.exec.backends.sys.platform", "darwin")
    with pytest.raises(CardTokenMissing) as mac:
        read_card_token()
    assert "security add-generic-password" in str(mac.value)
