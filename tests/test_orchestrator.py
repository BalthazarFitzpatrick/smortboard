"""mission control turns. the runner is always faked - no docker, no model, ever."""

import json

import pytest

from smortboard.orchestrator import OrchestratorRegistry, run_orchestrator_turn
from smortboard.store.api import Store


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "board.db") as s:
        yield s


@pytest.fixture
def board(store):
    b = store.create_board("dev")
    store.create_repo(b["id"], "repo", "/repo", "main", test_command="uv run pytest")
    return b


def _runner(payload):
    def run(prompt, model, budget_usd):
        return json.dumps(payload)

    return run


TWO_CARDS = {
    "reply": "ok",
    "plan": "p",
    "cards": [
        {
            "title": "a",
            "description": "",
            "repo": "repo",
            "criteria": ["c"],
            "tasks": [],
            "leases": ["x.py"],
            "depends_on": [],
        },
        {
            "title": "b",
            "description": "",
            "repo": "nope",
            "criteria": [],
            "tasks": [],
            "leases": [],
            "depends_on": ["a"],
        },
    ],
}


def test_a_turn_creates_cards_resolves_repo_and_deps_and_flags_the_unknown_repo(store, board):
    result = run_orchestrator_turn(store, board["id"], "build a and b", runner=_runner(TWO_CARDS))
    assert result.error is None

    cards = store.list_cards(board["id"])
    by_title = {c["title"]: c for c in cards}
    assert len(cards) == 2
    assert by_title["a"]["status"] == "todo"
    assert by_title["a"]["repo_id"] is not None
    assert by_title["b"]["repo_id"] is None
    assert store.get_dependencies(by_title["b"]["id"]) == [by_title["a"]["id"]]

    messages = store.list_orchestrator_messages(board["id"])
    assert [m["author"] for m in messages] == ["fabian", "board", "orchestrator"]
    assert "nope" in messages[1]["body"]
    assert messages[1]["author"] == "board"
    reply = messages[2]
    assert reply["body"] == "ok"
    assert {c["title"] for c in reply["cards"]} == {"a", "b"}

    assert store.get_plan(board["id"]) == "p"


def test_a_card_carries_the_model_proposed_and_a_non_name_is_refused(store, board):
    # the model reaches `claude --model`, so only plain aliases and ids get through
    base = TWO_CARDS["cards"][0]
    payload = {
        "reply": "ok",
        "plan": "p",
        "cards": [
            {**base, "title": "fast", "model": "Haiku"},
            {**base, "title": "odd", "model": "opus; rm -rf /"},
            {**base, "title": "plain", "model": None},
        ],
    }
    run_orchestrator_turn(store, board["id"], "go", runner=_runner(payload))
    by_title = {c["title"]: c for c in store.list_cards(board["id"])}
    assert by_title["fast"]["model"] == "haiku"
    assert by_title["odd"]["model"] is None
    assert by_title["plain"]["model"] is None
    notes = [
        m["body"] for m in store.list_orchestrator_messages(board["id"]) if m["author"] == "board"
    ]
    assert any("is not a model name" in note for note in notes)


def test_a_failing_runner_stores_a_board_error_and_leaves_no_plan(store, board):
    def _boom(prompt, model, budget_usd):
        raise RuntimeError("no docker")

    result = run_orchestrator_turn(store, board["id"], "build it", runner=_boom)
    assert result.error is not None

    messages = store.list_orchestrator_messages(board["id"])
    assert [m["author"] for m in messages] == ["fabian", "board"]
    assert "no docker" in messages[1]["body"]
    assert store.get_plan(board["id"]) is None


def test_an_unparseable_response_stores_a_board_error(store, board):
    def _garbage(prompt, model, budget_usd):
        return "not json"

    result = run_orchestrator_turn(store, board["id"], "build it", runner=_garbage)
    assert result.error is not None
    messages = store.list_orchestrator_messages(board["id"])
    assert messages[-1]["author"] == "board"
    assert store.list_cards(board["id"]) == []


def test_a_turn_with_no_cards_is_just_conversation(store, board):
    payload = {"reply": "sure, tell me more", "plan": "", "cards": []}
    result = run_orchestrator_turn(store, board["id"], "hey", runner=_runner(payload))
    assert result.error is None
    assert store.list_cards(board["id"]) == []
    assert store.list_orchestrator_messages(board["id"])[-1]["body"] == "sure, tell me more"


def test_depends_on_resolves_against_an_existing_card_by_title(store, board):
    existing = store.create_card(board["id"], None, "already there")
    payload = {
        "reply": "ok",
        "plan": "p",
        "cards": [
            {
                "title": "new one",
                "description": "",
                "repo": None,
                "criteria": [],
                "tasks": [],
                "leases": [],
                "depends_on": ["already there"],
            }
        ],
    }
    run_orchestrator_turn(store, board["id"], "add a dependent", runner=_runner(payload))
    new_card = next(c for c in store.list_cards(board["id"]) if c["title"] == "new one")
    assert store.get_dependencies(new_card["id"]) == [existing["id"]]


# -- OrchestratorRegistry: thinking flag and one turn per board --------------


def test_registry_runs_a_turn_and_clears_thinking(tmp_path):
    db = tmp_path / "board.db"
    with Store(db) as store:
        board = store.create_board("dev")

    registry = OrchestratorRegistry(db)
    started = registry.start(
        board["id"], "hi", runner=_runner({"reply": "hi", "plan": "", "cards": []})
    )
    assert started
    import time

    deadline = time.time() + 5
    while registry.thinking(board["id"]) and time.time() < deadline:
        time.sleep(0.02)
    assert not registry.thinking(board["id"])
    assert registry.error(board["id"]) is None


def test_registry_refuses_a_second_turn_while_thinking(tmp_path):
    import threading

    db = tmp_path / "board.db"
    with Store(db) as store:
        board = store.create_board("dev")

    gate = threading.Event()

    def _slow_runner(prompt, model, budget_usd):
        gate.wait(timeout=5)
        return json.dumps({"reply": "done", "plan": "", "cards": []})

    registry = OrchestratorRegistry(db)
    assert registry.start(board["id"], "first", runner=_slow_runner)
    assert registry.thinking(board["id"])
    assert not registry.start(board["id"], "second", runner=_slow_runner)
    gate.set()
