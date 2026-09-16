"""live steering: a note operator sends while a card is running reaches the agent at its next step,
not only on the card's next run.

Proven mechanic (two spikes, claude 2.1.197 in the card image, haiku): with
`--input-format stream-json`, a user-turn line written after a `result` event lands in the SAME
session with its memory; one written during a turn lands between two tool calls. Nothing here invokes
the real `claude` binary or docker - every process is a fake Popen with scripted stdout lines and a
stdin buffer we can inspect.
"""

import json
import subprocess
import threading

import pytest

from smortboard.exec import runner
from smortboard.exec.backends import ContainerBackend
from smortboard.exec.runner import build_command, run_process, user_message_line
from smortboard.server.runs import RunRegistry
from smortboard.store.api import Store
from tests.test_lifecycle import (  # noqa: F401 (fixtures + helpers)
    _Backend,
    _stub_gates,
    board,
    repo,
)

# -- build_command: the prompt moves from argv to the first stdin turn -----------------------


def test_stream_input_drops_the_prompt_from_argv_and_asks_for_stream_json():
    cmd = build_command("the brief", None, stream_input=True)
    assert "the brief" not in cmd
    assert "--input-format" in cmd and "stream-json" in cmd
    assert cmd[cmd.index("--input-format") + 1] == "stream-json"


def test_default_build_command_is_unchanged_for_reviewer_and_orchestrator():
    """the old one-shot shape - prompt in argv, no --input-format - still the default, since
    reviewer.py and orchestrator.py were not touched and still call build_command this way"""
    cmd = build_command("the brief", None)
    assert "the brief" in cmd
    assert "--input-format" not in cmd


def test_user_message_line_is_one_stream_json_user_turn():
    line = user_message_line("hello")
    assert line.endswith("\n")
    event = json.loads(line)
    assert event == {"type": "user", "message": {"role": "user", "content": "hello"}}


# -- run_process: the live steering loop, against a fake Popen -------------------------------


class _FakeStdin:
    def __init__(self):
        self.writes: list[str] = []
        self.closed = False

    def write(self, text):
        if self.closed:
            raise ValueError("write to closed stdin")
        self.writes.append(text)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _FakeProcess:
    def __init__(self, stdout_lines):
        self.stdin = _FakeStdin()
        self.stdout = iter(stdout_lines)
        self.stderr = _StrStream("")
        self.returncode = 0

    def wait(self, timeout=None):
        pass


class _StrStream:
    def __init__(self, text):
        self._text = text

    def read(self):
        return self._text


def _result_line(*, result_text, cost, turns):
    return (
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": result_text,
                "session_id": "s1",
                "total_cost_usd": cost,
                "num_turns": turns,
            }
        )
        + "\n"
    )


def test_a_note_between_two_result_events_is_delivered_and_stdin_stays_open(tmp_path, monkeypatch):
    lines = [
        _result_line(result_text="turn one done", cost=0.10, turns=3),
        _result_line(result_text="turn two done", cost=0.25, turns=5),
    ]
    fake = _FakeProcess(lines)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)

    calls = {"n": 0}

    def pending_notes():
        calls["n"] += 1
        # only the first result event finds a queued note - the second finds none, which is what
        # lets stdin close and the process exit
        if calls["n"] == 1:
            return [{"id": "c1", "body": "check the edge case"}]
        return []

    with Store(tmp_path / "b.db") as store:
        board_row = store.create_board("b")
        card = store.create_card(board_row["id"], None, "a card")
        marker = "Note from operator, via the board [c0ffee12]: "
        result = run_process(
            store,
            card["id"],
            ["claude"],
            token_line="tok3n\n",
            stream_prompt="the brief",
            pending_notes=pending_notes,
            note_marker=marker,
        )

        events = [e["kind"] for e in store.list_events(card["id"])]
        assert events.count("note_delivered") == 1
        delivered = next(e for e in store.list_events(card["id"]) if e["kind"] == "note_delivered")
        assert delivered["payload"]["comment_ids"] == ["c1"]

    # token line, the brief's first turn, then the queued note - three writes, in order
    assert fake.stdin.writes[0] == "tok3n\n"
    assert json.loads(fake.stdin.writes[1].strip())["message"]["content"] == "the brief"
    note_turn = json.loads(fake.stdin.writes[2].strip())
    assert note_turn["message"]["content"] == (marker + "check the edge case")
    assert len(fake.stdin.writes) == 3
    assert fake.stdin.closed  # no notes on the second result -> stdin closes -> process exits

    # the last result decides the outcome; cost and turns are per turn, so they add up
    assert result.result_text == "turn two done"
    assert result.total_cost_usd == pytest.approx(0.35)
    assert result.num_turns == 8


def test_no_pending_notes_closes_stdin_after_the_first_result(tmp_path, monkeypatch):
    fake = _FakeProcess([_result_line(result_text="done", cost=0.05, turns=1)])
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)

    with Store(tmp_path / "b.db") as store:
        board_row = store.create_board("b")
        card = store.create_card(board_row["id"], None, "a card")
        run_process(
            store, card["id"], ["claude"], stream_prompt="the brief", pending_notes=lambda: []
        )

    assert fake.stdin.closed
    assert len(fake.stdin.writes) == 1  # only the brief - never asked to deliver anything


def test_a_note_queued_mid_turn_is_written_before_the_turn_ends(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "NOTE_POLL_SECONDS", 0.01)
    fake = _FakeProcess([])
    queued = [{"id": "c1", "body": "use the other file"}]
    written = threading.Event()

    def stdout():
        yield json.dumps({"type": "assistant", "message": {"content": []}}) + "\n"
        # the turn stays open until the feeder has written the note, as a tool call would
        assert written.wait(2), "the note was never written mid-turn"
        yield _result_line(result_text="done", cost=0.1, turns=2)

    def pending_notes():
        if not queued:
            return []
        written.set()
        return [queued.pop()]

    fake.stdout = stdout()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)

    marker = "Note from operator, via the board [c0ffee12]: "
    with Store(tmp_path / "b.db") as store:
        board_row = store.create_board("b")
        card = store.create_card(board_row["id"], None, "a card")
        run_process(
            store,
            card["id"],
            ["claude"],
            stream_prompt="the brief",
            pending_notes=pending_notes,
            note_marker=marker,
        )
        delivered = [e for e in store.list_events(card["id"]) if e["kind"] == "note_delivered"]

    assert json.loads(fake.stdin.writes[1])["message"]["content"] == (marker + "use the other file")
    assert len(fake.stdin.writes) == 2  # the brief and the note, nothing at the result
    assert [e["payload"]["comment_ids"] for e in delivered] == [["c1"]]
    assert fake.stdin.closed


def test_stream_prompt_none_keeps_the_old_one_shot_stdin_shape(tmp_path, monkeypatch):
    """stdin_text is the old one-shot handoff (reviewer, orchestrator) - unaffected by streaming"""
    fake = _FakeProcess([_result_line(result_text="done", cost=0.01, turns=1)])
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)

    run_process(None, "c", ["claude"], stdin_text="tok3n\n")

    assert fake.stdin.writes == ["tok3n\n"]
    assert fake.stdin.closed  # closed immediately, per the old shape - no live loop entered


# -- the container command line: stdin stays open past the token line ------------------------


def test_docker_command_drops_dev_null_and_streams_json():
    cmd = ContainerBackend(image="img")._docker_command(
        "/clone", "the brief", "/s.json", "sonnet", None
    )
    inner = cmd[-1]
    assert "< /dev/null" not in inner
    assert "the brief" not in inner  # travels as a stdin turn, not baked into the shell command
    assert "--input-format stream-json" in inner


def test_docker_command_s_system_prompt_names_the_marker_it_was_given():
    """F4: the same marker run_card hands to run_process must be the one the worker's own system
    prompt names, or the agent has nothing to check a live note against"""
    marker = "Note from operator, via the board [c0ffee12]: "
    cmd = ContainerBackend(image="img")._docker_command(
        "/clone", "the brief", "/s.json", "sonnet", None, None, marker
    )
    inner = cmd[-1]
    assert marker in inner


# -- RunRegistry: notes only queue while a card's run is actually going -----------------------


def test_queue_note_only_accepted_while_the_run_is_going(tmp_path):
    registry = RunRegistry(tmp_path / "b.db")
    assert registry.queue_note("nope", {"id": "c1", "body": "hi"}) is False

    release = threading.Event()
    started = threading.Event()

    def _runner(store, card_id, **kwargs):
        started.set()
        release.wait(timeout=5)

        class _Result:
            phase = "opened"
            pr_url = None
            blocked_reason_code = None
            refusal = None

        return _Result()

    state = registry.start("card-1", runner=_runner)
    started.wait(timeout=5)
    assert state.running

    assert registry.queue_note("card-1", {"id": "c1", "body": "hold on"}) is True
    assert registry.pop_pending("card-1") == [{"id": "c1", "body": "hold on"}]
    # drained - a second pop before anything new is queued finds nothing
    assert registry.pop_pending("card-1") == []

    release.set()
    for _ in range(50):
        if not state.running:
            break
        threading.Event().wait(0.05)
    assert registry.queue_note("card-1", {"id": "c2", "body": "too late"}) is False


# -- lifecycle: pending_notes reaches every worker turn, including a fix round ----------------


class _CapturingBackend(_Backend):
    """same as _Backend, but keeps every kwarg the lifecycle handed to run_card - _Backend's own
    .calls only records card_id/prompt/path, which cannot prove pending_notes made it through"""

    def run_card(self, store, card_id, worktree_path, prompt, settings_path, **kwargs):
        result = super().run_card(store, card_id, worktree_path, prompt, settings_path, **kwargs)
        self.calls[-1]["kwargs"] = kwargs
        return result


def test_pending_notes_reaches_the_worker_call(board, monkeypatch):  # noqa: F811 (pytest fixture)
    import smortboard.lifecycle as lifecycle_module

    store, card_id = board
    _stub_gates(monkeypatch)
    backend = _CapturingBackend()
    sentinel = lambda: []  # noqa: E731

    lifecycle_module.run_card_lifecycle(store, card_id, backend=backend, pending_notes=sentinel)

    assert len(backend.calls) == 1
    assert backend.calls[0]["kwargs"]["pending_notes"] is sentinel


def test_pending_notes_reaches_the_fix_round_worker_call(board, monkeypatch):  # noqa: F811
    import smortboard.lifecycle as lifecycle_module
    from smortboard.review.reviewer import ReviewFinding, ReviewResult

    store, card_id = board
    store.update_card(card_id, findings_route="fix")
    finding = ReviewFinding(severity="high", category="bug", file="a.py", line=1, message="oops")
    calls = {"n": 0}

    def _review(*a, **k):
        calls["n"] += 1
        return ReviewResult(approved=calls["n"] > 1, findings=[finding] if calls["n"] == 1 else [])

    _stub_gates(monkeypatch)
    monkeypatch.setattr("smortboard.lifecycle.run_review", _review)
    backend = _CapturingBackend()
    sentinel = lambda: []  # noqa: E731

    lifecycle_module.run_card_lifecycle(store, card_id, backend=backend, pending_notes=sentinel)

    assert len(backend.calls) == 2, "one initial run plus one fix round"
    assert all(call["kwargs"]["pending_notes"] is sentinel for call in backend.calls)


# -- the worker system prompt teaches the note protocol, and a delivered note carries the marker --


def test_worker_system_prompt_carries_the_note_protocol():
    """a second spike showed an unmarked mid-turn message reads as a prompt injection and gets
    refused - the agent has to be told up front what a genuine live note looks like"""
    marker = runner.new_note_marker()
    prompt = runner.SYSTEM_PROMPT + runner.note_marker_paragraph(marker)
    assert marker.strip() in prompt
    assert "prompt injection" in prompt.lower()
    assert "priority" in prompt.lower() or "override" in prompt.lower()


def test_two_runs_get_different_note_markers():
    """F4: a fixed marker is guessable from repo content the worker reads - each run mints its own"""
    assert runner.new_note_marker() != runner.new_note_marker()


def test_a_delivered_note_carries_the_run_s_own_marker(tmp_path, monkeypatch):
    fake = _FakeProcess(
        [
            _result_line(result_text="turn one", cost=0.1, turns=1),
            _result_line(result_text="turn two", cost=0.2, turns=2),
        ]
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
    calls = {"n": 0}

    def pending_notes():
        calls["n"] += 1
        return [{"id": "c1", "body": "ship it"}] if calls["n"] == 1 else []

    marker = "Note from operator, via the board [deadbeef]: "
    run_process(
        None,
        "c",
        ["claude"],
        stream_prompt="brief",
        pending_notes=pending_notes,
        note_marker=marker,
    )
    note_turn = json.loads(fake.stdin.writes[1].strip())
    assert note_turn["message"]["content"].startswith(marker)


def test_run_process_mints_its_own_marker_when_none_is_given(tmp_path, monkeypatch):
    """a caller with no notes-explaining prompt to match (tests, the reviewer) still gets a note
    marker rather than a crash - it just won't be one the agent's prompt names"""
    fake = _FakeProcess([_result_line(result_text="done", cost=0.05, turns=1)])
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
    run_process(
        None,
        "c",
        ["claude"],
        stream_prompt="brief",
        pending_notes=lambda: [{"id": "c1", "body": "x"}],
    )
    note_turn = json.loads(fake.stdin.writes[1].strip())
    assert note_turn["message"]["content"].startswith("Note from")


# -- the http route: delivery answers "live" while the run is going, "next_run" once it is not ----


def test_conversation_post_and_get_report_live_delivery_while_the_run_is_going(tmp_path):
    import json as _json
    import threading as _threading
    import urllib.request as _urlreq

    from smortboard.server.app import build_server

    ready = _threading.Event()
    holder = {}

    def _serve():
        server_store = Store(tmp_path / "b.db")
        server = build_server(server_store, port=0, host="127.0.0.1")
        holder["server"] = server
        board_row = server_store.create_board("b")
        holder["card_id"] = server_store.create_card(board_row["id"], None, "a card")["id"]
        ready.set()
        server.serve_forever()
        server_store.close()

    thread = _threading.Thread(target=_serve, daemon=True)
    thread.start()
    ready.wait(timeout=5)
    server = holder["server"]
    card_id = holder["card_id"]
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def _call(method, path, body=None):
        data = _json.dumps(body).encode() if body is not None else None
        req = _urlreq.Request(
            f"{base}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        with _urlreq.urlopen(req) as resp:
            return resp.status, _json.loads(resp.read())

    try:
        # before any run: next_run both ways
        status, body = _call("POST", f"/api/cards/{card_id}/conversation", {"message": "hi"})
        assert status == 201 and body["delivery"] == "next_run"
        status, body = _call("GET", f"/api/cards/{card_id}/conversation")
        assert body["delivery"] == "next_run"

        # a run that blocks until released, so the window where it is "going" is controllable
        release = _threading.Event()
        started = _threading.Event()

        def _blocking_runner(store, cid, **kwargs):
            started.set()
            release.wait(timeout=5)

            class _Result:
                phase = "opened"
                pr_url = None
                blocked_reason_code = None
                refusal = None

            return _Result()

        server.runs.start(card_id, runner=_blocking_runner)
        started.wait(timeout=5)

        status, body = _call("GET", f"/api/cards/{card_id}/conversation")
        assert body["running"] is True and body["delivery"] == "live"

        status, body = _call(
            "POST", f"/api/cards/{card_id}/conversation", {"message": "check the edge case"}
        )
        assert status == 201 and body["delivery"] == "live"

        release.set()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_note_delivered_event_renders_as_a_dim_board_line(tmp_path):
    import json as _json
    import threading as _threading
    import urllib.request as _urlreq

    from smortboard.server.app import build_server

    ready = _threading.Event()
    holder = {}

    def _serve():
        store = Store(tmp_path / "b.db")
        server = build_server(store, port=0, host="127.0.0.1")
        holder["server"] = server
        board_row = store.create_board("b")
        card = store.create_card(board_row["id"], None, "a card")
        store.append_event(card["id"], "note_delivered", {"comment_ids": ["x1", "x2"]})
        holder["card_id"] = card["id"]
        ready.set()
        server.serve_forever()
        store.close()

    thread = _threading.Thread(target=_serve, daemon=True)
    thread.start()
    ready.wait(timeout=5)
    server = holder["server"]

    try:
        with _urlreq.urlopen(
            f"http://127.0.0.1:{server.server_address[1]}"
            f"/api/cards/{holder['card_id']}/conversation"
        ) as resp:
            data = _json.loads(resp.read())
        lines = [m["body"] for m in data["messages"] if m["author"] == "board"]
        assert any("delivered 2 notes" in line for line in lines), lines
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
