"""read-only clones and operator paths for a mission-control turn. Real git runs here - a shallow
single-branch clone is the point - but no docker and no model, ever."""

import subprocess
from pathlib import Path

from smortboard.exec.repo_snapshot import (
    EXTRA_MOUNT,
    REPOS_MOUNT,
    RepoSnapshotError,
    build_repo_snapshot,
)


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)


def _make_repo(path: Path) -> None:
    """a repo on main with a committed file, a card/* branch, and a .claude/worktrees dir -
    exactly what a live checkout carries and a read-only clone must not."""
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    _git(path, "config", "user.email", "test@test.com")
    _git(path, "config", "user.name", "test")
    (path / "README.md").write_text("hello\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "initial")
    # a card branch and a worktree dir - both must be absent from the clone
    _git(path, "branch", "card/abc123")
    (path / ".claude" / "worktrees" / "abc123").mkdir(parents=True)
    (path / ".claude" / "worktrees" / "abc123" / "scratch.txt").write_text("card work\n")


def test_a_repo_is_cloned_read_only_without_card_branches_or_worktrees(tmp_path):
    repo = tmp_path / "src"
    _make_repo(repo)

    snapshot = build_repo_snapshot(
        [{"name": "src", "path": str(repo), "default_branch": "main"}], []
    )
    try:
        # the clone mount is read-only and lands under /repos/<name>
        assert "-v" in snapshot.mount_args
        clone_mount = next(m for m in snapshot.mount_args if f"{REPOS_MOUNT}/src:ro" in m)
        clone_path = Path(clone_mount.split(":")[0])

        assert (clone_path / "README.md").exists()
        # no worktree dir travelled in the clone
        assert not (clone_path / ".claude" / "worktrees").exists()
        # only the default branch - no card/* branch came along
        branches = subprocess.run(
            ["git", "-C", str(clone_path), "branch", "--list"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert "card/abc123" not in branches
        assert "main" in branches
    finally:
        snapshot.cleanup()


def test_cleanup_removes_the_clone_dir(tmp_path):
    repo = tmp_path / "src"
    _make_repo(repo)
    snapshot = build_repo_snapshot(
        [{"name": "src", "path": str(repo), "default_branch": "main"}], []
    )
    clone_mount = next(m for m in snapshot.mount_args if f"{REPOS_MOUNT}/src:ro" in m)
    clone_path = Path(clone_mount.split(":")[0])
    assert clone_path.exists()
    snapshot.cleanup()
    assert not clone_path.exists()
    snapshot.cleanup()  # idempotent - a second call is a no-op, not an error


def test_an_existing_extra_path_is_mounted_read_only(tmp_path):
    shots = tmp_path / "screenshots"
    shots.mkdir()
    snapshot = build_repo_snapshot([], [str(shots)])
    try:
        assert f"{shots}:{EXTRA_MOUNT}/screenshots:ro" in snapshot.mount_args
        assert snapshot.warnings == []
    finally:
        snapshot.cleanup()


def test_a_missing_extra_path_is_skipped_with_a_warning_not_a_crash(tmp_path):
    missing = tmp_path / "not-here"
    snapshot = build_repo_snapshot([], [str(missing)])
    try:
        assert snapshot.mount_args == []
        assert len(snapshot.warnings) == 1
        assert str(missing) in snapshot.warnings[0]
    finally:
        snapshot.cleanup()


def test_a_repo_that_will_not_clone_warns_and_is_skipped(tmp_path):
    def _boom(source, branch, dest):
        raise RepoSnapshotError("no such ref")

    snapshot = build_repo_snapshot(
        [{"name": "gone", "path": "/nope", "default_branch": "main"}], [], cloner=_boom
    )
    try:
        assert snapshot.mount_args == []
        assert any("gone" in w for w in snapshot.warnings)
    finally:
        snapshot.cleanup()
