"""repo_setup: every starting point for a board's folder, with real git and a fake gh whose
`repo create` makes a local bare repo - so pushes really happen, and nothing reaches GitHub"""

import subprocess
from pathlib import Path

import pytest

from smortboard.repo_setup import (
    DEFAULT_GITIGNORE,
    NeedsConfirm,
    SetupRefused,
    default_runner,
    prepare,
)


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path, monkeypatch):
    # the operator's global config (signing, hooks) must not decide whether a test commit works
    empty = tmp_path / "gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "t")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "t@t")


class FakeGh:
    """git runs for real; gh answers as the logged-in user `tester` and creates a local bare repo"""

    def __init__(self, remotes: Path, owner: str = "tester", create_fails: bool = False) -> None:
        self.remotes, self.owner, self.create_fails = remotes, owner, create_fails
        self.calls: list[list[str]] = []

    def __call__(self, cmd, cwd=None, timeout=60):
        self.calls.append(list(cmd))
        if cmd[0] != "gh":
            return default_runner(cmd, cwd=cwd, timeout=timeout)
        if cmd[1:3] == ["api", "user"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{self.owner}\n", stderr="")
        if cmd[1:3] == ["repo", "create"]:
            if self.create_fails:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="name already exists")
            name = cmd[3].split("/")[1]
            source = cmd[cmd.index("--source") + 1]
            bare = self.remotes / f"{name}.git"
            subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
            subprocess.run(["git", "-C", source, "remote", "add", "origin", str(bare)], check=True)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected gh call {cmd}")

    def pushes(self):
        return [c for c in self.calls if c[:1] == ["git"] and "push" in c]


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _repo(path, files, branch="main"):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    for rel, text in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text)
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "x")
    return path


def _remote_heads(bare):
    out = subprocess.run(
        ["git", "--git-dir", str(bare), "for-each-ref", "--format=%(refname:short)", "refs/heads"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted(out.split())


@pytest.fixture
def gh(tmp_path):
    remotes = tmp_path / "remotes"
    remotes.mkdir()
    return FakeGh(remotes)


def _never_pushed_main(gh):
    for call in gh.pushes():
        assert not any(arg.endswith(("main", ":refs/heads/main")) for arg in call), call


def test_an_empty_folder_becomes_a_repo_on_github_with_development_pushed(tmp_path, gh):
    folder = tmp_path / "fresh"
    folder.mkdir()
    result = prepare(folder, runner=gh)

    assert result.base == "development"
    assert result.created_origin
    assert _git(folder, "log", "--format=%s", "main") == "initial project scaffold"
    assert (folder / "README.md").read_text() == "# fresh\n"
    assert (folder / ".gitignore").read_text() == DEFAULT_GITIGNORE
    assert _remote_heads(gh.remotes / "fresh.git") == ["development"]
    assert result.push_main == (
        f"git -C {folder.resolve()} push -u origin main && "
        "gh repo edit tester/fresh --default-branch main"
    )
    assert "created private GitHub repo tester/fresh" in result.steps
    create = next(c for c in gh.calls if c[:3] == ["gh", "repo", "create"])
    assert "--private" in create and "--push" not in create
    _never_pushed_main(gh)


def test_a_folder_with_files_asks_first_and_writes_nothing(tmp_path, gh):
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n")
    (folder / ".env").write_text("TOKEN=secret\n")
    with pytest.raises(NeedsConfirm) as asked:
        prepare(folder, runner=gh)
    assert asked.value.files == ["app.py"]  # .env is left out by the default ignore
    assert sorted(p.name for p in folder.iterdir()) == [".env", "app.py"]
    assert not any(c[:1] == ["gh"] for c in gh.calls)


def test_confirmed_the_first_commit_holds_the_files_but_never_the_secret(tmp_path, gh):
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n")
    (folder / ".env").write_text("TOKEN=secret\n")
    prepare(folder, confirm=True, runner=gh)
    committed = _git(folder, "ls-tree", "-r", "--name-only", "main").split()
    assert sorted(committed) == [".gitignore", "app.py"]
    _never_pushed_main(gh)


def test_a_repo_without_development_gets_it_branched_from_its_default(tmp_path, gh):
    repo = _repo(tmp_path / "old", {"a.py": ""})
    result = prepare(repo, runner=gh)
    assert "branched development from main" in result.steps
    assert _git(repo, "rev-parse", "development") == _git(repo, "rev-parse", "main")
    _never_pushed_main(gh)


def test_a_repo_with_an_origin_but_no_development_there_only_pushes_development(tmp_path, gh):
    bare = gh.remotes / "shared.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    repo = _repo(tmp_path / "shared", {"a.py": ""})
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "origin", "main")
    result = prepare(repo, runner=gh)
    assert not result.created_origin and result.push_main is None
    assert _remote_heads(bare) == ["development", "main"]
    assert not any(c[:1] == ["gh"] for c in gh.calls)


def test_a_repo_that_is_ready_is_left_alone(tmp_path, gh):
    bare = gh.remotes / "ready.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    repo = _repo(tmp_path / "ready", {"a.py": ""})
    _git(repo, "branch", "development")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "origin", "main", "development")
    result = prepare(repo, runner=gh)
    assert result.steps == []
    assert gh.pushes() == []


def test_development_only_on_origin_is_fetched_not_rebranched(tmp_path, gh):
    upstream = _repo(tmp_path / "upstream", {"a.py": ""})
    _git(upstream, "checkout", "-q", "-b", "development")
    (upstream / "b.py").write_text("")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "-q", "-m", "dev work")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(upstream), str(clone)], check=True)
    result = prepare(clone, runner=gh)
    assert "fetched development from origin" in result.steps
    assert _git(clone, "log", "-1", "--format=%s", "development") == "dev work"


def test_a_taken_repo_name_is_refused_with_what_to_do(tmp_path):
    remotes = tmp_path / "remotes"
    remotes.mkdir()
    folder = tmp_path / "taken"
    folder.mkdir()
    with pytest.raises(
        SetupRefused, match="already exists, add it as origin yourself|add it as origin"
    ):
        prepare(folder, runner=FakeGh(remotes, create_fails=True))


def test_a_folder_name_github_would_refuse_says_rename(tmp_path, gh):
    folder = tmp_path / "my project!"
    folder.mkdir()
    with pytest.raises(SetupRefused, match="rename the folder"):
        prepare(folder, runner=gh)


def test_not_a_folder_is_refused(tmp_path, gh):
    with pytest.raises(SetupRefused, match="not a folder"):
        prepare(tmp_path / "nope", runner=gh)
