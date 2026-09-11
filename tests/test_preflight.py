"""GET /api/preflight: machine checks reuse backends' probes, repo checks reuse a fake runner.

Every docker/gh/git call goes through smortboard.preflight's runner, so nothing here calls any of
them for real except plain git against a temp repo this test creates itself.
"""

import stat
import subprocess

import pytest

from smortboard.preflight import run_preflight
from smortboard.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "board.db")
    yield s
    s.close()


def _ok(stdout="", cmd=None):
    return subprocess.CompletedProcess(cmd or [], returncode=0, stdout=stdout, stderr="")


def _fail(stderr="boom", cmd=None):
    return subprocess.CompletedProcess(cmd or [], returncode=1, stdout="", stderr=stderr)


def _all_ok_runner(cmd, timeout=10, cwd=None):
    if cmd[:2] == ["git", "branch"]:
        return _ok("  main\n")
    if cmd[:3] == ["git", "remote", "get-url"]:
        return _ok("git@github.com:acme/widgets.git\n")
    if cmd[:3] == ["git", "ls-remote", "--heads"]:
        return _ok("abc123\trefs/heads/main\n")
    if cmd[:3] == ["gh", "repo", "view"]:
        return _ok("acme/widgets\n")
    if cmd[:2] == ["gh", "auth"]:
        return _ok()
    if cmd[:3] == ["docker", "image", "inspect"]:
        return _ok()
    raise AssertionError(f"unexpected command: {cmd}")


def _by_id(checks, check_id):
    return next(c for c in checks if c["id"] == check_id)


# ---- machine checks -----------------------------------------------------------------------


def test_docker_not_installed_fails_with_an_install_fix(monkeypatch, store):
    monkeypatch.setattr("smortboard.preflight.shutil.which", lambda name: None)
    checks = run_preflight(store, runner=_all_ok_runner)
    row = _by_id(checks, "docker")
    assert row["status"] == "fail"
    assert row["group"] == "machine"
    assert "install" in row["fix"].lower()


def test_docker_installed_but_daemon_down(monkeypatch, store):
    monkeypatch.setattr("smortboard.preflight.shutil.which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr("smortboard.preflight.docker_available", lambda: False)
    checks = run_preflight(store, runner=_all_ok_runner)
    row = _by_id(checks, "docker")
    assert row["status"] == "fail"
    assert "daemon" in row["detail"]
    # a missing image is not worth reporting while the daemon itself is down
    image_row = _by_id(checks, "card-image")
    assert image_row["status"] == "fail"
    assert "docker is not reachable" in image_row["detail"]


def test_card_image_missing_gives_the_exact_build_command(monkeypatch, store):
    monkeypatch.setattr("smortboard.preflight.docker_available", lambda: True)
    monkeypatch.setattr("smortboard.preflight.card_image_available", lambda i=None: False)
    monkeypatch.setattr("smortboard.preflight.card_image", lambda: "smortboard-card:latest")
    checks = run_preflight(store, runner=_all_ok_runner)
    row = _by_id(checks, "card-image")
    assert row["status"] == "fail"
    assert row["fix"] == "docker build -f docker/card.Dockerfile -t smortboard-card:latest ."


def test_token_file_absent_points_at_setup_token(monkeypatch, store, tmp_path):
    missing = tmp_path / "no-token-here"
    checks = run_preflight(store, token_path=missing, runner=_all_ok_runner)
    row = _by_id(checks, "card-token")
    assert row["status"] == "fail"
    assert "claude setup-token" in row["fix"]
    assert str(missing) in row["detail"]


def test_token_file_wrong_mode_warns_and_never_prints_the_token(monkeypatch, store, tmp_path):
    token_file = tmp_path / "card_token"
    token_file.write_text("x" * 108)
    token_file.chmod(0o644)
    checks = run_preflight(store, token_path=token_file, runner=_all_ok_runner)
    row = _by_id(checks, "card-token")
    assert row["status"] == "warn"
    assert "600" in row["fix"]
    assert "x" * 108 not in str(row)


def test_token_file_looks_truncated_warns(store, tmp_path):
    token_file = tmp_path / "card_token"
    token_file.write_text("short")
    token_file.chmod(0o600)
    checks = run_preflight(store, token_path=token_file, runner=_all_ok_runner)
    row = _by_id(checks, "card-token")
    assert row["status"] == "warn"
    assert "truncated" in row["fix"]


def test_token_file_present_and_healthy_is_ok(store, tmp_path):
    token_file = tmp_path / "card_token"
    token_file.write_text("x" * 108)
    token_file.chmod(0o600)
    checks = run_preflight(store, token_path=token_file, runner=_all_ok_runner)
    assert _by_id(checks, "card-token")["status"] == "ok"


def test_gh_not_installed(monkeypatch, store, tmp_path):
    token_file = tmp_path / "card_token"
    token_file.write_text("x" * 108)
    token_file.chmod(0o600)
    real_which = __import__("shutil").which

    def which(name):
        return None if name == "gh" else real_which(name)

    monkeypatch.setattr("smortboard.preflight.shutil.which", which)
    checks = run_preflight(store, token_path=token_file, runner=_all_ok_runner)
    row = _by_id(checks, "gh")
    assert row["status"] == "fail"
    assert "cli.github.com" in row["fix"]


def test_gh_not_authenticated(store, tmp_path):
    token_file = tmp_path / "card_token"
    token_file.write_text("x" * 108)
    token_file.chmod(0o600)

    def runner(cmd, timeout=10, cwd=None):
        if cmd[:2] == ["gh", "auth"]:
            return _fail("not logged in")
        return _all_ok_runner(cmd, timeout, cwd)

    checks = run_preflight(store, token_path=token_file, runner=runner)
    row = _by_id(checks, "gh")
    assert row["status"] == "fail"
    assert row["fix"] == "gh auth login"


# ---- per-repo checks -----------------------------------------------------------------------


def _git(repo_path, *args):
    subprocess.run(["git", "-C", str(repo_path), *args], check=True, capture_output=True)


def _init_repo(tmp_path, name="widgets"):
    repo_path = tmp_path / name
    repo_path.mkdir()
    _git(repo_path, "init", "-b", "main")
    _git(repo_path, "config", "user.email", "a@b.c")
    _git(repo_path, "config", "user.name", "a")
    (repo_path / "README.md").write_text("hi")
    _git(repo_path, "add", "README.md")
    _git(repo_path, "commit", "-m", "first")
    return repo_path


def test_repo_path_missing_fails_every_downstream_check_without_guessing(store, tmp_path):
    board = store.create_board("b")
    store.create_repo(
        board["id"],
        name="ghost",
        path=str(tmp_path / "nope"),
        default_branch="main",
        test_command="pytest",
    )
    checks = run_preflight(store, runner=_all_ok_runner)
    rows = [c for c in checks if c["group"] == "ghost"]
    path_row = next(c for c in rows if c["label"] == "repo path")
    assert path_row["status"] == "fail"
    assert "clone" in path_row["fix"]
    branch_row = next(c for c in rows if c["label"] == "default branch")
    assert branch_row["status"] == "fail"
    assert "not checked" in branch_row["detail"]


def test_repo_registered_with_no_test_command_fails_that_row_only(monkeypatch, store, tmp_path):
    monkeypatch.setattr("smortboard.preflight.docker_available", lambda: True)
    repo_path = _init_repo(tmp_path)
    board = store.create_board("b")
    store.create_repo(board["id"], name="widgets", path=str(repo_path), default_branch="main")
    checks = run_preflight(store, runner=_all_ok_runner)
    rows = {c["label"]: c for c in checks if c["group"] == "widgets"}
    assert rows["test command"]["status"] == "fail"
    assert "key b" in rows["test command"]["fix"]
    assert rows["repo path"]["status"] == "ok"
    assert rows["default branch"]["status"] == "ok"
    assert rows["origin remote"]["status"] == "ok"
    assert rows["default branch on origin"]["status"] == "ok"
    assert rows["gh can see the repo"]["status"] == "ok"
    assert rows["repo image"]["status"] == "ok"


def test_no_origin_remote_names_the_github_repo_setup(monkeypatch, store, tmp_path):
    monkeypatch.setattr("smortboard.preflight.docker_available", lambda: True)
    repo_path = _init_repo(tmp_path)

    def runner(cmd, timeout=10, cwd=None):
        if cmd[:3] == ["git", "remote", "get-url"]:
            return _fail("no such remote")
        return _all_ok_runner(cmd, timeout, cwd)

    board = store.create_board("b")
    store.create_repo(
        board["id"],
        name="widgets",
        path=str(repo_path),
        default_branch="main",
        test_command="pytest",
    )
    checks = run_preflight(store, runner=runner)
    row = next(c for c in checks if c["group"] == "widgets" and c["label"] == "origin remote")
    assert row["status"] == "fail"
    assert "create the matching repo on github" in row["fix"].lower()
    # downstream checks that need origin are simply absent, not silently marked ok
    assert not [
        c for c in checks if c["group"] == "widgets" and c["label"] == "default branch on origin"
    ]


def test_gh_cannot_see_the_repo_tells_you_to_create_it(monkeypatch, store, tmp_path):
    monkeypatch.setattr("smortboard.preflight.docker_available", lambda: True)
    repo_path = _init_repo(tmp_path)

    def runner(cmd, timeout=10, cwd=None):
        if cmd[:3] == ["gh", "repo", "view"]:
            return _fail("not found")
        return _all_ok_runner(cmd, timeout, cwd)

    board = store.create_board("b")
    store.create_repo(
        board["id"],
        name="widgets",
        path=str(repo_path),
        default_branch="main",
        test_command="pytest",
    )
    checks = run_preflight(store, runner=runner)
    row = next(c for c in checks if c["group"] == "widgets" and c["label"] == "gh can see the repo")
    assert row["status"] == "fail"
    assert "gh repo create" in row["fix"]


def test_missing_repo_image_gives_a_build_fix(monkeypatch, store, tmp_path):
    monkeypatch.setattr("smortboard.preflight.docker_available", lambda: True)
    repo_path = _init_repo(tmp_path)

    def runner(cmd, timeout=10, cwd=None):
        if cmd[:3] == ["docker", "image", "inspect"]:
            return _fail("no such image")
        return _all_ok_runner(cmd, timeout, cwd)

    board = store.create_board("b")
    store.create_repo(
        board["id"],
        name="widgets",
        path=str(repo_path),
        default_branch="main",
        test_command="pytest",
        image="widgets:dev",
    )
    checks = run_preflight(store, runner=runner)
    row = next(c for c in checks if c["group"] == "widgets" and c["label"] == "repo image")
    assert row["status"] == "fail"
    assert "widgets:dev" in row["fix"]


def test_repos_across_every_board_are_included(monkeypatch, store, tmp_path):
    monkeypatch.setattr("smortboard.preflight.docker_available", lambda: True)
    b1 = store.create_board("b1")
    b2 = store.create_board("b2")
    r1 = _init_repo(tmp_path, "one")
    r2 = _init_repo(tmp_path, "two")
    store.create_repo(b1["id"], name="one", path=str(r1), default_branch="main", test_command="t")
    store.create_repo(b2["id"], name="two", path=str(r2), default_branch="main", test_command="t")
    checks = run_preflight(store, runner=_all_ok_runner)
    assert {c["group"] for c in checks} >= {"one", "two"}


def test_never_includes_a_token_value_anywhere(store, tmp_path):
    token_file = tmp_path / "card_token"
    secret = "sk-super-secret-token-value-do-not-leak"
    token_file.write_text(secret)
    token_file.chmod(0o600)
    checks = run_preflight(store, token_path=token_file, runner=_all_ok_runner)
    assert secret not in str(checks)
    assert stat.filemode(token_file.stat().st_mode).endswith("------")
