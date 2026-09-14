"""smortboard.review.mergeable: keeping a card's branch mergeable against its base.

Real git, no fakes - this is the module that does the actual `git merge`, so what matters is
whether that command's outcome (clean, conflicting, already current) is read correctly.
"""

import subprocess

import pytest

from smortboard.review.mergeable import check_mergeable, merge_branch, sync_with_base


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def origin(tmp_path):
    path = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(path)], check=True)
    return path


@pytest.fixture
def branch(tmp_path, origin):
    """a card's own worktree: cloned from origin, checked out on its own branch"""
    path = tmp_path / "branch"
    _git_clone(origin, path)
    _git(path, "checkout", "-q", "-b", "card/x")
    return path


def _git_clone(origin, path):
    subprocess.run(["git", "clone", "-q", str(origin), str(path)], check=True, capture_output=True)
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "f.txt").write_text("base\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "base")
    _git(path, "push", "-q", "origin", "main")


def _advance_main(origin, tmp_path, name, line):
    """pushes one more commit to origin/main from a second clone, as another PR merging would"""
    other = tmp_path / f"other-{name}"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, capture_output=True)
    _git(other, "config", "user.email", "t@t")
    _git(other, "config", "user.name", "t")
    (other / line[0]).write_text(line[1])
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", f"advance {name}")
    _git(other, "push", "-q", "origin", "main")


def test_not_behind_is_clean_and_not_behind(branch):
    result = check_mergeable(branch, "card/x", "origin/main")
    assert result.clean
    assert not result.behind


def test_behind_but_disjoint_change_is_clean_and_behind(origin, branch, tmp_path):
    (branch / "card.txt").write_text("card work\n")
    _git(branch, "add", "-A")
    _git(branch, "commit", "-q", "-m", "card work")
    _advance_main(origin, tmp_path, "1", ("main.txt", "main work\n"))
    _git(branch, "fetch", "-q", "origin", "main")

    result = check_mergeable(branch, "card/x", "origin/main")
    assert result.clean
    assert result.behind


def test_behind_with_a_real_conflict_names_the_file(origin, branch, tmp_path):
    (branch / "f.txt").write_text("card version\n")
    _git(branch, "add", "-A")
    _git(branch, "commit", "-q", "-m", "card edits f.txt")
    _advance_main(origin, tmp_path, "1", ("f.txt", "main version\n"))
    _git(branch, "fetch", "-q", "origin", "main")

    result = check_mergeable(branch, "card/x", "origin/main")
    assert not result.clean
    assert result.behind
    assert result.conflicting_files == ["f.txt"]
    # non-destructive: the worktree is untouched, still checked out on the card branch
    assert _git(branch, "branch", "--show-current") == "card/x"
    assert _git(branch, "status", "--porcelain") == ""


def test_merge_branch_lands_a_clean_merge_commit(origin, branch, tmp_path):
    (branch / "card.txt").write_text("card work\n")
    _git(branch, "add", "-A")
    _git(branch, "commit", "-q", "-m", "card work")
    _advance_main(origin, tmp_path, "1", ("main.txt", "main work\n"))
    _git(branch, "fetch", "-q", "origin", "main")

    result = merge_branch(branch, "origin/main")
    assert result.clean
    assert result.merged
    assert (branch / "main.txt").exists()  # the merge actually landed origin's commit
    assert (branch / "card.txt").exists()  # and kept the card's own


def test_merge_branch_aborts_a_conflict_and_leaves_the_worktree_clean(origin, branch, tmp_path):
    (branch / "f.txt").write_text("card version\n")
    _git(branch, "add", "-A")
    _git(branch, "commit", "-q", "-m", "card edits f.txt")
    _advance_main(origin, tmp_path, "1", ("f.txt", "main version\n"))
    _git(branch, "fetch", "-q", "origin", "main")

    result = merge_branch(branch, "origin/main")
    assert not result.clean
    assert result.conflicting_files == ["f.txt"]
    assert _git(branch, "status", "--porcelain") == ""  # merge --abort actually ran
    assert (branch / "f.txt").read_text() == "card version\n"  # untouched


def test_sync_with_base_has_nothing_to_fetch_without_a_remote(tmp_path):
    path = tmp_path / "no-remote"
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "f.txt").write_text("x\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "first")
    assert sync_with_base(path, "main") is None
