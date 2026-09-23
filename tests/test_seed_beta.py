"""seed-beta: the comet catcher repo and board, with real git and a real sqlite store"""

import shutil
import subprocess

import pytest

from smortboard import seed_beta as seed_module
from smortboard.cli import main
from smortboard.orchestrator import card_text_warnings
from smortboard.seed_beta import CARDS, SeedRefused, seed_beta
from smortboard.store import Store


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


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_the_repo_is_one_commit_on_main_with_development_cut_from_it(store, tmp_path):
    repo = tmp_path / "comet"
    seed_beta(store, repo)

    assert _git(repo, "branch", "--show-current") == "main"
    assert _git(repo, "rev-parse", "main") == _git(repo, "rev-parse", "development")
    assert _git(repo, "rev-list", "--count", "main") == "1"
    assert _git(repo, "status", "--porcelain") == ""
    assert (repo / "test" / "smoke.test.js").is_file()
    assert (repo / "public" / "index.html").is_file()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_starter_commit_passes_its_own_test_gate(store, tmp_path):
    """the base has to be green before any card, or every card blocks BASE_RED"""
    repo = tmp_path / "comet"
    seed_beta(store, repo)
    result = subprocess.run(["node", "--test"], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_board_is_set_up_to_run(store, tmp_path):
    result = seed_beta(store, tmp_path / "comet")

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


def test_every_card_can_run_as_seeded(store, tmp_path):
    result = seed_beta(store, tmp_path / "comet")
    cards = store.list_cards(result.board["id"])

    assert len(cards) == len(CARDS) == 15
    for card in cards:
        assert card["leases"], card["title"]
        assert card["criteria"], card["title"]
        assert (card["lab"], card["model"]) == ("anthropic", "sonnet")
        assert card["complexity"] in (1, 2)
        assert card["repo_id"] == result.repo["id"]
        assert card["status"] == "todo"


def test_the_cards_have_the_shape_the_beta_doc_promises(store, tmp_path):
    result = seed_beta(store, tmp_path / "comet")
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


def test_a_folder_that_is_not_empty_is_refused_and_nothing_is_written(store, tmp_path):
    repo = tmp_path / "comet"
    repo.mkdir()
    (repo / "keep.txt").write_text("mine")

    with pytest.raises(SeedRefused, match="not an empty folder"):
        seed_beta(store, repo)
    assert store.list_boards() == []
    assert [path.name for path in repo.iterdir()] == ["keep.txt"]


def test_an_existing_board_name_is_refused_before_the_repo_is_made(store, tmp_path):
    store.create_board("comet-catcher")
    with pytest.raises(SeedRefused, match="already exists"):
        seed_beta(store, tmp_path / "comet")
    assert not (tmp_path / "comet").exists()
    assert len(store.list_boards()) == 1


def test_a_failing_git_step_takes_its_folder_back_out(store, tmp_path, monkeypatch):
    real_git = seed_module._git

    def failing_git(repo_dir, *args):
        if args[0] == "commit":
            raise SeedRefused("git commit failed: no identity")
        real_git(repo_dir, *args)

    monkeypatch.setattr(seed_module, "_git", failing_git)
    with pytest.raises(SeedRefused, match="git commit failed"):
        seed_beta(store, tmp_path / "comet")
    assert not (tmp_path / "comet").exists()
    assert store.list_boards() == []


def test_the_cli_seeds_and_prints_the_github_steps(tmp_path, capsys):
    db_path = tmp_path / "board.sqlite3"
    repo = tmp_path / "comet"
    main(["seed-beta", str(repo), "--db", str(db_path), "--name", "arcade"])

    out = capsys.readouterr().out
    path = repo.resolve()
    assert f"gh repo create comet --private --source {path} --push" in out
    assert f"git -C {path} push -u origin development" in out
    with Store(db_path) as store:
        assert [board["name"] for board in store.list_boards()] == ["arcade"]


def test_the_cli_refuses_demo_and_a_refused_seed_says_nothing_was_seeded(tmp_path):
    with pytest.raises(SystemExit, match="--demo has nowhere"):
        main(["seed-beta", str(tmp_path / "comet"), "--demo"])

    taken = tmp_path / "taken"
    taken.mkdir()
    (taken / "x").write_text("")
    with pytest.raises(SystemExit, match="nothing seeded"):
        main(["seed-beta", str(taken), "--db", str(tmp_path / "board.sqlite3")])
