"""two cards cutting worktrees in one repo at once.

Measured on the smolsmort board: two run-all cards started 17 ms apart, both cut from
origin/<base>, and the second failed `could not lock config file .git/config` - `worktree add -b`
from a remote ref writes upstream tracking into the shared config, and git fails a held lock rather
than waiting for it.
"""

import subprocess
import threading

from smortboard.exec.worktrees import WorktreeError, create_worktree


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def _repo_with_origin(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "thing.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "first")
    bare = tmp_path / "origin.git"
    _git(repo, "clone", "-q", "--bare", str(repo), str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "fetch", "-q", "origin")
    return repo


def test_many_cards_cut_from_a_remote_base_at_once_all_get_worktrees(tmp_path):
    repo = _repo_with_origin(tmp_path)
    count = 12
    barrier = threading.Barrier(count)
    errors = []

    def _cut(i):
        barrier.wait()
        try:
            create_worktree(repo, f"card-{i}", base="origin/main")
        except WorktreeError as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=_cut, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


def test_a_card_branch_does_not_track_the_base(tmp_path):
    """the card's branch gets its own upstream when the board pushes it, never the base's"""
    repo = _repo_with_origin(tmp_path)
    create_worktree(repo, "card-1", base="origin/main")
    tracking = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "branch.card/card-1.merge"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracking.stdout.strip() == ""
