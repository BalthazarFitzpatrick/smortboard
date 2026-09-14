"""smortboard.review.integrate: the board landing a card on development. real git, no fakes."""

import subprocess

import pytest

from smortboard.review.integrate import integrate


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _clone(origin, path):
    subprocess.run(["git", "clone", "-q", str(origin), str(path)], check=True, capture_output=True)
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")


def _commit(repo, name, text):
    (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"wrote {name}")


@pytest.fixture
def origin(tmp_path):
    path = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(path)], check=True)
    seed = tmp_path / "seed"
    _clone(path, seed)
    _commit(seed, "f.txt", "base\n")
    _git(seed, "push", "-q", "origin", "main")
    _git(seed, "push", "-q", "origin", "main:development")
    return path


@pytest.fixture
def card(tmp_path, origin):
    """a card's worktree on its own branch, cut from development, one commit of work on it"""
    path = tmp_path / "card"
    _clone(origin, path)
    _git(path, "checkout", "-q", "-b", "card/x", "origin/development")
    _commit(path, "work.txt", "done\n")
    return path


def test_the_card_lands_on_development_as_one_two_parent_merge(origin, card):
    before = _git(origin, "rev-parse", "development")
    tip = _git(card, "rev-parse", "card/x")
    result = integrate(card, "card/x", "development", "a card")
    assert result.sha
    assert _git(origin, "rev-parse", "development") == result.sha
    assert _git(origin, "rev-list", "--parents", "-n", "1", result.sha).split()[1:] == [before, tip]
    # the merged tree is exactly the reviewed branch's tree
    assert _git(origin, "rev-parse", f"{result.sha}^{{tree}}") == _git(
        card, "rev-parse", "card/x^{tree}"
    )
    # and the card branch itself was pushed, so its pull request shows the merged commits
    assert _git(origin, "rev-parse", "card/x") == tip


def test_main_is_never_written(origin, card):
    before = _git(origin, "rev-parse", "main")
    result = integrate(card, "card/x", "main", "a card")
    assert result.sha is None and "protected" in result.reason
    assert _git(origin, "rev-parse", "main") == before


def test_a_base_that_moved_is_a_retry_not_an_overwrite(tmp_path, origin, card):
    other = tmp_path / "other"
    _clone(origin, other)
    _git(other, "checkout", "-q", "development")
    _commit(other, "other.txt", "landed first\n")
    _git(other, "push", "-q", "origin", "development")
    moved_to = _git(origin, "rev-parse", "development")

    # the card's view of origin/development is stale but still contained, so only the push can tell
    result = integrate(card, "card/x", "development", "a card")
    assert result.sha is None and result.moved
    assert _git(origin, "rev-parse", "development") == moved_to


def test_a_branch_that_does_not_contain_the_base_is_sent_back_to_sync(tmp_path, origin, card):
    other = tmp_path / "other"
    _clone(origin, other)
    _git(other, "checkout", "-q", "development")
    _commit(other, "other.txt", "landed first\n")
    _git(other, "push", "-q", "origin", "development")
    _git(card, "fetch", "-q", "origin")

    result = integrate(card, "card/x", "development", "a card")
    assert result.sha is None and result.moved
    assert "does not contain" in result.reason
