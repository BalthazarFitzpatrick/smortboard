"""asset resolution: local smortboard/ui/ first, ui_base package as fallback

follows ui_base/CLAUDE.md's documented pattern verbatim — containment check via .resolve()
and .parents, then delegate to read_asset rather than reimplementing traversal protection
"""

from pathlib import Path

from ui_base import UiBaseError, read_asset

LOCAL_UI_DIR = Path(__file__).resolve().parent.parent / "ui"

_CONTENT_TYPES = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class AssetNotFound(Exception):
    """no asset by this name in either the local ui dir or ui_base"""


def content_type_for(name: str) -> str:
    return _CONTENT_TYPES.get(Path(name).suffix, "application/octet-stream")


def resolve_asset(name: str) -> bytes:
    """local files win; fall back to ui_base's own asset store"""
    target = (LOCAL_UI_DIR / name).resolve()
    if LOCAL_UI_DIR.is_dir() and LOCAL_UI_DIR in target.parents and target.is_file():
        return target.read_bytes()
    try:
        return read_asset(name)
    except UiBaseError as exc:
        raise AssetNotFound(name) from exc
