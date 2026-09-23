"""watchdogs on a run: a silent stream, an uncapped lab's wall clock, and a hung git remote.

Real child processes (the test interpreter printing and sleeping) and a real git remote made to
hang, but never docker, claude or codex.
"""

import json
import subprocess
import sys
import threading
import time

from smortboard import lifecycle
from smortboard.exec import runner, worktrees
from smortboard.exec.runner import TIME_CAP_SUBTYPE, RunResult, run_process
from smortboard.exec.worktrees import (
    WorktreeError,
    _fetch,
    branch_diverged_from_origin,
    fetch_base,
    repo_lock,
)
from smortboard.labs.claude_code import ClaudeCodeAdapter
from smortboard.labs.codex import CodexAdapter
from smortboard.store import Store


def _script(body):
    return [sys.executable, "-c", body]


def _silent_after_one_line():
    line = json.dumps({"type": "system", "subtype": "init"})
    return _script(f"import time; print({line!r}, flush=True); time.sleep(60)")


def _chatty(seconds):
    line = json.dumps({"type": "system", "subtype": "tick"})
    return _script(
        f"import time\nfor _ in range(int({seconds} / 0.05)):\n"
        f"    print({line!r}, flush=True); time.sleep(0.05)"
    )


def _card(tmp_path):
    store = Store(tmp_path / "board.db")
    board = store.create_board("b")
    return store, store.create_card(board["id"], None, "c")["id"]


def test_a_silent_run_is_killed_and_classified_api_unreachable(tmp_path):
    """measured: card ab103f07's run took 285 minutes, 225 of them one silent gap"""
    store, card_id = _card(tmp_path)
    started = time.monotonic()
    result = run_process(
        store,
        card_id,
        _silent_after_one_line(),
        adapter=ClaudeCodeAdapter(),
        profile="default",
        stall_seconds=0.5,
    )
    assert time.monotonic() - started < 15
    assert result.blocked_reason_code == "API_UNREACHABLE"
    stalled = [e for e in store.list_events(card_id) if e["kind"] == "run_stalled"]
    assert len(stalled) == 1
    assert stalled[0]["payload"]["limit_seconds"] == 0.5
    assert stalled[0]["payload"]["gap_seconds"] >= 0
    store.close()


def test_a_chatty_run_is_not_mistaken_for_a_stall(tmp_path):
    store, card_id = _card(tmp_path)
    result = run_process(
        store,
        card_id,
        _chatty(1.5),
        adapter=ClaudeCodeAdapter(),
        profile="default",
        stall_seconds=0.5,
    )
    assert result.blocked_reason_code == "CRASH"  # no result event: an ordinary crash, not a stall
    assert "run_stalled" not in [e["kind"] for e in store.list_events(card_id)]
    store.close()


def test_codex_runs_are_capped_on_the_wall_clock(tmp_path, monkeypatch):
    """codex has no budget cap: no native one, and no price to estimate spend from"""
    monkeypatch.setattr(runner, "UNBUDGETED_TIME_CAP_SECONDS", 0.5)
    store, card_id = _card(tmp_path)
    started = time.monotonic()
    result = run_process(
        store, card_id, _chatty(30), adapter=CodexAdapter(), profile="default", stall_seconds=0
    )
    assert time.monotonic() - started < 15
    assert result.subtype == TIME_CAP_SUBTYPE
    assert result.blocked_reason_code == "CRASH"
    capped = [e for e in store.list_events(card_id) if e["kind"] == "run_time_capped"]
    assert len(capped) == 1 and capped[0]["payload"]["limit_seconds"] == 0.5
    store.close()


def test_a_lab_with_its_own_budget_gets_no_wall_clock_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "UNBUDGETED_TIME_CAP_SECONDS", 0.3)
    store, card_id = _card(tmp_path)
    result = run_process(store, card_id, _chatty(1.0), adapter=ClaudeCodeAdapter(), profile="p")
    assert result.subtype != TIME_CAP_SUBTYPE
    assert "run_time_capped" not in [e["kind"] for e in store.list_events(card_id)]
    store.close()


def test_a_time_capped_worker_with_no_commits_names_the_time_limit(tmp_path, monkeypatch):
    from tests.test_lifecycle import _Backend, _stub_gates

    store = Store(tmp_path / "b.db")
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "i"],
    ):
        subprocess.run(["git", "-C", str(repo_path), *args], check=True, capture_output=True)
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "r", str(repo_path), "main", test_command="true")
    card = store.create_card(board["id"], repo["id"], "c", leases=["a.py"])
    _stub_gates(monkeypatch)
    monkeypatch.setattr(lifecycle, "branch_has_commits", lambda *a, **k: False)
    capped = RunResult(TIME_CAP_SUBTYPE, True, "CRASH", "s", None, 0, "ran out of time")
    result = lifecycle.run_card_lifecycle(store, card["id"], backend=_Backend(capped))
    assert result.blocked_reason_code == "CRASH"
    assert "time limit" in store.list_comments(card["id"])[-1]["body"]
    store.close()


# -- git fetch against a remote that never answers ----------------------------------------------


def _hung_remote(tmp_path):
    """a repo whose origin accepts the connection and then says nothing for ten seconds"""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q"]
        + ["--allow-empty", "-m", "i"],
        check=True,
    )
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(origin)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(origin)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "remote.origin.uploadpack", "sleep 10; git-upload-pack"],
        check=True,
    )
    return repo


def _lock_is_free(repo):
    acquired = threading.Event()

    def _take():
        with repo_lock(repo):
            acquired.set()

    threading.Thread(target=_take, daemon=True).start()
    return acquired.wait(5)


def test_a_hung_remote_times_out_instead_of_holding_the_repo_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(worktrees, "GIT_FETCH_TIMEOUT_SECONDS", 1)
    repo = _hung_remote(tmp_path)
    started = time.monotonic()
    try:
        _fetch(repo, "origin", "main")
        raise AssertionError("a hung fetch returned instead of raising")
    except WorktreeError as exc:
        assert "did not finish within 1s" in str(exc)
    assert fetch_base(repo, "main") is False
    assert branch_diverged_from_origin(repo, "main") is None
    assert time.monotonic() - started < 15  # three fetches, each killed after one second
    assert _lock_is_free(repo)
