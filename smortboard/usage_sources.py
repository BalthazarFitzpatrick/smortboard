"""account-wide rate-limit windows read from the labs themselves, not from the board's own runs.

claude: the oauth usage endpoint, else the usage-watcher's last.json. every reader returns neutral
windows (same keys as telemetry's event windows) or [] when its source is missing or unreadable.
a token is only ever sent to the endpoint, never logged or returned.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
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


# -- codex: the app-server answers account/rateLimits/read; a rollout file is the fallback ------

_CODEX_WINDOW_NAMES = {300: "five_hour", 10080: "seven_day"}
_CODEX_RPC_TIMEOUT_SECONDS = 15


def _codex_dir() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _codex_window(block: Any, source: str, fetched_at: float) -> dict[str, Any] | None:
    """one codex limit block, either the app-server shape or the rollout shape"""
    if not isinstance(block, dict):
        return None
    minutes = block.get("windowDurationMins", block.get("window_minutes"))
    percent = block.get("usedPercent", block.get("used_percent"))
    name = _CODEX_WINDOW_NAMES.get(minutes)
    if name is None or not isinstance(percent, (int, float)):
        return None
    window = _window(
        name, percent, block.get("resetsAt", block.get("resets_at")), source, fetched_at
    )
    return {**window, "lab": "openai"}


def parse_codex_rate_limits(limits: Any, source: str, fetched_at: float) -> list[dict[str, Any]]:
    """primary and secondary blocks to neutral windows; a null block is ignored"""
    if not isinstance(limits, dict):
        return []
    blocks = (
        _codex_window(limits.get(key), source, fetched_at) for key in ("primary", "secondary")
    )
    return [window for window in blocks if window]


def _read_codex_app_server(exe: str, now: float) -> list[dict[str, Any]]:
    """the server exits when stdin closes, so stdin stays open until the reply or the timeout"""
    requests = [
        {
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "smortboard", "version": "0"}},
        },
        {"method": "initialized"},
        {"id": 2, "method": "account/rateLimits/read"},
    ]
    try:
        process = subprocess.Popen(  # exe is the codex binary found on PATH
            [exe, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return []
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    deadline = time.monotonic() + _CODEX_RPC_TIMEOUT_SECONDS
    try:
        for request_body in requests:
            process.stdin.write(json.dumps(request_body) + "\n")
        process.stdin.flush()
        while (left := deadline - time.monotonic()) > 0:
            line = lines.get(timeout=left)
            if line is None:
                break
            try:
                reply = json.loads(line)
            except ValueError:
                continue
            if isinstance(reply, dict) and reply.get("id") == 2:
                limits = (reply.get("result") or {}).get("rateLimits")
                return parse_codex_rate_limits(limits, "app_server", now)
    except (OSError, queue.Empty):
        pass
    finally:
        process.kill()
    return []


def read_codex_rollout(codex_dir: Path | None = None, now: float | None = None) -> list[dict]:
    """last non-null token_count.rate_limits in the newest rollout file"""
    now = now if now is not None else time.time()
    rollouts = list(((codex_dir or _codex_dir()) / "sessions").glob("**/rollout-*.jsonl"))
    if not rollouts:
        return []
    newest = max(rollouts, key=lambda path: path.stat().st_mtime)
    found: dict[str, Any] | None = None
    try:
        lines = newest.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        payload = entry.get("payload") if isinstance(entry, dict) else None
        is_count = isinstance(payload, dict) and payload.get("type") == "token_count"
        if is_count and isinstance(payload.get("rate_limits"), dict):
            found = payload["rate_limits"]
    return parse_codex_rate_limits(found, "rollout", now)


def codex_windows(codex_dir: Path | None = None, now: float | None = None) -> list[dict]:
    """app-server first, else the newest rollout; cached like the claude reading"""
    now = now if now is not None else time.time()
    cached = _cache.get("codex")
    if cached and now - cached[0] < _CACHE_SECONDS:
        return cached[1]
    exe = shutil.which("codex")
    windows = (_read_codex_app_server(exe, now) if exe else []) or read_codex_rollout(
        codex_dir, now
    )
    _cache["codex"] = (now, windows)
    return windows
