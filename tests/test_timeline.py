"""RUN REPLAY's backend projection: smortboard/timeline.py plus its route.

Fixture events are hand-built from the real payload shapes seen in a run (see the module docstring
in timeline.py) - no model call, no docker, ever.
"""

import json
import threading
from http.client import HTTPConnection

import pytest

from smortboard.server.app import build_server
from smortboard.store import Store
from smortboard.timeline import card_timeline


def _assistant_text(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _assistant_tool_use(tool_use_id, name, tool_input):
    return {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}]
        },
    }


def _user_tool_result(tool_use_id, content, is_error=False):
    return {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": content,
                    "is_error": is_error,
                }
            ]
        },
    }


def _result(permission_denials=None):
    return {"type": "result", "permission_denials": permission_denials or []}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "board.db")
    yield s
    s.close()


@pytest.fixture
def card_id(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "a card")
    return card["id"]


def _append(store, card_id, kind, payload):
    store.append_event(card_id, kind, payload)


def test_flattens_narration_edit_and_bash_into_ordered_steps(store, card_id):
    _append(store, card_id, "lifecycle_started", {})
    _append(store, card_id, "assistant", _assistant_text("looking at the readme"))
    _append(
        store,
        card_id,
        "assistant",
        _assistant_tool_use("t1", "Read", {"file_path": "/workspace/README.md"}),
    )
    _append(store, card_id, "user", _user_tool_result("t1", "1\t# smortboard"))
    _append(
        store,
        card_id,
        "assistant",
        _assistant_tool_use(
            "t2", "Edit", {"file_path": "/workspace/a.py", "old_string": "x", "new_string": "y"}
        ),
    )
    _append(
        store, card_id, "user", _user_tool_result("t2", "The file has been updated successfully.")
    )
    _append(
        store, card_id, "assistant", _assistant_tool_use("t3", "Bash", {"command": "pytest -q"})
    )
    _append(store, card_id, "user", _user_tool_result("t3", "3 passed"))

    result = card_timeline(store, card_id)
    kinds = [s["kind"] for s in result["steps"]]
    assert kinds == ["narration", "read", "edit", "run"]
    edit_step = result["steps"][2]
    assert edit_step["ok"] is True
    assert edit_step["detail"]["old_text"] == "x"
    assert edit_step["detail"]["new_text"] == "y"
    run_step = result["steps"][3]
    assert run_step["detail"]["command"] == "pytest -q"
    assert run_step["detail"]["output"] == "3 passed"


def test_a_denied_tool_use_is_refused_not_merely_failed(store, card_id):
    _append(store, card_id, "lifecycle_started", {})
    _append(store, card_id, "assistant", _assistant_tool_use("t1", "Bash", {"command": "rm -rf /"}))
    _append(store, card_id, "user", _user_tool_result("t1", "permission denied", is_error=True))
    _append(
        store,
        card_id,
        "result",
        _result(permission_denials=[{"tool_use_id": "t1", "tool_name": "Bash"}]),
    )

    result = card_timeline(store, card_id)
    step = result["steps"][0]
    assert step["kind"] == "refused"
    assert step["ok"] is False


def test_a_plain_tool_error_is_failed_but_not_refused(store, card_id):
    _append(store, card_id, "lifecycle_started", {})
    _append(
        store,
        card_id,
        "assistant",
        _assistant_tool_use(
            "t1", "Edit", {"file_path": "/a.py", "old_string": "x", "new_string": "y"}
        ),
    )
    _append(
        store,
        card_id,
        "user",
        _user_tool_result(
            "t1", "<tool_use_error>File has not been read yet.</tool_use_error>", is_error=True
        ),
    )
    _append(store, card_id, "result", _result())

    result = card_timeline(store, card_id)
    step = result["steps"][0]
    assert step["kind"] == "edit"
    assert step["ok"] is False


def test_test_review_and_merge_request_carry_their_own_verdict(store, card_id):
    _append(store, card_id, "lifecycle_started", {})
    _append(store, card_id, "worker_summary", {"text": "did the thing"})
    _append(
        store,
        card_id,
        "test_gate",
        {"passed": True, "command": "pytest", "exit_code": 0, "output": "ok"},
    )
    _append(
        store,
        card_id,
        "review_gate",
        {"approved": False, "error": None, "findings": [{"severity": "high"}]},
    )
    _append(store, card_id, "merge_request", {"opened": False, "refusal": "review not approved"})
    _append(store, card_id, "decision", {"decision": "rejected"})

    result = card_timeline(store, card_id)
    by_kind = {s["kind"]: s for s in result["steps"]}
    assert by_kind["summary"]["ok"] is None
    assert by_kind["test"]["ok"] is True
    assert by_kind["review"]["ok"] is False
    assert by_kind["pull request"]["ok"] is False
    assert by_kind["decision"]["ok"] is False


def test_multiple_attempts_are_listed_and_the_default_is_the_latest_that_reached_the_worker(
    store, card_id
):
    _append(store, card_id, "lifecycle_started", {})
    _append(store, card_id, "assistant", _assistant_text("refused before doing anything"))
    _append(store, card_id, "lifecycle_started", {})
    _append(store, card_id, "worker_summary", {"text": "second attempt did the real work"})
    _append(
        store,
        card_id,
        "test_gate",
        {"passed": True, "command": "pytest", "exit_code": 0, "output": "ok"},
    )
    _append(store, card_id, "lifecycle_started", {})  # a third attempt with no outcome yet

    result = card_timeline(store, card_id)
    assert len(result["attempts"]) == 3
    assert result["attempts"][1]["reached_worker"] is True
    assert result["attempts"][0]["reached_worker"] is False
    assert result["attempt"] == 2  # the third attempt never reached the worker, so it is skipped
    assert any(s["kind"] == "summary" for s in result["steps"])


def test_attempt_query_param_selects_a_specific_attempt(store, card_id):
    _append(store, card_id, "lifecycle_started", {})
    _append(store, card_id, "assistant", _assistant_text("first"))
    _append(store, card_id, "lifecycle_started", {})
    _append(store, card_id, "assistant", _assistant_text("second"))

    first = card_timeline(store, card_id, attempt=1)
    assert first["steps"][0]["detail"]["text"] == "first"
    second = card_timeline(store, card_id, attempt=2)
    assert second["steps"][0]["detail"]["text"] == "second"


def test_a_card_with_no_events_yields_an_empty_timeline(store, card_id):
    result = card_timeline(store, card_id)
    assert result == {"attempt": None, "attempts": [], "steps": []}


def test_large_write_content_is_bounded(store, card_id):
    _append(store, card_id, "lifecycle_started", {})
    huge = "x" * 10_000
    _append(
        store,
        card_id,
        "assistant",
        _assistant_tool_use("t1", "Write", {"file_path": "/a.txt", "content": huge}),
    )
    _append(store, card_id, "user", _user_tool_result("t1", "written"))

    result = card_timeline(store, card_id)
    step = result["steps"][0]
    assert step["kind"] == "write"
    assert len(step["detail"]["preview"]) < len(huge)
    assert step["detail"]["truncated"] is True


def test_timeline_route_returns_the_projection(tmp_path):
    # the store's sqlite3 connection is bound to its creating thread, so build both the store and
    # the server inside the same thread that will run serve_forever - see test_server.py
    ready = threading.Event()
    holder = {}

    def _run():
        store = Store(tmp_path / "board.db")
        board = store.create_board("b")
        card = store.create_card(board["id"], None, "a card")
        store.append_event(card["id"], "lifecycle_started", {})
        store.append_event(card["id"], "worker_summary", {"text": "done"})
        server = build_server(store, port=0, host="127.0.0.1")
        holder["server"] = server
        holder["card_id"] = card["id"]
        ready.set()
        server.serve_forever()
        store.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    ready.wait()
    port = holder["server"].server_address[1]
    card_id = holder["card_id"]
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", f"/api/cards/{card_id}/timeline")
        response = conn.getresponse()
        body = json.loads(response.read())
        assert response.status == 200, body
        assert body["attempt"] == 1
        assert body["steps"][0]["kind"] == "summary"

        conn.request("GET", f"/api/cards/{card_id}/timeline?attempt=1")
        response = conn.getresponse()
        response.read()
        assert response.status == 200

        conn.request("GET", "/api/cards/does-not-exist/timeline")
        response = conn.getresponse()
        response.read()
        assert response.status == 404
    finally:
        holder["server"].shutdown()
        thread.join()
        holder["server"].server_close()
