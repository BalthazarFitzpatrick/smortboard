"""the plan's whole-system test: a card driven over real http, in a real git repo, to a decision.

Fakes sit only where the board meets something it cannot run here - the model (a backend that
commits real files), docker (the test gate runs the repo's command directly, and the reviewer reads
the real diff), and gh (a pull request is recorded, not opened). Everything between them is the
production path: the server, the run thread with its own store, the lifecycle, worktrees, the event
log, the outcome projection, accept and reject.
"""

import json
import subprocess
import threading
import time
import urllib.error
import urllib.request

import pytest

from smortboard import lifecycle
from smortboard.exec.runner import RunResult
from smortboard.exec.worktrees import branch_exists, worktree_path
from smortboard.review import decide, merge_request, reviewer
from smortboard.review.gates import GateResult
from smortboard.review.merge_request import MergeRequestResult
from smortboard.review.reviewer import ReviewFinding, ReviewResult
from smortboard.server.app import build_server
from smortboard.store import Store

PR_URL = "https://github.com/o/r/pull/7"


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


class Worker:
    """the model's stand-in: writes `content` into thing.txt and commits it, like a card would.

    `contents` is one entry per run, so a fix round can write something different from the first.
    """

    name = "fake"

    def __init__(self, *contents):
        self.contents = list(contents)
        self.prompts = []

    def run_card(self, store, card_id, worktree_path, prompt, settings_path, **kwargs):
        self.prompts.append(prompt)
        content = self.contents[min(len(self.prompts), len(self.contents)) - 1]
        (worktree_path / "thing.txt").write_text(content)
        _git(worktree_path, "add", "thing.txt")
        # --allow-empty: a fix round that writes the same content still commits, as an agent would
        _git(worktree_path, "commit", "-q", "--allow-empty", "-m", f"wrote {content!r}")
        return RunResult(
            subtype="success",
            is_error=False,
            blocked_reason_code=None,
            session_id="s",
            total_cost_usd=0.1,
            num_turns=3,
            result_text=f"wrote thing.txt ({len(self.prompts)})",
        )


def _test_gate(store, card_id, work_path, repo):
    """the repo's own test command, run for real against what the card committed"""
    done = subprocess.run(
        repo["test_command"], shell=True, cwd=work_path, capture_output=True, text=True
    )
    result = GateResult(
        passed=done.returncode == 0,
        command=repo["test_command"],
        exit_code=done.returncode,
        output=done.stdout + done.stderr,
    )
    store.append_event(
        card_id,
        "test_gate",
        {
            "passed": result.passed,
            "command": result.command,
            "exit_code": result.exit_code,
            "output": result.output,
        },
    )
    return result


def _review(store, card_id, diff, *args, **kwargs):
    """reads the real diff: a line with SECRET in it is a leaked credential"""
    findings = [
        ReviewFinding("leaked_credential", "critical", "thing.txt", "a secret reached the diff", 1)
        for line in diff.splitlines()
        if line.startswith("+") and "SECRET" in line
    ]
    result = ReviewResult(approved=not findings, findings=findings)
    reviewer._record(store, card_id, result)
    return result


def _open_pr(store, card_id, repo_path, branch, base="main"):
    result = MergeRequestResult(opened=True, branch=branch, url=PR_URL)
    merge_request._record(store, card_id, result)
    return result


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "README").write_text("r\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "first")
    return path


@pytest.fixture
def board(tmp_path, repo, monkeypatch):
    """a running server plus a board, a repo and a card on it, with the three boundaries faked"""
    worker = Worker("hello")
    closed = []
    monkeypatch.setattr(lifecycle, "require_card_runtime", lambda *a, **k: worker)
    monkeypatch.setattr(lifecycle, "run_test_gate", _test_gate)
    monkeypatch.setattr(lifecycle, "run_review", _review)
    monkeypatch.setattr(lifecycle, "open_merge_request", _open_pr)
    monkeypatch.setattr(decide, "close_merge_request", lambda path, url: closed.append(url))

    db = tmp_path / "board.db"
    ready = threading.Event()
    holder = {}

    def _serve():
        store = Store(db)
        holder["server"] = build_server(store, port=0, host="127.0.0.1")
        ready.set()
        holder["server"].serve_forever()
        store.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    ready.wait()
    url = f"http://127.0.0.1:{holder['server'].server_address[1]}"

    b = _call(url, "POST", "/api/boards", {"name": "b"})
    r = _call(
        url,
        "POST",
        f"/api/boards/{b['id']}/repos",
        {"name": "repo", "path": str(repo), "test_command": "test -s thing.txt"},
    )
    card = _call(
        url,
        "POST",
        "/api/cards",
        {
            "board_id": b["id"],
            "repo_id": r["id"],
            "title": "write the thing",
            "criteria": ["thing.txt exists and is not empty"],
            "leases": ["thing.txt"],
        },
    )
    yield {
        "url": url,
        "card": card["id"],
        "board": b["id"],
        "repo": repo,
        "db": db,
        "worker": worker,
        "closed": closed,
    }
    holder["server"].shutdown()
    thread.join()
    holder["server"].server_close()


def _call(url, method, path, body=None, expect=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            status, payload = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, payload = exc.code, exc.read()
    if expect is not None:
        assert status == expect, payload
    return json.loads(payload) if payload else None


def _run(env):
    """starts the card over http and waits for the run thread to finish, the way the board polls"""
    _call(env["url"], "POST", f"/api/cards/{env['card']}/run", expect=202)
    deadline = time.time() + 20
    while time.time() < deadline:
        state = _call(env["url"], "GET", f"/api/cards/{env['card']}/run")
        if not state["running"]:
            assert state["error"] is None, state["error"]
            return state
        time.sleep(0.05)
    raise AssertionError("the card never finished")


def _card(env, card_id=None):
    return _call(env["url"], "GET", f"/api/cards/{card_id or env['card']}")


def _outcome(env):
    return _call(env["url"], "GET", f"/api/cards/{env['card']}/outcome", expect=200)


def test_a_card_runs_to_checking_with_both_verdicts_and_is_accepted(board):
    state = _run(board)
    assert state["phase"] == "opened"
    assert state["pr_url"] == PR_URL

    card = _card(board)
    assert card["status"] == "checking"
    assert card["blocked_reason_code"] is None
    outcome = _outcome(board)
    assert outcome["summary"] == "wrote thing.txt (1)"
    assert outcome["tests"]["passed"] is True
    assert outcome["review"]["approved"] is True
    assert outcome["pr_url"] == PR_URL

    card = _call(board["url"], "POST", f"/api/cards/{board['card']}/accept", expect=200)
    assert card["status"] == "accepted"
    assert not worktree_path(board["repo"], board["card"]).exists()  # the worktree is released
    assert branch_exists(board["repo"], board["card"])  # the pull request is built on the branch


def test_a_card_with_findings_lands_in_attention_not_in_checking(board):
    board["worker"].contents = ["SECRET=hunter2"]
    state = _run(board)
    assert state["phase"] == "blocked"
    card = _card(board)
    assert card["status"] == "doing"  # never reached checking: the reviewer did not approve
    assert card["blocked_reason_code"] == "REVIEW_REJECTED"
    assert card["review_flag"] == 1
    outcome = _outcome(board)
    assert outcome["review"]["approved"] is False
    assert outcome["review"]["findings"][0]["category"] == "leaked_credential"
    assert outcome["pr_url"] is None
    assert len(board["worker"].prompts) == 1  # attention is the default route: no fix round


def test_on_the_fix_route_the_worker_gets_the_findings_and_the_card_reaches_checking(board):
    board["worker"].contents = ["SECRET=hunter2", "hello"]
    _call(
        board["url"], "PATCH", f"/api/cards/{board['card']}", {"findings_route": "fix"}, expect=200
    )
    state = _run(board)
    assert state["phase"] == "opened"
    assert _card(board)["status"] == "checking"
    fix_prompt = board["worker"].prompts[1]
    assert "did not approve" in fix_prompt
    assert "a secret reached the diff" in fix_prompt
    outcome = _outcome(board)
    assert outcome["fix_rounds"] == 1
    assert outcome["summary"] == "wrote thing.txt (1)"  # the work's summary, not the fix's


def test_the_global_route_forces_every_card_and_the_rounds_run_out(board):
    board["worker"].contents = ["SECRET=hunter2"]
    # the card says attention, the board says fix - the board wins
    _call(board["url"], "PATCH", f"/api/cards/{board['card']}", {"findings_route": "attention"})
    _call(board["url"], "PATCH", "/api/settings", {"findings_route": "fix"}, expect=200)
    state = _run(board)
    assert state["phase"] == "blocked"
    assert state["blocked_reason_code"] == "REVIEW_REJECTED"
    assert len(board["worker"].prompts) == 1 + lifecycle.MAX_FIX_ROUNDS
    assert _outcome(board)["fix_rounds"] == lifecycle.MAX_FIX_ROUNDS


def test_rejecting_keeps_the_diff_closes_the_pr_and_the_next_run_starts_clean(board):
    with Store(board["db"]) as store:
        dependent = store.create_card(board["board"], None, "builds on the thing")["id"]
        store.add_dependency(dependent, board["card"])
    _run(board)

    card = _call(board["url"], "POST", f"/api/cards/{board['card']}/reject", expect=200)
    assert card["status"] == "rejected"
    assert board["closed"] == [PR_URL]
    assert not worktree_path(board["repo"], board["card"]).exists()
    assert not branch_exists(board["repo"], board["card"])
    with Store(board["db"]) as store:
        diffs = store.list_attachments(board["card"])
        assert [a["filename"] for a in diffs] == ["attempt-1.diff"]
        assert b"+hello" in store.get_attachment_blob(diffs[0]["id"])
    blocked = _card(board, dependent)
    assert blocked["blocked_reason_code"] == "DEPENDENCY_REJECTED"
    assert blocked["review_flag"] == 1

    # the next run cuts fresh from base: one commit on top of main, not two
    board["worker"].contents = ["hello again"]
    state = _run(board)
    assert state["phase"] == "opened"
    assert _git(board["repo"], "rev-list", "--count", f"main..card/{board['card']}") == "1"
    assert _outcome(board)["summary"] == "wrote thing.txt (2)"


def test_a_decision_on_a_card_nobody_has_run_is_refused(board):
    _call(board["url"], "POST", f"/api/cards/{board['card']}/accept", expect=409)
    _call(board["url"], "POST", f"/api/cards/{board['card']}/reject", expect=409)
    assert _card(board)["status"] == "todo"
