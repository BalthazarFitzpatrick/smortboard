"""server tests: run a real http server in a thread against a tmp_path database"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from smortboard.server.app import build_server
from smortboard.store import Store


@pytest.fixture
def running_server(tmp_path):
    # the store's sqlite3 connection is bound to its creating thread, so build both
    # the store and the server inside the same thread that will run serve_forever
    ready = threading.Event()
    holder = {}

    def _run():
        store = Store(tmp_path / "board.db")
        server = build_server(store, port=0, host="127.0.0.1")
        holder["server"] = server
        ready.set()
        server.serve_forever()
        store.close()  # closed on the same thread that created the connection

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    ready.wait()
    port = holder["server"].server_address[1]
    yield f"http://127.0.0.1:{port}"
    holder["server"].shutdown()
    thread.join()
    holder["server"].server_close()


def _request(url, method="GET", body=None, headers=None):
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers = {**(headers or {}), "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            payload = resp.read()
            parsed = json.loads(payload) if payload else None
            return resp.status, parsed
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        parsed = json.loads(payload) if payload else None
        return exc.code, parsed


def test_health(running_server):
    status, body = _request(f"{running_server}/health")
    assert status == 200
    assert body["ok"] is True
    assert "version" in body


def test_board_and_card_round_trip(running_server):
    status, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    assert status == 201
    assert board["name"] == "dev"

    status, boards = _request(f"{running_server}/api/boards")
    assert status == 200
    assert any(b["id"] == board["id"] for b in boards)

    status, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {
            "board_id": board["id"],
            "repo_id": None,
            "title": "wire the server",
            "tasks": ["a", "b"],
            "criteria": ["works"],
        },
    )
    assert status == 201
    assert card["title"] == "wire the server"
    assert [t["text"] for t in card["tasks"]] == ["a", "b"]
    assert card["depends_on"] == []
    assert card["comments"] == []

    status, fetched = _request(f"{running_server}/api/cards/{card['id']}")
    assert status == 200
    assert fetched["id"] == card["id"]

    status, cards = _request(f"{running_server}/api/boards/{board['id']}/cards")
    assert status == 200
    assert len(cards) == 1


def test_patch_card_writable_fields(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x"},
    )
    status, updated = _request(
        f"{running_server}/api/cards/{card['id']}", "PATCH", {"status": "doing"}
    )
    assert status == 200
    assert updated["status"] == "doing"


def test_patch_card_model_over_http(running_server):
    # the server kept its own copy of the writable fields, which went stale and answered 400 here
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x"},
    )
    status, updated = _request(
        f"{running_server}/api/cards/{card['id']}", "PATCH", {"model": "haiku"}
    )
    assert status == 200
    assert updated["model"] == "haiku"


def test_patch_card_unknown_field_is_400(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x"},
    )
    status, body = _request(
        f"{running_server}/api/cards/{card['id']}", "PATCH", {"tasks": ["nope"]}
    )
    assert status == 400
    assert "error" in body


def test_patch_card_blocked_without_reason_is_400(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x"},
    )
    status, body = _request(
        f"{running_server}/api/cards/{card['id']}", "PATCH", {"status": "blocked"}
    )
    assert status == 400
    assert "error" in body


def test_patch_task_done_round_trips(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x", "tasks": ["ship it"]},
    )
    task_id = card["tasks"][0]["id"]

    status, task = _request(f"{running_server}/api/tasks/{task_id}", "PATCH", {"done": True})
    assert status == 200
    assert task["done"] == 1

    status, fetched = _request(f"{running_server}/api/cards/{card['id']}")
    assert fetched["tasks"][0]["done"] == 1


def test_patch_task_unknown_field_is_400(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x", "tasks": ["ship it"]},
    )
    task_id = card["tasks"][0]["id"]

    status, body = _request(f"{running_server}/api/tasks/{task_id}", "PATCH", {"text": "rewritten"})
    assert status == 400
    assert "error" in body


def test_get_missing_card_is_404(running_server):
    status, body = _request(f"{running_server}/api/cards/does-not-exist")
    assert status == 404
    assert "error" in body


def test_delete_a_card_that_has_been_used(running_server):
    """the endpoint dropped the connection without a status for any card with children, because the
    store raised and nothing caught it. a 000 from curl is the least diagnosable failure there is."""
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "used"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "has been used"},
    )
    _request(
        f"{running_server}/api/cards/{card['id']}/comments",
        "POST",
        {"author": "me", "body": "hello"},
    )

    status, _ = _request(f"{running_server}/api/cards/{card['id']}", "DELETE")
    assert status == 204

    status, _ = _request(f"{running_server}/api/cards/{card['id']}", "GET")
    assert status == 404


def test_delete_card(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x"},
    )
    status, body = _request(f"{running_server}/api/cards/{card['id']}", "DELETE")
    assert status == 204
    assert body is None

    status, _ = _request(f"{running_server}/api/cards/{card['id']}")
    assert status == 404


def test_comments(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x"},
    )
    status, comment = _request(
        f"{running_server}/api/cards/{card['id']}/comments",
        "POST",
        {"author": "operator", "body": "looks good"},
    )
    assert status == 201
    assert comment["body"] == "looks good"

    status, fetched = _request(f"{running_server}/api/cards/{card['id']}")
    assert [c["body"] for c in fetched["comments"]] == ["looks good"]


def test_events(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x"},
    )
    status, events = _request(f"{running_server}/api/cards/{card['id']}/events")
    assert status == 200
    assert events == []


def test_attachment_upload_and_download(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x"},
    )

    boundary = "smortboundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="note.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        "hello world\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    req = urllib.request.Request(
        f"{running_server}/api/cards/{card['id']}/attachments",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req) as resp:
        status = resp.status
        attachment = json.loads(resp.read())
    assert status == 201
    assert attachment["filename"] == "note.txt"
    assert "blob" not in attachment

    with urllib.request.urlopen(
        f"{running_server}/api/cards/{card['id']}/attachments/{attachment['id']}"
    ) as resp:
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "text/plain"
        assert resp.read() == b"hello world"


def test_asset_traversal_is_404(running_server):
    status, _ = _request(f"{running_server}/ui/../pyproject.toml")
    assert status == 404


def test_asset_served_from_local_ui_dir_over_ui_base(running_server, tmp_path, monkeypatch):
    import smortboard.server.assets as assets_module

    local_dir = tmp_path / "ui"
    local_dir.mkdir()
    (local_dir / "base.css").write_text("/* local override */")
    monkeypatch.setattr(assets_module, "LOCAL_UI_DIR", local_dir)

    status, _ = (None, None)
    import urllib.request as ur

    with ur.urlopen(f"{running_server}/ui/base.css") as resp:
        status = resp.status
        body = resp.read()
    assert status == 200
    assert body == b"/* local override */"


def test_asset_falls_back_to_ui_base(running_server):
    with urllib.request.urlopen(f"{running_server}/ui/base.css") as resp:
        assert resp.status == 200
        assert len(resp.read()) > 0


def test_unknown_asset_is_404(running_server):
    status, _ = _request(f"{running_server}/ui/does-not-exist.css")
    assert status == 404
