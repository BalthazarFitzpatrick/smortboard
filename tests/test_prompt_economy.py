"""what each prompt spends tokens on: the worker's command list and reading rule, mission control's
cacheable system prompt and compact snapshot, and the spend a turn records.

Docker and the model are faked - only the functions that would otherwise shell out.
"""

import json
import shlex

from smortboard.consolidate import run_fold_turn
from smortboard.exec.backends import ContainerBackend
from smortboard.exec.runner import (
    READING_RULES,
    RunResult,
    allowed_tools_for_repo,
    commands_preamble,
)
from smortboard.orchestrator import (
    _LEDGER_RULES,
    _TEST_RULES,
    CARD_TEXT_RULES,
    build_board_snapshot,
    run_orchestrator_turn,
)
from smortboard.prompts import LOWERCASE_RULE
from smortboard.review.reviewer import _build_prompt
from smortboard.store.api import Store
from smortboard.telemetry import EVIDENCE_CARD_LIMIT, board_evidence

REPO = {
    "test_command": "uv run --no-sync pytest -q",
    "lint_command": "uv run ruff check . && uv run ruff format --check .",
}
REPLY = json.dumps({"reply": "ok", "plan": "p", "cards": []})


def _system_prompt(docker_cmd: list[str]) -> str:
    words = shlex.split(docker_cmd[-1])
    return words[words.index("--system-prompt") + 1]


def test_the_worker_is_told_every_granted_command_verbatim():
    brief = commands_preamble(REPO)
    grants = [tool[5:-1] for tool in allowed_tools_for_repo(REPO) if tool.startswith("Bash(")]
    assert grants == [
        "git *",
        "uv run --no-sync pytest -q *",
        "uv run ruff check . *",
        "uv run ruff format --check . *",
        "uv run ruff format . *",
    ]
    for grant in grants:
        assert f"\n- {grant}\n" in brief
    for fact in ("no pipes", "no docker", "never push", "no dependency installs", "outside your"):
        assert fact in brief


def test_the_reading_rule_rides_every_worker_prompt_even_an_edited_one(tmp_path):
    with Store(tmp_path / "b.db") as store:
        default = ContainerBackend(image="img")._docker_command(
            tmp_path / "c", "p", tmp_path / "s.json", "sonnet", REPO, store
        )
        store.set_prompt("worker", "a custom worker prompt")
        edited = ContainerBackend(image="img")._docker_command(
            tmp_path / "c", "p", tmp_path / "s.json", "sonnet", REPO, store
        )
    assert "Grep for the line numbers first" in READING_RULES
    assert READING_RULES in _system_prompt(default)
    assert READING_RULES in _system_prompt(edited)
    assert "a custom worker prompt" in _system_prompt(edited)


def _capture_turns(monkeypatch, seen: list) -> None:
    """swaps the production runner for one that records what each turn would have sent"""

    def fake_real_runner(store, board_id, token_path, system_prompt, read_paths, **kwargs):
        def run(prompt, model, budget_usd, screenshot_path=None):
            seen.append({"system": system_prompt, "prompt": prompt})
            return REPLY

        return run

    monkeypatch.setattr("smortboard.orchestrator._real_runner", fake_real_runner)


def test_two_turns_share_one_static_system_prompt_and_carry_no_rules(tmp_path, monkeypatch):
    seen = []
    _capture_turns(monkeypatch, seen)
    with Store(tmp_path / "b.db") as store:
        board = store.create_board("b")
        run_orchestrator_turn(store, board["id"], "first")
        store.create_card(board["id"], None, "a card that changes the snapshot")
        run_orchestrator_turn(store, board["id"], "second")
    first, second = seen
    assert first["system"] == second["system"], "the cached prefix must not move between turns"
    assert first["prompt"] != second["prompt"]
    for rules in (CARD_TEXT_RULES, _LEDGER_RULES, _TEST_RULES):
        assert rules in first["system"]
        assert rules not in first["prompt"] and rules not in second["prompt"]


def test_the_test_rules_ride_an_edited_mission_control_prompt_too(tmp_path, monkeypatch):
    seen = []
    _capture_turns(monkeypatch, seen)
    with Store(tmp_path / "b.db") as store:
        board = store.create_board("b")
        store.set_prompt("orchestrator", "a custom mission control prompt")
        run_orchestrator_turn(store, board["id"], "plan it")
    system = seen[0]["system"]
    assert system.startswith("a custom mission control prompt")
    assert "TEST RULES. Tests are required on every card." in system
    assert _TEST_RULES in system


def test_the_snapshot_is_sent_compact(tmp_path, monkeypatch):
    seen = []
    _capture_turns(monkeypatch, seen)
    with Store(tmp_path / "b.db") as store:
        board = store.create_board("b")
        store.create_card(board["id"], None, "a card")
        run_orchestrator_turn(store, board["id"], "plan it")
        snapshot = build_board_snapshot(store, board["id"])
    prompt = seen[0]["prompt"]
    sent = prompt.split("Board snapshot:\n", 1)[1].split("\n", 1)[0]
    assert json.loads(sent).keys() == snapshot.keys()
    assert "\n  " not in sent and '": ' not in sent


def test_long_messages_are_cut_in_the_snapshot_not_in_the_store(tmp_path):
    with Store(tmp_path / "b.db") as store:
        board = store.create_board("b")
        store.add_orchestrator_message(board["id"], "orchestrator", "x" * 2000)
        store.add_orchestrator_message(board["id"], "orchestrator", "short")
        bodies = [m["body"] for m in build_board_snapshot(store, board["id"])["messages"]]
        stored = store.list_orchestrator_messages(board["id"])[0]["body"]
    assert bodies[0] == "x" * 800 + " [... 1200 chars cut]"
    assert bodies[1] == "short"
    assert stored == "x" * 2000


def _finished(store, card_id, model="claude-sonnet-4"):
    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(
        card_id,
        "result",
        {"total_cost_usd": 0.1, "num_turns": 3, "modelUsage": {model: {"costUSD": 0.1}}},
    )
    store.append_event(card_id, "worker_summary", {"text": "done"})
    store.append_event(card_id, "review_gate", {"approved": True, "findings": []})
    store.append_event(card_id, "merge_request", {"url": "https://example.test/pr/1"})


def test_evidence_lists_the_newest_cards_and_scores_them_all(tmp_path):
    with Store(tmp_path / "b.db") as store:
        board = store.create_board("b")
        cards = [store.create_card(board["id"], None, f"card {i}") for i in range(25)]
        for card in cards:
            _finished(store, card["id"])
        # an old card touched last counts as newest
        store.update_card(cards[0]["id"], title="card 0, renamed")
        evidence = board_evidence(store, board["id"])
    listed = [row["id"] for row in evidence["cards"]]
    assert len(listed) == EVIDENCE_CARD_LIMIT == 20
    assert listed[0] == cards[0]["id"][:8]
    assert cards[1]["id"][:8] not in listed
    assert sum(row["cards"] for row in evidence["model_scorecard"]) == 25


def test_a_mission_control_turn_writes_its_tokens_to_board_spend(tmp_path, monkeypatch):
    monkeypatch.setattr("smortboard.orchestrator.docker_available", lambda: True)
    monkeypatch.setattr("smortboard.orchestrator.read_card_token", lambda path=None: "t0k3n")

    def run_process(store, card_id, cmd, *args, **kwargs):
        return RunResult(
            "success",
            False,
            None,
            "s1",
            0.42,
            2,
            REPLY,
            input_tokens=12,
            output_tokens=340,
            cached_tokens=9000,
            cache_creation_tokens=4100,
        )

    monkeypatch.setattr("smortboard.orchestrator.run_process", run_process)
    with Store(tmp_path / "b.db") as store:
        board = store.create_board("b")
        result = run_orchestrator_turn(store, board["id"], "plan it")
        rows = store.list_board_spend(board["id"])
    assert result.error is None
    assert len(rows) == 1
    row = rows[0]
    assert (row["role"], row["cost_usd"]) == ("orchestrator", 0.42)
    assert (row["input_tokens"], row["output_tokens"]) == (12, 340)
    assert (row["cached_tokens"], row["cache_creation_tokens"]) == (9000, 4100)


def test_a_spend_row_without_tokens_keeps_them_unknown(tmp_path):
    with Store(tmp_path / "b.db") as store:
        board = store.create_board("b")
        row = store.add_board_spend(board["id"], "fold", 0.2)
    assert row["input_tokens"] is None and row["cache_creation_tokens"] is None


def test_the_fold_snapshot_is_sent_compact(tmp_path):
    prompts = []

    def run(prompt, model, budget_usd, screenshot_path=None):
        prompts.append(prompt)
        return json.dumps({"summary": "nothing", "groups": []})

    with Store(tmp_path / "b.db") as store:
        board = store.create_board("b")
        store.create_card(board["id"], None, "a", leases=["x.py"])
        store.create_card(board["id"], None, "b", leases=["x.py"])
        run_fold_turn(store, board["id"], runner=run)
    sent = prompts[0].split("Board snapshot:\n", 1)[1].split("\n", 1)[0]
    assert json.loads(sent)["cards"]
    assert '": ' not in sent


def test_the_lowercase_rule_rides_every_role_prompt_even_an_edited_one(tmp_path, monkeypatch):
    assert "commit messages, pull request titles and bodies" in LOWERCASE_RULE
    seen, folds = [], []
    _capture_turns(monkeypatch, seen)

    def fold(prompt, model, budget_usd, screenshot_path=None):
        folds.append(prompt)
        return json.dumps({"summary": "nothing", "groups": []})

    with Store(tmp_path / "b.db") as store:
        board = store.create_board("b")
        store.create_card(board["id"], None, "a", leases=["x.py"])
        store.create_card(board["id"], None, "b", leases=["x.py"])
        for role in ("worker", "orchestrator", "reviewer"):
            store.set_prompt(role, f"a custom {role} prompt")
        worker = ContainerBackend(image="img")._docker_command(
            tmp_path / "c", "p", tmp_path / "s.json", "sonnet", REPO, store
        )
        run_orchestrator_turn(store, board["id"], "plan it")
        run_fold_turn(store, board["id"], runner=fold)
        review = _build_prompt(store, "diff --git a/x b/x")
    assert "a custom worker prompt" in _system_prompt(worker)
    assert LOWERCASE_RULE in _system_prompt(worker)
    assert seen[0]["system"].startswith("a custom orchestrator prompt")
    assert LOWERCASE_RULE in seen[0]["system"]
    assert LOWERCASE_RULE in folds[0]
    assert review.startswith("a custom reviewer prompt")
    assert LOWERCASE_RULE in review
