"""screenshots.py: url validation and the one-png-per-call contract.

playwright itself is always faked here - these tests never launch a real browser, per the card's
scope ("tests cover the flow with playwright faked").
"""

import pytest

from smortboard.screenshots import ScreenshotRefused, take_board_screenshot, validate_board_url


def test_loopback_urls_pass_validation():
    assert validate_board_url("http://127.0.0.1:8000/ui/index.html")
    assert validate_board_url("http://localhost:8000/ui/index.html")


def test_a_non_localhost_url_is_refused_before_any_browser_starts(tmp_path):
    calls = []

    def fake_taker(url, out_path):
        calls.append(url)

    with pytest.raises(ScreenshotRefused):
        take_board_screenshot("http://example.com/", tmp_path, taker=fake_taker)
    assert calls == []


def test_a_host_that_merely_contains_localhost_is_refused(tmp_path):
    # substring tricks like "localhost.evil.com" must not pass as the real loopback host
    calls = []
    with pytest.raises(ScreenshotRefused):
        take_board_screenshot(
            "http://localhost.evil.com/", tmp_path, taker=lambda u, p: calls.append(u)
        )
    assert calls == []


def test_a_non_http_scheme_is_refused(tmp_path):
    calls = []
    with pytest.raises(ScreenshotRefused):
        take_board_screenshot("file:///etc/passwd", tmp_path, taker=lambda u, p: calls.append(u))
    assert calls == []


def test_a_loopback_url_writes_exactly_one_png(tmp_path):
    def fake_taker(url, out_path):
        out_path.write_bytes(b"fake-png-bytes")

    path = take_board_screenshot("http://127.0.0.1:8000/ui/index.html", tmp_path, taker=fake_taker)
    assert path.parent == tmp_path
    assert path.read_bytes() == b"fake-png-bytes"
    assert list(tmp_path.iterdir()) == [path]
