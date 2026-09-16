"""who may talk to the api: a loopback host name, a same-origin browser, and the board's own key.

the board binds to loopback, but a web page the operator happens to have open can still aim
requests at it - blind form posts (csrf) or, via dns rebinding, full reads. three checks close that:
the Host header must name the board rather than some other domain, a state-changing browser request
must come from the board's own origin, and every /api/ request must carry the api key - as a
SameSite=Strict cookie (the browser) or the X-Smortboard-Key header (smortboard-land and scripts).
the key lives in a mode-600 file so it survives a restart and a local cli can read it.
"""

import hmac
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from smortboard.exec.backends import config_base
from smortboard.profiles import _mode_ok, write_token_file

# a test seam, mirrors the other *_PATH_ENV overrides
API_KEY_PATH_ENV = "SMORTBOARD_API_KEY_PATH"
KEY_HEADER = "X-Smortboard-Key"
KEY_QUERY = "key"

_LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_SAME_SITE_FETCH = frozenset({"same-origin", "none"})


def api_key_path() -> Path:
    override = os.environ.get(API_KEY_PATH_ENV)
    return Path(override) if override else config_base() / "smortboard" / "api_key"


def load_or_create_api_key(path: Path | None = None) -> str:
    """reads the board's api key, minting a new one when there is none or it is not owner-only"""
    path = path or api_key_path()
    if path.exists() and _mode_ok(path):
        key = path.read_text().strip()
        if key:
            return key
    key = secrets.token_urlsafe(32)
    write_token_file(path, key)
    return key


def cookie_name(port: int) -> str:
    # cookies ignore the port, so the demo board on 8001 must not overwrite the real board's key
    return f"smortboard_key_{port}"


def _hostname(value: str) -> str:
    # urlsplit handles "host:port", "[::1]:port" and a bare name alike
    return (urlsplit(f"//{value}").hostname or "").lower()


def allowed_hostnames(bound_host: str) -> frozenset[str]:
    extra = {bound_host.lower()} if bound_host not in ("0.0.0.0", "::", "") else set()
    return _LOOPBACK_NAMES | extra


def host_ok(host_header: str | None, allowed: frozenset[str]) -> bool:
    """a rebinding page reaches the board under its own domain, so the Host header gives it away"""
    return bool(host_header) and _hostname(host_header) in allowed


def origin_ok(
    method: str, origin: str | None, fetch_site: str | None, allowed: frozenset[str]
) -> bool:
    """a state-changing request from a browser must come from the board's own page; a request with
    neither header is not from a browser and is left to the key check"""
    if method in _SAFE_METHODS:
        return True
    if origin is not None:
        return origin != "null" and _hostname(urlsplit(origin).netloc) in allowed
    if fetch_site is not None:
        return fetch_site in _SAME_SITE_FETCH
    return True


def key_ok(presented: str | None, key: str) -> bool:
    return bool(presented) and hmac.compare_digest(presented.encode(), key.encode())


def cookie_value(cookie_header: str | None, name: str) -> str | None:
    for part in (cookie_header or "").split(";"):
        cookie, _, value = part.strip().partition("=")
        if cookie == name:
            return value
    return None
