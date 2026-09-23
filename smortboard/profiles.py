"""lab-scoped credential profiles, with read-only compatibility for legacy state and tokens."""

import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from smortboard.exec.backends import card_token_path, config_base
from smortboard.labs.catalog import resolve_ref

DEFAULT_PROFILE = "default"
DEFAULT_LAB = "anthropic"
STATE_PATH_ENV = "SMORTBOARD_PROFILES_STATE_PATH"
_STATE_LOCK = threading.Lock()
_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
_KINDS = {"anthropic": ("oauth",), "openai": ("access_token", "api_key", "auth_json")}


class ProfileError(RuntimeError):
    """a profile operation could not be completed"""


def _validate_lab(lab: str) -> None:
    if not isinstance(lab, str) or lab not in _KINDS:
        raise ProfileError(f"unknown lab '{lab}'")


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not name or any(ch not in _NAME_CHARS for ch in name):
        raise ProfileError(
            f"'{name}' is not a valid profile name - letters, digits, '-' and '_' only."
        )


def profiles_dir(lab: str = DEFAULT_LAB) -> Path:
    _validate_lab(lab)
    return config_base() / "smortboard" / "tokens" / lab


def state_path() -> Path:
    override = os.environ.get(STATE_PATH_ENV)
    return Path(override) if override else config_base() / "smortboard" / "profiles.json"


def _empty_lab() -> dict[str, Any]:
    return {"active": None, "profiles": [], "limits": {}, "kinds": {}}


def _default_state() -> dict[str, Any]:
    state = _empty_lab()
    state.update(active=DEFAULT_PROFILE, profiles=[DEFAULT_PROFILE])
    return {"version": 2, "labs": {DEFAULT_LAB: state}}


def _load_state() -> dict[str, Any]:
    """reading a v1 or missing file never writes or moves anything"""
    try:
        data = json.loads(state_path().read_text())
    except (json.JSONDecodeError, OSError):
        return _default_state()
    if not isinstance(data, dict):
        return _default_state()
    labs = data.get("labs") if data.get("version") == 2 else {DEFAULT_LAB: data}
    if not isinstance(labs, dict):
        return _default_state()
    result = {"version": 2, "labs": {}}
    for lab, values in labs.items():
        if lab not in _KINDS or not isinstance(values, dict):
            continue
        state = _empty_lab()
        names = values.get("profiles") or []
        if not isinstance(names, list):
            continue
        for name in names:
            try:
                _validate_name(name)
            except ProfileError:
                continue
            if name not in state["profiles"]:
                state["profiles"].append(name)
        active = values.get("active")
        state["active"] = (
            active if active in state["profiles"] else next(iter(state["profiles"]), None)
        )
        for key in ("limits", "kinds"):
            if isinstance(values.get(key), dict):
                state[key] = dict(values[key])
        result["labs"][lab] = state
    return result


def _lab_state(state: dict[str, Any], lab: str) -> dict[str, Any]:
    _validate_lab(lab)
    return state["labs"].setdefault(lab, _empty_lab())


def _save_state(state: dict[str, Any]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp_path.write_text(json.dumps(state, indent=2))
    tmp_path.replace(path)


def configured_labs() -> list[str]:
    return [lab for lab, state in _load_state()["labs"].items() if state["profiles"]]


def profile_path(name: str, lab: str = DEFAULT_LAB) -> Path:
    """legacy anthropic files remain in place; newly added profiles use lab directories"""
    _validate_lab(lab)
    _validate_name(name)
    if lab == DEFAULT_LAB and name == DEFAULT_PROFILE:
        return card_token_path()
    path = profiles_dir(lab) / name
    legacy = profiles_dir(lab).parent / name
    if lab == DEFAULT_LAB and not path.exists() and legacy.is_file():
        return legacy
    return path


def _mode_ok(path: Path) -> bool:
    return (path.stat().st_mode & 0o077) == 0


def profile_kind(name: str, lab: str = DEFAULT_LAB) -> str:
    state = _lab_state(_load_state(), lab)
    return state["kinds"].get(name, _KINDS[lab][0])


def list_profiles(now: float | None = None, lab: str = DEFAULT_LAB) -> list[dict[str, Any]]:
    state = _lab_state(_load_state(), lab)
    now = time.time() if now is None else now
    rows = []
    for name in state["profiles"]:
        path = profile_path(name, lab)
        present = path.is_file()
        limited_until = (state["limits"].get(name) or {}).get("limited_until")
        rows.append(
            {
                "lab": lab,
                "kind": state["kinds"].get(name, _KINDS[lab][0]),
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


def list_all_profiles(now: float | None = None) -> list[dict[str, Any]]:
    return [row for lab in configured_labs() for row in list_profiles(now, lab)]


def active_profile(lab: str = DEFAULT_LAB) -> str | None:
    return _lab_state(_load_state(), lab)["active"]


def active_profile_path(lab: str = DEFAULT_LAB) -> Path:
    name = active_profile(lab)
    if name is None:
        raise ProfileError(f"no credential profile configured for {lab}")
    return profile_path(name, lab)


def has_multiple_profiles(lab: str = DEFAULT_LAB) -> bool:
    return len(_lab_state(_load_state(), lab)["profiles"]) > 1


def token_path_for_run(
    explicit_override: str | Path | None, lab: str = DEFAULT_LAB
) -> str | Path | None:
    if explicit_override is not None:
        return explicit_override
    active = active_profile(lab)
    if active is None:
        raise ProfileError(f"no credential profile configured for {lab}")
    return None if lab == DEFAULT_LAB and active == DEFAULT_PROFILE else profile_path(active, lab)


def token_path_for_profile(
    name: str, explicit_override: str | Path | None = None
) -> str | Path | None:
    """resolve the captured anthropic identity, even if another run has since rotated it"""
    if explicit_override is not None:
        return explicit_override
    return None if name == DEFAULT_PROFILE else profile_path(name, DEFAULT_LAB)


def write_token_file(path: str | Path, token: str) -> None:
    """create private files without changing the process-wide umask of concurrent runs"""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    if path.is_symlink():
        raise ProfileError(f"{path} is a symbolic link - refusing to write a token")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(token.strip() + "\n")
    if not _mode_ok(path):
        raise ProfileError(f"{path} is not mode 600 after writing - refusing to use it")


def add_profile(
    name: str, token: str | None = None, lab: str = DEFAULT_LAB, kind: str | None = None
) -> Path:
    _validate_name(name)
    _validate_lab(lab)
    kind = kind or _KINDS[lab][0]
    if kind not in _KINDS[lab]:
        raise ProfileError(f"invalid credential kind '{kind}' for {lab}")
    if token is not None and kind == "auth_json":
        token = _auth_json(token)
    with _STATE_LOCK:
        document = _load_state()
        state = _lab_state(document, lab)
        if name in state["profiles"]:
            raise ProfileError(f"profile '{name}' already exists")
        path = profile_path(name, lab)
        if token is not None:
            write_token_file(path, token)
        state["profiles"].append(name)
        state["kinds"][name] = kind
        if state["active"] is None:
            state["active"] = name
        _save_state(document)
        return path


def _pick_replacement(state: dict[str, Any], exclude: str, now: float) -> str | None:
    remaining = [name for name in state["profiles"] if name != exclude]
    for name in remaining:
        limited_until = (state["limits"].get(name) or {}).get("limited_until")
        if not (limited_until and limited_until > now):
            return name
    return next(iter(remaining), None)


def remove_profile(
    name: str,
    now: float | None = None,
    lab: str = DEFAULT_LAB,
    referenced_cards: list[dict[str, Any]] | None = None,
) -> None:
    """callers pass lab-dependent cards to protect the final credential of a configured lab"""
    now = time.time() if now is None else now
    with _STATE_LOCK:
        document = _load_state()
        state = _lab_state(document, lab)
        if name not in state["profiles"]:
            raise ProfileError(f"no such profile '{name}'")
        if len(state["profiles"]) <= 1:
            if referenced_cards:
                labels = ", ".join(
                    f"{card.get('title', card['id'])} ({card['id']})" for card in referenced_cards
                )
                raise ProfileError(
                    f"'{name}' is the last {lab} profile, still referenced by cards: {labels}"
                )
            if referenced_cards is None:
                raise ProfileError(f"'{name}' is the last remaining profile and cannot be removed")
        path = profile_path(name, lab)
        if state["active"] == name:
            state["active"] = _pick_replacement(state, name, now)
        state["profiles"].remove(name)
        state["limits"].pop(name, None)
        state["kinds"].pop(name, None)
        _save_state(document)
        path.unlink(missing_ok=True)


def set_active(name: str, lab: str = DEFAULT_LAB) -> None:
    with _STATE_LOCK:
        document = _load_state()
        state = _lab_state(document, lab)
        if name not in state["profiles"]:
            raise ProfileError(f"no such profile '{name}'")
        if state["active"] != name:
            state["active"] = name
            _save_state(document)


def _mark_limited(state: dict[str, Any], name: str, resets_at: float | None) -> None:
    if name not in state["profiles"]:
        state["profiles"].append(name)
    state["limits"][name] = {"limited_until": resets_at}


def mark_limited(name: str, resets_at: float | None, lab: str = DEFAULT_LAB) -> None:
    _validate_name(name)
    with _STATE_LOCK:
        document = _load_state()
        _mark_limited(_lab_state(document, lab), name, resets_at)
        _save_state(document)


def is_limited(name: str, now: float | None = None, lab: str = DEFAULT_LAB) -> bool:
    state = _lab_state(_load_state(), lab)
    limited_until = (state["limits"].get(name) or {}).get("limited_until")
    return bool(limited_until and limited_until > (time.time() if now is None else now))


def _next_available(state: dict[str, Any], now: float) -> str | None:
    order = state["profiles"]
    active = state["active"]
    if active in order:
        idx = order.index(active)
        order = order[idx + 1 :] + order[: idx + 1]
    for name in order:
        limited_until = (state["limits"].get(name) or {}).get("limited_until")
        if not (limited_until and limited_until > now):
            return name
    return None


def next_available(now: float | None = None, lab: str = DEFAULT_LAB) -> str | None:
    return _next_available(_lab_state(_load_state(), lab), time.time() if now is None else now)


def _earliest_reset(state: dict[str, Any], now: float) -> float | None:
    resets = [
        info.get("limited_until")
        for info in state["limits"].values()
        if info.get("limited_until") and info["limited_until"] > now
    ]
    return min(resets) if resets else None


def earliest_reset(now: float | None = None, lab: str = DEFAULT_LAB) -> float | None:
    return _earliest_reset(_lab_state(_load_state(), lab), time.time() if now is None else now)


def handle_usage_limit(
    resets_at: float | None,
    now: float | None = None,
    lab: str = DEFAULT_LAB,
    name: str | None = None,
) -> dict[str, Any]:
    """mark the run's credential and rotate within its lab under one state lock"""
    now = time.time() if now is None else now
    with _STATE_LOCK:
        document = _load_state()
        state = _lab_state(document, lab)
        if len(state["profiles"]) <= 1:
            if state["profiles"] and state_path().exists():
                _mark_limited(state, name or state["active"], resets_at)
                _save_state(document)
            return {"rotated": False, "next_profile": None, "earliest_reset": None}
        _mark_limited(state, name or state["active"], resets_at)
        next_profile = _next_available(state, now)
        if next_profile is not None:
            state["active"] = next_profile
        _save_state(document)
        return {
            "rotated": True,
            "next_profile": next_profile,
            "earliest_reset": _earliest_reset(state, now),
        }


def usable_fallback(
    refs: list[str],
    from_lab: str,
    skip: Callable[[str, str], bool] | None = None,
) -> tuple[str, str, str] | None:
    """the first fallback ref whose lab has a readable, unlimited credential: (lab, model, profile).

    every observed limit is an account-wide five_hour/seven_day window, so a ref in the limited lab
    itself is never a fallback. `skip(lab, profile)` drops a candidate the caller already knows is
    out - a paused lab, a profile this turn already tried."""
    for ref in refs or []:
        target = resolve_ref(ref)
        if target is None or target[0] == from_lab:
            continue
        lab, model = target
        profile = next_available(lab=lab)
        if profile is None or (skip is not None and skip(lab, profile)):
            continue
        try:
            read_profile_token(lab, profile)
        except ProfileError:
            continue
        return lab, model, profile
    return None


def read_profile_token(lab: str, name: str) -> str:
    """read credential files only at execution time"""
    from smortboard.exec.backends import CardTokenMissing, read_card_token

    path = profile_path(name, lab)
    if lab == DEFAULT_LAB and name == DEFAULT_PROFILE:
        try:
            return read_card_token()
        except CardTokenMissing as exc:
            raise ProfileError(str(exc)) from exc
    try:
        if not _mode_ok(path):
            raise ProfileError(f"{path} is not mode 600 - refusing to read it. chmod 600 {path}")
        token = path.read_text().strip()
    except OSError as exc:
        raise ProfileError(f"cannot read {lab} credential at {path}") from exc
    if profile_kind(name, lab) == "auth_json":
        return _auth_json(token)
    if not token or any(char.isspace() for char in token):
        raise ProfileError(f"no usable {lab} credential at {path}")
    return token


def _auth_json(token: str) -> str:
    """validate a ChatGPT login without exposing its contents in errors or metadata"""
    try:
        data = json.loads(token)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProfileError(
            "ChatGPT login must be a JSON object containing tokens.access_token"
        ) from exc
    tokens = data.get("tokens") if isinstance(data, dict) else None
    access = tokens.get("access_token") if isinstance(tokens, dict) else None
    if not isinstance(access, str) or not access.strip():
        raise ProfileError("ChatGPT login must be a JSON object containing tokens.access_token")
    return json.dumps(data, separators=(",", ":"))


def read_active_token(lab: str = DEFAULT_LAB) -> str:
    active = active_profile(lab)
    if active is None:
        raise ProfileError(f"no credential profile configured for {lab}")
    return read_profile_token(lab, active)
