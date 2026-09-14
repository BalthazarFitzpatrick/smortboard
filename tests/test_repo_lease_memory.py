"""remembering a lease approval for a repo, so a later card on it is never asked for the same
path twice: Store's repo_remembered_leases table, the hook reading it as a second list beside a
card's own lease, and the lease/approve route's `remember` flag.

fakes and http harness follow test_lease_approve.py's conventions - no container, no model.
"""

import json
import subprocess
import threading

import pytest

from smortboard.exec.leases import write_lease_settings
from smortboard.lifecycle import LifecycleResult
from smortboard.server import runs as runs_module
from smortboard.server.app import build_server
from smortboard.store.api import Store
from smortboard.store.errors import NotFoundError
from tests.test_lease_approve import _call, _run_server


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "board.db") as s:
        yield s


def _board_and_repo(store):
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "r", "/tmp/r", "main")
    return board, repo


# ---- Store: remember_lease_paths / forget_lease_path / remembered_leases -----------------------


def test_remember_lease_paths_adds_and_is_visible_on_the_repo(store):
    _, repo = _board_and_repo(store)
    store.remember_lease_paths(repo["id"], ["docs/**"])
    assert [g["path_glob"] for g in store.get_repo(repo["id"])["remembered_leases"]] == ["docs/**"]
    assert [g["path_glob"] for g in store.list_repos(repo["board_id"])[0]["remembered_leases"]] == [
        "docs/**"
    ]


def test_remember_lease_paths_dedupes_against_what_is_already_remembered(store):
    _, repo = _board_and_repo(store)
    store.remember_lease_paths(repo["id"], ["docs/**"])
    result = store.remember_lease_paths(repo["id"], ["docs/**", "ui/board.js"])
    assert sorted(g["path_glob"] for g in result) == ["docs/**", "ui/board.js"]


def test_remember_lease_paths_rejects_a_bad_glob(store):
    _, repo = _board_and_repo(store)
    with pytest.raises(ValueError):
        store.remember_lease_paths(repo["id"], ["../escape.py"])


def test_remember_lease_paths_404_on_an_unknown_repo(store):
    with pytest.raises(NotFoundError):
        store.remember_lease_paths("nope", ["docs/**"])


def test_forget_lease_path_removes_only_the_one_asked_for(store):
    _, repo = _board_and_repo(store)
    store.remember_lease_paths(repo["id"], ["docs/**", "ui/board.js"])
    kept = next(
        g["id"] for g in store.remembered_leases(repo["id"]) if g["path_glob"] == "ui/board.js"
    )
    removed = next(
        g["id"] for g in store.remembered_leases(repo["id"]) if g["path_glob"] == "docs/**"
    )
    result = store.forget_lease_path(repo["id"], removed)
    assert [g["id"] for g in result] == [kept]


def test_forget_lease_path_is_a_no_op_on_an_unknown_lease_id(store):
    _, repo = _board_and_repo(store)
    store.remember_lease_paths(repo["id"], ["docs/**"])
    result = store.forget_lease_path(repo["id"], "nope")
    assert [g["path_glob"] for g in result] == ["docs/**"]


def test_forget_lease_path_404_on_an_unknown_repo(store):
    with pytest.raises(NotFoundError):
        store.forget_lease_path("nope", "also-nope")


def test_a_fresh_repo_has_no_remembered_leases(store):
    _, repo = _board_and_repo(store)
    assert store.get_repo(repo["id"])["remembered_leases"] == []


# ---- the hook: reads path_globs and remembered_globs, allows either -----------------------------


def _guard_command(settings_path):
    settings = json.loads(settings_path.read_text())
    return settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


def _run_guard(command, root, rel):
    payload = json.dumps({"tool_input": {"file_path": str(root / rel)}})
    return subprocess.run(
        command.split(" ", 1), input=payload, capture_output=True, text=True
    ).returncode


def test_write_lease_settings_writes_both_lists(tmp_path):
    settings_path = write_lease_settings(tmp_path / "wt", ["src/**"], remembered_globs=["docs/**"])
    lease = json.loads(settings_path.with_name("lease.json").read_text())
    assert lease["path_globs"] == ["src/**"]
    assert lease["remembered_globs"] == ["docs/**"]


def test_write_lease_settings_defaults_remembered_globs_to_empty(tmp_path):
    settings_path = write_lease_settings(tmp_path / "wt", ["src/**"])
    lease = json.loads(settings_path.with_name("lease.json").read_text())
    assert lease["remembered_globs"] == []


def test_hook_allows_a_path_in_either_list(tmp_path):
    root = (tmp_path / "workspace").resolve()
    settings_path = write_lease_settings(
        tmp_path / "wt", ["src/**"], remembered_globs=["docs/**"], root=str(root)
    )
    command = _guard_command(settings_path)

    assert _run_guard(command, root, "src/thing.py") == 0
    assert _run_guard(command, root, "docs/readme.md") == 0
    assert _run_guard(command, root, "elsewhere.py") == 2


def test_hook_refuses_when_the_repo_has_no_remembered_leases_yet(tmp_path):
    root = (tmp_path / "workspace").resolve()
    settings_path = write_lease_settings(tmp_path / "wt", ["src/**"], root=str(root))
    command = _guard_command(settings_path)

    assert _run_guard(command, root, "docs/readme.md") == 2


# ---- the route: remember: true also remembers for the repo --------------------------------------


def _seed_lease_conflict_with_repo(store):
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "r", "/tmp/r", "main")
    card = store.create_card(board["id"], repo["id"], "a card")
    store.update_card(card["id"], blocked_reason_code="LEASE_CONFLICT", review_flag=True)
    return card["id"], repo["id"]


def test_route_400_before_remembering_anything_on_a_bad_glob(tmp_path):
    for _, card_id, base in _run_server(
        tmp_path, lambda store: _seed_lease_conflict_with_repo(store)[0]
    ):
        status, body = _call(
            f"{base}/api/cards/{card_id}/lease/approve",
            "POST",
            {"paths": ["../escape.py"], "remember": True},
        )
        assert status == 400


def test_route_approve_with_remember_widens_the_card_and_remembers_for_the_repo(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        runs_module,
        "run_card_lifecycle",
        lambda store, cid, **kwargs: LifecycleResult(card_id=cid, phase="opened"),
    )
    ready = threading.Event()
    holder = {}

    def _serve():
        store = Store(tmp_path / "board.db")
        holder["card_id"], holder["repo_id"] = _seed_lease_conflict_with_repo(store)
        srv = build_server(store, port=0, host="127.0.0.1")
        holder["server"] = srv
        ready.set()
        srv.serve_forever()
        store.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    ready.wait()
    base = f"http://127.0.0.1:{holder['server'].server_address[1]}"
    try:
        status, _body = _call(
            f"{base}/api/cards/{holder['card_id']}/lease/approve",
            "POST",
            {"paths": ["docs/**"], "remember": True},
        )
        assert status == 202
    finally:
        holder["server"].shutdown()
        thread.join(timeout=2)

    # sqlite connections are bound to the thread that opened them, so reading the result back
    # happens through a fresh connection from this (the test) thread, once the server's own has
    # shut down and stopped writing
    with Store(tmp_path / "board.db") as verify:
        repo = verify.get_repo(holder["repo_id"])
        assert [g["path_glob"] for g in repo["remembered_leases"]] == ["docs/**"]

        # a second card on the same repo never has to ask for docs/** again
        second = verify.create_card(
            repo["board_id"], holder["repo_id"], "second card", leases=["src/**"]
        )
        settings_path = write_lease_settings(
            tmp_path / "wt-second",
            ["src/**"],
            remembered_globs=[g["path_glob"] for g in repo["remembered_leases"]],
            root=str(tmp_path / "wt-second"),
        )
        command = _guard_command(settings_path)
        root = (tmp_path / "wt-second").resolve()
        assert _run_guard(command, root, "docs/notes.md") == 0
        assert second["id"]  # created without error; only its lease matters above


def test_route_approve_without_remember_does_not_touch_the_repos_remembered_leases(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        runs_module,
        "run_card_lifecycle",
        lambda store, cid, **kwargs: LifecycleResult(card_id=cid, phase="opened"),
    )
    ready = threading.Event()
    holder = {}

    def _serve():
        store = Store(tmp_path / "board.db")
        holder["card_id"], holder["repo_id"] = _seed_lease_conflict_with_repo(store)
        srv = build_server(store, port=0, host="127.0.0.1")
        holder["server"] = srv
        ready.set()
        srv.serve_forever()
        store.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    ready.wait()
    base = f"http://127.0.0.1:{holder['server'].server_address[1]}"
    try:
        status, _body = _call(
            f"{base}/api/cards/{holder['card_id']}/lease/approve", "POST", {"paths": ["docs/**"]}
        )
        assert status == 202
    finally:
        holder["server"].shutdown()
        thread.join(timeout=2)

    with Store(tmp_path / "board.db") as verify:
        assert verify.get_repo(holder["repo_id"])["remembered_leases"] == []


# ---- the route: DELETE /api/repos/<id>/lease/<lease_id> -----------------------------------------


def test_route_delete_remembered_lease_removes_only_the_one_asked_for(tmp_path):
    def _seed(store):
        _, repo = _board_and_repo(store)
        store.remember_lease_paths(repo["id"], ["docs/**", "ui/board.js"])
        removed_id = next(
            g["id"] for g in store.remembered_leases(repo["id"]) if g["path_glob"] == "docs/**"
        )
        return repo["id"], removed_id

    for _server, seeded, base in _run_server(tmp_path, _seed):
        repo_id, lease_id = seeded
        status, body = _call(f"{base}/api/repos/{repo_id}/lease/{lease_id}", "DELETE")
        assert status == 200
        assert [g["path_glob"] for g in body] == ["ui/board.js"]


def test_route_delete_unknown_lease_id_is_a_no_op(tmp_path):
    def _seed(store):
        _, repo = _board_and_repo(store)
        store.remember_lease_paths(repo["id"], ["docs/**"])
        return repo["id"]

    for _server, repo_id, base in _run_server(tmp_path, _seed):
        status, body = _call(f"{base}/api/repos/{repo_id}/lease/nope", "DELETE")
        assert status == 200
        assert [g["path_glob"] for g in body] == ["docs/**"]


def test_route_delete_404_on_an_unknown_repo(tmp_path):
    for _server, _seeded, base in _run_server(tmp_path, lambda store: None):
        status, _body = _call(f"{base}/api/repos/nope/lease/also-nope", "DELETE")
        assert status == 404
