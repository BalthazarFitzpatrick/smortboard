"""ui cards prove their change with one screenshot: smortboard.review.screenshot end to end, and
its wiring into smortboard.lifecycle. playwright itself is real where used; every board is faked -
either a stand-in context manager, or (for the one test that drives real playwright) a plain
stdlib http.server standing in for the board's own /ui/index.html."""

import http.server
import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from smortboard import lifecycle
from smortboard.exec.runner import RunResult
from smortboard.review import screenshot
from smortboard.review.gates import GateResult
from smortboard.review.merge_request import MergeRequestResult
from smortboard.review.reviewer import ReviewResult
from smortboard.server.access import KEY_HEADER, cookie_name
from smortboard.store.api import Store

REPO_ROOT = Path(__file__).resolve().parents[1]

UI_DIFF = """\
diff --git a/smortboard/ui/board.js b/smortboard/ui/board.js
index 1111111..2222222 100644
--- a/smortboard/ui/board.js
+++ b/smortboard/ui/board.js
@@ -1 +1 @@
-old
+new
"""

BACKEND_DIFF = """\
diff --git a/smortboard/lifecycle.py b/smortboard/lifecycle.py
index 1111111..2222222 100644
--- a/smortboard/lifecycle.py
+++ b/smortboard/lifecycle.py
@@ -1 +1 @@
-old
+new
"""

RENAMED_INTO_UI_DIFF = """\
diff --git a/smortboard/old_name.js b/smortboard/ui/new_name.js
similarity index 100%
rename from smortboard/old_name.js
rename to smortboard/ui/new_name.js
"""


# -- diff_touches_ui ----------------------------------------------------------


@pytest.mark.parametrize(
    "module", ["smortboard.lifecycle", "smortboard.attention", "smortboard.review.gates"]
)
def test_screenshot_support_does_not_create_an_import_cycle(module):
    subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        timeout=10,
    )


def test_a_diff_touching_ui_is_detected():
    assert screenshot.diff_touches_ui(UI_DIFF)


def test_a_diff_touching_only_backend_python_is_not_a_ui_change():
    assert not screenshot.diff_touches_ui(BACKEND_DIFF)


def test_an_empty_diff_is_not_a_ui_change():
    assert not screenshot.diff_touches_ui("")


def test_a_rename_into_ui_still_counts():
    assert screenshot.diff_touches_ui(RENAMED_INTO_UI_DIFF)


# -- parse_screenshot_line -----------------------------------------------------


def test_the_screenshot_line_is_read_from_the_final_message():
    text = "did the thing\n\nSCREENSHOT: the settings panel\n"
    assert screenshot.parse_screenshot_line(text) == "the settings panel"


def test_a_missing_screenshot_line_defaults_to_the_board():
    assert (
        screenshot.parse_screenshot_line("did the thing, no line here") == screenshot.DEFAULT_VIEW
    )


def test_no_result_text_at_all_defaults_to_the_board():
    assert screenshot.parse_screenshot_line(None) == screenshot.DEFAULT_VIEW


def test_only_the_last_screenshot_line_counts():
    # a fix round's own summary can carry one too - the one describing the diff as it stands wins
    text = "SCREENSHOT: the old view\n...\nSCREENSHOT: the new view"
    assert screenshot.parse_screenshot_line(text) == "the new view"


def test_the_marker_is_matched_case_insensitively():
    assert screenshot.parse_screenshot_line("screenshot: the board itself") == "the board itself"


# -- take_screenshot: the flow, board and capture faked ------------------------


class _FakeBoard:
    """stands in for ThrowawayBoard - a context manager exposing base_url, nothing runs for real"""

    entered_with = []

    def __init__(self, worktree_path):
        self.worktree_path = worktree_path
        self.base_url = "http://127.0.0.1:1"

    def __enter__(self):
        _FakeBoard.entered_with.append(self.worktree_path)
        return self

    def __exit__(self, *exc_info):
        return False


class _RaisingBoard:
    def __init__(self, worktree_path):
        raise screenshot.ScreenshotUnavailable("the board never answered /health")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "board.db")
    board = s.create_board("b")
    card = s.create_card(board["id"], None, "changed the panel css")
    yield s, card["id"]
    s.close()


def test_a_clean_run_attaches_exactly_one_png(store):
    st, card_id = store
    seen_views = []

    def _capture(base_url, view):
        seen_views.append((base_url, view))
        return b"\x89PNG\r\n\x1a\nfake-bytes"

    result = screenshot.take_screenshot(
        st,
        card_id,
        "/some/worktree",
        "SCREENSHOT: the board",
        board_factory=_FakeBoard,
        capture=_capture,
    )
    assert result.attached
    assert result.filename == "screenshot-1.png"
    assert seen_views == [("http://127.0.0.1:1", "the board")]

    attachments = st.list_attachments(card_id)
    assert len(attachments) == 1
    assert attachments[0]["media_type"] == "image/png"
    assert st.get_attachment_blob(attachments[0]["id"]) == b"\x89PNG\r\n\x1a\nfake-bytes"


def test_repeat_runs_number_their_screenshots(store):
    st, card_id = store
    for _ in range(2):
        screenshot.take_screenshot(
            st,
            card_id,
            "/some/worktree",
            None,
            board_factory=_FakeBoard,
            capture=lambda *a: b"png-bytes",
        )
    filenames = sorted(a["filename"] for a in st.list_attachments(card_id))
    assert filenames == ["screenshot-1.png", "screenshot-2.png"]


def test_a_board_that_never_comes_up_is_a_note_not_a_crash(store):
    st, card_id = store
    result = screenshot.take_screenshot(
        st, card_id, "/some/worktree", None, board_factory=_RaisingBoard, capture=lambda *a: b""
    )
    assert not result.attached
    assert result.note and "never answered" in result.note
    assert st.list_attachments(card_id) == []


def test_a_capture_that_raises_is_a_note_not_a_crash(store):
    st, card_id = store

    def _boom(base_url, view):
        raise RuntimeError("playwright is not installed")

    result = screenshot.take_screenshot(
        st, card_id, "/some/worktree", None, board_factory=_FakeBoard, capture=_boom
    )
    assert not result.attached
    assert "playwright is not installed" in result.note
    assert st.list_attachments(card_id) == []


# -- latest_screenshot ----------------------------------------------------------


def test_latest_screenshot_ignores_other_attachment_kinds(store):
    st, card_id = store
    st.add_attachment(card_id, "attempt-1.diff", "text/x-diff", b"diff")
    st.add_attachment(card_id, "screenshot-1.png", "image/png", b"one")
    st.add_attachment(card_id, "screenshot-2.png", "image/png", b"two")
    card = st.get_card(card_id)
    shot = screenshot.latest_screenshot(card)
    assert shot["filename"] == "screenshot-2.png"


def test_latest_screenshot_is_none_without_one(store):
    st, card_id = store
    assert screenshot.latest_screenshot(st.get_card(card_id)) is None


# -- lifecycle wiring: gated on the diff, never blocks the card ----------------


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "smortboard").mkdir()
    (path / "smortboard" / "ui").mkdir()
    (path / "smortboard" / "ui" / "board.js").write_text("old\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "first")
    return path


class _CommittingBackend:
    """the fake worker: actually edits and commits a file in the worktree it is handed, the way a
    real card run would - lifecycle's screenshot gate reads the real diff, so the diff has to be
    real too."""

    name = "fake"

    def __init__(self, path_in_worktree, result_text):
        self.path_in_worktree = path_in_worktree
        self.result_text = result_text

    def run_card(self, store, card_id, worktree_path, prompt, settings_path, **kwargs):
        target = Path(worktree_path) / self.path_in_worktree
        target.write_text("new\n")
        _git(worktree_path, "add", "-A")
        _git(worktree_path, "commit", "-q", "-m", "card change")
        return RunResult(
            subtype="success",
            is_error=False,
            blocked_reason_code=None,
            session_id="s",
            total_cost_usd=0.1,
            num_turns=1,
            result_text=self.result_text,
        )


@pytest.fixture
def board(tmp_path, repo):
    store = Store(tmp_path / "board.db")
    b = store.create_board("b")
    r = store.create_repo(
        b["id"], "repo", str(repo), "main", test_command="true", image="img:latest"
    )
    card = store.create_card(b["id"], r["id"], "restyle the panel", leases=["smortboard/ui/**"])
    yield store, card["id"]
    store.close()


def _stub_gates(monkeypatch):
    monkeypatch.setattr(
        lifecycle, "run_test_gate", lambda *a, **k: GateResult(True, "true", 0, "ok")
    )
    monkeypatch.setattr(
        lifecycle, "run_review", lambda *a, **k: ReviewResult(approved=True, findings=[])
    )
    monkeypatch.setattr(
        lifecycle,
        "open_merge_request",
        lambda *a, **k: MergeRequestResult(opened=True, branch="card/x", url="https://x/pull/1"),
    )


def test_a_ui_touching_card_gets_exactly_one_screenshot_attached(board, monkeypatch):
    store, card_id = board
    _stub_gates(monkeypatch)
    calls = []

    def _fake_take_screenshot(st, cid, worktree_path, result_text, **kwargs):
        calls.append((cid, result_text))
        st.add_attachment(cid, "screenshot-1.png", "image/png", b"png")
        return screenshot.ScreenshotResult(attached=True, filename="screenshot-1.png")

    monkeypatch.setattr(lifecycle, "take_screenshot", _fake_take_screenshot)
    backend = _CommittingBackend("smortboard/ui/board.js", "did it\n\nSCREENSHOT: the panel")
    result = lifecycle.run_card_lifecycle(store, card_id, backend=backend)

    assert result.phase == "opened"
    assert calls == [(card_id, "did it\n\nSCREENSHOT: the panel")]
    assert len(store.list_attachments(card_id)) == 1


def test_a_non_ui_card_never_calls_take_screenshot(tmp_path, monkeypatch):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "thing.py").write_text("x = 1\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "first")

    store = Store(tmp_path / "board.db")
    b = store.create_board("b")
    r = store.create_repo(
        b["id"], "repo", str(path), "main", test_command="true", image="img:latest"
    )
    card = store.create_card(b["id"], r["id"], "fix a backend bug", leases=["thing.py"])

    _stub_gates(monkeypatch)
    called = []
    monkeypatch.setattr(lifecycle, "take_screenshot", lambda *a, **k: called.append(1))
    backend = _CommittingBackend("thing.py", "did it")
    result = lifecycle.run_card_lifecycle(store, card["id"], backend=backend)

    assert result.phase == "opened"
    assert called == []
    assert store.list_attachments(card["id"]) == []
    store.close()


def test_a_screenshot_failure_leaves_a_note_and_still_opens_the_pr(board, monkeypatch):
    store, card_id = board
    _stub_gates(monkeypatch)
    monkeypatch.setattr(
        lifecycle,
        "take_screenshot",
        lambda *a, **k: screenshot.ScreenshotResult(
            attached=False, note="playwright is not installed"
        ),
    )
    backend = _CommittingBackend("smortboard/ui/board.js", "did it")
    result = lifecycle.run_card_lifecycle(store, card_id, backend=backend)

    assert result.phase == "opened"
    assert result.pr_url == "https://x/pull/1"
    notes = [c["body"] for c in store.list_comments(card_id)]
    assert any("screenshot not attached" in n and "playwright is not installed" in n for n in notes)
    store.close()


# -- real playwright against a faked http server -------------------------------


class _StaticHandler(http.server.BaseHTTPRequestHandler):
    """the "server faked": a plain page at /ui/index.html and a /health playwright never needs"""

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            return
        body = b"<html><body><h1 id='marker'>fake board</h1></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # the test output does not need an access log


def test_capture_takes_a_real_screenshot_of_a_faked_server():
    pytest.importorskip("playwright.sync_api")
    port = screenshot._free_port()
    httpd = http.server.HTTPServer(("127.0.0.1", port), _StaticHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        png = screenshot._capture(f"http://127.0.0.1:{port}", screenshot.DEFAULT_VIEW)
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_capture_takes_a_real_screenshot_of_a_faked_server_authenticated_board():
    pytest.importorskip("playwright.sync_api")
    with screenshot.ThrowawayBoard(REPO_ROOT) as board:
        png = screenshot._capture(board.base_url, screenshot.DEFAULT_VIEW, board.api_key)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


# -- the real throwaway board subprocess ---------------------------------------


def test_throwaway_board_serves_the_worktree_and_tears_down_after():
    # /workspace is itself a valid smortboard checkout, so it stands in for a card's worktree here
    with screenshot.ThrowawayBoard(REPO_ROOT) as board:
        db_path = Path(board._db_path)
        assert db_path.exists()
        with urllib.request.urlopen(f"{board.base_url}/health", timeout=5) as resp:
            assert resp.status == 200
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(f"{board.base_url}/api/boards", timeout=5)
        assert refused.value.code == 401
        request = urllib.request.Request(
            f"{board.base_url}/api/boards", headers={KEY_HEADER: board.api_key}
        )
        with urllib.request.urlopen(request, timeout=5) as resp:
            boards = json.load(resp)
        assert len(boards) == 1
        assert boards[0]["name"] == "ui review — invented content"
        request = urllib.request.Request(
            f"{board.base_url}/api/boards/{boards[0]['id']}/cards",
            headers={KEY_HEADER: board.api_key},
        )
        with urllib.request.urlopen(request, timeout=5) as resp:
            cards = json.load(resp)
        assert {card["status"] for card in cards} == {"todo", "checking", "accepted", "rejected"}
        assert all(card["repo_id"] is None for card in cards)
    assert not db_path.exists()


@pytest.mark.parametrize("target", ["file", "folder", "ui", "package"])
def test_ui_overlay_refuses_symlinks_without_reading_their_targets(tmp_path, target):
    worktree = tmp_path / "worktree"
    ui = worktree / "smortboard" / "ui"
    ui.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "marker.txt").write_text("harmless host fixture")
    if target == "file":
        (ui / "linked.txt").symlink_to(outside / "marker.txt")
    elif target == "folder":
        (ui / "linked").symlink_to(outside, target_is_directory=True)
    elif target == "ui":
        ui.rmdir()
        ui.symlink_to(outside, target_is_directory=True)
    else:
        ui.rmdir()
        ui.parent.rmdir()
        ui.parent.symlink_to(outside, target_is_directory=True)
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(screenshot.ScreenshotUnavailable, match="symlink"):
        screenshot._stage_host_backend(worktree, stage)
    assert list(stage.iterdir()) == []


def test_staging_uses_trusted_backend_and_only_the_worktree_ui(tmp_path):
    ui = tmp_path / "worktree" / "smortboard" / "ui"
    ui.mkdir(parents=True)
    (ui / "board.js").write_text("synthetic-ui-change")
    (ui.parent / "cli.py").write_text("raise RuntimeError('untrusted backend must not run')")
    stage = tmp_path / "stage"
    stage.mkdir()
    screenshot._stage_host_backend(ui.parents[1], stage)
    assert (stage / "smortboard" / "ui" / "board.js").read_text() == "synthetic-ui-change"
    assert (stage / "smortboard" / "cli.py").read_bytes() == (
        REPO_ROOT / "smortboard" / "cli.py"
    ).read_bytes()


def test_startup_timeout_stops_before_draining_and_removes_temporary_files(tmp_path, monkeypatch):
    events = []

    class Process:
        stdout = stderr = None

        def poll(self):
            return None

        def terminate(self):
            events.append("terminated")

        def communicate(self, timeout):
            assert events == ["terminated"]
            assert timeout == 5
            events.append("drained")
            return "", "synthetic startup failure"

    monkeypatch.setattr(screenshot, "_stage_host_backend", lambda *args: None)
    monkeypatch.setattr(screenshot.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(screenshot, "_wait_until_ready", lambda *args: False)
    board = screenshot.ThrowawayBoard(tmp_path)
    with pytest.raises(screenshot.ScreenshotUnavailable, match="synthetic startup failure"):
        board.__enter__()
    assert events == ["terminated", "drained"]
    assert not Path(board._db_path).exists()
    assert not Path(board._stage_dir).exists()


def test_stop_kills_a_process_that_ignores_termination(tmp_path):
    events = []

    class Process:
        def poll(self):
            return None

        def terminate(self):
            events.append("terminated")

        def kill(self):
            events.append("killed")

        def communicate(self, timeout):
            assert timeout == 5
            if events == ["terminated"]:
                raise subprocess.TimeoutExpired("synthetic board", timeout)
            events.append("drained")
            return "", "stopped"

    board = screenshot.ThrowawayBoard(tmp_path)
    board._process = Process()
    assert board._stop() == "stopped"
    assert events == ["terminated", "killed", "drained"]
    Path(board._db_path).unlink()


@pytest.mark.parametrize(
    ("request_url", "method", "allowed"),
    [
        ("http://127.0.0.1:8000/ui/index.html", "GET", True),
        ("http://127.0.0.1:8000/api/boards", "GET", True),
        ("http://127.0.0.1:8000/api/boards/synthetic/cards", "GET", True),
        ("http://127.0.0.1:8001/ui/index.html", "GET", False),
        ("https://127.0.0.1:8000/ui/index.html", "GET", False),
        ("http://localhost:8000/ui/index.html", "GET", False),
        ("https://outside.invalid/fixture", "GET", False),
        ("http://127.0.0.1:8000/api/folders", "GET", False),
        ("http://127.0.0.1:8000/api/online-repos", "GET", False),
        ("http://127.0.0.1:8000/api/export", "GET", False),
        ("http://127.0.0.1:8000/api/boards", "POST", False),
        ("http://127.0.0.1:8000/api/cards/synthetic/run", "POST", False),
    ],
)
def test_capture_requests_are_read_only_and_confined_to_the_synthetic_board(
    request_url, method, allowed
):
    assert screenshot._capture_request_ok("http://127.0.0.1:8000", request_url, method) is allowed


def test_capture_authenticates_and_waits_for_content_with_network_guards(monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    seen = {}

    class Context:
        def route(self, pattern, handler):
            seen["route"] = handler

        def route_web_socket(self, pattern, handler):
            seen["websocket"] = handler

        def add_cookies(self, cookies):
            seen["cookies"] = cookies

        def new_page(self):
            return self

        def set_default_timeout(self, timeout):
            pass

        def goto(self, url):
            seen["url"] = url

        def locator(self, selector):
            assert selector == ".card-strip"
            return SimpleNamespace(first=self)

        def wait_for(self, state):
            seen["wait"] = state

        def screenshot(self, full_page):
            assert full_page
            return b"synthetic-png"

    class Browser:
        def new_context(self, **kwargs):
            seen["context"] = kwargs
            return Context()

        def close(self):
            seen["closed"] = True

    class Playwright:
        chromium = None

        def __enter__(self):
            self.chromium = self
            return self

        def __exit__(self, *args):
            return False

        def launch(self, **kwargs):
            seen["launch"] = kwargs
            return Browser()

    monkeypatch.setattr(playwright, "sync_playwright", Playwright)
    assert (
        screenshot._capture("http://127.0.0.1:8000", screenshot.DEFAULT_VIEW, "synthetic-key")
        == b"synthetic-png"
    )
    assert seen["launch"]["headless"] is True
    assert seen["context"] == {"service_workers": "block", "accept_downloads": False}
    assert seen["url"] == "http://127.0.0.1:8000/ui/index.html"
    assert seen["cookies"] == [
        {
            "name": cookie_name(8000),
            "value": "synthetic-key",
            "url": "http://127.0.0.1:8000",
            "httpOnly": True,
            "sameSite": "Strict",
        }
    ]
    assert seen["wait"] == "visible"
    assert seen["closed"]
    route_events = []
    seen["route"](
        SimpleNamespace(
            request=SimpleNamespace(url="http://127.0.0.1:8001/ui/index.html", method="GET"),
            abort=lambda: route_events.append("blocked"),
            continue_=lambda: route_events.append("allowed"),
        )
    )
    assert route_events == ["blocked"]
    seen["websocket"](SimpleNamespace(close=lambda: route_events.append("websocket blocked")))
    assert route_events[-1] == "websocket blocked"
