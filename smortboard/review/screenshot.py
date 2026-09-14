"""a ui card proves its change with one screenshot, taken on the host after both gates pass.

Runs only for a card whose diff touches smortboard/ui/ - the plain css/js/html directory ui_base
serves from. Everything else about a card (backend python, docs, tests) has nothing visual to
prove, so no screenshot is worth the cost of one.

NEVER FAILS THE CARD. A missing playwright install, a board that would not start, a page that
would not load - any of it becomes a note on the card (see ScreenshotResult.note), because a
screenshot is evidence for the operator, not a gate the work has to pass.

THE THROWAWAY BOARD IS A SUBPROCESS, NOT AN IN-PROCESS SERVER. The live board may be serving the
operator on this same host right now, and the two must never share a thread, a socket, or the
asset-resolution globals smortboard.server.assets keeps at module scope.

THE SUBPROCESS NEVER EXECUTES THE WORKTREE'S PYTHON. diff_touches_ui only checks whether a diff
touches smortboard/ui/, not that it touches only that - a two-file diff can carry a ui/ line and a
backend change together, and both gates only ever ran that backend change inside docker with tests
and a reviewer watching, never live on the host. So the throwaway board runs a fresh copy of the
HOST's own smortboard package (already the trusted code this process itself is running), with only
smortboard/ui/ - plain css/js/html, never imported, only served to a browser - overlaid from the
worktree. That overlay is what makes the screenshot show the card's own ui/ changes; nothing else
about the worktree is ever read into the process that runs.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# the diff prefix that triggers a screenshot - this repo's own ui/, not a per-repo setting: only
# smortboard's own board has a ui to screenshot at all right now
UI_DIFF_PREFIX = "smortboard/ui/"

# the line a ui card's final message ends with, naming the view to open
SCREENSHOT_MARKER = "SCREENSHOT:"
DEFAULT_VIEW = "the board"

# attachments this module writes all share this prefix, so the pr body and a re-run both know
# which attachment is the latest screenshot without a second event kind to keep in sync
SCREENSHOT_ATTACHMENT_PREFIX = "screenshot-"

# how long the throwaway board gets to answer /health before the screenshot is given up as failed
BOARD_READY_TIMEOUT_SECONDS = 20.0
BOARD_POLL_SECONDS = 0.2

# a page load and screenshot that has not finished in this long is a hang, not slow work
PAGE_TIMEOUT_MS = 15_000

# only ever opened - the board and its throwaway peer both bind loopback only, never a network
# interface a screenshot run could reach out further than the machine it is on
LOCALHOST = "127.0.0.1"


# files a host package copy never needs - build artifacts, not source the board would serve
_STAGE_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")


def _stage_host_backend(worktree_path: str | Path, stage_dir: Path) -> None:
    """copies the HOST's own smortboard package into stage_dir, then overlays smortboard/ui/ from
    the worktree - the only part of the worktree this ever reads.

    `Path(__file__)` here resolves inside this same module, i.e. the host's own checkout, never
    the worktree's copy of screenshot.py - so this always stages the code already trusted to run
    on this host, regardless of what a card changed.
    """
    host_package = Path(__file__).resolve().parents[1]
    staged_package = stage_dir / "smortboard"
    shutil.copytree(host_package, staged_package, ignore=_STAGE_IGNORE)
    shutil.rmtree(staged_package / "ui")
    shutil.copytree(Path(worktree_path) / "smortboard" / "ui", staged_package / "ui")


class ScreenshotUnavailable(RuntimeError):
    """the throwaway board could not be started or never answered - caught by take_screenshot,
    never allowed to reach the lifecycle as an exception"""


@dataclass
class ScreenshotResult:
    """what happened, in a shape the lifecycle can note without inspecting an exception.

    `attached` is False for every ordinary failure - playwright missing, the board never came up,
    the page never loaded. The card still reaches its pull request either way.
    """

    attached: bool
    filename: str | None = None
    attachment_id: str | None = None
    note: str | None = None


def diff_touches_ui(diff_text: str) -> bool:
    """whether a unified diff touches smortboard/ui/ - checked on the diff already pulled for the
    reviewer, so this never shells out a second time."""
    for line in diff_text.splitlines():
        if not line.startswith("diff --git "):
            continue
        paths = line[len("diff --git ") :].split()
        # "a/<path> b/<path>" - either side names the file, bar a rename
        if any(p[2:].startswith(UI_DIFF_PREFIX) for p in paths if len(p) > 2 and p[1] == "/"):
            return True
    return False


def parse_screenshot_line(result_text: str | None) -> str:
    """the view named after SCREENSHOT: in the worker's final message, or the default.

    Only the LAST such line counts - a fix round's own summary can carry one too, and that is the
    one that describes the diff as it stands now.
    """
    if not result_text:
        return DEFAULT_VIEW
    view = None
    for line in result_text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith(SCREENSHOT_MARKER):
            view = stripped[len(SCREENSHOT_MARKER) :].strip()
    return view or DEFAULT_VIEW


def _free_port() -> int:
    # bind to port 0, read what the os handed out, then let go - the same trick HTTPServer(...,
    # 0) uses internally, done here so the port is known before the subprocess that will actually
    # listen on it even starts
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((LOCALHOST, 0))
        return sock.getsockname()[1]


def _wait_until_ready(base_url: str, deadline: float) -> bool:
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1) as resp:
                if resp.status == 200:
                    return True
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(BOARD_POLL_SECONDS)
    return False


class ThrowawayBoard:
    """one `smortboard` process, serving the HOST's own backend with the worktree's smortboard/ui/
    overlaid on top, on a free port with a temp db.

    Used as a context manager: the process is up and answering /health by the time `with` hands
    back control, and is killed, its temp db removed, and its staged code deleted on the way out,
    success or failure either way.
    """

    def __init__(self, worktree_path: str | Path) -> None:
        self.worktree_path = str(worktree_path)
        self.port = _free_port()
        self.base_url = f"http://{LOCALHOST}:{self.port}"
        self._db_fd, self._db_path = tempfile.mkstemp(prefix="smortboard-screenshot-", suffix=".db")
        os.close(self._db_fd)
        self._stage_dir: str | None = None
        self._process: subprocess.Popen | None = None

    def __enter__(self) -> ThrowawayBoard:
        self._stage_dir = tempfile.mkdtemp(prefix="smortboard-screenshot-stage-")
        try:
            _stage_host_backend(self.worktree_path, Path(self._stage_dir))
            env = dict(os.environ)
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                os.pathsep.join([self._stage_dir, existing]) if existing else self._stage_dir
            )
            self._process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "smortboard.cli",
                    "--host",
                    LOCALHOST,
                    "--port",
                    str(self.port),
                    "--db",
                    self._db_path,
                    "--no-browser",
                ],
                cwd=self._stage_dir,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if not _wait_until_ready(self.base_url, time.monotonic() + BOARD_READY_TIMEOUT_SECONDS):
                stderr = self._process.stderr.read() if self._process.stderr else ""
                self._stop()
                raise ScreenshotUnavailable(
                    f"the throwaway board never answered /health: {stderr}".strip()
                )
        except Exception:
            shutil.rmtree(self._stage_dir, ignore_errors=True)
            raise
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._stop()
        Path(self._db_path).unlink(missing_ok=True)
        if self._stage_dir is not None:
            shutil.rmtree(self._stage_dir, ignore_errors=True)

    def _stop(self) -> None:
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=10)


def _capture(base_url: str, view: str) -> bytes:
    """opens the board and screenshots it. lazy playwright import: a host with no browsers
    installed fails exactly here, into the note the caller writes, rather than at module import."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_default_timeout(PAGE_TIMEOUT_MS)
            page.goto(f"{base_url}/ui/index.html")
            if view and view != DEFAULT_VIEW:
                _open_named_view(page, view)
            return page.screenshot(full_page=True)
        finally:
            browser.close()


def _open_named_view(page: Any, view: str) -> None:
    """best-effort: opens the card panel whose title contains the named view, per board.js's own
    `.card-strip[data-card-id] .card-title` markup. a view that is not found is not an error - the
    screenshot still shows the board itself, which is the documented default."""
    strip = page.locator(".card-strip", has=page.locator(".card-title", has_text=view)).first
    if strip.count():
        strip.click()


def take_screenshot(
    store: Any,
    card_id: str,
    worktree_path: str | Path,
    result_text: str | None,
    board_factory: Any = ThrowawayBoard,
    capture: Any = _capture,
) -> ScreenshotResult:
    """the whole flow: name the view, stand up a throwaway board from the worktree, screenshot it,
    attach the png. Never raises - every failure becomes a ScreenshotResult.note instead.

    `board_factory` and `capture` are swapped out in tests: a fake board (any context manager
    exposing `.base_url`) stands in for the real subprocess, and a fake capture stands in for
    playwright when it is not installed. Production uses the real ones.
    """
    view = parse_screenshot_line(result_text)
    try:
        with board_factory(worktree_path) as board:
            png = capture(board.base_url, view)
    except Exception as exc:  # noqa: BLE001 - a screenshot failure is a note, never a crash
        return ScreenshotResult(attached=False, note=f"the screenshot could not be taken: {exc}")

    attempt = 1 + sum(
        a["filename"].startswith(SCREENSHOT_ATTACHMENT_PREFIX)
        for a in store.list_attachments(card_id)
    )
    filename = f"{SCREENSHOT_ATTACHMENT_PREFIX}{attempt}.png"
    attachment = store.add_attachment(card_id, filename, "image/png", png)
    return ScreenshotResult(attached=True, filename=filename, attachment_id=attachment["id"])


def latest_screenshot(card: dict[str, Any]) -> dict[str, Any] | None:
    """the most recent screenshot attachment on a card dict (as store.get_card returns it), or
    None - the pr body reads this rather than a second event kind kept in sync by hand."""
    shots = [
        a
        for a in card.get("attachments") or []
        if a["filename"].startswith(SCREENSHOT_ATTACHMENT_PREFIX)
    ]
    return shots[-1] if shots else None
