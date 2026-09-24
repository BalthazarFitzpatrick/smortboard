"""seed-beta: the comet catcher repo and board, with real git, a real sqlite store, and a fake gh
whose `repo create` makes a local bare repo - so development really gets pushed, and nothing
reaches GitHub"""

import functools
import shutil
import subprocess

import pytest

from smortboard import cli
from smortboard.cli import main
from smortboard.orchestrator import card_text_warnings
from smortboard.seed_beta import CARDS, SeedRefused, seed_beta
from smortboard.store import Store
from tests.test_repo_setup import FakeGh, _never_pushed_main, _remote_heads


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


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "board.sqlite3") as opened:
        yield opened


@pytest.fixture
def gh(tmp_path, monkeypatch):
    remotes = tmp_path / "remotes"
    remotes.mkdir()
    fake = FakeGh(remotes)
    # the cli has no runner flag, so its seed_beta gets the fake here
    monkeypatch.setattr(cli, "seed_beta", functools.partial(seed_beta, runner=fake))
    return fake


def _gh_calls(gh):
    return [call for call in gh.calls if call[:1] == ["gh"]]


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_the_repo_is_one_commit_on_main_with_development_cut_from_it(store, tmp_path, gh):
    repo = tmp_path / "comet"
    seed_beta(store, repo, runner=gh)

    assert _git(repo, "branch", "--show-current") == "main"
    assert _git(repo, "rev-parse", "main") == _git(repo, "rev-parse", "development")
    assert _git(repo, "rev-list", "--count", "main") == "1"
    assert _git(repo, "status", "--porcelain") == ""
    assert (repo / "test" / "smoke.test.js").is_file()
    assert (repo / "public" / "index.html").is_file()
    assert _git(repo, "show", "main:.gitignore") == "data/\nnode_modules/"


def test_seeding_creates_a_private_origin_and_pushes_only_development(store, tmp_path, gh):
    repo = tmp_path / "comet"
    result = seed_beta(store, repo, runner=gh)

    assert _git(repo, "remote", "get-url", "origin") == str(gh.remotes / "comet.git")
    assert _remote_heads(gh.remotes / "comet.git") == ["development"]
    create = next(call for call in gh.calls if call[:3] == ["gh", "repo", "create"])
    assert "--private" in create and "--push" not in create
    _never_pushed_main(gh)
    assert result.setup.push_main == (
        f"git -C {repo.resolve()} push -u origin main && "
        "gh repo edit tester/comet --default-branch main"
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_starter_commit_passes_its_own_test_gate(store, tmp_path, gh):
    """the base has to be green before any card, or every card blocks BASE_RED"""
    repo = tmp_path / "comet"
    seed_beta(store, repo, runner=gh)
    result = subprocess.run(["node", "--test"], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_board_is_set_up_to_run(store, tmp_path, gh):
    result = seed_beta(store, tmp_path / "comet", runner=gh)

    assert result.board["name"] == "comet-catcher"
    assert result.board["lease_mode"] == "strict"
    assert result.board["daily_budget_usd"] == 30.0
    repo = store.get_repo(result.repo["id"])
    assert repo["default_branch"] == "development"
    assert repo["test_command"] == "node --test"
    assert repo["path"] == str((tmp_path / "comet").resolve())


def test_every_card_keeps_the_card_text_rules():
    for spec in CARDS:
        assert card_text_warnings(spec) == [], spec["title"]


def test_every_card_can_run_as_seeded(store, tmp_path, gh):
    result = seed_beta(store, tmp_path / "comet", runner=gh)
    cards = store.list_cards(result.board["id"])

    assert len(cards) == len(CARDS) == 15
    for card in cards:
        assert card["leases"], card["title"]
        assert card["criteria"], card["title"]
        assert (card["lab"], card["model"]) == ("anthropic", "sonnet")
        assert card["complexity"] in (1, 2)
        assert card["repo_id"] == result.repo["id"]
        assert card["status"] == "todo"


def test_the_cards_have_the_shape_the_beta_doc_promises(store, tmp_path, gh):
    result = seed_beta(store, tmp_path / "comet", runner=gh)
    by_title = {card["title"]: card for card in store.list_cards(result.board["id"])}
    key_of = {by_title[spec["title"]]["id"]: spec["key"] for spec in CARDS}
    deps = {spec["key"]: set() for spec in CARDS}
    leases = {}
    for spec in CARDS:
        card = by_title[spec["title"]]
        deps[spec["key"]] = {key_of[dep] for dep in card["depends_on"]}
        leases[spec["key"]] = {lease["path_glob"] for lease in card["leases"]}

    # three tracks start at once, on leases that never overlap
    roots = sorted(key for key, parents in deps.items() if not parents)
    assert roots == ["core", "skills", "store"]
    assert not (leases["core"] & leases["skills"] or leases["core"] & leases["store"])
    assert not leases["skills"] & leases["store"]

    def depth(key):
        return 1 + max((depth(parent) for parent in deps[key]), default=0)

    assert depth("loop") >= 3  # core -> render + input -> loop
    assert deps["highscores"] == {"validate", "loop"}  # the api track meets the game track
    assert "public/js/core.js" not in leases["upgrades"]  # its natural change reaches core.js
    assert deps["mute"] == deps["pause"] == {"loop"}  # two small cards, left separate for fold
    assert depth("readme") == max(depth(key) for key in deps)


def test_a_folder_that_is_not_empty_is_refused_and_nothing_is_written(store, tmp_path, gh):
    repo = tmp_path / "comet"
    repo.mkdir()
    (repo / "keep.txt").write_text("mine")

    with pytest.raises(SeedRefused, match="not an empty folder"):
        seed_beta(store, repo, runner=gh)
    assert store.list_boards() == []
    assert [path.name for path in repo.iterdir()] == ["keep.txt"]
    assert gh.calls == []


def test_an_existing_board_name_is_refused_before_the_repo_is_made(store, tmp_path, gh):
    store.create_board("comet-catcher")
    with pytest.raises(SeedRefused, match="already exists"):
        seed_beta(store, tmp_path / "comet", runner=gh)
    assert not (tmp_path / "comet").exists()
    assert len(store.list_boards()) == 1
    assert gh.calls == []


@pytest.mark.parametrize(
    ("fake", "says"),
    [
        ({"create_fails": True}, "could not create tester/comet"),
        ({"owner": ""}, "gh auth login"),
    ],
    ids=["repo name taken", "gh not logged in"],
)
def test_a_gh_refusal_takes_the_folder_back_out(store, tmp_path, fake, says):
    remotes = tmp_path / "remotes"
    remotes.mkdir()
    with pytest.raises(SeedRefused, match=says):
        seed_beta(store, tmp_path / "comet", runner=FakeGh(remotes, **fake))
    assert not (tmp_path / "comet").exists()
    assert store.list_boards() == []


def test_a_gh_refusal_leaves_an_empty_folder_it_was_given_empty(store, tmp_path):
    remotes = tmp_path / "remotes"
    remotes.mkdir()
    repo = tmp_path / "comet"
    repo.mkdir()
    with pytest.raises(SeedRefused, match="could not create"):
        seed_beta(store, repo, runner=FakeGh(remotes, create_fails=True))
    assert list(repo.iterdir()) == []
    assert store.list_boards() == []


def test_the_cli_seeds_and_prints_the_one_step_that_is_yours(tmp_path, capsys, gh):
    db_path = tmp_path / "board.sqlite3"
    repo = tmp_path / "comet"
    main(["seed-beta", str(repo), "--db", str(db_path), "--name", "arcade"])

    out = capsys.readouterr().out
    path = repo.resolve()
    assert "created private GitHub repo tester/comet, pushed development" in out
    assert "one step is yours - run this once:" in out
    assert (
        f"  git -C {path} push -u origin main && gh repo edit tester/comet --default-branch main"
        in out
    )
    assert "press h until every row is green" in out
    assert "w runs the whole board" in out
    assert "--push" not in out
    assert _remote_heads(gh.remotes / "comet.git") == ["development"]
    _never_pushed_main(gh)
    with Store(db_path) as store:
        assert [board["name"] for board in store.list_boards()] == ["arcade"]


def test_the_cli_refuses_demo_and_a_refused_seed_says_nothing_was_seeded(tmp_path, gh):
    with pytest.raises(SystemExit, match="--demo has nowhere"):
        main(["seed-beta", str(tmp_path / "comet"), "--demo"])

    taken = tmp_path / "taken"
    taken.mkdir()
    (taken / "x").write_text("")
    with pytest.raises(SystemExit, match="nothing seeded"):
        main(["seed-beta", str(taken), "--db", str(tmp_path / "board.sqlite3")])
    assert gh.calls == []
