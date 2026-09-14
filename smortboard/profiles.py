"""named claude credential profiles: several subscriptions, one active at a time.

Per the credential-profiles card: rotate to the next profile when the active one hits its rate
limit, instead of parking the whole board until the window resets. A profile is a name plus a
mode-600 token file under ~/.config/smortboard/tokens/<name>. "default" is special-cased onto the
pre-existing card_token file (smortboard.exec.backends.card_token_path) rather than a file of its
own, so a board that only ever had one credential keeps working exactly as before - nothing is
moved, and nothing here is ever written to disk while only "default" is configured. That last part
is not an optimisation: BoardScheduler calls into this module on every USAGE_LIMIT, including from
tests this card cannot edit, and those must not grow a real ~/.config/smortboard/profiles.json as a
side effect of running the suite.

A token VALUE never passes through this module except the moment write_token_file() puts one on
disk - list/read/mark/rotate operations move names, paths and timestamps only.
"""

import json
import os
import time
from pathlib import Path
from typing import Any

from smortboard.exec.backends import card_token_path, config_base

DEFAULT_PROFILE = "default"

# a test seam, mirrors CARD_TOKEN_PATH_ENV - points the state file at a tmp path instead of the
# operator's real config directory
STATE_PATH_ENV = "SMORTBOARD_PROFILES_STATE_PATH"

_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


class ProfileError(RuntimeError):
    """a profile operation could not be completed - bad name, unknown profile, insecure file"""


def profiles_dir() -> Path:
    return config_base() / "smortboard" / "tokens"


def state_path() -> Path:
    """where profile metadata (active profile, per-profile limit windows) lives - names and
    timestamps only, never a token value"""
    override = os.environ.get(STATE_PATH_ENV)
    return Path(override) if override else config_base() / "smortboard" / "profiles.json"


def _default_state() -> dict[str, Any]:
    return {"active": DEFAULT_PROFILE, "profiles": [DEFAULT_PROFILE], "limits": {}}


def _load_state() -> dict[str, Any]:
    """a pure read - an absent or corrupt file is just the default state, never written back here.
    see the module docstring: this is called on every scheduler tick, so it must never turn a
    single-profile board into one with a state file on disk."""
    path = state_path()
    if not path.is_file():
        return _default_state()
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return _default_state()
    state = _default_state()
    state["active"] = data.get("active") or DEFAULT_PROFILE
    names = data.get("profiles")
    state["profiles"] = list(names) if names else [DEFAULT_PROFILE]
    if DEFAULT_PROFILE not in state["profiles"]:
        state["profiles"].insert(0, DEFAULT_PROFILE)
    state["limits"] = dict(data.get("limits") or {})
    return state


def _save_state(state: dict[str, Any]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def _validate_name(name: str) -> None:
    if not name or any(ch not in _NAME_CHARS for ch in name):
        raise ProfileError(
            f"'{name}' is not a valid profile name - letters, digits, '-' and '_' only."
        )


def profile_path(name: str) -> Path:
    """the token file for one profile - the legacy card_token file itself for 'default', so
    adopting profiles never moves anything the operator already set up"""
    if name == DEFAULT_PROFILE:
        return card_token_path()
    return profiles_dir() / name


def _mode_ok(path: Path) -> bool:
    return (path.stat().st_mode & 0o077) == 0


def list_profiles(now: float | None = None) -> list[dict[str, Any]]:
    """every configured profile: present, mode 600, and whether it is rate-limited right now -
    the shape preflight renders one row per profile from"""
    state = _load_state()
    now = time.time() if now is None else now
    rows = []
    for name in state["profiles"]:
        path = profile_path(name)
        present = path.is_file()
        limited_until = (state["limits"].get(name) or {}).get("limited_until")
        rows.append(
            {
                "name": name,
                "path": str(path),
                "active": name == state["active"],
                "present": present,
                "mode_ok": _mode_ok(path) if present else None,
                "limited_until": limited_until,
                "limited_now": bool(limited_until and limited_until > now),
            }
        )
    return rows


def active_profile() -> str:
    return _load_state()["active"]


def active_profile_path() -> Path:
    return profile_path(active_profile())


def has_multiple_profiles() -> bool:
    return len(_load_state()["profiles"]) > 1


def token_path_for_run(explicit_override: str | Path | None) -> str | Path | None:
    """what a card run should pass as token_path: an explicit override always wins (tests, and any
    deployment that pins SMORTBOARD_CARD_TOKEN_PATH); otherwise None for the default profile -
    preserving its file-then-keychain fallback exactly as before profiles existed - or the named
    profile's own file, which has no keychain fallback of its own."""
    if explicit_override is not None:
        return explicit_override
    active = active_profile()
    return None if active == DEFAULT_PROFILE else profile_path(active)


def write_token_file(path: str | Path, token: str) -> None:
    """writes a token with umask 077 so it (and its parent dir) land at owner-only regardless of
    the process umask, then verifies - a file that somehow ends up group- or world-readable is
    refused rather than silently used."""
    path = Path(path)
    previous = os.umask(0o077)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token.strip() + "\n")
    finally:
        os.umask(previous)
    path.chmod(0o600)
    if not _mode_ok(path):
        raise ProfileError(f"{path} is not mode 600 after writing - refusing to use it")


def add_profile(name: str, token: str | None = None) -> Path:
    _validate_name(name)
    state = _load_state()
    if name in state["profiles"]:
        raise ProfileError(f"profile '{name}' already exists")
    path = profile_path(name)
    if token is not None:
        write_token_file(path, token)
    state["profiles"].append(name)
    _save_state(state)
    return path


def remove_profile(name: str) -> None:
    if name == DEFAULT_PROFILE:
        raise ProfileError("the 'default' profile cannot be removed")
    state = _load_state()
    if name not in state["profiles"]:
        raise ProfileError(f"no such profile '{name}'")
    if state["active"] == name:
        raise ProfileError(f"'{name}' is the active profile - switch to another one first")
    state["profiles"].remove(name)
    state["limits"].pop(name, None)
    _save_state(state)


def set_active(name: str) -> None:
    state = _load_state()
    if name not in state["profiles"]:
        raise ProfileError(f"no such profile '{name}'")
    state["active"] = name
    _save_state(state)


def mark_limited(name: str, resets_at: float | None) -> None:
    """records that `name` hit its rate limit until resets_at. called by the scheduler, never by a
    human, which is why an unknown name is registered rather than refused - the profile it names is
    real (it was just the active one), only unrecorded here yet."""
    state = _load_state()
    if name not in state["profiles"]:
        state["profiles"].append(name)
    state["limits"][name] = {"limited_until": resets_at}
    _save_state(state)


def is_limited(name: str, now: float | None = None) -> bool:
    state = _load_state()
    limited_until = (state["limits"].get(name) or {}).get("limited_until")
    now = time.time() if now is None else now
    return bool(limited_until and limited_until > now)


def next_available(now: float | None = None) -> str | None:
    """the next profile after the active one, in rotation order, that is not limited right now -
    None once every configured profile is"""
    state = _load_state()
    order = state["profiles"]
    active = state["active"]
    if active in order:
        idx = order.index(active)
        rotated = order[idx + 1 :] + order[: idx + 1]
    else:
        rotated = list(order)
    now = time.time() if now is None else now
    for name in rotated:
        limited_until = (state["limits"].get(name) or {}).get("limited_until")
        if not (limited_until and limited_until > now):
            return name
    return None


def earliest_reset(now: float | None = None) -> float | None:
    """the soonest a currently-limited profile resets - what the board parks until once every
    profile is limited"""
    state = _load_state()
    now = time.time() if now is None else now
    resets = [
        info.get("limited_until")
        for info in state["limits"].values()
        if info.get("limited_until") and info["limited_until"] > now
    ]
    return min(resets) if resets else None


def read_active_token() -> str:
    """the active profile's token, the same way a card run reads it - for tooling and tests, never
    for anything that could log or print it."""
    from smortboard.exec.backends import CardTokenMissing, read_card_token

    active = active_profile()
    path = profile_path(active)
    if active != DEFAULT_PROFILE and path.is_file() and not _mode_ok(path):
        raise ProfileError(f"{path} is not mode 600 - refusing to read it. chmod 600 {path}")
    try:
        return read_card_token(token_path_for_run(None))
    except CardTokenMissing as exc:
        raise ProfileError(str(exc)) from exc
