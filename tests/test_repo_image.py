"""the repo test image: stack detection, the generated Dockerfile, and the build itself.

`run` is always a fake - no real docker daemon needed to prove the wiring is right. Real docker
availability is exercised only implicitly, through shutil.which, which a monkeypatch controls too.
"""

import subprocess

import pytest

from smortboard.repo_image import (
    BuildResult,
    build_repo_image,
    detect_stack,
    dockerfile_for,
    rebuild_if_stale,
)
from smortboard.store.api import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "b.db")
    yield s
    s.close()


# a fake `run` never shells out, so a real docker on PATH is not what any of these tests are
# proving - without this, they only passed on a machine that happened to have docker installed,
# and failed the gate itself (no docker binary in the sandboxed test container). the one test that
# wants shutil.which to report "missing" overrides this with its own monkeypatch, which wins since
# it runs after fixture setup
@pytest.fixture(autouse=True)
def _docker_on_path(monkeypatch):
    monkeypatch.setattr("smortboard.repo_image.shutil.which", lambda name: "/usr/bin/docker")


def _fake_run(returncode=0, stdout="build ok", stderr=""):
    def run(cmd, cwd, capture_output, text, timeout, check):
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    return run


def test_detect_stack_prefers_python_when_both_lockfiles_exist(tmp_path):
    (tmp_path / "uv.lock").write_text("")
    (tmp_path / "package-lock.json").write_text("")
    assert detect_stack(tmp_path) == "python"


def test_detect_stack_finds_node_from_a_lockfile_alone(tmp_path):
    (tmp_path / "package-lock.json").write_text("")
    assert detect_stack(tmp_path) == "node"


def test_detect_stack_finds_node_from_pnpm(tmp_path):
    (tmp_path / "pnpm-lock.yaml").write_text("")
    assert detect_stack(tmp_path) == "node"


def test_detect_stack_is_none_for_an_unrecognised_repo(tmp_path):
    (tmp_path / "readme.md").write_text("hello")
    assert detect_stack(tmp_path) is None


def test_dockerfile_for_refuses_an_unknown_stack():
    with pytest.raises(ValueError):
        dockerfile_for("rust")


def test_a_python_repo_builds_and_sets_the_image(tmp_path, store):
    (tmp_path / "uv.lock").write_text("")
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "myrepo", str(tmp_path), "main")

    result = build_repo_image(repo, run=_fake_run())
    assert result.ok
    assert result.tag == "myrepo-repo:latest"
    assert (tmp_path / "docker" / "smortboard-repo.Dockerfile").exists()
    assert "uv sync --frozen" in (tmp_path / "docker" / "smortboard-repo.Dockerfile").read_text()


def test_a_node_repo_builds_the_npm_variant(tmp_path, store):
    (tmp_path / "package-lock.json").write_text("{}")
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "webapp", str(tmp_path), "main")

    result = build_repo_image(repo, run=_fake_run())
    assert result.ok
    assert result.tag == "webapp-repo:latest"
    written = (tmp_path / "docker" / "smortboard-repo.Dockerfile").read_text()
    assert "npm ci" in written


def test_an_unrecognised_stack_gets_a_clear_message_and_builds_nothing(tmp_path, store):
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "mystery", str(tmp_path), "main")

    result = build_repo_image(repo, run=_fake_run())
    assert not result.ok
    assert result.tag is None
    assert "lockfile" in result.log
    assert not (tmp_path / "docker").exists()


def test_an_existing_dockerfile_is_never_overwritten(tmp_path, store):
    (tmp_path / "uv.lock").write_text("")
    docker_dir = tmp_path / "docker"
    docker_dir.mkdir()
    hand_written = "# a person wrote this by hand\nFROM something:else\n"
    (docker_dir / "smortboard-repo.Dockerfile").write_text(hand_written)
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "myrepo", str(tmp_path), "main")

    result = build_repo_image(repo, run=_fake_run())
    assert result.ok
    assert (docker_dir / "smortboard-repo.Dockerfile").read_text() == hand_written


def test_a_failed_build_reports_the_log_tail_and_sets_no_image(tmp_path, store):
    (tmp_path / "uv.lock").write_text("")
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "myrepo", str(tmp_path), "main")

    result = build_repo_image(repo, run=_fake_run(returncode=1, stdout="", stderr="boom: line 40"))
    assert not result.ok
    assert result.tag is None
    assert "boom: line 40" in result.log


def test_docker_missing_from_path_is_reported_without_calling_run(tmp_path, store, monkeypatch):
    (tmp_path / "uv.lock").write_text("")
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "myrepo", str(tmp_path), "main")

    monkeypatch.setattr("smortboard.repo_image.shutil.which", lambda name: None)
    called = []
    result = build_repo_image(repo, run=lambda *a, **k: called.append(1))
    assert not result.ok
    assert "docker is not on path" in result.log.lower()
    assert called == []


def test_build_result_is_a_plain_dataclass():
    r = BuildResult(ok=True, tag="x:latest", log="")
    assert r.ok and r.tag == "x:latest"


# ---- rebuild_if_stale -----------------------------------------------------------------------


def _init_git_repo(path):
    _run_git = ["git", "-C", str(path)]
    subprocess.run([*_run_git, "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run([*_run_git, "config", "user.email", "a@b.c"], check=True, capture_output=True)
    subprocess.run([*_run_git, "config", "user.name", "a"], check=True, capture_output=True)


def _commit_uv_lock(path, when):
    (path / "uv.lock").write_text("version = 1\n")
    env = {**__import__("os").environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    subprocess.run(["git", "-C", str(path), "add", "uv.lock"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "uv.lock"],
        check=True,
        capture_output=True,
        env=env,
    )


def _probe(created_at, cmd, timeout=10, cwd=None):
    if cmd[:4] == ["docker", "image", "inspect", "-f"]:
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{created_at}\n", stderr="")
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def test_rebuild_if_stale_rebuilds_when_uv_lock_postdates_the_image(tmp_path, store):
    _init_git_repo(tmp_path)
    _commit_uv_lock(tmp_path, "2026-09-19T12:00:00+00:00")
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "myrepo", str(tmp_path), "main")
    repo = {**repo, "image": "myrepo-repo:latest"}

    result = rebuild_if_stale(
        repo,
        run=_fake_run(),
        probe=lambda cmd, timeout=10, cwd=None: _probe("2026-09-01T00:00:00Z", cmd, timeout, cwd),
    )
    assert result is not None
    assert result.ok
    assert result.tag == "myrepo-repo:latest"


def test_rebuild_if_stale_skips_a_fresh_image(tmp_path, store):
    _init_git_repo(tmp_path)
    _commit_uv_lock(tmp_path, "2026-01-01T00:00:00+00:00")
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "myrepo", str(tmp_path), "main")
    repo = {**repo, "image": "myrepo-repo:latest"}

    called = []
    result = rebuild_if_stale(
        repo,
        run=lambda *a, **k: called.append(1),
        probe=lambda cmd, timeout=10, cwd=None: _probe("2026-09-19T00:00:00Z", cmd, timeout, cwd),
    )
    assert result is None
    assert called == []


def test_rebuild_if_stale_never_touches_a_hand_set_custom_image(tmp_path, store):
    _init_git_repo(tmp_path)
    _commit_uv_lock(tmp_path, "2026-09-19T12:00:00+00:00")
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "myrepo", str(tmp_path), "main")
    repo = {**repo, "image": "some-custom-image:v3"}

    called = []
    result = rebuild_if_stale(
        repo,
        run=lambda *a, **k: called.append(1),
        probe=lambda cmd, timeout=10, cwd=None: _probe("2026-09-01T00:00:00Z", cmd, timeout, cwd),
    )
    assert result is None
    assert called == []
