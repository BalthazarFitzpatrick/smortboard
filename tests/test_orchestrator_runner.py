"""the production orchestrator runner's docker command and its screenshot flow.

Docker and the model are faked - only the functions that would otherwise shell out - but the
read-only clones are built for real. The screenshot flow fakes the runner and playwright too, per
the card's scope.
"""

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from smortboard.exec.runner import RunResult
from smortboard.orchestrator import (
    ORCHESTRATOR_ALLOWED_TOOLS,
    run_orchestrator_turn,
)
from smortboard.store.api import Store

GOOD_JSON = json.dumps({"reply": "ok", "plan": "p", "cards": []})


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)


def _make_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    _git(path, "config", "user.email", "test@test.com")
    _git(path, "config", "user.name", "test")
    (path / "README.md").write_text("hello\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "initial")


def _wire(monkeypatch, calls, result_text=GOOD_JSON, token="t0k3n"):
    monkeypatch.setattr("smortboard.orchestrator.docker_available", lambda: True)
    monkeypatch.setattr("smortboard.orchestrator.read_card_token", lambda p=None: token)

    def _fake_run_process(store, card_id, cmd, stdin_text=None, container_name=None, **kwargs):
        calls.append({"cmd": cmd, "stdin": stdin_text, "container_name": container_name})
        return RunResult(
            subtype="success",
            is_error=False,
            blocked_reason_code=None,
            session_id="s1",
            total_cost_usd=0.01,
            num_turns=1,
            result_text=result_text,
        )

    monkeypatch.setattr("smortboard.orchestrator.run_process", _fake_run_process)


def test_the_command_mounts_clones_and_extra_paths_read_only(tmp_path, monkeypatch):
    repo = tmp_path / "src"
    _make_repo(repo)
    shots = tmp_path / "screens"
    shots.mkdir()
    missing = tmp_path / "gone"
    calls = []
    _wire(monkeypatch, calls)

    with Store(tmp_path / "b.db") as store:
        board = store.create_board("dev")
        store.create_repo(board["id"], "src", str(repo), "main", test_command="uv run pytest")
        store.set_setting("mission_control_read_paths", json.dumps([str(shots), str(missing)]))
        result = run_orchestrator_turn(store, board["id"], "plan it")

    assert result.error is None
    cmd = calls[0]["cmd"]
    joined = shlex.join(cmd)

    # a read-only clone mounted under /repos, and the existing extra path under /extra
    assert any(":/repos/src:ro" in c for c in cmd)
    assert f"{shots}:/extra/screens:ro" in cmd
    # the missing path was skipped, not mounted
    assert all("/extra/gone" not in c for c in cmd)

    # every -v mount is read-only
    vmounts = [cmd[i + 1] for i, c in enumerate(cmd) if c == "-v"]
    assert vmounts and all(m.endswith(":ro") for m in vmounts)

    # working dir is the mount parent, never a clone (a repo's own settings must not apply)
    assert cmd[cmd.index("-w") + 1] == "/"

    # read-only tools allowed, everything that writes or shells out refused
    for tool in ORCHESTRATOR_ALLOWED_TOOLS:
        assert f"--allowedTools {tool}" in joined
    for tool in ("Edit", "Write", "Bash"):
        assert f"--disallowedTools {tool}" in joined

    # named so the turn can be stopped
    assert "--name" in cmd
    assert calls[0]["container_name"] == cmd[cmd.index("--name") + 1]


def test_a_missing_read_path_becomes_a_board_message_and_the_turn_still_runs(tmp_path, monkeypatch):
    missing = tmp_path / "nope"
    calls = []
    _wire(monkeypatch, calls)

    with Store(tmp_path / "b.db") as store:
        board = store.create_board("dev")
        store.set_setting("mission_control_read_paths", json.dumps([str(missing)]))
        result = run_orchestrator_turn(store, board["id"], "plan it")
        notes = [
            m["body"]
            for m in store.list_orchestrator_messages(board["id"])
            if m["author"] == "board"
        ]

    assert result.error is None
    assert calls  # the turn still ran
    assert any(str(missing) in note for note in notes)


def test_the_screenshot_rerun_mounts_the_shots_dir_read_only_beside_the_clones(
    tmp_path, monkeypatch
):
    # the second, screenshot-aware run mounts exactly the turn's shots dir at /extra/shots:ro,
    # alongside the repo clones, so the Read tool the turn already has can open the image
    repo = tmp_path / "src"
    _make_repo(repo)
    calls = []
    asks = json.dumps({"reply": "let me look", "plan": "", "cards": [], "screenshot": "board"})
    answers = json.dumps({"reply": "seen", "plan": "p", "cards": [], "screenshot": None})
    _wire(monkeypatch, calls, result_text=asks)

    # the faked run_process returns `asks` first, then `answers`, so exactly one re-run happens
    seq = iter([asks, answers])

    def _fake_run_process(store, card_id, cmd, stdin_text=None, container_name=None, **kwargs):
        calls.append({"cmd": cmd, "stdin": stdin_text, "container_name": container_name})
        return RunResult(
            subtype="success",
            is_error=False,
            blocked_reason_code=None,
            session_id="s1",
            total_cost_usd=0.01,
            num_turns=1,
            result_text=next(seq),
        )

    monkeypatch.setattr("smortboard.orchestrator.run_process", _fake_run_process)

    def taker(url, out_path):
        out_path.write_bytes(b"fake-png-bytes")

    with Store(tmp_path / "b.db") as store:
        board = store.create_board("dev")
        store.create_repo(board["id"], "src", str(repo), "main", test_command="uv run pytest")
        result = run_orchestrator_turn(
            store,
            board["id"],
            "what does it look like",
            board_url="http://127.0.0.1:8000/ui/index.html",
            screenshot_taker=taker,
        )

    assert result.error is None
    assert len(calls) == 2
    first, second = calls[0]["cmd"], calls[1]["cmd"]
    # only the re-run carries the shots mount, and it is read-only and under /extra
    assert all("/extra/shots" not in c for c in first)
    assert any(c.endswith(":/extra/shots:ro") for c in second)
    # the repo clone is still mounted on the re-run
    assert any(":/repos/src:ro" in c for c in second)


def test_the_credential_travels_on_stdin_not_argv(tmp_path, monkeypatch):
    calls = []
    _wire(monkeypatch, calls, token="s3cr3t-token")

    with Store(tmp_path / "b.db") as store:
        board = store.create_board("dev")
        run_orchestrator_turn(store, board["id"], "plan it")

    call = calls[0]
    joined = shlex.join(call["cmd"])
    assert "-e" not in call["cmd"] and "--env" not in call["cmd"]
    assert "s3cr3t-token" not in joined
    assert call["stdin"] == "s3cr3t-token\n"


def test_no_docker_fails_the_turn_without_crashing(tmp_path, monkeypatch):
    monkeypatch.setattr("smortboard.orchestrator.docker_available", lambda: False)
    with Store(tmp_path / "b.db") as store:
        board = store.create_board("dev")
        result = run_orchestrator_turn(store, board["id"], "plan it")
        assert result.error is not None
        authors = [m["author"] for m in store.list_orchestrator_messages(board["id"])]
        assert "board" in authors


@pytest.mark.parametrize("raw", ["not json", json.dumps({"a": 1}), ""])
def test_a_malformed_read_paths_setting_is_no_paths_not_a_crash(tmp_path, raw):
    with Store(tmp_path / "b.db") as store:
        store.set_setting("mission_control_read_paths", raw)
        assert store.mission_control_read_paths() == []


# --- run_orchestrator_turn's screenshot flow: one ask, one png, one re-run (runner faked) ---


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "board.db") as s:
        yield s


@pytest.fixture
def board(store):
    return store.create_board("dev")


def _runner_sequence(payloads):
    """a fake OrchestratorRunner returning each payload in turn, one per call - records every
    call's screenshot_path, and whether/what it could read there AT CALL TIME, since the real
    turn cleans the shots directory up the moment its container run returns."""
    calls = []

    def run(prompt, model, budget_usd, screenshot_path=None):
        calls.append(
            {
                "prompt": prompt,
                "screenshot_path": screenshot_path,
                "screenshot_bytes": screenshot_path.read_bytes() if screenshot_path else None,
            }
        )
        return json.dumps(payloads[len(calls) - 1])

    run.calls = calls
    return run


def _fake_taker(written=b"fake-png-bytes"):
    calls = []

    def taker(url, out_path):
        calls.append(url)
        out_path.write_bytes(written)

    taker.calls = calls
    return taker


ASKS_FOR_SCREENSHOT = {"reply": "let me look", "plan": "", "cards": [], "screenshot": "board"}

ANSWERS_AFTER_LOOKING = {
    "reply": "i see a red column header",
    "plan": "p",
    "cards": [],
    "screenshot": None,
}


def test_a_screenshot_request_writes_one_png_and_reruns_with_it_readable(store, board):
    runner = _runner_sequence([ASKS_FOR_SCREENSHOT, ANSWERS_AFTER_LOOKING])
    taker = _fake_taker()

    result = run_orchestrator_turn(
        store,
        board["id"],
        "what does the board look like",
        runner=runner,
        board_url="http://127.0.0.1:8000/ui/index.html",
        screenshot_taker=taker,
    )

    assert result.error is None
    assert len(runner.calls) == 2
    assert taker.calls == ["http://127.0.0.1:8000/ui/index.html"]

    second_call = runner.calls[1]
    assert second_call["screenshot_path"] is not None
    assert second_call["screenshot_bytes"] == b"fake-png-bytes"

    messages = store.list_orchestrator_messages(board["id"])
    # only the re-run's reply is ever shown - the intermediate "let me look" never lands
    assert [m["body"] for m in messages] == [
        "what does the board look like",
        "i see a red column header",
    ]


def test_a_second_screenshot_request_in_the_rerun_is_refused(store, board):
    asks_again = {**ANSWERS_AFTER_LOOKING, "screenshot": "board", "reply": "let me look again"}
    runner = _runner_sequence([ASKS_FOR_SCREENSHOT, asks_again])
    taker = _fake_taker()

    result = run_orchestrator_turn(
        store,
        board["id"],
        "what does the board look like",
        runner=runner,
        board_url="http://127.0.0.1:8000/ui/index.html",
        screenshot_taker=taker,
    )

    assert result.error is None
    # exactly one screenshot ever taken and exactly one re-run - the second ask is never acted on
    assert len(runner.calls) == 2
    assert len(taker.calls) == 1

    messages = store.list_orchestrator_messages(board["id"])
    board_notes = [m["body"] for m in messages if m["author"] == "board"]
    assert any("second screenshot" in note for note in board_notes)
    assert messages[-1]["body"] == "let me look again"


def test_a_non_localhost_board_url_is_refused_before_any_browser_starts(store, board):
    runner = _runner_sequence([ASKS_FOR_SCREENSHOT])
    taker = _fake_taker()

    result = run_orchestrator_turn(
        store,
        board["id"],
        "what does the board look like",
        runner=runner,
        board_url="http://example.com/",
        screenshot_taker=taker,
    )

    assert result.error is None
    assert taker.calls == []
    # no browser ever opened, so there is nothing to re-run against - the first reply stands
    assert len(runner.calls) == 1

    messages = store.list_orchestrator_messages(board["id"])
    assert messages[-1]["body"] == "let me look"
    board_notes = [m["body"] for m in messages if m["author"] == "board"]
    assert any("screenshot" in note for note in board_notes)


def test_no_screenshot_field_means_no_screenshot_is_taken(store, board):
    payload = {"reply": "sure", "plan": "", "cards": [], "screenshot": None}
    runner = _runner_sequence([payload])
    taker = _fake_taker()

    result = run_orchestrator_turn(store, board["id"], "hi", runner=runner, screenshot_taker=taker)

    assert result.error is None
    assert taker.calls == []
    assert len(runner.calls) == 1


def test_an_old_style_runner_with_no_screenshot_path_argument_still_works(store, board):
    # a runner that predates this card's screenshot_path kwarg must still work for a plain turn
    def run(prompt, model, budget_usd):
        return json.dumps({"reply": "ok", "plan": "p", "cards": [], "screenshot": None})

    result = run_orchestrator_turn(store, board["id"], "hi", runner=run)
    assert result.error is None
