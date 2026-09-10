"""phase 4 routes over real http: orchestrator, conversation, roster, usage, prompts.

Fakes only touch the model/docker boundary - the orchestrator's runner is monkeypatched onto
OrchestratorRegistry.start's default, the way test_whole_system.py fakes the card runtime.
"""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from smortboard.server.app import build_server
from smortboard.store import Store


@pytest.fixture
def running_server(tmp_path):
    ready = threading.Event()
    holder = {}

    def _run():
        store = Store(tmp_path / "board.db")
        server = build_server(store, port=0, host="127.0.0.1")
        holder["server"] = server
        ready.set()
        server.serve_forever()
        store.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    ready.wait()
    port = holder["server"].server_address[1]
    yield f"http://127.0.0.1:{port}", holder["server"]
    holder["server"].shutdown()
    thread.join()
    holder["server"].server_close()


def _request(url, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            payload = resp.read()
            return resp.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        return exc.code, json.loads(payload) if payload else None


def _board(base_url):
    _, board = _request(f"{base_url}/api/boards", "POST", {"name": "dev"})
    return board


def _ok_runner(prompt, model, budget_usd):
    return json.dumps({"reply": "sounds good", "plan": "the plan", "cards": []})


def _slow_runner_factory():
    gate = threading.Event()

    def run(prompt, model, budget_usd):
        gate.wait(timeout=5)
        return json.dumps({"reply": "done", "plan": "", "cards": []})

    return run, gate


# -- orchestrator --------------------------------------------------------------


def test_orchestrator_post_then_get_until_not_thinking(running_server):
    base_url, server = running_server
    board = _board(base_url)

    # OrchestratorRegistry.start takes the runner explicitly - patch it in via the server instance
    original_start = server.orchestrator.start

    def _patched_start(board_id, message, runner=None, message_already_stored=False):
        return original_start(
            board_id, message, runner=_ok_runner, message_already_stored=message_already_stored
        )

    server.orchestrator.start = _patched_start

    status, body = _request(
        f"{base_url}/api/boards/{board['id']}/orchestrator", "POST", {"message": "build it"}
    )
    assert status == 202
    assert body["thinking"] is True
    assert [m["author"] for m in body["messages"]] == ["operator"]

    deadline = time.time() + 5
    while time.time() < deadline:
        status, body = _request(f"{base_url}/api/boards/{board['id']}/orchestrator")
        if not body["thinking"]:
            break
        time.sleep(0.02)
    assert status == 200
    assert body["thinking"] is False
    assert body["error"] is None
    assert [m["author"] for m in body["messages"]] == ["operator", "orchestrator"]
    assert body["plan"] == "the plan"
    assert "model" in body


def test_orchestrator_post_while_thinking_is_409(running_server):
    base_url, server = running_server
    board = _board(base_url)
    runner, gate = _slow_runner_factory()
    original_start = server.orchestrator.start
    server.orchestrator.start = lambda board_id, message, **kw: original_start(
        board_id,
        message,
        runner=runner,
        message_already_stored=kw.get("message_already_stored", False),
    )

    status, _ = _request(
        f"{base_url}/api/boards/{board['id']}/orchestrator", "POST", {"message": "first"}
    )
    assert status == 202
    status, body = _request(
        f"{base_url}/api/boards/{board['id']}/orchestrator", "POST", {"message": "second"}
    )
    assert status == 409
    assert "error" in body
    gate.set()


def test_orchestrator_post_empty_message_is_400(running_server):
    base_url, _server = running_server
    board = _board(base_url)
    status, body = _request(
        f"{base_url}/api/boards/{board['id']}/orchestrator", "POST", {"message": "   "}
    )
    assert status == 400
    assert "error" in body


def test_orchestrator_get_on_a_fresh_board_is_empty(running_server):
    base_url, _server = running_server
    board = _board(base_url)
    status, body = _request(f"{base_url}/api/boards/{board['id']}/orchestrator")
    assert status == 200
    assert body == {
        "messages": [],
        "plan": None,
        "thinking": False,
        "error": None,
        "model": "opus",
    }


# -- card conversation -----------------------------------------------------


def test_conversation_walks_events_and_comments_into_one_timeline(running_server):
    base_url, _server = running_server
    board = _board(base_url)
    _, card = _request(
        f"{base_url}/api/cards", "POST", {"board_id": board["id"], "repo_id": None, "title": "x"}
    )
    # events are runner-only and not reachable over http, and a second sqlite connection to the
    # server's own db file is unsafe from another thread - so this test covers the comment half
    # of the timeline; the event half (assistant text, gate lines) is exercised in
    # test_lifecycle.py and test_reviewer.py, which append events through the store directly
    status, comment = _request(
        f"{base_url}/api/cards/{card['id']}/conversation", "POST", {"message": "please redo x"}
    )
    assert status == 201
    assert comment["author"] == "operator"
    assert comment["body"] == "please redo x"

    status, view = _request(f"{base_url}/api/cards/{card['id']}/conversation")
    assert status == 200
    assert view["card_id"] == card["id"]
    assert view["running"] is False
    assert view["delivery"] == "next_run"
    assert view["messages"][-1] == {
        "author": "operator",
        "body": "please redo x",
        "created_at": view["messages"][-1]["created_at"],
    }


def test_conversation_post_empty_message_is_400(running_server):
    base_url, _server = running_server
    board = _board(base_url)
    _, card = _request(
        f"{base_url}/api/cards", "POST", {"board_id": board["id"], "repo_id": None, "title": "x"}
    )
    status, body = _request(
        f"{base_url}/api/cards/{card['id']}/conversation", "POST", {"message": ""}
    )
    assert status == 400
    assert "error" in body


# -- roster and usage --------------------------------------------------------


def test_roster_is_empty_on_a_fresh_board(running_server):
    base_url, _server = running_server
    status, body = _request(f"{base_url}/api/roster")
    assert status == 200
    assert body == []


def test_usage_is_empty_on_a_fresh_board(running_server):
    base_url, _server = running_server
    status, body = _request(f"{base_url}/api/usage")
    assert status == 200
    assert body == {"windows": [], "models": [], "total_cost_usd": 0.0, "runs": 0}


# -- prompts -----------------------------------------------------------------


def test_prompts_list_shows_defaults_until_saved(running_server):
    base_url, _server = running_server
    status, prompts = _request(f"{base_url}/api/prompts")
    assert status == 200
    roles = {p["role"] for p in prompts}
    assert roles == {"orchestrator", "worker", "reviewer"}
    assert all(p["version"] == 0 and p["is_default"] for p in prompts)


def test_patch_prompt_saves_a_new_version(running_server):
    base_url, _server = running_server
    status, updated = _request(
        f"{base_url}/api/prompts/worker", "PATCH", {"body": "a new worker prompt"}
    )
    assert status == 200
    assert updated == {
        "role": "worker",
        "version": 1,
        "body": "a new worker prompt",
        "is_default": False,
    }
    status, prompts = _request(f"{base_url}/api/prompts")
    worker = next(p for p in prompts if p["role"] == "worker")
    assert worker["version"] == 1
    assert worker["is_default"] is False


def test_patch_unknown_role_is_404(running_server):
    base_url, _server = running_server
    status, body = _request(f"{base_url}/api/prompts/not-a-role", "PATCH", {"body": "x"})
    assert status == 404
    assert "error" in body
