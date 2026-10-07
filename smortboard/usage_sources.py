"""account-wide rate-limit windows read from the labs themselves, not from the board's own runs.

claude: the oauth usage endpoint, else the usage-watcher's last.json. every reader returns neutral
windows (same keys as telemetry's event windows) or [] when its source is missing or unreadable.
a token is only ever sent to the endpoint, never logged or returned.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request

_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_OAUTH_BETA = "oauth-2025-04-20"
_FETCH_TIMEOUT_SECONDS = 5
_CACHE_SECONDS = 60
# last.json is rewritten on every status line refresh, so anything older is a closed session
_LAST_JSON_MAX_AGE_SECONDS = 900
_CLAUDE_WINDOWS = ("five_hour", "seven_day")

_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _read_oauth_token(config_dir: Path) -> str | None:
    try:
        creds = json.loads((config_dir / ".credentials.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    token = (
        (creds.get("claudeAiOauth") or {}).get("accessToken") if isinstance(creds, dict) else None
    )
    return token if isinstance(token, str) and token else None


def _epoch(value: Any) -> float | None:
    """resets_at as epoch seconds, from an epoch number or an iso string"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return None
    return None


def _window(name: str, percent: Any, resets_at: Any, source: str, fetched_at: float) -> dict:
    return {
        "type": name,
        "lab": "anthropic",
        "profile": "default",
        "status": "ok",
        # the ui draws a 0-1 fraction; both sources report 0-100
        "utilization": percent / 100 if isinstance(percent, (int, float)) else None,
        "resets_at": _epoch(resets_at),
        "source": source,
        "fetched_at": fetched_at,
    }


def parse_oauth_usage(body: dict[str, Any], fetched_at: float) -> list[dict[str, Any]]:
    windows = []
    for name in _CLAUDE_WINDOWS:
        block = body.get(name)
        if isinstance(block, dict) and block.get("utilization") is not None:
            windows.append(
                _window(name, block["utilization"], block.get("resets_at"), "oauth", fetched_at)
            )
    return windows


def fetch_claude_oauth(config_dir: Path | None = None, now: float | None = None) -> list[dict]:
    token = _read_oauth_token(config_dir or _claude_dir())
    if token is None:
        return []
    http_request = request.Request(
        _USAGE_URL, headers={"Authorization": f"Bearer {token}", "anthropic-beta": _OAUTH_BETA}
    )
    try:
        with request.urlopen(http_request, timeout=_FETCH_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read())
    except (error.URLError, OSError, ValueError):
        return []
    return (
        parse_oauth_usage(body, now if now is not None else time.time())
        if isinstance(body, dict)
        else []
    )


def read_claude_last_json(config_dir: Path | None = None, now: float | None = None) -> list[dict]:
    now = now if now is not None else time.time()
    path = (config_dir or _claude_dir()) / "usage-watcher" / "state" / "last.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    ts = state.get("ts") if isinstance(state, dict) else None
    if not isinstance(ts, (int, float)) or now - ts > _LAST_JSON_MAX_AGE_SECONDS:
        return []
    limits = state.get("rate_limits") or {}
    return [
        _window(name, block.get("used_percentage"), block.get("resets_at"), "last_json", ts)
        for name in _CLAUDE_WINDOWS
        if isinstance(block := limits.get(name), dict) and block.get("used_percentage") is not None
    ]


def claude_windows(config_dir: Path | None = None, now: float | None = None) -> list[dict]:
    """oauth first, else last.json; the oauth answer is cached so a poll does not hit the network"""
    now = now if now is not None else time.time()
    cached = _cache.get("claude")
    if cached and now - cached[0] < _CACHE_SECONDS:
        return cached[1]
    windows = fetch_claude_oauth(config_dir, now) or read_claude_last_json(config_dir, now)
    _cache["claude"] = (now, windows)
    return windows
