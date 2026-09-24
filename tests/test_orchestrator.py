"""mission control turns. the runner is always faked - no docker, no model, ever."""

import json

import pytest

from smortboard.consolidate import FOLD_JSON_SCHEMA
from smortboard.orchestrator import (
    ORCHESTRATOR_JSON_SCHEMA,
    OrchestratorRegistry,
    build_system_prompt,
    card_text_warnings,
    run_orchestrator_turn,
)
from smortboard.store.api import Store
from tests.test_reviewer_path import _objects


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
    # the operator's reply still lands - planning mode is a conversation, not a refusal
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


# -- strict schemas: what codex's --output-schema accepts, and what comes back --


@pytest.mark.parametrize(
    ("schema", "count"),
    # mission control: the reply, a card, a test command
    [(ORCHESTRATOR_JSON_SCHEMA, 3), (FOLD_JSON_SCHEMA, 2)],
    ids=["orchestrator", "fold"],
)
def test_mission_control_and_fold_schemas_are_strict_at_every_object_level(schema, count):
    """the reviewer's failure on card 4c56f435 - `invalid_json_schema ... 'additionalProperties'
    is required to be supplied and to be false` - holds for every schema codex is handed"""
    objects = list(_objects(schema))
    assert len(objects) == count
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert sorted(obj["required"]) == sorted(obj["properties"])


def test_a_strict_reply_with_its_optional_values_null_reads_like_one_without_them(store, board):
    """strict output sends every key, the optional ones as null: no repo, no model, no ledger
    link, no screenshot re-run, and no board note about any of them"""
    card_schema = ORCHESTRATOR_JSON_SCHEMA["properties"]["cards"]["items"]["properties"]
    assert all("null" in card_schema[key]["type"] for key in ("repo", "model", "lab", "task_id"))
    bare = {
        "title": "bare",
        "description": "GOAL: bare",
        "repo": None,
        "criteria": ["bare works"],
        "tasks": [],
        "leases": ["x.py"],
        "depends_on": [],
        "model": None,
        "lab": None,
        "task_id": None,
        "complexity": "low",
    }
    full = bare | {
        "title": "full",
        "repo": "repo",
        "leases": ["y.py"],
        "depends_on": ["bare"],
        "model": "haiku",
        "task_id": "t1",
        "complexity": "high",
    }
    payload = {
        "reply": "ok",
        "plan": "p",
        "screenshot": None,
        "cards": [bare, full],
        "test_commands": [],
    }
    assert set(payload) == set(ORCHESTRATOR_JSON_SCHEMA["properties"])
    assert set(bare) == set(full) == set(card_schema)
    calls = []

    def run(prompt, model, budget_usd):
        calls.append(prompt)
        return json.dumps(payload)

    result = run_orchestrator_turn(store, board["id"], "go", runner=run)
    assert result.error is None
    assert len(calls) == 1
    by_title = {c["title"]: c for c in store.list_cards(board["id"])}
    made_bare, made_full = by_title["bare"], by_title["full"]
    assert [made_bare[key] for key in ("repo_id", "model", "lab", "ledger_task")] == [None] * 4
    assert made_bare["complexity"] == 1
    assert made_full["repo_id"] is not None
    assert (made_full["model"], made_full["ledger_task"], made_full["complexity"]) == (
        "haiku",
        "t1",
        3,
    )
    assert made_full["depends_on"] == [made_bare["id"]]
    authors = [m["author"] for m in store.list_orchestrator_messages(board["id"])]
    assert authors == ["operator", "orchestrator"]


# -- test_commands: the runner mission control names for a repo that has none --


@pytest.fixture
def fresh(store, board):
    """a second repo on the board, with no test command yet"""
    return store.create_repo(board["id"], "fresh", "/fresh", "main")


def _names_command(repo, command, **extra):
    entry = {"repo": repo, "command": command}
    return {"reply": "ok", "plan": "p", "cards": [], "test_commands": [entry], **extra}


def _board_notes(store, board_id):
    messages = store.list_orchestrator_messages(board_id)
    return [m["body"] for m in messages if m["author"] == "board"]


def test_a_named_test_command_is_stored_on_a_repo_without_one(store, board, fresh):
    payload = _names_command("fresh", "node --test")
    run_orchestrator_turn(store, board["id"], "go", runner=_runner(payload))
    assert store.get_repo(fresh["id"])["test_command"] == "node --test"
    assert _board_notes(store, board["id"]) == ['set fresh\'s test command to "node --test"']


@pytest.mark.parametrize(
    ("name", "command", "note"),
    [
        ("repo", "node --test", 'kept repo\'s test command "uv run pytest"'),
        (
            "fresh",
            "pytest; rm -rf /",
            "refused fresh's proposed test command: not a plain test runner call",
        ),
        ("nope", "node --test", 'no repo named "nope", so its proposed test command was not set'),
    ],
    ids=["already-set", "shell-syntax", "unknown-repo"],
)
def test_a_named_test_command_never_replaces_one_or_passes_unsafe_or_unmatched(
    store, board, fresh, name, command, note
):
    before = {r["name"]: r["test_command"] for r in store.list_repos(board["id"])}
    payload = _names_command(name, command)
    run_orchestrator_turn(store, board["id"], "go", runner=_runner(payload))
    assert {r["name"]: r["test_command"] for r in store.list_repos(board["id"])} == before
    assert _board_notes(store, board["id"]) == [note]


def test_planning_mode_stores_no_test_command(store, board, fresh):
    payload = _names_command("fresh", "node --test")
    run_orchestrator_turn(store, board["id"], "go", runner=_runner(payload), mode="planning")
    assert store.get_repo(fresh["id"])["test_command"] is None
    assert any("1 proposed test command" in n for n in _board_notes(store, board["id"]))


def _replies(*payloads):
    queue = iter(payloads)

    def run(prompt, model, budget_usd, screenshot_path=None):
        return json.dumps(next(queue))

    return run


def _write_png(url, out_path):
    out_path.write_bytes(b"png")


def test_the_screenshot_rerun_carries_its_own_test_commands(store, board, fresh):
    asks = {"reply": "let me look", "plan": "", "cards": [], "screenshot": "board"}
    answers = _names_command("fresh", "node --test", screenshot=None)
    run_orchestrator_turn(
        store,
        board["id"],
        "look",
        runner=_replies(asks, answers),
        board_url="http://127.0.0.1:8000/ui/index.html",
        screenshot_taker=_write_png,
    )
    assert store.get_repo(fresh["id"])["test_command"] == "node --test"


def test_a_failed_screenshot_keeps_the_first_replys_test_commands(store, board, fresh):
    # a non-loopback board url is refused before any re-run, so the first reply stands
    asks = _names_command("fresh", "node --test", screenshot="board")
    run_orchestrator_turn(
        store,
        board["id"],
        "look",
        runner=_replies(asks),
        board_url="http://example.com/",
        screenshot_taker=_write_png,
    )
    assert store.get_repo(fresh["id"])["test_command"] == "node --test"


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

    # generous, not a measurement: the suite runs `-n auto` and a worker thread on a
    # loaded machine can wait seconds to be scheduled at all
    deadline = time.time() + 30
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

    # generous, not a measurement: the suite runs `-n auto` and a worker thread on a
    # loaded machine can wait seconds to be scheduled at all
    deadline = time.time() + 30
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


def test_registry_leaves_credential_selection_to_the_role_runner(tmp_path, monkeypatch):
    """the role can retry on another lab, so the registry must not pin an active token"""
    import time
    from types import SimpleNamespace

    from smortboard import orchestrator

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
    # generous, not a measurement: the suite runs `-n auto` and a worker thread on a
    # loaded machine can wait seconds to be scheduled at all
    deadline = time.time() + 30
    while registry.thinking(board["id"]) and time.time() < deadline:
        time.sleep(0.02)
    # the contract is that no turn pins a token, not how many turns landed in this window:
    # run_orchestrator_turn is patched on the module, so a daemon thread outliving an earlier test
    # appends here too, and asserting a call count makes this test fail for someone else's leak
    assert seen
    assert all(token_path is None for token_path in seen)


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


def test_the_orchestrator_budget_setting_caps_the_turn(store, board):
    budgets = []

    def run(prompt, model, budget_usd):
        budgets.append(budget_usd)
        return json.dumps({"reply": "ok", "plan": "p", "cards": []})

    run_orchestrator_turn(store, board["id"], "go", runner=run)
    store.set_setting("orchestrator_budget_usd", 3)
    run_orchestrator_turn(store, board["id"], "again", runner=run)
    assert budgets == [1.0, 3.0]


def test_the_card_text_rules_ride_in_every_turn_prompt():
    prompt = build_system_prompt("", {})
    assert "CARD TEXT RULES" in prompt
    assert "title: at most 8 words" in prompt and "description: at most 20 words" in prompt
    assert '"fox dug hole, dreams of nicer den"' in prompt, "the telegram-style example rides too"
    assert "8 to 10" not in prompt, "a title has a maximum, never a minimum"


def test_card_text_warnings_cap_the_title_at_eight_words_with_no_minimum():
    assert card_text_warnings({"title": "fix login"}) == []
    assert card_text_warnings({"title": "one two three four five six seven eight"}) == []
    nine = "one two three four five six seven eight nine"
    assert card_text_warnings({"title": nine}) == [
        f'"{nine}" runs long: 9 words in the title (max 8)'
    ]
    long_criterion = " ".join(["word"] * 13)
    notes = card_text_warnings({"title": "t", "criteria": ["short one", long_criterion]})
    assert notes == ['"t" runs long: 1 criteria over 12 words']


def test_long_card_text_is_reported_not_cut(store, board):
    long_title = "one two three four five six seven eight nine ten eleven twelve"
    long_description = " ".join(["word"] * 30)
    payload = {
        "reply": "ok",
        "plan": "p",
        "cards": [{**TWO_CARDS["cards"][0], "title": long_title, "description": long_description}],
    }
    run_orchestrator_turn(store, board["id"], "go", runner=_runner(payload))
    card = next(c for c in store.list_cards(board["id"]) if c["title"] == long_title)
    assert card["description"] == long_description, "the board never shortens card text"
    notes = [
        m["body"] for m in store.list_orchestrator_messages(board["id"]) if m["author"] == "board"
    ]
    joined = " ".join(notes)
    assert "12 words in the title" in joined and "30 words in the description" in joined
