"""the settings panel's backup section over http: GET /api/export downloads the bundle, POST
/api/import uploads one and adds its boards beside the ones already here - never over them"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from smortboard.server import access
from smortboard.server.app import build_server
from smortboard.store import Store

KEY = "k" * 43
_BOUNDARY = "smortboundary"


@pytest.fixture
def base(tmp_path):
    ready = threading.Event()
    holder = {}

    def _run():
        store = Store(tmp_path / "board.db")
        server = build_server(store, port=0, host="127.0.0.1", api_key=KEY)
        holder["server"] = server
        ready.set()
        server.serve_forever()
        store.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    ready.wait()
    yield f"http://127.0.0.1:{holder['server'].server_address[1]}"
    holder["server"].shutdown()
    thread.join()
    holder["server"].server_close()


def _call(url, method="GET", data=None, headers=None, key=KEY):
    headers = dict(headers or {})
    if key:
        headers[access.KEY_HEADER] = key
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _json(url, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    status, _, raw = _call(url, method, data, headers)
    return status, json.loads(raw) if raw else None


def _upload(base, content, key=KEY, headers=None):
    body = (
        f"--{_BOUNDARY}\r\n"
        'Content-Disposition: form-data; name="bundle"; filename="backup.json"\r\n'
        "Content-Type: application/json\r\n\r\n"
    ).encode()
    body += content + f"\r\n--{_BOUNDARY}--\r\n".encode()
    headers = {"Content-Type": f"multipart/form-data; boundary={_BOUNDARY}", **(headers or {})}
    status, _, raw = _call(f"{base}/api/import", "POST", body, headers, key=key)
    return status, json.loads(raw) if raw else None


def _board_with_a_card(base, name="alpha"):
    _, board = _json(f"{base}/api/boards", "POST", {"name": name})
    _json(f"{base}/api/cards", "POST", {"board_id": board["id"], "repo_id": None, "title": "t"})
    return board


def _export(base):
    status, headers, raw = _call(f"{base}/api/export")
    assert status == 200
    return headers, raw


def test_export_downloads_every_board_as_a_json_attachment(base):
    board = _board_with_a_card(base)

    headers, raw = _export(base)

    assert headers["Content-Type"] == "application/json"
    assert headers["Content-Disposition"].startswith('attachment; filename="smortboard-')
    assert headers["Content-Disposition"].endswith('.json"')
    assert headers["X-Content-Type-Options"] == "nosniff"
    bundle = json.loads(raw)
    assert [b["id"] for b in bundle["boards"]] == [board["id"]]
    assert [c["title"] for c in bundle["cards"]] == ["t"]


def test_import_adds_the_bundles_boards_beside_the_ones_here(base):
    board = _board_with_a_card(base)
    _, raw = _export(base)

    status, body = _upload(base, raw)

    assert status == 201
    [added] = body["boards"]
    assert added["name"] == "alpha" and added["id"] != board["id"]
    _, boards = _json(f"{base}/api/boards")
    assert {b["id"] for b in boards} == {board["id"], added["id"]}
    _, original_cards = _json(f"{base}/api/boards/{board['id']}/cards")
    _, copied_cards = _json(f"{base}/api/boards/{added['id']}/cards")
    assert [c["title"] for c in original_cards] == ["t"]
    assert [c["title"] for c in copied_cards] == ["t"]
    assert copied_cards[0]["id"] != original_cards[0]["id"]


@pytest.mark.parametrize(
    ("content", "said"),
    [
        (b"not json at all", "not json"),
        (b'{"cards": []}', "not a smortboard bundle"),
        (b'{"boards": []}', "no boards"),
    ],
)
def test_import_refuses_what_is_not_a_bundle_and_adds_nothing(base, content, said):
    board = _board_with_a_card(base)

    status, body = _upload(base, content)

    assert status == 400
    assert said in body["error"]
    _, boards = _json(f"{base}/api/boards")
    assert [b["id"] for b in boards] == [board["id"]]


def test_import_refuses_a_row_naming_a_board_outside_the_bundle(base):
    """pointing a card at an existing board's id is how an import would overwrite one"""
    board = _board_with_a_card(base)
    _, raw = _export(base)
    bundle = json.loads(raw)
    other = _board_with_a_card(base, "beta")
    bundle["cards"][0]["board_id"] = other["id"]

    status, body = _upload(base, json.dumps(bundle).encode())

    assert status == 400
    assert "not in the bundle's boards" in body["error"]
    _, boards = _json(f"{base}/api/boards")
    assert {b["id"] for b in boards} == {board["id"], other["id"]}
    _, beta_cards = _json(f"{base}/api/boards/{other['id']}/cards")
    assert len(beta_cards) == 1


def test_import_refuses_a_body_that_is_not_multipart(base):
    status, body = _json(f"{base}/api/import", "POST", {"boards": []})
    assert status == 400
    assert "multipart" in body["error"]


def test_export_and_import_need_the_api_key(base):
    assert _call(f"{base}/api/export", key=None)[0] == 401
    assert _upload(base, b"{}", key=None)[0] == 401


def test_a_cross_origin_import_is_refused(base):
    status, _ = _upload(base, b"{}", headers={"Origin": "http://evil.example"})
    assert status == 403
