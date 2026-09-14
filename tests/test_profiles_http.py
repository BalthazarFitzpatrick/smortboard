"""profile routes over real http: list, add (with the token shape check), activate, remove.

Every test points SMORTBOARD_PROFILES_STATE_PATH, SMORTBOARD_CARD_TOKEN_PATH and XDG_CONFIG_HOME
at tmp_path, the same seams tests/test_profiles.py uses - never the operator's real
~/.config/smortboard.
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from smortboard.server.app import build_server
from smortboard.store import Store

VALID_TOKEN = "a" * 108


@pytest.fixture(autouse=True)
def _isolated_profiles(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("SMORTBOARD_PROFILES_STATE_PATH", str(tmp_path / "profiles.json"))
    monkeypatch.setenv("SMORTBOARD_CARD_TOKEN_PATH", str(tmp_path / "card_token"))


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
    yield f"http://127.0.0.1:{port}"
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


def test_list_profiles_starts_with_just_default(running_server):
    status, body = _request(f"{running_server}/api/profiles")
    assert status == 200
    assert body == [{"name": "default", "active": True, "present": False, "limited_until": None}]


def test_add_profile_writes_a_mode_600_file_and_never_echoes_the_token(running_server, tmp_path):
    status, body = _request(
        f"{running_server}/api/profiles", "POST", {"name": "alt", "token": VALID_TOKEN}
    )
    assert status == 201
    assert body == {"name": "alt", "active": False, "present": True, "limited_until": None}
    assert "token" not in body
    assert json.dumps(body).find(VALID_TOKEN) == -1

    token_path = tmp_path / "smortboard" / "tokens" / "alt"
    assert token_path.is_file()
    assert token_path.read_text().strip() == VALID_TOKEN
    assert (token_path.stat().st_mode & 0o777) == 0o600

    status, body = _request(f"{running_server}/api/profiles")
    row = next(r for r in body if r["name"] == "alt")
    assert row["present"] is True
    assert "token" not in row


def test_add_profile_rejects_a_malformed_token_and_writes_no_file(running_server, tmp_path):
    status, body = _request(
        f"{running_server}/api/profiles", "POST", {"name": "bad", "token": "too-short"}
    )
    assert status == 400
    assert "error" in body
    assert not (tmp_path / "smortboard" / "tokens" / "bad").exists()

    status, body = _request(f"{running_server}/api/profiles")
    assert not any(r["name"] == "bad" for r in body)


def test_add_profile_rejects_a_token_with_internal_whitespace(running_server):
    status, body = _request(
        f"{running_server}/api/profiles",
        "POST",
        {"name": "spacey", "token": "a" * 50 + " " + "a" * 50},
    )
    assert status == 400
    assert "error" in body


def test_activate_switches_the_active_profile(running_server):
    _request(f"{running_server}/api/profiles", "POST", {"name": "alt", "token": VALID_TOKEN})
    status, body = _request(f"{running_server}/api/profiles/alt/activate", "POST")
    assert status == 200
    assert body["active"] is True

    status, body = _request(f"{running_server}/api/profiles")
    active = next(r for r in body if r["active"])
    assert active["name"] == "alt"


def test_activate_unknown_profile_is_refused(running_server):
    status, body = _request(f"{running_server}/api/profiles/ghost/activate", "POST")
    assert status == 404
    assert "error" in body


def test_remove_deletes_the_token_file(running_server, tmp_path):
    _request(f"{running_server}/api/profiles", "POST", {"name": "alt", "token": VALID_TOKEN})
    token_path = tmp_path / "smortboard" / "tokens" / "alt"
    assert token_path.is_file()

    status, _ = _request(f"{running_server}/api/profiles/alt", "DELETE")
    assert status == 204
    assert not token_path.exists()

    status, body = _request(f"{running_server}/api/profiles")
    assert not any(r["name"] == "alt" for r in body)


def test_remove_the_active_profile_switches_active_first(running_server):
    _request(f"{running_server}/api/profiles", "POST", {"name": "alt", "token": VALID_TOKEN})
    _request(f"{running_server}/api/profiles/alt/activate", "POST")
    status, _ = _request(f"{running_server}/api/profiles/alt", "DELETE")
    assert status == 204

    status, body = _request(f"{running_server}/api/profiles")
    assert status == 200
    assert body == [{"name": "default", "active": True, "present": False, "limited_until": None}]


def test_remove_default_is_now_allowed(running_server, tmp_path):
    _request(f"{running_server}/api/profiles", "POST", {"name": "alt", "token": VALID_TOKEN})
    status, _ = _request(f"{running_server}/api/profiles/default", "DELETE")
    assert status == 204
    status, body = _request(f"{running_server}/api/profiles")
    assert not any(r["name"] == "default" for r in body)


def test_remove_the_last_remaining_profile_is_refused(running_server):
    status, body = _request(f"{running_server}/api/profiles/default", "DELETE")
    assert status == 409
    assert "error" in body


def test_remove_unknown_profile_is_404(running_server):
    status, body = _request(f"{running_server}/api/profiles/ghost", "DELETE")
    assert status == 404
    assert "error" in body
