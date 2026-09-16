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
            "leases": ["y.py"],
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
    assert [m["author"] for m in messages] == ["operator", "board", "orchestrator"]
    assert "nope" in messages[1]["body"]
    assert messages[1]["author"] == "board"
    reply = messages[2]
    assert reply["body"] == "ok"
    assert {c["title"] for c in reply["cards"]} == {"a", "b"}

    assert store.get_plan(board["id"]) == "p"


def test_planning_mode_creates_no_card_and_says_so_on_the_board(store, board):
    result = run_orchestrator_turn(
        store, board["id"], "build a and b", runner=_runner(TWO_CARDS), mode="planning"
    )
    assert result.error is None
    assert store.list_cards(board["id"]) == []
    board_notes = [
        m["body"] for m in store.list_orchestrator_messages(board["id"]) if m["author"] == "board"
    ]
    assert any("2 proposed card" in note for note in board_notes)
    # operator's reply still lands - planning mode is a conversation, not a refusal
    reply = [
        m for m in store.list_orchestrator_messages(board["id"]) if m["author"] == "orchestrator"
    ]
    assert reply and reply[0]["body"] == "ok"


def test_planning_mode_with_no_proposed_cards_adds_no_board_note(store, board):
    payload = {"reply": "still thinking", "plan": "p", "cards": []}
    run_orchestrator_turn(store, board["id"], "hey", runner=_runner(payload), mode="planning")
    board_notes = [
        m["body"] for m in store.list_orchestrator_messages(board["id"]) if m["author"] == "board"
    ]
    assert board_notes == []  # nothing was ignored, so nothing to say


def test_manage_mode_creates_cards_same_as_the_default(store, board):
    result = run_orchestrator_turn(
        store, board["id"], "build a and b", runner=_runner(TWO_CARDS), mode="manage"
    )
    assert result.error is None
    assert len(store.list_cards(board["id"])) == 2


def test_the_turn_prompt_carries_the_mode_rule(store, board):
    seen_prompts = []

    def _capture(prompt, model, budget_usd):
        seen_prompts.append(prompt)
        return json.dumps({"reply": "ok", "plan": "p", "cards": []})

    run_orchestrator_turn(store, board["id"], "hi", runner=_capture, mode="planning")
    assert "PLANNING MODE" in seen_prompts[0]
    run_orchestrator_turn(store, board["id"], "hi", runner=_capture, mode="manage")
    assert "MANAGE MODE" in seen_prompts[1]


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
    assert [m["author"] for m in messages] == ["operator", "board"]
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


def test_registry_passes_its_mode_argument_through_to_the_turn(tmp_path):
    db = tmp_path / "board.db"
    with Store(db) as store:
        board = store.create_board("dev")
        store.create_repo(board["id"], "repo", "/repo", "main")

    registry = OrchestratorRegistry(db)
    assert registry.start(board["id"], "build a and b", runner=_runner(TWO_CARDS), mode="planning")
    import time

    deadline = time.time() + 5
    while registry.thinking(board["id"]) and time.time() < deadline:
        time.sleep(0.02)
    with Store(db) as store:
        assert store.list_cards(board["id"]) == []  # planning mode reached the real turn


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


def test_registry_turns_use_the_active_profile_token(tmp_path, monkeypatch):
    """a shift+p switch reaches mission control, not only card runs"""
    import time
    from types import SimpleNamespace

    from smortboard import orchestrator, profiles

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    state = tmp_path / "profiles.json"
    monkeypatch.setenv("SMORTBOARD_PROFILES_STATE_PATH", str(state))
    state.write_text(json.dumps({"active": "second", "profiles": ["default", "second"]}))

    seen = []

    def fake_turn(store, board_id, message, token_path=None, **_kwargs):
        seen.append(token_path)
        return SimpleNamespace(error=None)

    monkeypatch.setattr(orchestrator, "run_orchestrator_turn", fake_turn)
    db = tmp_path / "board.db"
    with Store(db) as store:
        board = store.create_board("dev")

    registry = OrchestratorRegistry(db)
    assert registry.start(board["id"], "hi")
    deadline = time.time() + 5
    while registry.thinking(board["id"]) and time.time() < deadline:
        time.sleep(0.02)
    assert seen == [profiles.profile_path("second")]


def test_a_proposed_card_with_no_lease_is_created_but_flagged(store, board):
    base = TWO_CARDS["cards"][0]
    payload = {"reply": "ok", "plan": "p", "cards": [{**base, "title": "bare", "leases": []}]}
    run_orchestrator_turn(store, board["id"], "go", runner=_runner(payload))
    assert [c["title"] for c in store.list_cards(board["id"])] == ["bare"]
    notes = [
        m["body"] for m in store.list_orchestrator_messages(board["id"]) if m["author"] == "board"
    ]
    assert any('"bare" has no lease' in note for note in notes)


@pytest.mark.parametrize("greedy", [["**"], ["**/*"], ["*/**"], ["*"], ["/etc/**"], ["../x"]])
def test_a_proposed_catch_all_or_climbing_lease_is_dropped_not_stored(store, board, greedy):
    """a model-proposed catch-all would let the worker write anywhere - the board drops it like
    any other invalid glob rather than passing it straight to create_card"""
    base = TWO_CARDS["cards"][0]
    payload = {"reply": "ok", "plan": "p", "cards": [{**base, "title": "greedy", "leases": greedy}]}
    run_orchestrator_turn(store, board["id"], "go", runner=_runner(payload))
    card = next(c for c in store.list_cards(board["id"]) if c["title"] == "greedy")
    assert card["leases"] == []
    notes = [
        m["body"] for m in store.list_orchestrator_messages(board["id"]) if m["author"] == "board"
    ]
    assert any('"greedy" has no lease' in note for note in notes)


def test_a_proposed_lease_keeps_its_literal_globs(store, board):
    base = TWO_CARDS["cards"][0]
    leases = ["**", "src/**", "docs/*.md"]
    payload = {"reply": "ok", "plan": "p", "cards": [{**base, "title": "mixed", "leases": leases}]}
    run_orchestrator_turn(store, board["id"], "go", runner=_runner(payload))
    card = next(c for c in store.list_cards(board["id"]) if c["title"] == "mixed")
    assert sorted(row["path_glob"] for row in card["leases"]) == ["docs/*.md", "src/**"]
