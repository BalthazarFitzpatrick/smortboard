"""the request gate: host, origin, content-type and api key checks in front of every route"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from smortboard.server import access
from smortboard.server.app import build_server
from smortboard.store import Store

KEY = "k" * 43


@pytest.fixture
def board(tmp_path):
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
    port = holder["server"].server_address[1]
    yield f"http://127.0.0.1:{port}", port
    holder["server"].shutdown()
    thread.join()
    holder["server"].server_close()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _call(url, method="GET", body=None, headers=None, content_type="application/json"):
    headers = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers.setdefault("Content-Type", content_type)
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.build_opener(_NoRedirect).open(req) as resp:
            return resp.status, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers)


def test_api_without_key_is_refused(board):
    base, _ = board
    assert _call(f"{base}/api/boards")[0] == 401


def test_api_with_header_key_is_served(board):
    base, _ = board
    assert _call(f"{base}/api/boards", headers={access.KEY_HEADER: KEY})[0] == 200


def test_api_with_wrong_key_is_refused(board):
    base, _ = board
    assert _call(f"{base}/api/boards", headers={access.KEY_HEADER: "x" * 43})[0] == 401


def test_api_with_cookie_key_is_served(board):
    base, port = board
    cookie = f"other=1; {access.cookie_name(port)}={KEY}"
    assert _call(f"{base}/api/boards", headers={"Cookie": cookie})[0] == 200


def test_foreign_host_is_refused_even_with_key(board):
    # a dns-rebinding page reaches the board under its own name
    base, port = board
    headers = {"Host": f"evil.test:{port}", access.KEY_HEADER: KEY}
    assert _call(f"{base}/api/boards", headers=headers)[0] == 403
    assert _call(f"{base}/ui/index.html", headers={"Host": "evil.test"})[0] == 403


def test_localhost_host_is_accepted(board):
    base, port = board
    headers = {"Host": f"localhost:{port}", access.KEY_HEADER: KEY}
    assert _call(f"{base}/api/boards", headers=headers)[0] == 200


def test_cross_origin_post_is_refused(board):
    base, _ = board
    headers = {"Origin": "http://evil.test", access.KEY_HEADER: KEY}
    status, _ = _call(f"{base}/api/boards", "POST", {"name": "x"}, headers)
    assert status == 403


def test_cross_site_fetch_without_origin_is_refused(board):
    base, _ = board
    headers = {"Sec-Fetch-Site": "cross-site", access.KEY_HEADER: KEY}
    assert _call(f"{base}/api/boards", "POST", {"name": "x"}, headers)[0] == 403


def test_same_origin_post_is_served(board):
    base, port = board
    headers = {"Origin": f"http://127.0.0.1:{port}", access.KEY_HEADER: KEY}
    assert _call(f"{base}/api/boards", "POST", {"name": "x"}, headers)[0] == 201


def test_non_json_body_is_refused(board):
    # a cross-site html form can only send text/plain or form encodings
    base, _ = board
    headers = {access.KEY_HEADER: KEY}
    status, _ = _call(f"{base}/api/boards", "POST", {"name": "x"}, headers, "text/plain")
    assert status == 415


def test_key_in_page_url_becomes_a_cookie(board):
    base, port = board
    status, headers = _call(f"{base}/ui/index.html?{access.KEY_QUERY}={KEY}")
    assert status == 303
    assert headers["Location"] == "/ui/index.html"
    cookie = headers["Set-Cookie"]
    assert cookie.startswith(f"{access.cookie_name(port)}={KEY};")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie


def test_wrong_key_in_page_url_just_serves_the_page(board):
    base, _ = board
    status, headers = _call(f"{base}/ui/index.html?{access.KEY_QUERY}=nope")
    assert status == 200
    assert "Set-Cookie" not in headers


def test_assets_and_health_need_no_key(board):
    base, _ = board
    assert _call(f"{base}/ui/index.html")[0] == 200
    assert _call(f"{base}/health")[0] == 200


def test_api_key_file_is_owner_only_and_stable(tmp_path):
    path = tmp_path / "cfg" / "api_key"
    first = access.load_or_create_api_key(path)
    assert (path.stat().st_mode & 0o777) == 0o600
    assert access.load_or_create_api_key(path) == first


def test_loose_api_key_file_is_replaced(tmp_path):
    path = tmp_path / "api_key"
    path.write_text("leaked\n")
    path.chmod(0o644)
    key = access.load_or_create_api_key(path)
    assert key != "leaked"
    assert (path.stat().st_mode & 0o777) == 0o600
