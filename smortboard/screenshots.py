"""one playwright screenshot of the running board, taken on the HOST - never inside a container.

Mission control never gets a browser tool and never touches the filesystem: it only asks for a
screenshot through a field in its JSON reply (see smortboard.orchestrator). This module is what
the board itself calls, on the host, to honour that ask - chromium, headless, one page, one PNG.

Only a loopback url is ever fetched. A repo's readme, a card's own text, or mission control's
reply itself could all name any url they like; `validate_board_url` refuses anything that is not
127.0.0.1/localhost BEFORE a browser is ever launched, so there is no path from untrusted text to
an outbound fetch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

# the board only ever serves on the loopback interface (smortboard.cli's own default) - nothing
# else is ever a legitimate target for this tool
_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})

# fixed name: one screenshot per turn, written into that turn's own throwaway directory, so there
# is never a question of which file is "the" screenshot
SCREENSHOT_FILENAME = "board.png"


class ScreenshotRefused(RuntimeError):
    """the url named was not the board's own loopback address"""


class ScreenshotTaker(Protocol):
    """navigates to `url` and writes a PNG to `out_path`. tests inject a fake - no real browser."""

    def __call__(self, url: str, out_path: Path) -> None: ...


def validate_board_url(url: str) -> str:
    """refuses anything but a loopback http(s) url. Called before any browser starts, so a
    non-localhost ask never gets as far as launching chromium."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in _ALLOWED_HOSTS:
        raise ScreenshotRefused(f'"{url}" is not a localhost url, so no screenshot was taken')
    return url


def _playwright_taker(url: str, out_path: Path) -> None:
    """the real taker: chromium headless, one page, one full-page png.

    imported lazily so a board that never takes a screenshot never needs playwright's browser
    binaries installed just to start up.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(url, wait_until="networkidle")
            page.screenshot(path=str(out_path), full_page=True)
        finally:
            browser.close()


def take_board_screenshot(
    url: str,
    out_dir: str | Path,
    taker: ScreenshotTaker | None = None,
) -> Path:
    """validates `url`, then writes exactly one PNG into `out_dir` and returns its path.

    `out_dir` is the turn's own scratch directory - the caller owns its lifetime (one per turn,
    cleaned up after), so a screenshot from one message can never be mistaken for another's.
    """
    validate_board_url(url)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / SCREENSHOT_FILENAME
    take = taker or _playwright_taker
    take(url, out_path)
    return out_path
