"""the landing lock: one holder per (repo, target branch), persisted so it survives a board
restart and reaches agents outside the board too - see smortboard/store/api.py for the fifo
queue itself. this module only resolves what "repo" means and offers a blocking helper that polls
until granted, heartbeats while held, and always releases.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from smortboard.store.api import Store
from smortboard.store.errors import NotFoundError

# default hold ttl and the client's heartbeat cadence while it holds the lock - see the module
# docstring in smortboard/review/integrate.py for why a stuck holder must not wedge the queue
DEFAULT_TTL_S = 600
HEARTBEAT_INTERVAL_S = 30
# how often a waiting caller polls the queue for its turn
POLL_INTERVAL_S = 2


def resolve_repo_key(identifier: str, store: Store | None = None) -> str:
    """turns a repo id, a filesystem path, or a remote url into one stable key.

    a repo id (matches a row in `store`) resolves to that repo's own resolved path, so the same
    checkout locks the same key whether it is reached by id or by path. anything else that exists
    on disk resolves to its own resolved path. anything else (a bare url, or a path store cannot
    see) is used verbatim - the fallback that lets an outside agent lock a repo the board has never
    heard of.
    """
    if store is not None:
        try:
            repo = store.get_repo_row(identifier)
            return str(Path(repo["path"]).expanduser().resolve())
        except NotFoundError:
            pass  # not a repo id - fall through to path/url handling
    expanded = Path(identifier).expanduser()
    if expanded.exists():
        return str(expanded.resolve())
    return identifier


def remote_url(repo_path: str | Path) -> str | None:
    """origin's fetch url, or None if there is no remote named origin - used to match an outside
    caller's --repo against a board-registered repo by url instead of by local path"""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


@contextmanager
def landing_lock(
    store: Store,
    repo_key: str,
    holder: str,
    branch: str,
    target: str = "development",
    ttl_s: int = DEFAULT_TTL_S,
    on_wait: Any | None = None,
) -> Iterator[None]:
    """blocks until this holder is granted the landing lock for (repo_key, target), yields while
    it heartbeats it does not actually run in a background thread here - callers doing long work
    (the client's own test command) heartbeat explicitly instead, see smortboard-land. this
    context manager is for the board's own in-process integrate path, which holds it only across
    a sync+push that is already fast.
    """
    granted = store.request_landing(repo_key, holder, branch, target, ttl_s=ttl_s)
    lease_id = granted["lease_id"]
    while not granted["granted"]:
        if on_wait is not None:
            on_wait(granted.get("position"))
        time.sleep(POLL_INTERVAL_S)
        granted = store.request_landing(
            repo_key, holder, branch, target, ttl_s=ttl_s, lease_id=lease_id
        )
    try:
        yield
    finally:
        store.release_landing(lease_id)
