"""a headless card run cannot wait for anything, and a run that committed nothing stops at once.

Measured on the smolsmort housekeeping card: the agent backgrounded pytest, set a Monitor and a
ScheduleWakeup, said "pausing here" and ended its turn - which ended the run, with 12 writes never
committed. The board then ran the full gate on the unchanged tree and had the reviewer approve an
empty diff before refusing for no commits.
"""

import json
import shlex
import subprocess

from smortboard import lifecycle
from smortboard.exec.backends import ContainerBackend
from smortboard.exec.leases import write_lease_settings
from smortboard.exec.runner import SYSTEM_PROMPT, WAITING_TOOLS, RunResult, build_command
from smortboard.store.api import Store


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)


def test_every_waiting_tool_is_disallowed_on_the_command_line():
    cmd = build_command("brief", None)
    denied = [cmd[i + 1] for i, part in enumerate(cmd) if part == "--disallowedTools"]
    assert denied == list(WAITING_TOOLS)
    assert {"Monitor", "ScheduleWakeup"} <= set(denied)


def test_the_headless_rule_reaches_the_worker_even_under_a_stored_prompt(tmp_path):
    """a prompt saved in the board replaces the default whole - measured on the live board, where
    a stored worker prompt froze the default - so the rule rides outside the editable prompt"""

    def _command(store):
        cmd = ContainerBackend(image="img")._docker_command(
            tmp_path / "clone", "p", tmp_path / "s.json", "sonnet", None, store
        )
        return shlex.join(cmd)

    with Store(tmp_path / "b.db") as store:
        default = _command(store)
        store.set_prompt("worker", "a custom worker prompt")
        custom = _command(store)
    assert "NOTHING WAKES YOU UP" in default
    assert "a custom worker prompt" in custom
    assert "NOTHING WAKES YOU UP" in custom
    assert "uncommitted changes are discarded" in custom
    assert "NOTHING WAKES YOU UP" not in SYSTEM_PROMPT  # not editable, so not in the prompt


def test_the_bash_guard_refuses_a_background_command(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    settings_path = write_lease_settings(tmp_path / "guards", ["**"], root=worktree)
    settings = json.loads(settings_path.read_text())
    entry = next(e for e in settings["hooks"]["PreToolUse"] if e["matcher"] == "Bash")
    hook = entry["hooks"][0]["command"].split(" ", 1)

    def _call(tool_input):
        payload = json.dumps({"tool_input": tool_input})
        return subprocess.run(hook, input=payload, capture_output=True, text=True)

    background = _call({"command": "uv run pytest -q", "run_in_background": True})
    assert background.returncode == 2
    assert "foreground" in background.stderr
    assert _call({"command": "uv run pytest -q"}).returncode == 0


class _NoCommitBackend:
    """a clean run that ends its turn without committing anything"""

    name = "fake"

    def run_card(self, store, card_id, worktree_path, prompt, settings_path, **kwargs):
        return RunResult(
            subtype="success",
            is_error=False,
            blocked_reason_code=None,
            session_id="s",
            total_cost_usd=0.1,
            num_turns=3,
            result_text="Pausing here - waiting for the pytest background run.",
        )


def test_a_run_with_no_commit_is_refused_before_the_gates(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "thing.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "first")

    store = Store(tmp_path / "b.db")
    board = store.create_board("b")
    r = store.create_repo(board["id"], "repo", str(repo), "main", test_command="true", image="i")
    card = store.create_card(board["id"], r["id"], "a card", leases=["thing.py"])
    gates = []
    monkeypatch.setattr(lifecycle, "run_test_gate", lambda *a, **k: gates.append("test"))
    monkeypatch.setattr(lifecycle, "run_review", lambda *a, **k: gates.append("review"))

    result = lifecycle.run_card_lifecycle(store, card["id"], backend=_NoCommitBackend())
    assert result.phase == "refused"
    assert "without a commit" in result.refusal
    assert gates == []
    store.close()
