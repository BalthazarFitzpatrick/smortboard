"""GET /api/preflight: machine checks reuse backends' probes, repo checks reuse a fake runner.

Every docker/gh/git call goes through smortboard.preflight's runner, so nothing here calls any of
them for real except plain git against a temp repo this test creates itself.
"""

import stat
import subprocess

import pytest

from smortboard import profiles
from smortboard.preflight import run_preflight
from smortboard.store import Store


@pytest.fixture(autouse=True)
def _isolated_profiles(monkeypatch, tmp_path):
    # preflight's own profile rows read smortboard.profiles directly, independent of the
    # token_path argument these tests pass to run_preflight - isolate them the same way
    # tests/test_profiles.py does, so this file never touches the real config directory
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("SMORTBOARD_PROFILES_STATE_PATH", str(tmp_path / "profiles.json"))
    monkeypatch.setenv("SMORTBOARD_CARD_TOKEN_PATH", str(tmp_path / "card_token_unused"))


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


def test_token_file_wrong_mode_fails_and_never_prints_the_token(monkeypatch, store, tmp_path):
    # read_card_token now refuses a non-600 file outright, so preflight reports it as
    # unusable (fail) rather than a mere warning - the run would refuse it too
    token_file = tmp_path / "card_token"
    token_file.write_text("x" * 108)
    token_file.chmod(0o644)
    checks = run_preflight(store, token_path=token_file, runner=_all_ok_runner)
    row = _by_id(checks, "card-token")
    assert row["status"] == "fail"
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


def test_the_token_row_follows_the_active_profile(store):
    """with no card_token on disk, the active profile's file is what a run uses"""
    profiles.add_profile("second", "y" * 108)
    profiles.set_active("second")
    checks = run_preflight(store, runner=_all_ok_runner)
    assert _by_id(checks, "card-token")["status"] == "ok"


def test_readiness_takes_the_active_profile_token(monkeypatch):
    from smortboard.server.runs import Readiness

    monkeypatch.setattr("smortboard.exec.backends.docker_available", lambda: True)
    monkeypatch.setattr("smortboard.exec.backends.card_image_available", lambda *_args: True)
    profiles.add_profile("second", "y" * 108)
    profiles.set_active("second")
    assert Readiness().check()["token"] is True


def test_the_default_profile_gets_its_own_row(store):
    checks = run_preflight(store, runner=_all_ok_runner)
    row = _by_id(checks, "profile-default")
    assert row["group"] == "machine"
    assert "default" in row["label"]


def test_a_second_profile_shows_present_and_mode(store, tmp_path):
    profiles.add_profile("work", token="a-token")
    checks = run_preflight(store, runner=_all_ok_runner)
    row = _by_id(checks, "profile-work")
    assert row["status"] == "ok"
    assert "mode 600" in row["detail"]


def test_an_absent_profile_file_fails_with_a_paste_fix(store):
    profiles.add_profile("work")  # registered, but no token file written
    checks = run_preflight(store, runner=_all_ok_runner)
    row = _by_id(checks, "profile-work")
    assert row["status"] == "fail"
    assert "claude setup-token" in row["fix"]


def test_a_rate_limited_profile_shows_limited_until(store):
    import time

    profiles.add_profile("work", token="a-token")
    until = time.time() + 3600
    profiles.mark_limited("work", until)
    checks = run_preflight(store, runner=_all_ok_runner)
    row = _by_id(checks, "profile-work")
    assert row["status"] == "warn"
    assert str(until) in row["detail"]


def test_profile_rows_never_include_a_token_value(store):
    secret = "sk-profile-secret-do-not-leak"
    profiles.add_profile("work", token=secret)
    checks = run_preflight(store, runner=_all_ok_runner)
    assert secret not in str(checks)


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


def test_gh_not_authenticated(store, tmp_path, monkeypatch):
    # the gate container has no gh binary, and this test is about the auth step after install
    monkeypatch.setattr("smortboard.preflight.shutil.which", lambda name: "/usr/bin/gh")
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


def _git(repo_path, *args, date=None):
    import os

    env = None
    if date is not None:
        env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    subprocess.run(["git", "-C", str(repo_path), *args], check=True, capture_output=True, env=env)


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


def test_stale_image_warns_when_uv_lock_committed_after_the_image_was_built(
    monkeypatch, store, tmp_path
):
    monkeypatch.setattr("smortboard.preflight.docker_available", lambda: True)
    repo_path = _init_repo(tmp_path)
    (repo_path / "uv.lock").write_text("version = 1\n")
    _git(repo_path, "add", "uv.lock")
    _git(repo_path, "commit", "-m", "add uv.lock", date="2026-09-19T12:00:00+00:00")

    def runner(cmd, timeout=10, cwd=None):
        if cmd[:4] == ["docker", "image", "inspect", "-f"]:
            return _ok("2026-09-01T00:00:00Z\n")
        if cmd[:3] == ["docker", "image", "inspect"]:
            return _ok()
        if cmd[:2] == ["git", "log"]:
            return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
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
    row = next(
        c for c in checks if c["group"] == "widgets" and c["label"] == "repo image freshness"
    )
    assert row["status"] == "warn"
    assert "uv.lock" in row["detail"]
    assert "docker build" in row["fix"]


def test_fresh_image_does_not_warn_about_uv_lock(monkeypatch, store, tmp_path):
    monkeypatch.setattr("smortboard.preflight.docker_available", lambda: True)
    repo_path = _init_repo(tmp_path)
    (repo_path / "uv.lock").write_text("version = 1\n")
    _git(repo_path, "add", "uv.lock")
    _git(repo_path, "commit", "-m", "add uv.lock", date="2026-01-01T00:00:00+00:00")

    def runner(cmd, timeout=10, cwd=None):
        if cmd[:4] == ["docker", "image", "inspect", "-f"]:
            return _ok("2026-09-19T00:00:00Z\n")
        if cmd[:3] == ["docker", "image", "inspect"]:
            return _ok()
        if cmd[:2] == ["git", "log"]:
            return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
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
    row = next(
        c for c in checks if c["group"] == "widgets" and c["label"] == "repo image freshness"
    )
    assert row["status"] == "ok"


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


def test_openai_profile_checks_use_its_kind_and_do_not_require_anthropic_length():
    from smortboard.preflight import _profile_checks

    profiles.add_profile("work", "short-but-valid", lab="openai", kind="api_key")
    row = _by_id(_profile_checks(), "profile-openai-work")
    assert row["status"] == "ok"
    assert row["lab"] == "openai"
    assert row["kind"] == "api_key"
    assert "short-but-valid" not in str(row)
    profiles.profile_path("work", "openai").unlink()
    row = _by_id(_profile_checks(), "profile-openai-work")
    assert row["status"] == "fail"
    assert "codex login --with-api-key" in row["fix"]
    assert "claude" not in row["fix"]


def test_empty_openai_token_is_not_reported_healthy():
    from smortboard.preflight import _profile_checks

    profiles.add_profile("work", "", lab="openai")
    assert _by_id(_profile_checks(), "profile-openai-work")["status"] == "fail"


def test_openai_missing_cli_names_image_and_unknown_prices_warn(monkeypatch, store):
    from smortboard.preflight import _lab_checks

    profiles.add_profile("work", "key", lab="openai", kind="api_key")
    monkeypatch.setattr("smortboard.preflight.docker_available", lambda: True)
    monkeypatch.setattr("smortboard.preflight.card_image", lambda: "test-card:latest")
    commands = []

    def runner(cmd, timeout=10, cwd=None):
        commands.append(cmd)
        return _fail("not installed")

    rows = _lab_checks(store, runner)
    image = _by_id(rows, "lab-openai-image-0")
    assert image["status"] == "fail"
    assert "test-card:latest" in image["detail"] and "codex" in image["detail"]
    assert _by_id(rows, "lab-openai-pricing")["status"] == "warn"
    assert commands[0][-2:] == ["test-card:latest", "--version"]
    assert "key" not in commands[0]
