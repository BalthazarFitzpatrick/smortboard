"""mission control reads each repo's ledger and folders, and cards a ledger task only once"""

import json
import subprocess

import pytest

from smortboard.orchestrator import build_board_snapshot, build_turn_prompt, run_orchestrator_turn
from smortboard.store import Store

TASKS = [
    {"id": 1, "title": "one", "status": "queued", "acceptance_criteria": "a thing works"},
    {"id": "slug", "title": "two", "status": "in_progress", "acceptance_criteria": ""},
    {"id": 3, "title": "three", "status": "done", "acceptance_criteria": ""},
]


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    (repo / "dev_ledger").mkdir(parents=True)
    (repo / "pkg" / "ui").mkdir(parents=True)
    (repo / "dev_ledger" / "TASKS.jsonl").write_text("".join(json.dumps(t) + "\n" for t in TASKS))
    (repo / "pkg" / "ui" / "board.js").write_text("x")
    (repo / "README.md").write_text("x")
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "a@b.c")
    _git(repo, "config", "user.name", "a")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "first")
    # an uncommitted ledger edit must not reach the prompt: it reads the default branch
    (repo / "dev_ledger" / "TASKS.jsonl").write_text(json.dumps({"id": 9, "title": "wip"}) + "\n")

    store = Store(tmp_path / "board.db")
    b = store.create_board("dev")
    store.create_repo(b["id"], name="repo", path=str(repo), default_branch="main")
    yield store, b["id"]
    store.close()


def _runner(cards):
    def run(prompt, model, budget_usd):
        return json.dumps({"reply": "ok", "plan": "", "cards": cards})

    return run


def _card(task_id, title="card one"):
    return {
        "title": title,
        "description": "d",
        "repo": "repo",
        "criteria": [],
        "tasks": [],
        "leases": ["pkg/**"],
        "depends_on": [],
        "model": None,
        "task_id": task_id,
    }


def test_the_snapshot_carries_open_tasks_and_folders(board):
    store, board_id = board
    repo = build_board_snapshot(store, board_id)["repos"][0]
    assert [t["id"] for t in repo["open_tasks"]] == ["1", "slug"]
    assert repo["open_tasks"][0]["criteria"] == "a thing works"
    assert all(t["linked_card"] is None for t in repo["open_tasks"])
    assert "pkg/ui/ (1)" in repo["layout"]
    assert "README.md" in repo["layout"]
    assert "open_tasks" in build_turn_prompt({"repos": [repo]}, "hi")


def test_a_ledger_task_is_carded_once(board):
    store, board_id = board
    run_orchestrator_turn(store, board_id, "card task 1", runner=_runner([_card("1")]))
    run_orchestrator_turn(
        store, board_id, "again", runner=_runner([_card("1", title="card one again")])
    )

    cards = store.list_cards(board_id)
    assert [c["title"] for c in cards] == ["card one"]
    notes = [
        m["body"] for m in store.list_orchestrator_messages(board_id) if m["author"] == "board"
    ]
    assert any("task 1 is already on card" in n for n in notes)
    linked = build_board_snapshot(store, board_id)["repos"][0]["open_tasks"][0]
    assert linked["linked_card"] == cards[0]["id"][:8]


def test_a_card_without_a_task_is_not_linked(board):
    store, board_id = board
    run_orchestrator_turn(store, board_id, "free card", runner=_runner([_card(None, "free")]))
    run_orchestrator_turn(store, board_id, "another", runner=_runner([_card(None, "free two")]))
    assert len(store.list_cards(board_id)) == 2


def test_an_untracked_ledger_symlink_into_a_private_repo_is_read_from_disk(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("x")
    (repo / ".gitignore").write_text("TASKS.jsonl\n")
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "a@b.c")
    _git(repo, "config", "user.name", "a")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "first")
    private = tmp_path / "private-ledgers" / "repo"
    private.mkdir(parents=True)
    (private / "TASKS.jsonl").write_text("".join(json.dumps(t) + "\n" for t in TASKS))
    (repo / "TASKS.jsonl").symlink_to("../private-ledgers/repo/TASKS.jsonl")

    store = Store(tmp_path / "board.db")
    board_id = store.create_board("dev")["id"]
    store.create_repo(board_id, name="repo", path=str(repo), default_branch="main")
    snapshot_repo = build_board_snapshot(store, board_id)["repos"][0]
    store.close()
    assert [t["id"] for t in snapshot_repo["open_tasks"]] == ["1", "slug"]


def test_a_repo_with_no_ledger_has_no_open_tasks(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("x")
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "a@b.c")
    _git(repo, "config", "user.name", "a")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "first")
    store = Store(tmp_path / "board.db")
    board_id = store.create_board("dev")["id"]
    store.create_repo(board_id, name="repo", path=str(repo), default_branch="main")
    assert build_board_snapshot(store, board_id)["repos"][0]["open_tasks"] == []
    store.close()
