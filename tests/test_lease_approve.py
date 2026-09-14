"""one-click lease approval from the inbox: extracting the wanted paths from telemetry, and the
POST /api/cards/<id>/lease/approve route that widens exactly those paths and resumes the card.

fakes match the same shapes test_attention.py and test_manual_run_leases.py already use for
answer_card's `runs` collaborator - no container, no model, no docker.
"""

import json
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from smortboard.attention import AnswerRefused, approve_lease, attention_rows, lease_conflict_wants
from smortboard.server.app import build_server
from smortboard.store.api import Store
from smortboard.store.errors import NotFoundError


def _call(url, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else None)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "board.db") as s:
        yield s


def _worker_result(denials=None):
    return {
        "total_cost_usd": 0.1,
        "num_turns": 1,
        "permission_denials": denials or [],
        "modelUsage": {},
        "result": "done",
    }


def _refused_attempt(store, card_id, denials):
    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(card_id, "result", _worker_result(denials=denials))


class _FakeState:
    def __init__(self, card_id, running=False):
        self.card_id = card_id
        self.running = running

    def as_dict(self):
        return {"card_id": self.card_id, "running": self.running, "phase": "running"}


class _FakeRuns:
    """matches RunRegistry's get()/start()/active() shape - see server/runs.py"""

    def __init__(self, running_card_id=None, active_ids=()):
        self.running_card_id = running_card_id
        self.active_ids = active_ids
        self.started = []

    def get(self, card_id):
        if card_id == self.running_card_id:
            return _FakeState(card_id, running=True)
        return None

    def active(self):
        return [SimpleNamespace(card_id=cid, running=True) for cid in self.active_ids]

    def start(self, card_id):
        self.started.append(card_id)
        return _FakeState(card_id, running=True)


def _board_and_card(store, **card_kwargs):
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "r", "/tmp/r", "main")
    card = store.create_card(board["id"], repo["id"], "a card", **card_kwargs)
    return board, repo, card


# ---- lease_conflict_wants -----------------------------------------------------------------------


def test_wants_keeps_only_edit_write_notebookedit_and_strips_workspace_prefix(store):
    _, _, card = _board_and_card(store)
    denials = [
        {"tool_name": "Bash", "tool_input": {"command": "git push"}},
        {"tool_name": "Edit", "tool_input": {"file_path": "/workspace/smortboard/ui/board.js"}},
        {"tool_name": "Write", "tool_input": {"file_path": "/workspace/docs/notes.md"}},
        {"tool_name": "NotebookEdit", "tool_input": {"file_path": "/workspace/nb.ipynb"}},
        {"tool_name": "Read", "tool_input": {"file_path": "/workspace/ignored.py"}},
    ]
    _refused_attempt(store, card["id"], denials)

    assert lease_conflict_wants(store, card["id"]) == [
        "smortboard/ui/board.js",
        "docs/notes.md",
        "nb.ipynb",
    ]


def test_wants_deduplicates_and_only_looks_at_the_latest_attempt(store):
    _, _, card = _board_and_card(store)
    _refused_attempt(
        store,
        card["id"],
        [{"tool_name": "Edit", "tool_input": {"file_path": "/workspace/old.py"}}],
    )
    store.append_event(card["id"], "worker_summary", {"text": "blocked"})
    _refused_attempt(
        store,
        card["id"],
        [
            {"tool_name": "Edit", "tool_input": {"file_path": "/workspace/a.py"}},
            {"tool_name": "Edit", "tool_input": {"file_path": "/workspace/a.py"}},
        ],
    )

    assert lease_conflict_wants(store, card["id"]) == ["a.py"]


def test_wants_is_empty_with_no_attempts_or_no_refusals(store):
    _, _, card = _board_and_card(store)
    assert lease_conflict_wants(store, card["id"]) == []
    _refused_attempt(store, card["id"], [])
    assert lease_conflict_wants(store, card["id"]) == []


def test_attention_rows_carries_wants_only_on_lease_conflict_rows(store):
    _, _, card = _board_and_card(store, leases=["src/**"])
    store.update_card(card["id"], blocked_reason_code="LEASE_CONFLICT", review_flag=True)
    _refused_attempt(
        store,
        card["id"],
        [{"tool_name": "Edit", "tool_input": {"file_path": "/workspace/ui/board.js"}}],
    )
    rows = attention_rows(store)
    assert rows[0]["wants"] == ["ui/board.js"]


# ---- approve_lease --------------------------------------------------------------------------------


def test_approve_lease_widens_and_resumes(store):
    _, _, card = _board_and_card(store, leases=["src/**"])
    store.update_card(card["id"], blocked_reason_code="LEASE_CONFLICT", review_flag=True)
    runs = _FakeRuns()

    result = approve_lease(store, runs, card["id"], ["ui/board.js"])

    assert result["running"] is True
    assert runs.started == [card["id"]]
    updated = store.get_card(card["id"])
    assert sorted(g["path_glob"] for g in updated["leases"]) == ["src/**", "ui/board.js"]
    assert updated["blocked_reason_code"] is None
    operator_comments = [c for c in updated["comments"] if c["author"] == "operator"]
    assert "ui/board.js" in operator_comments[-1]["body"]


def test_approve_lease_keeps_existing_globs_and_dedupes(store):
    _, _, card = _board_and_card(store, leases=["src/**", "ui/board.js"])
    store.update_card(card["id"], blocked_reason_code="LEASE_CONFLICT", review_flag=True)
    runs = _FakeRuns()

    approve_lease(store, runs, card["id"], ["ui/board.js", "ui/new.js"])

    updated = store.get_card(card["id"])
    assert sorted(g["path_glob"] for g in updated["leases"]) == [
        "src/**",
        "ui/board.js",
        "ui/new.js",
    ]


def test_approve_lease_400_on_empty_or_invalid_paths(store):
    _, _, card = _board_and_card(store)
    store.update_card(card["id"], blocked_reason_code="LEASE_CONFLICT", review_flag=True)
    runs = _FakeRuns()

    with pytest.raises(ValueError):
        approve_lease(store, runs, card["id"], [])
    with pytest.raises(ValueError):
        approve_lease(store, runs, card["id"], None)


def test_approve_lease_400_on_a_bad_glob(store):
    _, _, card = _board_and_card(store)
    store.update_card(card["id"], blocked_reason_code="LEASE_CONFLICT", review_flag=True)
    runs = _FakeRuns()

    with pytest.raises(ValueError):
        approve_lease(store, runs, card["id"], ["../escape.py"])


def test_approve_lease_404_on_an_unknown_card(store):
    runs = _FakeRuns()
    with pytest.raises(NotFoundError):
        approve_lease(store, runs, "nope", ["a.py"])


def test_approve_lease_409_when_not_blocked_on_lease_conflict(store):
    _, _, card = _board_and_card(store)
    store.update_card(card["id"], blocked_reason_code="CRASH", review_flag=True)
    runs = _FakeRuns()

    with pytest.raises(AnswerRefused):
        approve_lease(store, runs, card["id"], ["a.py"])


def test_approve_lease_widens_but_leaves_lease_conflict_when_resume_is_refused(store):
    """another card is running with an overlapping lease: the widen must stick even though the
    resume itself is refused, so a later plain answer can resume once that card finishes"""
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "r", "/tmp/r", "main")
    running = store.create_card(board["id"], repo["id"], "running one", leases=["ui/**"])
    card = store.create_card(board["id"], repo["id"], "wants to widen", leases=["src/**"])
    store.update_card(card["id"], blocked_reason_code="LEASE_CONFLICT", review_flag=True)
    runs = _FakeRuns(active_ids=[running["id"]])

    with pytest.raises(AnswerRefused):
        approve_lease(store, runs, card["id"], ["ui/board.js"])

    updated = store.get_card(card["id"])
    assert sorted(g["path_glob"] for g in updated["leases"]) == ["src/**", "ui/board.js"]
    # still LEASE_CONFLICT - the inbox answer keeps working for it
    assert updated["blocked_reason_code"] == "LEASE_CONFLICT"
    assert runs.started == []


# ---- the route itself, over real HTTP -------------------------------------------------------------
# only the cases that never touch RunRegistry.start (which would spin up a real card lifecycle) -
# the happy path through approve_lease is already proven above with a fake runs registry.


def _run_server(tmp_path, setup):
    """spins up a real server with the store built and seeded inside its own thread - sqlite3
    connections are bound to the thread that opened them (see build_server's own note), so setup
    has to happen there too, not from the test thread."""
    ready = threading.Event()
    holder = {}

    def _serve():
        store = Store(tmp_path / "board.db")
        holder["card_id"] = setup(store)
        srv = build_server(store, port=0, host="127.0.0.1")
        holder["server"] = srv
        ready.set()
        srv.serve_forever()
        store.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    ready.wait()
    try:
        yield (
            holder["server"],
            holder.get("card_id"),
            f"http://127.0.0.1:{holder['server'].server_address[1]}",
        )
    finally:
        holder["server"].shutdown()
        thread.join(timeout=2)


def _seed_lease_conflict(store):
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "r", "/tmp/r", "main")
    card = store.create_card(board["id"], repo["id"], "a card")
    store.update_card(card["id"], blocked_reason_code="LEASE_CONFLICT", review_flag=True)
    return card["id"]


def _seed_crash(store):
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "r", "/tmp/r", "main")
    card = store.create_card(board["id"], repo["id"], "a card")
    store.update_card(card["id"], blocked_reason_code="CRASH", review_flag=True)
    return card["id"]


def test_route_400_on_empty_paths(tmp_path):
    for _, card_id, base in _run_server(tmp_path, _seed_lease_conflict):
        status, body = _call(f"{base}/api/cards/{card_id}/lease/approve", "POST", {"paths": []})
        assert status == 400
        assert "paths" in body["error"]


def test_route_404_on_unknown_card(tmp_path):
    for _, _card_id, base in _run_server(tmp_path, lambda store: None):
        status, _body = _call(f"{base}/api/cards/nope/lease/approve", "POST", {"paths": ["a.py"]})
        assert status == 404


def test_route_409_when_not_blocked_on_lease_conflict(tmp_path):
    for _, card_id, base in _run_server(tmp_path, _seed_crash):
        status, body = _call(
            f"{base}/api/cards/{card_id}/lease/approve", "POST", {"paths": ["a.py"]}
        )
        assert status == 409
        assert "lease conflict" in body["error"]
