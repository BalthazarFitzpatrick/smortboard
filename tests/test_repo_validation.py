"""validate_repo: what a person registering a repo can act on, checked against real git repos"""

import subprocess

import pytest

from smortboard.store.repo_validation import validate_repo


def _init_repo(path, branch="main"):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    # a branch only exists in git after a commit reaches it - an empty init has no refs at all
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.email=t@t.com",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "init",
        ],
        check=True,
    )


def test_valid_repo_returns_expanded_path(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    result = validate_repo("smortboard", str(repo), "main")
    assert result == str(repo)


def test_empty_name_is_rejected(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    with pytest.raises(ValueError, match="name must not be empty"):
        validate_repo("", str(repo), "main")


def test_empty_default_branch_is_rejected(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    with pytest.raises(ValueError, match="default_branch must not be empty"):
        validate_repo("smortboard", str(repo), "")


def test_missing_path_is_rejected(tmp_path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(ValueError, match="path does not exist"):
        validate_repo("smortboard", str(missing), "main")


def test_path_that_is_a_file_is_rejected(tmp_path):
    file_path = tmp_path / "not-a-dir"
    file_path.write_text("x")
    with pytest.raises(ValueError, match="not a directory"):
        validate_repo("smortboard", str(file_path), "main")


def test_non_git_directory_is_rejected(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(ValueError, match="not a git repository"):
        validate_repo("smortboard", str(plain), "main")


def test_missing_branch_is_rejected(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo, branch="main")
    with pytest.raises(ValueError, match="does not exist"):
        validate_repo("smortboard", str(repo), "release")


def test_tilde_path_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = tmp_path / "repo"
    _init_repo(repo)
    result = validate_repo("smortboard", "~/repo", "main")
    assert result == str(repo)
