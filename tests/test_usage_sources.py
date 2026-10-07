"""account-wide claude usage: oauth first, last.json second, a closed window shown as reset"""

import json
from urllib import error

from smortboard import usage_sources
from smortboard.store import Store
from smortboard.telemetry import usage_projection

NOW = 1_791_400_000.0
OAUTH_BODY = {
    "five_hour": {"utilization": 3.0, "resets_at": "2026-10-07T20:50:00.448238+00:00"},
    "seven_day": {"utilization": 29.0, "resets_at": "2026-10-12T16:00:00.448256+00:00"},
    "seven_day_opus": None,
}


def _config_dir(tmp_path, token=True, last_json_ts=None):
    if token:
        creds = {"claudeAiOauth": {"accessToken": "sk-ant-oat01-secret"}}
        (tmp_path / ".credentials.json").write_text(json.dumps(creds))
    if last_json_ts is not None:
        state = tmp_path / "usage-watcher" / "state"
        state.mkdir(parents=True)
        limits = {
            "five_hour": {"used_percentage": 4, "resets_at": 1_791_406_200},
            "seven_day": {"used_percentage": 29, "resets_at": 1_791_820_800},
        }
        (state / "last.json").write_text(json.dumps({"ts": last_json_ts, "rate_limits": limits}))
    return tmp_path


class _Response:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_oauth_fixture_gives_five_hour_3_and_seven_day_29(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_sources.request, "urlopen", lambda *a, **k: _Response(OAUTH_BODY))
    windows = usage_sources.fetch_claude_oauth(_config_dir(tmp_path), NOW)
    by_type = {w["type"]: w for w in windows}
    assert by_type["five_hour"]["utilization"] == 0.03
    assert by_type["seven_day"]["utilization"] == 0.29
    assert by_type["five_hour"]["source"] == "oauth"
    assert by_type["five_hour"]["fetched_at"] == NOW
    assert isinstance(by_type["seven_day"]["resets_at"], float)


def test_token_is_sent_only_as_a_header_and_never_returned(tmp_path, monkeypatch):
    seen = {}

    def fake_urlopen(http_request, timeout):
        seen["auth"] = http_request.get_header("Authorization")
        return _Response(OAUTH_BODY)

    monkeypatch.setattr(usage_sources.request, "urlopen", fake_urlopen)
    windows = usage_sources.fetch_claude_oauth(_config_dir(tmp_path), NOW)
    assert seen["auth"] == "Bearer sk-ant-oat01-secret"
    assert "secret" not in json.dumps(windows)


def test_missing_token_falls_to_last_json(tmp_path, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("no token, no request")

    monkeypatch.setattr(usage_sources.request, "urlopen", no_network)
    config = _config_dir(tmp_path, token=False, last_json_ts=NOW - 30)
    usage_sources._cache.clear()
    windows = usage_sources.claude_windows(config, NOW)
    assert {w["type"]: w["utilization"] for w in windows} == {"five_hour": 0.04, "seven_day": 0.29}
    assert {w["source"] for w in windows} == {"last_json"}


def test_failed_request_falls_to_last_json(tmp_path, monkeypatch):
    def refused(*a, **k):
        raise error.URLError("down")

    monkeypatch.setattr(usage_sources.request, "urlopen", refused)
    config = _config_dir(tmp_path, last_json_ts=NOW - 30)
    usage_sources._cache.clear()
    assert {w["source"] for w in usage_sources.claude_windows(config, NOW)} == {"last_json"}


def test_stale_last_json_is_rejected(tmp_path):
    config = _config_dir(tmp_path, token=False, last_json_ts=NOW - 3600)
    assert usage_sources.read_claude_last_json(config, NOW) == []


def test_nothing_readable_gives_no_windows(tmp_path):
    usage_sources._cache.clear()
    assert usage_sources.claude_windows(tmp_path, NOW) == []


def test_expired_reset_is_shown_as_rolled_over(tmp_path):
    window = {
        "type": "five_hour",
        "lab": "anthropic",
        "profile": "default",
        "status": "ok",
        "utilization": 0.8,
        "resets_at": 1.0,
        "source": "last_json",
        "fetched_at": 1.0,
    }
    with Store(tmp_path / "board.db") as store:
        [shown] = usage_projection(store, [window])["windows"]
    assert shown["rolled_over"] is True
    assert shown["utilization"] is None


def test_account_window_replaces_the_board_window_for_the_same_key(tmp_path):
    window = {
        "type": "five_hour",
        "lab": "anthropic",
        "profile": "default",
        "status": "ok",
        "utilization": 0.03,
        "resets_at": 32_000_000_000.0,
        "source": "oauth",
        "fetched_at": NOW,
    }
    with Store(tmp_path / "board.db") as store:
        [shown] = usage_projection(store, [window])["windows"]
    assert shown["source"] == "oauth"
    assert shown["utilization"] == 0.03


# -- codex ----------------------------------------------------------------------------------

APP_SERVER_LIMITS = {
    "limitId": "codex",
    "primary": {"usedPercent": 14, "windowDurationMins": 10080, "resetsAt": 1_791_957_313},
    "secondary": None,
}


def test_app_server_shape_gives_seven_day_14_and_ignores_the_null_secondary():
    windows = usage_sources.parse_codex_rate_limits(APP_SERVER_LIMITS, "app_server", NOW)
    assert len(windows) == 1
    assert windows[0]["type"] == "seven_day"
    assert windows[0]["lab"] == "openai"
    assert windows[0]["utilization"] == 0.14
    assert windows[0]["resets_at"] == 1_791_957_313.0
    assert windows[0]["source"] == "app_server"


def test_a_five_hour_primary_and_seven_day_secondary_are_both_kept():
    limits = {
        "primary": {"usedPercent": 6, "windowDurationMins": 300, "resetsAt": 10},
        "secondary": {"usedPercent": 40, "windowDurationMins": 10080, "resetsAt": 20},
    }
    windows = usage_sources.parse_codex_rate_limits(limits, "app_server", NOW)
    assert {w["type"]: w["utilization"] for w in windows} == {"five_hour": 0.06, "seven_day": 0.4}


def test_an_unknown_window_length_is_ignored():
    limits = {"primary": {"usedPercent": 6, "windowDurationMins": 60, "resetsAt": 10}}
    assert usage_sources.parse_codex_rate_limits(limits, "app_server", NOW) == []


def test_rollout_fallback_reads_the_last_non_null_rate_limits(tmp_path):
    # the rollout shape follows the plan's description and has not been seen on a real file
    day = tmp_path / "sessions" / "2026" / "10" / "07"
    day.mkdir(parents=True)
    old = {"used_percent": 1.0, "window_minutes": 10080, "resets_at": 1}
    new = {"used_percent": 13.0, "window_minutes": 10080, "resets_at": 2}
    lines = [
        {"payload": {"type": "token_count", "rate_limits": {"primary": old, "secondary": None}}},
        {"payload": {"type": "token_count", "rate_limits": {"primary": new, "secondary": None}}},
        {"payload": {"type": "token_count", "rate_limits": None}},
        "not json",
    ]
    text = "\n".join(line if isinstance(line, str) else json.dumps(line) for line in lines)
    (day / "rollout-a.jsonl").write_text(text)
    [window] = usage_sources.read_codex_rollout(tmp_path, NOW)
    assert window["utilization"] == 0.13
    assert window["source"] == "rollout"
    assert window["resets_at"] == 2.0


def test_no_codex_and_no_rollouts_gives_no_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_sources.shutil, "which", lambda name: None)
    usage_sources._cache.clear()
    assert usage_sources.codex_windows(tmp_path, NOW) == []
