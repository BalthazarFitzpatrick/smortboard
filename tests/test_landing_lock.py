"""the landing lock: store persistence, ttl eviction, the fifo queue, the http routes, and the
client's refusal of a protected target and release-on-failure. concurrency is exercised with real
threads against one sqlite file, since that is what two processes racing a push actually look
like."""

import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from smortboard import land_cli
from smortboard.server.app import build_server
from smortboard.store import Store
from tests.test_lease_approve import _call


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "board.sqlite3") as s:
        yield s


def test_a_free_slot_is_granted_immediately(store):
    result = store.request_landing("repoA", "agent1", "feature/x", "development", ttl_s=60)
    assert result["granted"] is True
    assert result["holder"] == "agent1"


def test_a_held_slot_queues_the_second_request(store):
    first = store.request_landing("repoA", "agent1", "feature/x", "development", ttl_s=60)
    second = store.request_landing("repoA", "agent2", "feature/y", "development", ttl_s=60)
    assert first["granted"] is True
    assert second["granted"] is False
    assert second["position"] == 1


def test_release_promotes_the_next_queued_lease(store):
    first = store.request_landing("repoA", "agent1", "feature/x", "development", ttl_s=60)
    second = store.request_landing("repoA", "agent2", "feature/y", "development", ttl_s=60)
    store.release_landing(first["lease_id"])
    status = store.request_landing(
        "repoA", "agent2", "feature/y", "development", ttl_s=60, lease_id=second["lease_id"]
    )
    assert status["granted"] is True
    assert status["holder"] == "agent2"


def test_two_concurrent_acquirers_the_second_gets_it_only_after_release(tmp_path):
    """two threads race the same slot, each through its own connection to the same file - a
    sqlite3 connection is bound to the thread that opened it (see Store.__init__), and two
    processes racing a real push are exactly this: two separate connections, one file. the loser
    must see it granted only once the winner has actually released, never before"""
    db_path = tmp_path / "board.db"
    Store(db_path).close()  # create and migrate the file up front, from this thread
    order = []
    order_lock = threading.Lock()
    first_granted = threading.Event()
    release_now = threading.Event()

    def winner():
        with Store(db_path) as store:
            result = store.request_landing("repoA", "winner", "w", "development", ttl_s=60)
            assert result["granted"] is True
            with order_lock:
                order.append("winner-granted")
            first_granted.set()
            release_now.wait(timeout=5)
            store.release_landing(result["lease_id"])
            with order_lock:
                order.append("winner-released")

    def loser():
        first_granted.wait(timeout=5)
        with Store(db_path) as store:
            result = store.request_landing("repoA", "loser", "l", "development", ttl_s=60)
            assert result["granted"] is False
            lease_id = result["lease_id"]
            release_now.set()
            granted = False
            for _ in range(50):
                status = store.request_landing(
                    "repoA", "loser", "l", "development", ttl_s=60, lease_id=lease_id
                )
                if status["granted"]:
                    granted = True
                    break
                time.sleep(0.02)
            assert granted
            with order_lock:
                order.append("loser-granted")

    t1, t2 = threading.Thread(target=winner), threading.Thread(target=loser)
    t1.start(), t2.start()
    t1.join(timeout=5), t2.join(timeout=5)
    assert order.index("winner-released") < order.index("loser-granted")


def test_a_stale_heartbeat_past_ttl_is_evicted_and_the_queue_promoted(store):
    held = store.request_landing("repoA", "stuck-agent", "feature/x", "development", ttl_s=1)
    store._conn.execute(
        "UPDATE landing_locks SET last_heartbeat = ? WHERE lease_id = ?",
        ((datetime.now(UTC) - timedelta(seconds=5)).isoformat(), held["lease_id"]),
    )
    store._conn.commit()
    queued = store.request_landing("repoA", "next-agent", "feature/y", "development", ttl_s=60)
    # the stale holder is gone by the time the queued request is asked about again
    status = store.request_landing(
        "repoA", "next-agent", "feature/y", "development", ttl_s=60, lease_id=queued["lease_id"]
    )
    assert status["granted"] is True
    assert status["holder"] == "next-agent"


def test_the_lock_survives_a_store_restart(tmp_path):
    db_path = tmp_path / "board.sqlite3"
    with Store(db_path) as s:
        s.request_landing("repoA", "agent1", "feature/x", "development", ttl_s=60)
    with Store(db_path) as reopened:
        rows = reopened.list_landing()
    assert len(rows) == 1
    assert rows[0]["holder"]["holder"] == "agent1"


def test_client_refuses_main_master_or_trunk_as_target():
    for target in ("main", "master", "trunk"):
        args = land_cli.main(["--repo", "/tmp/nonexistent-repo", "--target", target, "--", "true"])
        assert args != 0


# ---- the http routes, over real http -------------------------------------------------------


def _run_server(tmp_path):
    ready = threading.Event()
    holder = {}

    def _serve():
        store = Store(tmp_path / "board.db")
        srv = build_server(store, port=0, host="127.0.0.1")
        holder["server"] = srv
        ready.set()
        srv.serve_forever()
        store.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    ready.wait()
    try:
        yield holder["server"], f"http://127.0.0.1:{holder['server'].server_address[1]}"
    finally:
        holder["server"].shutdown()
        thread.join(timeout=2)


def test_post_landing_grants_then_queues_a_second_caller(tmp_path):
    for _server, base in _run_server(tmp_path):
        status, first = _call(
            f"{base}/api/repos/repoA/landing",
            "POST",
            {"holder": "agent1", "branch": "feature/x"},
        )
        assert status == 200 and first["granted"] is True

        status, second = _call(
            f"{base}/api/repos/repoA/landing",
            "POST",
            {"holder": "agent2", "branch": "feature/y"},
        )
        assert status == 202 and second["granted"] is False and second["position"] == 1

        status, rows = _call(f"{base}/api/landing")
        assert status == 200 and len(rows) == 1 and len(rows[0]["queue"]) == 1

        status, _released = _call(f"{base}/api/landing/{first['lease_id']}", "DELETE")
        assert status == 200

        status, rows = _call(f"{base}/api/landing")
        assert rows[0]["holder"]["holder"] == "agent2"


# ---- the client, end to end over a real server and a real git remote ------------------------


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _clone(origin, path):
    subprocess.run(["git", "clone", "-q", str(origin), str(path)], check=True, capture_output=True)
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")


def _commit(repo, name, text):
    (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"wrote {name}")


@pytest.fixture
def origin(tmp_path):
    path = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(path)], check=True)
    seed = tmp_path / "seed"
    _clone(path, seed)
    _commit(seed, "f.txt", "base\n")
    _git(seed, "push", "-q", "origin", "main")
    _git(seed, "push", "-q", "origin", "main:development")
    return path


@pytest.fixture
def agent_clone(tmp_path, origin):
    path = tmp_path / "agent"
    _clone(origin, path)
    _git(path, "checkout", "-q", "-b", "fix/x", "origin/development")
    _commit(path, "work.txt", "done\n")
    return path


def test_client_releases_the_lock_when_the_test_command_fails(tmp_path, origin, agent_clone):
    for _server, base in _run_server(tmp_path):
        exit_code = land_cli.main(
            [
                "--repo",
                str(agent_clone),
                "--target",
                "development",
                "--url",
                base,
                "--",
                "false",
            ]
        )
        assert exit_code != 0
        status, rows = _call(f"{base}/api/landing")
        assert status == 200 and rows == []


def test_client_lands_on_target_when_the_test_command_passes(tmp_path, origin, agent_clone):
    for _server, base in _run_server(tmp_path):
        exit_code = land_cli.main(
            [
                "--repo",
                str(agent_clone),
                "--target",
                "development",
                "--url",
                base,
                "--",
                "true",
            ]
        )
        assert exit_code == 0
        tip = _git(agent_clone, "rev-parse", "HEAD")
        assert _git(origin, "rev-parse", "development") == tip
