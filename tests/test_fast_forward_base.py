"""fast_forward_base: the local base follows origin after a landing, and nothing with local work,
nothing diverged and nothing protected is ever moved - real git, a bare origin, no network"""

import subprocess

import pytest

from smortboard.exec.worktrees import fast_forward_base


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path, monkeypatch):
    empty = tmp_path / "gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "t")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "t@t")


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(repo, name):
    (repo / name).write_text(name)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def folder(tmp_path):
    """the operator's folder on main, a development branch, and origin two landings ahead of it"""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    repo = tmp_path / "folder"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _commit(repo, "scaffold")
    _git(repo, "branch", "development")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "origin", "main", "development")
    # two landings pushed from elsewhere, the way integrate pushes a merge commit
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", "-b", "development", str(bare), str(other)], check=True)
    _commit(other, "one")
    landed = _commit(other, "two")
    _git(other, "push", "-q", "origin", "development")
    _git(repo, "fetch", "-q", "origin")
    return repo, landed


def test_a_base_nobody_has_checked_out_moves_to_what_landed(folder):
    repo, landed = folder
    assert fast_forward_base(repo, "development") == "moved"
    assert _git(repo, "rev-parse", "development") == landed
    assert fast_forward_base(repo, "development") == "current"


def test_a_clean_checkout_of_the_base_is_fast_forwarded(folder):
    repo, landed = folder
    _git(repo, "checkout", "-q", "development")
    assert fast_forward_base(repo, "development") == "moved"
    assert _git(repo, "rev-parse", "HEAD") == landed
    assert (repo / "two").exists(), "the working tree follows too"


def test_a_checkout_with_local_changes_is_left_alone(folder):
    repo, _ = folder
    _git(repo, "checkout", "-q", "development")
    before = _git(repo, "rev-parse", "HEAD")
    (repo / "scaffold").write_text("edited")
    assert fast_forward_base(repo, "development") == "dirty"
    assert _git(repo, "rev-parse", "HEAD") == before
    assert (repo / "scaffold").read_text() == "edited"


def test_a_local_base_with_its_own_commits_is_never_moved(folder):
    repo, _ = folder
    _git(repo, "checkout", "-q", "development")
    mine = _commit(repo, "mine")
    _git(repo, "checkout", "-q", "main")
    assert fast_forward_base(repo, "development") == "diverged"
    assert _git(repo, "rev-parse", "development") == mine


def test_main_is_never_moved_even_when_origin_is_ahead(folder, tmp_path):
    repo, _ = folder
    other = tmp_path / "main-clone"
    subprocess.run(
        ["git", "clone", "-q", "-b", "main", str(repo / ".." / "origin.git"), str(other)],
        check=True,
    )
    _commit(other, "on-main")
    _git(other, "push", "-q", "origin", "main")
    _git(repo, "fetch", "-q", "origin")
    before = _git(repo, "rev-parse", "main")
    assert fast_forward_base(repo, "main") == "protected"
    assert _git(repo, "rev-parse", "main") == before


def test_no_local_branch_or_no_origin_branch_is_missing(folder):
    repo, _ = folder
    assert fast_forward_base(repo, "feature-x") == "missing"
