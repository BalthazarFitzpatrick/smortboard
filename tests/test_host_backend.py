"""the host backend (open run mode): the environment a card sees, a push that cannot succeed, the
lease checks that still hold, a stop that ends the whole process group, and how a board picks it.

Never starts the real `claude`: run_process is replaced by a function that edits the worktree the
way an agent would. The process-group test starts real, harmless `sleep` children.
"""

import os
import shutil
import signal
import subprocess
import sys
import time

import pytest

from smortboard import lifecycle
from smortboard.exec import backends
from smortboard.exec.backends import (
    CardRuntimeUnavailable,
    HostBackend,
    host_env,
    require_host_runtime,
    write_host_guards,
)
from smortboard.exec.runner import ProcessHandle, RunResult
from smortboard.exec.worktrees import create_worktree
from smortboard.store.api import Store


def _git(path, *args, env=None):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=False, capture_output=True, text=True, env=env
    )


def _repo(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "README.md").write_text("hello\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "first")
    return path


def _ok():
    return RunResult(
        subtype="success",
        is_error=False,
        blocked_reason_code=None,
        session_id="s1",
        total_cost_usd=0.01,
        num_turns=1,
        result_text="done",
    )


SECRETS = {
    "GH_TOKEN": "gh-secret",
    "GITHUB_TOKEN": "gh-secret",
    "SSH_AUTH_SOCK": "/tmp/agent.sock",
    "AWS_SECRET_ACCESS_KEY": "aws-secret",
    "ANTHROPIC_API_KEY": "api-secret",
}


def test_host_env_is_built_from_an_allowlist(tmp_path, monkeypatch):
    for key, value in SECRETS.items():
        monkeypatch.setenv(key, value)
    env = host_env(tmp_path / "home")
    assert not set(SECRETS) & set(env)
    assert "gh-secret" not in env.values()
    assert env["HOME"] == str(tmp_path / "home")
    assert env["PATH"] == os.environ["PATH"]
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"


def test_a_host_card_cannot_push_to_any_remote(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = _repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "remote", "add", "other", f"file://{origin}")
    _git(repo, "push", "-q", "origin", "main")
    before = _git(origin, "rev-parse", "main").stdout
    (repo / "README.md").write_text("changed\n")
    _git(repo, "commit", "-q", "-am", "second")
    env = host_env(tmp_path / "home")
    for argv in (
        ["push", "origin", "HEAD:main"],
        ["push", "other", "HEAD:main"],
        ["push", str(origin), "HEAD:main"],
        ["push"],
    ):
        run = _git(repo, *argv, env=env)
        assert run.returncode != 0, f"{argv} should have failed: {run.stdout}{run.stderr}"
    assert _git(origin, "rev-parse", "main").stdout == before
    # control: the same push with the normal environment goes through, so the failures above are
    # the scrub's doing and not a broken fixture
    assert _git(repo, "push", "origin", "HEAD:main").returncode == 0
    assert _git(origin, "rev-parse", "main").stdout != before
    # fetching still works: a card may need the base
    assert _git(repo, "fetch", "origin", env=env).returncode == 0


def test_a_host_card_commits_as_the_board_and_has_no_credential_helper(tmp_path):
    repo = _repo(tmp_path / "repo")
    env = host_env(tmp_path / "home")
    (repo / "new.txt").write_text("x\n")
    _git(repo, "add", "new.txt", env=env)
    assert _git(repo, "commit", "-q", "-m", "by the card", env=env).returncode == 0
    assert (
        _git(repo, "log", "-1", "--format=%an <%ae>").stdout.strip()
        == "smortboard <smortboard@localhost>"
    )
    assert _git(repo, "config", "--get", "credential.helper", env=env).stdout.strip() == ""


def _board(tmp_path, monkeypatch, leases):
    repo = _repo(tmp_path / "repo")
    token = tmp_path / "token"
    token.write_text("test-token")
    token.chmod(0o600)
    store = Store(tmp_path / "board.db")
    board = store.create_board("b")
    registered = store.create_repo(board["id"], "repo", str(repo), "main")
    card = store.create_card(board["id"], registered["id"], "card", leases=leases)
    tree = create_worktree(repo, card["id"])
    store.append_event(card["id"], "worktree_created", {"base_commit": tree.base_commit})
    return store, card, registered, tree, token


def test_a_host_run_works_in_the_worktree_with_a_clean_env_and_a_group(tmp_path, monkeypatch):
    for key, value in SECRETS.items():
        monkeypatch.setenv(key, value)
    seen = {}

    def worker(store, card_id, cmd, cwd=None, **kwargs):
        seen.update(cmd=cmd, cwd=cwd, **kwargs)
        (cwd / "allowed.py").write_text("x = 1\n")
        _git(cwd, "add", "allowed.py")
        _git(cwd, "commit", "-q", "-m", "worker change")
        return _ok()

    monkeypatch.setattr(backends, "run_process", worker)
    store, card, registered, tree, token = _board(tmp_path, monkeypatch, ["allowed.py"])
    with store:
        result = HostBackend().run_card(
            store,
            card["id"],
            tree.path,
            "prompt",
            tmp_path / "settings.json",
            repo=registered,
            token_path=token,
        )
    assert result.blocked_reason_code is None
    assert seen["cwd"] == tree.path.resolve()
    assert seen["new_session"] is True
    assert not set(SECRETS) & set(seen["env"])
    assert seen["env"]["HOME"] != os.environ.get("HOME")
    assert not os.path.exists(seen["env"]["HOME"]), "the throwaway home is removed after the run"
    assert "test-token" not in " ".join(seen["cmd"]), "the token goes on stdin, never in argv"
    assert seen["token_line"] == "test-token\n"
    # the agent's commit is on the card's branch in the real worktree: no clone, no fetch-back
    assert _git(tree.path, "log", "-1", "--format=%s").stdout.strip() == "worker change"


def test_a_host_commit_outside_the_lease_is_flagged(tmp_path, monkeypatch):
    def worker(store, card_id, cmd, cwd=None, **kwargs):
        (cwd / "README.md").write_text("unauthorised\n")
        _git(cwd, "commit", "-q", "-am", "out of lease")
        return _ok()

    monkeypatch.setattr(backends, "run_process", worker)
    store, card, registered, tree, token = _board(tmp_path, monkeypatch, ["allowed.py"])
    with store:
        result = HostBackend().run_card(
            store,
            card["id"],
            tree.path,
            "prompt",
            tmp_path / "settings.json",
            repo=registered,
            token_path=token,
        )
        events = list(store.list_events(card["id"]))
    assert result.blocked_reason_code == "LEASE_CONFLICT"
    finished = [event for event in events if event["kind"] == "lease_check_finished"][-1]
    assert finished["payload"]["passed"] is False
    assert "README.md" in finished["payload"]["outside"]


def test_the_host_backend_refuses_a_lab_it_has_not_measured(tmp_path, monkeypatch):
    store, card, registered, tree, token = _board(tmp_path, monkeypatch, ["a.py"])
    with store, pytest.raises(CardRuntimeUnavailable, match="claude only"):
        HostBackend().run_card(
            store,
            card["id"],
            tree.path,
            "prompt",
            tmp_path / "settings.json",
            model="openai/gpt-5.5",
            repo=registered,
            token_path=token,
        )


def test_host_guards_are_real_paths_and_read_only(tmp_path):
    worktree = tmp_path / "tree"
    worktree.mkdir()
    out = tmp_path / "guards"
    out.mkdir()
    settings = write_host_guards(out, ["a.py"], root=worktree)
    assert settings.parent == out and settings.is_file()
    assert str(worktree) in (out / "lease.json").read_text()
    assert sys.executable in settings.read_text()
    assert str(out) in settings.read_text(), "hooks point at the host guard dir, not /smortboard"
    assert not os.access(out / "lease.json", os.W_OK) or os.geteuid() == 0
    out.chmod(0o700)  # let pytest clean the directory


def test_stopping_a_host_run_ends_the_whole_process_group(tmp_path):
    process = subprocess.Popen(
        ["sh", "-c", "sleep 301 & sleep 302 & wait"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.4)  # let the shell fork its children
    group = os.getpgid(process.pid)
    os.killpg(group, 0)  # the group is alive
    ProcessHandle(process, group=True).terminate(timeout=5)
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    os.killpg(group, signal.SIGKILL)
    raise AssertionError("children of the stopped run were still alive")


def test_require_host_runtime_says_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    token = tmp_path / "token"
    token.write_text("t")
    token.chmod(0o600)
    with pytest.raises(CardRuntimeUnavailable, match="claude cli is not on PATH"):
        require_host_runtime(token)
    with pytest.raises(CardRuntimeUnavailable, match="claude only"):
        require_host_runtime(token, lab="openai")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude")
    assert require_host_runtime(token).name == "host"


def test_an_open_board_asks_for_the_host_runtime_not_docker(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    store = Store(tmp_path / "board.db")
    board = store.create_board("b")
    store.set_setting("allow_open_mode", "on")
    registered = store.create_repo(board["id"], "repo", str(repo), "main", test_command="true")
    card = store.create_card(board["id"], registered["id"], "card", leases=["a.py"])
    store.set_board_run_mode(board["id"], "open")
    asked = []

    def host(*args, **kwargs):
        asked.append("host")
        raise CardRuntimeUnavailable("  no claude cli")

    def container(*args, **kwargs):
        asked.append("container")
        raise CardRuntimeUnavailable("  no docker")

    monkeypatch.setattr(lifecycle, "require_host_runtime", host)
    monkeypatch.setattr(lifecycle, "require_card_runtime", container)
    with store:
        result = lifecycle.run_card_lifecycle(store, card["id"])
    assert asked == ["host"]
    assert result.refusal and "no claude cli" in result.refusal
