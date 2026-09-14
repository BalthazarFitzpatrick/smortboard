"""run_orchestrator_turn's screenshot flow: one ask, one png, one re-run.

the runner is always faked - no docker, no model, ever (same rule as test_orchestrator.py) - and
playwright is always faked too, per the card's scope.
"""

import json

import pytest

from smortboard.orchestrator import run_orchestrator_turn
from smortboard.store.api import Store


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
