"""from online repo: listing what gh can see, cloning a pick, and the board that ends up made"""

import json
import subprocess

import pytest

from smortboard import online_repos
from smortboard.online_repos import clone_online_repo, list_online_repos
from tests import test_server
from tests.test_repo_setup import _git, _remote_heads
from tests.test_server import _init_repo, _request

running_server = test_server.running_server
# autouse: the setup after a clone runs real git against a gh that never reaches GitHub
fake_gh = test_server.fake_gh


def _done(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


@pytest.fixture(autouse=True)
def _gh_on_path(monkeypatch):
    monkeypatch.setattr(online_repos.shutil, "which", lambda name: "/usr/bin/gh")


def _runner(calls, *, logged_in=True, listing="[]", clone=None):
    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["gh", "auth", "status"]:
            return _done(returncode=0 if logged_in else 1)
        if cmd[:3] == ["gh", "repo", "list"]:
            return _done(listing)
        if cmd[:3] == ["gh", "repo", "clone"]:
            return clone(cmd) if clone else _done()
        raise AssertionError(f"unexpected command: {cmd}")

    return run


def test_the_listing_is_reshaped_for_the_ui():
    rows = [
        {"nameWithOwner": "me/one", "isPrivate": True, "description": None},
        {"nameWithOwner": "me/two", "isPrivate": False, "description": "a thing"},
    ]
    repos = list_online_repos(_runner([], listing=json.dumps(rows)))
    assert repos == [
        {"name_with_owner": "me/one", "private": True, "description": ""},
        {"name_with_owner": "me/two", "private": False, "description": "a thing"},
    ]


def test_a_missing_gh_is_refused_with_the_fix(monkeypatch):
    monkeypatch.setattr(online_repos.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="not installed.*gh auth login"):
        list_online_repos(_runner([]))


def test_a_logged_out_gh_is_refused_with_the_fix():
    with pytest.raises(ValueError, match="gh auth login"):
        list_online_repos(_runner([], logged_in=False))


def test_a_clone_lands_in_folder_slash_name(tmp_path):
    def clone(cmd):
        (tmp_path / "proj").mkdir()
        return _done()

    calls = []
    path = clone_online_repo("me/proj", str(tmp_path), _runner(calls, clone=clone))
    assert path == str(tmp_path.resolve() / "proj")
    assert ["gh", "repo", "clone", "me/proj", path] in calls


@pytest.mark.parametrize("bad", ["", "proj", "me/proj/extra", "me/..", "-x/proj", "me/pro j"])
def test_a_name_that_is_not_owner_slash_name_never_reaches_gh(tmp_path, bad):
    calls = []
    with pytest.raises(ValueError, match="not a github owner/name"):
        clone_online_repo(bad, str(tmp_path), _runner(calls))
    assert calls == []


def test_a_clone_never_lands_on_something_that_exists(tmp_path):
    (tmp_path / "proj").mkdir()
    calls = []
    with pytest.raises(ValueError, match="already exists"):
        clone_online_repo("me/proj", str(tmp_path), _runner(calls))
    assert calls == []


def test_a_folder_that_is_not_one_is_refused(tmp_path):
    with pytest.raises(ValueError, match="not a folder"):
        clone_online_repo("me/proj", str(tmp_path / "nope"), _runner([]))


@pytest.mark.parametrize("folder", ["", "relative/dir"])
def test_a_relative_folder_never_lands_in_the_servers_own_directory(folder):
    calls = []
    with pytest.raises(ValueError, match="absolute"):
        clone_online_repo("me/proj", folder, _runner(calls))
    assert calls == []


def test_a_failed_clone_says_why(tmp_path):
    with pytest.raises(ValueError, match="the clone failed: repository not found"):
        clone_online_repo(
            "me/proj",
            str(tmp_path),
            _runner([], clone=lambda cmd: _done(returncode=1, stderr="repository not found")),
        )


# ---- through the server ------------------------------------------------------------------------


@pytest.fixture
def online_gh(monkeypatch, fake_gh):
    """gh as the server calls it: lists repos, and clones `owner/name` from a local bare repo
    fake_gh.remotes/<name>.git, so a clone has a real origin and nothing reaches GitHub"""
    calls = []

    def clone(cmd):
        name, target = cmd[3].split("/")[1], cmd[4]
        return subprocess.run(
            ["git", "clone", "-q", str(fake_gh.remotes / f"{name}.git"), target],
            capture_output=True,
            text=True,
            check=False,
        )

    listing = json.dumps([{"nameWithOwner": "me/one", "isPrivate": True, "description": None}])
    run = _runner(calls, listing=listing, clone=clone)
    monkeypatch.setattr(online_repos, "default_runner", run)
    return calls


def _bare_remote(fake_gh, tmp_path, name, *, development):
    source = tmp_path / "source" / name
    _init_repo(source)
    if development:
        _git(source, "branch", "development")
    bare = fake_gh.remotes / f"{name}.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(source), str(bare)], check=True)
    return bare


def _clone(server, repo, folder):
    return _request(
        f"{server}/api/boards/from-online-repo", "POST", {"repo": repo, "folder": str(folder)}
    )


def test_the_route_lists_repos(running_server, online_gh):
    status, body = _request(f"{running_server}/api/online-repos")
    assert status == 200
    assert body == [{"name_with_owner": "me/one", "private": True, "description": ""}]


def test_the_route_says_when_gh_is_missing(running_server, monkeypatch):
    monkeypatch.setattr(online_repos.shutil, "which", lambda name: None)
    status, body = _request(f"{running_server}/api/online-repos")
    assert status == 400 and "not installed" in body["error"]


def test_a_clone_with_development_on_origin_fetches_it_and_pushes_nothing(
    running_server, tmp_path, fake_gh, online_gh
):
    _bare_remote(fake_gh, tmp_path, "withdev", development=True)
    folder = tmp_path / "work"
    folder.mkdir()
    status, body = _clone(running_server, "me/withdev", folder)
    assert status == 201
    assert body["board"]["name"] == "withdev"
    assert body["repo"]["path"] == str(folder.resolve() / "withdev")
    assert body["repo"]["default_branch"] == "development"
    assert body["setup"] == {"steps": ["fetched development from origin"], "push_main": None}
    assert body["tests"] == {"command": None, "has_tests": False}
    assert fake_gh.pushes() == []
    _, repos = _request(f"{running_server}/api/boards/{body['board']['id']}/repos")
    assert [r["name"] for r in repos] == ["withdev"]


def test_a_clone_without_development_branches_and_pushes_it(
    running_server, tmp_path, fake_gh, online_gh
):
    bare = _bare_remote(fake_gh, tmp_path, "nodev", development=False)
    folder = tmp_path / "work"
    folder.mkdir()
    status, body = _clone(running_server, "me/nodev", folder)
    assert status == 201
    assert body["repo"]["default_branch"] == "development"
    assert body["setup"] == {
        "steps": ["branched development from main", "pushed development"],
        "push_main": None,
    }
    assert _remote_heads(bare) == ["development", "main"]


def test_a_refused_clone_creates_no_board(running_server, tmp_path, online_gh):
    # no bare repo behind the name, so the clone itself fails
    status, body = _clone(running_server, "me/missing", tmp_path)
    assert status == 400 and "the clone failed" in body["error"]
    assert not (tmp_path / "missing").exists()
    _, boards = _request(f"{running_server}/api/boards")
    assert boards == []


def test_a_name_that_is_not_owner_slash_name_creates_no_board(running_server, tmp_path, online_gh):
    status, body = _clone(running_server, "not-a-name", tmp_path)
    assert status == 400 and "owner/name" in body["error"]
    assert online_gh == []
    _, boards = _request(f"{running_server}/api/boards")
    assert boards == []


def test_an_existing_target_is_refused_before_gh_runs(running_server, tmp_path, online_gh):
    (tmp_path / "taken").mkdir()
    status, body = _clone(running_server, "me/taken", tmp_path)
    assert status == 400 and "already exists" in body["error"]
    assert online_gh == []
    _, boards = _request(f"{running_server}/api/boards")
    assert boards == []


def test_a_clone_whose_setup_fails_stays_on_disk_and_is_named(
    running_server, tmp_path, fake_gh, monkeypatch
):
    bare = _bare_remote(fake_gh, tmp_path, "gone", development=False)

    def clone(cmd):
        done = subprocess.run(["git", "clone", "-q", str(bare), cmd[4]], check=False)
        # the origin vanishes after the clone, so setup cannot push development
        _git(cmd[4], "remote", "set-url", "origin", str(tmp_path / "nowhere.git"))
        return done

    monkeypatch.setattr(online_repos, "default_runner", _runner([], clone=clone))
    status, body = _clone(running_server, "me/gone", tmp_path)
    target = tmp_path.resolve() / "gone"
    assert status == 400
    assert body["error"].startswith(f"cloned into {target}, then ")
    assert "the clone stays there" in body["error"]
    assert (target / ".git").is_dir()
    _, boards = _request(f"{running_server}/api/boards")
    assert boards == []
