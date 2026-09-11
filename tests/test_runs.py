"""running a card from the board: the registry, and the two endpoints over it.

The lifecycle itself is replaced everywhere here - what is under test is that a run happens off the
request thread, that a card cannot be started twice, and that a thread dying becomes visible state
rather than silence.
"""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from smortboard.lifecycle import LifecycleResult
from smortboard.server import runs as runs_module
from smortboard.server.app import build_server
from smortboard.store import Store


def _wait(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def db(tmp_path):
    store = Store(tmp_path / "board.db")
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "a card")
    path = store.path
    store.close()
    return path, card["id"]


def test_a_run_happens_on_its_own_thread_with_its_own_store(db):
    """the store's connection belongs to the thread that opened it, so the run must open its own"""
    path, card_id = db
    seen = {}

    def _runner(store, cid, **kwargs):
        seen["thread"] = threading.current_thread().name
        seen["store_is_new"] = store.path == path
        store.add_comment(cid, author="t", body="the run could write")
        return LifecycleResult(card_id=cid, phase="opened", pr_url="https://x/1")

    registry = runs_module.RunRegistry(path)
    state = registry.start(card_id, runner=_runner)
    assert _wait(lambda: not state.running)
    assert seen["thread"] != threading.current_thread().name
    assert seen["store_is_new"]
    assert state.pr_url == "https://x/1"
    assert state.phase == "opened"


def test_a_card_cannot_be_started_twice(db):
    path, card_id = db
    release = threading.Event()

    def _slow(store, cid, **kwargs):
        release.wait(timeout=5)
        return LifecycleResult(card_id=cid, phase="opened")

    registry = runs_module.RunRegistry(path)
    first = registry.start(card_id, runner=_slow)
    second = registry.start(card_id, runner=_slow)
    assert second is first  # the same run, not a second agent in the same worktree
    assert len(registry.active()) == 1
    release.set()
    assert _wait(lambda: not first.running)


def test_a_finished_card_can_be_started_again(db):
    path, card_id = db
    registry = runs_module.RunRegistry(path)
    first = registry.start(
        card_id, runner=lambda s, c, **k: LifecycleResult(card_id=c, phase="blocked")
    )
    assert _wait(lambda: not first.running)
    second = registry.start(
        card_id, runner=lambda s, c, **k: LifecycleResult(card_id=c, phase="opened")
    )
    assert second is not first
    assert _wait(lambda: not second.running)


def test_a_thread_that_dies_becomes_visible_state_not_silence(db):
    path, card_id = db

    def _explode(store, cid, **kwargs):
        raise RuntimeError("docker went away")

    registry = runs_module.RunRegistry(path)
    state = registry.start(card_id, runner=_explode)
    assert _wait(lambda: not state.running)
    assert state.phase == "refused"
    assert "docker went away" in state.error


class _FakeProcess:
    """never a real process - proves RunRegistry.stop() reaches whatever run_process registers"""

    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        pass

    def wait(self, timeout=None):
        pass


def test_stopping_a_card_with_no_run_is_a_409(db):
    path, card_id = db
    registry = runs_module.RunRegistry(path)
    with pytest.raises(runs_module.RunNotActiveError):
        registry.stop(card_id)


def test_stop_terminates_the_registered_process_and_marks_the_run_stopping(db):
    """the running lifecycle's own thread reads stop_requested() and on_process() - this proves
    RunRegistry actually wires both through to whatever `runner` (here, a fake) is handed"""
    path, card_id = db
    process = _FakeProcess()
    registered = threading.Event()
    seen_stopping = threading.Event()
    release = threading.Event()

    def _runner(store, cid, on_phase=None, stop_requested=None, on_process=None, **kwargs):
        on_process(runs_module.ProcessHandle(process))
        registered.set()
        # the run only learns it was stopped on its NEXT check, same as the real lifecycle
        while not release.is_set():
            if stop_requested():
                seen_stopping.set()
                break
            release.wait(timeout=0.05)
        return LifecycleResult(card_id=cid, phase="stopped")

    registry = runs_module.RunRegistry(path)
    state = registry.start(card_id, runner=_runner)
    assert registered.wait(timeout=5)

    registry.stop(card_id)
    assert process.terminated
    assert _wait(lambda: seen_stopping.is_set())
    release.set()
    assert _wait(lambda: not state.running)
    assert state.phase == "stopped"


def test_stopping_a_finished_card_is_a_409(db):
    path, card_id = db
    registry = runs_module.RunRegistry(path)
    state = registry.start(
        card_id, runner=lambda s, c, **k: LifecycleResult(card_id=c, phase="opened")
    )
    assert _wait(lambda: not state.running)
    with pytest.raises(runs_module.RunNotActiveError):
        registry.stop(card_id)


def test_the_phase_is_reported_while_the_run_is_still_going(db):
    path, card_id = db
    at_testing = threading.Event()
    release = threading.Event()

    def _runner(store, cid, on_phase=None, **kwargs):
        on_phase("testing")
        at_testing.set()
        release.wait(timeout=5)
        return LifecycleResult(card_id=cid, phase="opened")

    registry = runs_module.RunRegistry(path)
    state = registry.start(card_id, runner=_runner)
    assert at_testing.wait(timeout=5)
    assert state.phase == "testing"
    assert state.running
    assert state.as_dict()["elapsed_seconds"] >= 0
    release.set()
    assert _wait(lambda: not state.running)


# -- over http ----------------------------------------------------------------


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runs_module,
        "run_card_lifecycle",
        lambda store, cid, **k: LifecycleResult(card_id=cid, phase="opened", pr_url="https://x/9"),
    )
    ready = threading.Event()
    holder = {}

    def _serve():
        store = Store(tmp_path / "board.db")
        board = store.create_board("b")
        holder["card_id"] = store.create_card(board["id"], None, "a card")["id"]
        srv = build_server(store, port=0, host="127.0.0.1")
        holder["server"] = srv
        ready.set()
        srv.serve_forever()
        store.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    ready.wait()
    port = holder["server"].server_address[1]
    yield f"http://127.0.0.1:{port}", holder["card_id"]
    holder["server"].shutdown()
    thread.join()
    holder["server"].server_close()


def _call(url, method="GET"):
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else None)


def test_posting_a_run_answers_immediately_with_202(server):
    base, card_id = server
    status, body = _call(f"{base}/api/cards/{card_id}/run", method="POST")
    assert status == 202  # accepted and running elsewhere, not finished
    assert body["card_id"] == card_id


def test_the_run_endpoint_reports_where_the_card_got_to(server):
    base, card_id = server
    _call(f"{base}/api/cards/{card_id}/run", method="POST")
    assert _wait(lambda: _call(f"{base}/api/cards/{card_id}/run")[1]["running"] is False)
    _, body = _call(f"{base}/api/cards/{card_id}/run")
    assert body["phase"] == "opened"
    assert body["pr_url"] == "https://x/9"


def test_a_card_that_never_ran_reports_no_phase_rather_than_404(server):
    base, card_id = server
    status, body = _call(f"{base}/api/cards/{card_id}/run")
    assert status == 200
    assert body["phase"] is None and body["running"] is False


def test_stopping_a_card_with_no_run_is_a_409_over_http(server):
    base, card_id = server
    status, body = _call(f"{base}/api/cards/{card_id}/stop", method="POST")
    assert status == 409
    assert "error" in body


def test_stopping_an_unknown_card_is_a_404(server):
    base, _ = server
    status, _body = _call(f"{base}/api/cards/nope/stop", method="POST")
    assert status == 404


def test_running_an_unknown_card_is_a_404_before_a_thread_is_started(server):
    base, _ = server
    status, _body = _call(f"{base}/api/cards/nope/run", method="POST")
    assert status == 404


def test_stopping_a_live_run_over_http_ends_it(tmp_path, monkeypatch):
    """POST /stop while a run is going: 200, the process is terminated, and the run finishes"""
    process = _FakeProcess()
    registered = threading.Event()
    release = threading.Event()

    def _slow_lifecycle(store, cid, on_phase=None, stop_requested=None, on_process=None, **kwargs):
        on_process(runs_module.ProcessHandle(process))
        registered.set()
        while not release.is_set() and not (stop_requested and stop_requested()):
            release.wait(timeout=0.05)
        return LifecycleResult(card_id=cid, phase="stopped" if stop_requested() else "opened")

    monkeypatch.setattr(runs_module, "run_card_lifecycle", _slow_lifecycle)
    ready = threading.Event()
    holder = {}

    def _serve():
        store = Store(tmp_path / "board.db")
        board = store.create_board("b")
        holder["card_id"] = store.create_card(board["id"], None, "a card")["id"]
        srv = build_server(store, port=0, host="127.0.0.1")
        holder["server"] = srv
        ready.set()
        srv.serve_forever()
        store.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    ready.wait()
    port = holder["server"].server_address[1]
    base = f"http://127.0.0.1:{port}"
    card_id = holder["card_id"]

    try:
        _call(f"{base}/api/cards/{card_id}/run", method="POST")
        assert registered.wait(timeout=5)

        status, body = _call(f"{base}/api/cards/{card_id}/stop", method="POST")
        assert status == 200
        assert process.terminated
        assert _wait(lambda: _call(f"{base}/api/cards/{card_id}/run")[1]["running"] is False)
        _, final = _call(f"{base}/api/cards/{card_id}/run")
        assert final["phase"] == "stopped"
    finally:
        release.set()
        holder["server"].shutdown()
        thread.join()
        holder["server"].server_close()


def test_the_board_lists_what_is_running(server):
    base, card_id = server
    _call(f"{base}/api/cards/{card_id}/run", method="POST")
    assert _wait(lambda: _call(f"{base}/api/runs")[1] == [])  # it finishes and drops off the list
    assert _call(f"{base}/api/runs")[0] == 200


def test_readiness_says_what_is_missing_rather_than_just_no(monkeypatch):
    monkeypatch.setattr(runs_module, "READINESS_TTL_SECONDS", 0)
    monkeypatch.setattr("smortboard.exec.backends.docker_available", lambda: False)
    monkeypatch.setattr("smortboard.exec.backends.card_image_available", lambda i=None: False)
    monkeypatch.setattr("smortboard.exec.backends.card_token_available", lambda p=None: False)
    answer = runs_module.Readiness().check()
    assert answer["ready"] is False
    assert any("Docker" in m for m in answer["missing"])
    assert any("setup-token" in m for m in answer["missing"])


def test_a_missing_card_image_is_named_before_a_card_crashes_on_it(monkeypatch):
    """nothing builds the image, and without this the failure is an opaque CRASH minutes in"""
    monkeypatch.setattr(runs_module, "READINESS_TTL_SECONDS", 0)
    monkeypatch.setattr("smortboard.exec.backends.docker_available", lambda: True)
    monkeypatch.setattr("smortboard.exec.backends.card_image_available", lambda i=None: False)
    monkeypatch.setattr("smortboard.exec.backends.card_token_available", lambda p=None: True)
    answer = runs_module.Readiness().check()
    assert answer["ready"] is False
    assert answer["image"] is False
    assert any("docker build -f docker/card.Dockerfile" in m for m in answer["missing"])


def test_a_missing_image_is_not_reported_while_docker_itself_is_down(monkeypatch):
    monkeypatch.setattr(runs_module, "READINESS_TTL_SECONDS", 0)
    monkeypatch.setattr("smortboard.exec.backends.docker_available", lambda: False)
    monkeypatch.setattr("smortboard.exec.backends.card_image_available", lambda i=None: False)
    monkeypatch.setattr("smortboard.exec.backends.card_token_available", lambda p=None: True)
    answer = runs_module.Readiness().check()
    assert not any("card image" in m for m in answer["missing"])


def test_readiness_is_cached_because_the_probes_are_not_free(monkeypatch):
    calls = []
    monkeypatch.setattr("smortboard.exec.backends.card_image_available", lambda i=None: True)
    monkeypatch.setattr(
        "smortboard.exec.backends.docker_available", lambda: (calls.append(1), True)[1]
    )
    monkeypatch.setattr("smortboard.exec.backends.card_token_available", lambda p=None: True)
    readiness = runs_module.Readiness()
    readiness.check()
    readiness.check()
    assert len(calls) == 1


def test_a_repo_can_be_created_from_the_board(server):
    """without this there was no way to give a card a repo, so the run button could never work"""
    base, card_id = server
    boards = _call(f"{base}/api/boards")[1]
    board_id = boards[0]["id"]
    status, repo = _call_json(
        f"{base}/api/boards/{board_id}/repos",
        {"name": "r", "path": "/tmp/r", "default_branch": "main", "test_command": "uv run pytest"},
    )
    assert status == 201
    assert repo["test_command"] == "uv run pytest"
    assert _call(f"{base}/api/boards/{board_id}/repos")[1][0]["id"] == repo["id"]

    status, card = _call_json(f"{base}/api/cards/{card_id}", {"repo_id": repo["id"]}, "PATCH")
    assert status == 200 and card["repo_id"] == repo["id"]


def _call_json(url, body, method="POST"):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else None)
