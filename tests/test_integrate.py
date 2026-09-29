"""smortboard.review.integrate: the board landing a card through its pull request. real git; github's
side of the merge is tests/fake_github.py."""

import subprocess

import pytest

from smortboard.review.integrate import integrate
from tests import fake_github


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


URL = "https://github.com/o/r/pull/7"


@pytest.fixture
def github(monkeypatch, origin):
    return fake_github.install(monkeypatch, origin, {URL: "development"})


def test_the_card_lands_through_its_pull_request_as_one_two_parent_merge(origin, card, github):
    before = _git(origin, "rev-parse", "development")
    tip = _git(card, "rev-parse", "card/x")
    result = integrate(card, "card/x", "development", "a card", URL)
    assert result.sha
    assert _git(origin, "rev-parse", "development") == result.sha
    assert _git(origin, "rev-list", "--parents", "-n", "1", result.sha).split()[1:] == [before, tip]
    # the merged tree is exactly the reviewed branch's tree
    assert _git(origin, "rev-parse", f"{result.sha}^{{tree}}") == _git(
        card, "rev-parse", "card/x^{tree}"
    )
    # the card branch itself was pushed, and the merge was pinned to that exact head
    assert _git(origin, "rev-parse", "card/x") == tip
    (merge,) = github.merges
    assert merge[:4] == ["pr", "merge", URL, "--merge"]
    assert merge[merge.index("--match-head-commit") + 1] == tip


def test_an_off_limit_base_is_never_written(origin, card, github):
    before = _git(origin, "rev-parse", "main")
    result = integrate(card, "card/x", "main", "a card", URL)
    assert result.sha is None and "off limits" in result.reason
    assert _git(origin, "rev-parse", "main") == before
    assert github.merges == []


def test_main_lands_like_any_base_once_it_is_not_off_limits(origin, card, monkeypatch):
    github = fake_github.install(monkeypatch, origin, {URL: "main"})
    _git(card, "merge", "-q", "origin/main")
    result = integrate(card, "card/x", "main", "a card", URL, off_limits=frozenset())
    assert result.sha and _git(origin, "rev-parse", "main") == result.sha
    assert len(github.merges) == 1


def test_no_pull_request_means_no_merge(origin, card, github):
    result = integrate(card, "card/x", "development", "a card", None)
    assert result.sha is None and not result.moved
    assert github.merges == []


def test_a_base_that_moved_is_a_retry_not_an_untested_merge(tmp_path, origin, card, github):
    other = tmp_path / "other"
    _clone(origin, other)
    _git(other, "checkout", "-q", "development")
    _commit(other, "other.txt", "landed first\n")
    _git(other, "push", "-q", "origin", "development")
    moved_to = _git(origin, "rev-parse", "development")

    # the card's view of origin/development is stale: the fetch right before the merge catches it
    result = integrate(card, "card/x", "development", "a card", URL)
    assert result.sha is None and result.moved
    assert "does not contain" in result.reason
    assert _git(origin, "rev-parse", "development") == moved_to
    assert github.merges == [], "nothing is merged that the tests did not see"


def test_a_head_that_moved_after_the_gates_is_a_retry(origin, card, github):
    github.refuse = "Head branch was modified. Review and try the merge again."
    result = integrate(card, "card/x", "development", "a card", URL)
    assert result.sha is None and result.moved
    assert "Head branch was modified" in result.reason


def test_a_ruleset_refusal_is_shown_not_retried(origin, card, github):
    github.refuse = (
        "Base branch policy prohibits the merge: 2 of 2 required status checks are expected"
    )
    result = integrate(card, "card/x", "development", "a card", URL)
    assert result.sha is None and not result.moved
    assert "required status checks" in result.reason
