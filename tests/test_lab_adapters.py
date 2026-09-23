import io
import json
import subprocess
import sys

import pytest

from smortboard.exec.runner import run_process
from smortboard.labs.base import (
    BashPolicy,
    Capabilities,
    LabEvent,
    RunRequest,
    ToolPolicy,
    estimate_usage,
)
from smortboard.labs.claude_code import ClaudeCodeAdapter
from smortboard.labs.codex import CodexAdapter
from smortboard.labs.registry import get_adapter


def test_claude_worker_command_preserved():
    req = RunRequest("brief", "/guards/settings.json", model="sonnet", system_prompt="rules")
    assert ClaudeCodeAdapter().build_command(req) == [
        "claude",
        "-p",
        "brief",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "acceptEdits",
        "--setting-sources",
        "project",
        "--system-prompt",
        "rules",
        "--settings",
        "/guards/settings.json",
        "--model",
        "sonnet",
        "--max-budget-usd",
        "5.0",
        "--allowedTools",
        "Bash(git *)",
        "--allowedTools",
        "Edit",
        "--allowedTools",
        "Read",
        "--allowedTools",
        "Write",
        "--allowedTools",
        "Glob",
        "--allowedTools",
        "Grep",
        "--disallowedTools",
        "Monitor",
        "--disallowedTools",
        "ScheduleWakeup",
        "--disallowedTools",
        "CronCreate",
        "--disallowedTools",
        "TaskOutput",
    ]


def test_codex_recorded_success_stream():
    adapter = CodexAdapter()
    assert adapter.normalize({"type": "thread.started", "thread_id": "thread-1"}) == []
    assert adapter.normalize({"type": "turn.started"}) == []
    text = adapter.normalize(
        {
            "type": "item.completed",
            "item": {
                "id": "item_0",
                "type": "agent_message",
                "text": "PINEAPPLE",
            },
        }
    )
    assert text[0].text == "PINEAPPLE"
    events = adapter.normalize(
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 12842,
                "cached_input_tokens": 10624,
                "cache_write_input_tokens": 0,
                "output_tokens": 7,
                "reasoning_output_tokens": 0,
            },
        }
    )
    assert events[0].usage["input_tokens"] == 12842
    assert events[0].usage["cached_tokens"] == 10624
    assert events[0].usage["cost_usd"] is None
    assert events[1].result["session_id"] == "thread-1"
    assert adapter.classify(events[1]) is None


@pytest.mark.parametrize(
    ("run_request", "can_commit"),
    [
        (RunRequest("brief", model="gpt-5.6-sol"), True),
        (RunRequest("brief", read_only=True), False),
        (RunRequest("brief", tool_policy=ToolPolicy(read_only=True)), False),
        (RunRequest("brief", role="reviewer", read_only=True), False),
        (RunRequest("brief", role="orchestrator", read_only=True), False),
        (RunRequest("brief", role="fold", read_only=True), False),
    ],
)
def test_codex_only_writable_workers_can_commit_in_the_container_clone(run_request, can_commit):
    command = CodexAdapter().build_command(run_request)
    grant = 'sandbox_workspace_write.writable_roots=["/workspace/.git"]'
    assert (grant in command) is can_commit
    assert command[command.index("--sandbox") + 1] == (
        "workspace-write" if can_commit else "read-only"
    )
    assert "--dangerously-bypass-approvals-and-sandbox" not in command


@pytest.mark.parametrize(
    ("message", "auth", "reason"),
    [
        ("Unauthorized: status 401", True, "CRASH"),
        ("Rate limit reached, 429", False, "USAGE_LIMIT"),
        ("Unsupported model status 400", False, "CRASH"),
    ],
)
def test_codex_error_classification(message, auth, reason):
    adapter = CodexAdapter()
    event = adapter.normalize({"type": "turn.failed", "error": {"message": message}})[0]
    assert event.result["auth_failed"] is auth
    assert adapter.classify(event) == reason


def test_codex_command_and_credentials():
    adapter = CodexAdapter()
    cmd = adapter.build_command(
        RunRequest(
            "brief",
            model="model-x",
            read_only=True,
            system_prompt="rules",
            json_schema={"type": "object"},
            schema_path="/guards/schema.json",
        )
    )
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert cmd[cmd.index("--output-schema") + 1] == "/guards/schema.json"
    assert 'developer_instructions="rules"' in cmd
    assert "--ignore-user-config" in cmd
    assert "--ignore-rules" not in cmd
    assert adapter.steering_line("a note") is None
    assert not adapter.capabilities.live_steering
    assert '"$CODEX_HOME/auth.json"' in adapter.auth_shell({"kind": "auth_json"})
    assert "--with-api-key" in adapter.auth_shell({"kind": "api_key"})
    assert "/home/agent/.codex:" in adapter.container_env()[1]
    assert get_adapter("openai") is not get_adapter("openai")


def test_token_pricing_excludes_cached_input_from_full_price(monkeypatch):
    monkeypatch.setattr(
        "smortboard.labs.catalog.load_catalog",
        lambda: {
            "openai": {
                "models": [
                    {
                        "id": "x",
                        "price_per_mtok": {"input": 10, "cached_input": 1, "output": 20},
                    }
                ]
            }
        },
    )
    usage = {"input_tokens": 1000, "cached_tokens": 800, "output_tokens": 100, "cost_usd": None}
    priced = estimate_usage(usage, "openai", "x")
    assert priced["cost_usd"] == pytest.approx(0.0048)
    assert priced["cost_estimated"]
    assert estimate_usage(usage, "openai", "unknown")["cost_usd"] is None


class FakeProcess:
    def __init__(self, events):
        self.stdin = None
        self.stdout = iter(json.dumps(event) + "\n" for event in events)
        self.stderr = io.StringIO("")
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def test_budget_watchdog_terminates_and_preserves_partial_structured_output(monkeypatch, tmp_path):
    from smortboard.store import Store
    from smortboard.telemetry import card_telemetry, usage_projection

    class Adapter(CodexAdapter):
        capabilities = Capabilities()

        def normalize(self, raw):
            if raw["type"] == "usage":
                return [LabEvent("usage", lab="openai", usage={"cost_usd": 0.6})]
            return super().normalize(raw)

    process = FakeProcess(
        [
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": '{"approved":true}'},
            },
            {"type": "usage"},
        ]
    )
    monkeypatch.setattr("smortboard.exec.runner.subprocess.Popen", lambda *a, **k: process)
    with Store(tmp_path / "board.db") as store:
        board = store.create_board("b")
        card = store.create_card(board["id"], None, "c")
        result = run_process(
            store,
            card["id"],
            ["fake"],
            adapter=Adapter(),
            model="x",
            profile="work",
            budget_usd=0.5,
        )
        assert usage_projection(store)["total_cost_usd"] == 0.6
        assert usage_projection(store)["models"][0]["cost_usd"] == 0.6
        assert usage_projection(store)["runs"] == 1
        attempt = card_telemetry(store, card["id"])["attempts"][0]
        assert attempt["cost_usd"] == 0.6
        assert attempt["worker_model"] == "openai/x"
    assert process.terminated
    assert result.subtype == "error_max_budget_usd"
    assert result.structured_output == {"approved": True}
    assert result.result_text == '{"approved":true}'
    assert result.total_cost_usd == 0.6
    assert (result.lab, result.model, result.profile) == ("openai", "x", "work")


def test_runner_records_run_profile_not_current_active_profile(monkeypatch):
    class Store:
        events = []

        def append_event(self, card, kind, payload):
            self.events.append((kind, payload))

    process = FakeProcess(
        [
            {
                "type": "rate_limit_event",
                "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour"},
            },
            {"type": "result", "subtype": "success", "total_cost_usd": 0.1},
        ]
    )
    monkeypatch.setattr("smortboard.exec.runner.subprocess.Popen", lambda *a, **k: process)
    store = Store()
    run_process(store, "c", ["fake"], model="sonnet", profile="original")
    assert all(payload["profile"] == "original" for _, payload in store.events)
    assert store.events[0][1]["neutral"][0]["rate_limit"]["status"] == "ok"
    assert store.events[1][1]["neutral"][-1]["result"]["ok"]


@pytest.mark.parametrize("cost", [None, 0.2])
def test_crash_before_final_event_preserves_partial_spend(monkeypatch, tmp_path, cost):
    from smortboard.store import Store
    from smortboard.telemetry import board_spend_today, card_telemetry, usage_projection

    class Adapter(CodexAdapter):
        def normalize(self, raw):
            return [LabEvent("usage", lab="openai", usage={"cost_usd": cost})]

    process = FakeProcess([{"type": "partial_usage"}])
    monkeypatch.setattr("smortboard.exec.runner.subprocess.Popen", lambda *args, **kwargs: process)
    with Store(tmp_path / "b.db") as store:
        board = store.create_board("b")
        card = store.create_card(board["id"], None, "c")
        result = run_process(store, card["id"], ["fake"], adapter=Adapter(), model="x")
        assert result.blocked_reason_code == "CRASH"
        assert board_spend_today(store, board["id"]) == cost
        assert usage_projection(store)["total_cost_usd"] == cost
        assert usage_projection(store)["models"][0]["cost_usd"] == cost
        assert card_telemetry(store, card["id"])["totals"]["cost_usd"] == cost


@pytest.mark.parametrize(
    ("tool", "args", "allowed"),
    [
        (
            "apply_patch",
            {"command": "*** Begin Patch\n*** Add File: src/a.py\n+x\n*** End Patch"},
            True,
        ),
        (
            "apply_patch",
            {"command": "*** Begin Patch\n*** Add File: docs/a.md\n+x\n*** End Patch"},
            False,
        ),
        (
            "apply_patch",
            {
                "command": "*** Begin Patch\n*** Update File: src/a.py\n*** Move to: docs/a.md\n*** End Patch"
            },
            False,
        ),
        ("Bash", {"command": "git status"}, True),
        ("Bash", {"command": "git status && git diff"}, True),
        ("Bash", {"command": "printf hi > docs/a.md"}, False),
        ("Bash", {"command": "git status; printf hi > docs/a.md"}, False),
        ("Bash", {"command": "git -c core.sshCommand=bad fetch"}, False),
    ],
)
def test_codex_guard_covers_patch_paths_and_shell_policy(tmp_path, tool, args, allowed):
    guards = CodexAdapter().guard_files(
        ["src/**"], BashPolicy(tmp_path / "guards", root=str(tmp_path), python=sys.executable)
    )
    result = subprocess.run(
        [sys.executable, str(guards.settings_path.parent / "codex_guard.py")],
        input=json.dumps({"tool_name": tool, "tool_input": args}),
        text=True,
        capture_output=True,
    )
    assert result.returncode == (0 if allowed else 2), result.stderr
