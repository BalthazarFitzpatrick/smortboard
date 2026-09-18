"""the repo test image: stack detection, the generated Dockerfile, and the build itself.

`run` is always a fake - no real docker daemon needed to prove the wiring is right. Real docker
availability is exercised only implicitly, through shutil.which, which a monkeypatch controls too.
"""

import json
import subprocess

import pytest

from smortboard.repo_image import (
    BASE_IMAGE_ID_LABEL,
    LOCK_HASH_LABEL,
    BuildResult,
    build_repo_image,
    check_image_freshness,
    detect_stack,
    dockerfile_for,
    lock_hash,
)
from smortboard.store.api import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "b.db")
    yield s
    s.close()


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


# ---- lock_hash --------------------------------------------------------------------------------


def test_lock_hash_changes_when_a_dependency_moves(tmp_path):
    (tmp_path / "uv.lock").write_text("a")
    (tmp_path / "pyproject.toml").write_text("b")
    before = lock_hash(tmp_path, "python")
    (tmp_path / "uv.lock").write_text("a-changed")
    assert lock_hash(tmp_path, "python") != before


def test_lock_hash_is_stable_for_the_same_files(tmp_path):
    (tmp_path / "uv.lock").write_text("a")
    (tmp_path / "pyproject.toml").write_text("b")
    assert lock_hash(tmp_path, "python") == lock_hash(tmp_path, "python")


def test_build_repo_image_stamps_the_build_with_its_lock_and_base(tmp_path, store):
    (tmp_path / "uv.lock").write_text("locked")
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "myrepo", str(tmp_path), "main")

    seen = []

    def run(cmd, cwd, capture_output, text, timeout, check):
        seen.append(cmd)
        if cmd[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(cmd, 0, "sha256:base123\n", "")
        return subprocess.CompletedProcess(cmd, 0, "build ok", "")

    result = build_repo_image(repo, run=run)
    assert result.ok
    build_cmd = seen[-1]
    expected_lock = lock_hash(tmp_path, "python")
    assert f"--label={LOCK_HASH_LABEL}={expected_lock}" in build_cmd
    assert f"--label={BASE_IMAGE_ID_LABEL}=sha256:base123" in build_cmd


def test_a_build_stamps_no_base_label_when_the_base_has_never_been_built(tmp_path, store):
    (tmp_path / "uv.lock").write_text("locked")
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "myrepo", str(tmp_path), "main")

    seen = []

    def run(cmd, cwd, capture_output, text, timeout, check):
        seen.append(cmd)
        if cmd[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(cmd, 1, "", "no such image")
        return subprocess.CompletedProcess(cmd, 0, "build ok", "")

    result = build_repo_image(repo, run=run)
    assert result.ok
    build_cmd = seen[-1]
    assert not any(part.startswith(f"--label={BASE_IMAGE_ID_LABEL}=") for part in build_cmd)


# ---- check_image_freshness ---------------------------------------------------------------------


def _inspect_response(*entries):
    def run(cmd):
        return subprocess.CompletedProcess(cmd, 0, json.dumps(list(entries)), "")

    return run


def _entry(tag, labels=None):
    return {"RepoTags": [tag], "Id": f"sha256:{tag}", "Config": {"Labels": labels or {}}}


def test_freshness_skips_repos_with_no_custom_image(tmp_path):
    repo = {"path": str(tmp_path), "name": "r"}
    result = check_image_freshness(repo, run=lambda cmd: (_ for _ in ()).throw(AssertionError))
    assert result.fresh


def test_freshness_reports_missing_when_the_image_was_never_built(tmp_path):
    (tmp_path / "uv.lock").write_text("a")
    repo = {"path": str(tmp_path), "name": "r", "image": "r-repo:latest"}
    result = check_image_freshness(repo, run=_inspect_response())
    assert not result.fresh
    assert result.reason == "missing"
    assert "never been built" in result.detail


def test_freshness_reports_missing_for_an_image_that_predates_stamping(tmp_path):
    (tmp_path / "uv.lock").write_text("a")
    repo = {"path": str(tmp_path), "name": "r", "image": "r-repo:latest"}
    result = check_image_freshness(repo, run=_inspect_response(_entry("r-repo:latest")))
    assert not result.fresh
    assert result.reason == "missing"
    assert "predates image stamping" in result.detail


def test_freshness_reports_a_moved_dependency(tmp_path):
    (tmp_path / "uv.lock").write_text("a")
    repo = {"path": str(tmp_path), "name": "r", "image": "r-repo:latest"}
    stale_labels = {LOCK_HASH_LABEL: "not-the-current-hash"}
    result = check_image_freshness(
        repo, run=_inspect_response(_entry("r-repo:latest", stale_labels))
    )
    assert not result.fresh
    assert result.reason == "lock"
    assert "dependency moved" in result.detail


def test_freshness_reports_a_moved_base_image(tmp_path):
    (tmp_path / "uv.lock").write_text("a")
    repo = {"path": str(tmp_path), "name": "r", "image": "r-repo:latest"}
    current_lock = lock_hash(tmp_path, "python")
    repo_labels = {LOCK_HASH_LABEL: current_lock, BASE_IMAGE_ID_LABEL: "sha256:old-base"}
    result = check_image_freshness(
        repo,
        run=_inspect_response(
            _entry("r-repo:latest", repo_labels),
            {"RepoTags": ["smortboard-card:latest"], "Id": "sha256:new-base", "Config": {}},
        ),
    )
    assert not result.fresh
    assert result.reason == "base"
    assert "moved underneath it" in result.detail


def test_freshness_passes_when_the_stamp_matches_lock_and_base(tmp_path):
    (tmp_path / "uv.lock").write_text("a")
    repo = {"path": str(tmp_path), "name": "r", "image": "r-repo:latest"}
    current_lock = lock_hash(tmp_path, "python")
    repo_labels = {LOCK_HASH_LABEL: current_lock, BASE_IMAGE_ID_LABEL: "sha256:same-base"}
    result = check_image_freshness(
        repo,
        run=_inspect_response(
            _entry("r-repo:latest", repo_labels),
            {"RepoTags": ["smortboard-card:latest"], "Id": "sha256:same-base", "Config": {}},
        ),
    )
    assert result.fresh
    assert result.reason is None


def test_a_rebuild_from_the_current_lock_passes_the_check(tmp_path, store):
    """the acceptance case end to end: build_repo_image stamps the image, and the very same
    stamp is what check_image_freshness reads back as fresh - no rebuild loop needed."""
    (tmp_path / "uv.lock").write_text("locked")
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "myrepo", str(tmp_path), "main")
    repo = dict(repo, image="myrepo-repo:latest")

    built = {}

    def build_run(cmd, cwd, capture_output, text, timeout, check):
        if cmd[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(cmd, 1, "", "no such image")
        if cmd[0:2] == ["docker", "build"]:
            for part in cmd:
                if part.startswith(f"--label={LOCK_HASH_LABEL}="):
                    built[LOCK_HASH_LABEL] = part.split("=", 2)[2]
            return subprocess.CompletedProcess(cmd, 0, "build ok", "")
        return subprocess.CompletedProcess(cmd, 0, "build ok", "")

    result = build_repo_image(repo, run=build_run)
    assert result.ok

    def inspect_run(cmd):
        return subprocess.CompletedProcess(
            cmd, 0, json.dumps([_entry("myrepo-repo:latest", built)]), ""
        )

    freshness = check_image_freshness(repo, run=inspect_run)
    assert freshness.fresh
