"""what each role's run is told about: only the tools it may use, and the effort the operator set.

Docker and the model are faked - only the functions that would otherwise shell out.
"""

import shlex

import pytest

from smortboard.exec.backends import ContainerBackend
from smortboard.exec.runner import RunResult
from smortboard.labs.base import RunRequest
from smortboard.labs.claude_code import ClaudeCodeAdapter, available_tools
from smortboard.labs.codex import CodexAdapter
from smortboard.orchestrator import _real_runner
from smortboard.review.reviewer import run_review
from smortboard.store.api import Store

REPO = {"test_command": "uv run pytest -q", "lint_command": "uv run ruff check ."}
GOOD = '{"reply": "ok", "plan": "p", "cards": [], "findings": []}'


def _agent_argv(docker_cmd: list[str]) -> list[str]:
    """the claude argv inside the container's `sh -c`, after the token handoff"""
    words = shlex.split(docker_cmd[-1])
    return words[words.index("claude") :]


def _flag(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def _fake_runs(monkeypatch, module: str, calls: list) -> None:
    monkeypatch.setattr(f"smortboard.{module}.docker_available", lambda: True)
    monkeypatch.setattr(f"smortboard.{module}.read_card_token", lambda path=None: "t0k3n")

    def run_process(store, card_id, cmd, *args, **kwargs):
        calls.append(cmd)
        return RunResult("success", False, None, "s1", 0.01, 1, GOOD)

    monkeypatch.setattr(f"smortboard.{module}.run_process", run_process)


def _worker_argv(tmp_path, store=None) -> list[str]:
    cmd = ContainerBackend(image="img")._docker_command(
        tmp_path / "clone", "p", tmp_path / "s.json", "sonnet", REPO, store
    )
    return _agent_argv(cmd)


def _reviewer_argv(tmp_path, monkeypatch, store=None, card_id="card") -> list[str]:
    calls = []
    _fake_runs(monkeypatch, "review.reviewer", calls)
    run_review(store, card_id, "diff --git a/x b/x\n+x\n", tmp_path, tmp_path / "g" / "s.json")
    return _agent_argv(calls[0])


def _board_argv(store, board_id, monkeypatch, role) -> list[str]:
    calls = []
    _fake_runs(monkeypatch, "orchestrator", calls)
    _real_runner(store, board_id, None, "rules", [], role=role)("brief", "sonnet", 1.0)
    return _agent_argv(calls[0])


def test_the_tool_list_is_the_allowlist_by_bare_name():
    assert available_tools(("Read", "Bash(git *)", "Bash(uv run pytest *)", "Grep")) == (
        "Read,Bash,Grep"
    )


def test_a_worker_is_described_only_its_six_tools_and_no_slash_commands(tmp_path):
    argv = _worker_argv(tmp_path)
    assert _flag(argv, "--tools") == "Read,Edit,Write,Glob,Grep,Bash"
    assert "--disable-slash-commands" in argv
    # the permission layer is untouched: still exactly the repo's commands, not a blanket Bash
    allowed = [argv[i + 1] for i, word in enumerate(argv) if word == "--allowedTools"]
    assert "Bash(uv run pytest -q *)" in allowed and "Bash" not in allowed
    assert _flag(argv, "--disallowedTools") == "Monitor"


def test_the_reviewer_is_described_read_glob_grep_and_keeps_its_schema(tmp_path, monkeypatch):
    argv = _reviewer_argv(tmp_path, monkeypatch)
    assert _flag(argv, "--tools") == "Read,Grep,Glob"
    assert "--disable-slash-commands" in argv
    # StructuredOutput is added by --json-schema itself - probed in the card image, 2.1.273
    assert _flag(argv, "--json-schema") is not None


@pytest.mark.parametrize("role", ["orchestrator", "fold"])
def test_board_turns_are_described_read_glob_grep_only(tmp_path, monkeypatch, role):
    with Store(tmp_path / "b.db") as store:
        board = store.create_board("b")
        argv = _board_argv(store, board["id"], monkeypatch, role)
    assert _flag(argv, "--tools") == "Read,Grep,Glob"
    assert "--disable-slash-commands" in argv
    assert _flag(argv, "--json-schema") is not None


def test_unset_effort_passes_no_flag_for_any_role(tmp_path, monkeypatch):
    with Store(tmp_path / "b.db") as store:
        board = store.create_board("b")
        card = store.create_card(board["id"], None, "c")
        argvs = [
            _worker_argv(tmp_path, store),
            _reviewer_argv(tmp_path, monkeypatch, store, card["id"]),
            _board_argv(store, board["id"], monkeypatch, "orchestrator"),
            _board_argv(store, board["id"], monkeypatch, "fold"),
        ]
    assert all("--effort" not in argv for argv in argvs)


def test_each_role_gets_its_own_effort(tmp_path, monkeypatch):
    with Store(tmp_path / "b.db") as store:
        store.set_settings(
            {
                "worker_effort": "low",
                "reviewer_effort": "medium",
                "orchestrator_effort": "high",
                "fold_effort": "low",
            }
        )
        board = store.create_board("b")
        card = store.create_card(board["id"], None, "c")
        assert _flag(_worker_argv(tmp_path, store), "--effort") == "low"
        reviewer = _reviewer_argv(tmp_path, monkeypatch, store, card["id"])
        assert _flag(reviewer, "--effort") == "medium"
        orchestrator = _board_argv(store, board["id"], monkeypatch, "orchestrator")
        assert _flag(orchestrator, "--effort") == "high"
        assert _flag(_board_argv(store, board["id"], monkeypatch, "fold"), "--effort") == "low"


def test_a_passed_effort_wins_over_the_role_setting(tmp_path, monkeypatch):
    with Store(tmp_path / "b.db") as store:
        store.set_settings({"worker_effort": "low", "reviewer_effort": "low"})
        board = store.create_board("b")
        card = store.create_card(board["id"], None, "c")
        cmd = ContainerBackend(image="img")._docker_command(
            tmp_path / "clone", "p", tmp_path / "s.json", "sonnet", REPO, store, effort="high"
        )
        assert _flag(_agent_argv(cmd), "--effort") == "high"
        calls = []
        _fake_runs(monkeypatch, "review.reviewer", calls)
        run_review(
            store,
            card["id"],
            "diff --git a/x b/x\n+x\n",
            tmp_path,
            tmp_path / "s.json",
            effort="medium",
        )
        assert _flag(_agent_argv(calls[0]), "--effort") == "medium"


def test_claude_and_codex_spell_effort_their_own_way():
    claude = ClaudeCodeAdapter().build_command(RunRequest("brief", effort="high"))
    assert _flag(claude, "--effort") == "high"
    assert "--effort" not in ClaudeCodeAdapter().build_command(RunRequest("brief"))

    codex = CodexAdapter().build_command(RunRequest("brief", model="gpt-5.6-sol", effort="low"))
    assert 'model_reasoning_effort="low"' in codex
    assert codex[codex.index('model_reasoning_effort="low"') - 1] == "-c"
    assert codex[-1] == "brief", "the prompt stays the last argument"
    plain = CodexAdapter().build_command(RunRequest("brief", model="gpt-5.6-sol"))
    assert not any("model_reasoning_effort" in word for word in plain)


@pytest.mark.parametrize(
    "model,effort",
    [
        ("anthropic/claude-opus-5-5", "max"),
        ("openai/gpt-6.1-sol", "xhigh"),
    ],
)
def test_saved_exact_model_and_effort_reach_the_adapter_argv(tmp_path, model, effort):
    path = tmp_path / "b.db"
    with Store(path) as store:
        board = store.create_board("exact")
        card = store.create_card(board["id"], None, "c", model=model, effort=effort)
    with Store(path) as store:
        card = store.get_card(card["id"])
        cmd = ContainerBackend(image="img")._docker_command(
            tmp_path / "clone",
            "p",
            tmp_path / "s.json",
            f"{card['lab']}/{card['model']}",
            REPO,
            store,
            effort=card["effort"],
            kind="auth_json",
        )
    words = shlex.split(cmd[-1])
    adapter_name = "claude" if card["lab"] == "anthropic" else "codex"
    argv = words[words.index(adapter_name) :]
    assert _flag(argv, "--model") == model.split("/")[1]
    if card["lab"] == "anthropic":
        assert _flag(argv, "--effort") == effort
    else:
        assert f'model_reasoning_effort="{effort}"' in argv


def test_worker_drops_inherited_effort_for_a_model_without_support(tmp_path):
    with Store(tmp_path / "b.db") as store:
        store.set_settings({"worker_model": "claude-opus-5-5", "worker_effort": "max"})
        cmd = ContainerBackend(image="img")._docker_command(
            tmp_path / "clone", "p", tmp_path / "s.json", "claude-haiku-4-5", REPO, store
        )
    assert "--effort" not in _agent_argv(cmd)


@pytest.mark.parametrize("role", ["orchestrator", "fold"])
def test_board_runner_drops_inherited_effort_after_selecting_another_model(
    tmp_path, monkeypatch, role
):
    with Store(tmp_path / "b.db") as store:
        store.set_settings({f"{role}_model": "claude-opus-5-5", f"{role}_effort": "max"})
        board = store.create_board("exact")
        calls = []
        _fake_runs(monkeypatch, "orchestrator", calls)
        _real_runner(store, board["id"], None, "rules", [], role=role)(
            "brief", "claude-haiku-4-5", 1.0
        )
    assert "--effort" not in _agent_argv(calls[0])


def test_reviewer_drops_inherited_effort_after_selecting_another_model(tmp_path, monkeypatch):
    with Store(tmp_path / "b.db") as store:
        store.set_settings({"reviewer_model": "claude-opus-5-5", "reviewer_effort": "max"})
        board = store.create_board("exact")
        card = store.create_card(board["id"], None, "c")
        calls = []
        _fake_runs(monkeypatch, "review.reviewer", calls)
        run_review(
            store,
            card["id"],
            "diff --git a/x b/x\n+x\n",
            tmp_path,
            tmp_path / "s.json",
            model="claude-haiku-4-5",
        )
    assert "--effort" not in _agent_argv(calls[0])
