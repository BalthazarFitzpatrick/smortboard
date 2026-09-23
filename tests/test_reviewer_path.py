"""the reviewer path: what the reviewer is shown, and what a reviewer that failed to answer costs.

Real git throughout the diff tests - the question is what `git diff` hands the reviewer once a
reused worktree has merged a newer origin/<base> than the local base it was cut from.
"""

import subprocess

import pytest

from smortboard import lifecycle
from smortboard.exec.runner import RunResult
from smortboard.review.gates import GateResult
from smortboard.review.merge_request import MergeRequestResult
from smortboard.review.reviewer import (
    DIFF_BEGIN,
    REVIEW_JSON_SCHEMA,
    ReviewFinding,
    ReviewResult,
    _build_prompt,
    _parse,
    run_review,
)
from smortboard.store.api import Store


def _git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], capture_output=True, check=True)


def _identity(path):
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")


@pytest.fixture
def repo_with_origin(tmp_path):
    """a repo whose origin is a bare clone of itself, so fetches never reach the network"""
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _identity(path)
    (path / "thing.py").write_text("x = 1\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "first")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(path), str(origin))
    _git(path, "remote", "add", "origin", str(origin))
    _git(path, "fetch", "-q", "origin")
    return path, origin


def _push_upstream(tmp_path, origin, name):
    """someone else lands a commit on origin/main; the local main does not move"""
    other = tmp_path / f"other-{name}"
    _git(tmp_path, "clone", "-q", str(origin), str(other))
    _identity(other)
    (other / name).write_text("upstream\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", f"upstream {name}")
    _git(other, "push", "-q", "origin", "main")


class _CommittingBackend:
    """a worker that really commits card.py on the card's branch, a new line each run"""

    name = "fake"

    def __init__(self):
        self.calls = 0

    def run_card(self, store, card_id, worktree_path, prompt, settings_path, **kwargs):
        self.calls += 1
        target = worktree_path / "card.py"
        target.write_text(f"run = {self.calls}\n")
        _git(worktree_path, "add", "card.py")
        _git(worktree_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "card")
        return RunResult(
            subtype="success",
            is_error=False,
            blocked_reason_code=None,
            session_id="s",
            total_cost_usd=0.1,
            num_turns=1,
            result_text="done",
        )


def test_a_reused_worktree_reviews_only_the_cards_changes_not_merged_upstream_commits(
    tmp_path, repo_with_origin, monkeypatch
):
    """suspicion from the lifecycle: the review diff used the LOCAL base while the pre-worker
    sync merged origin/<base> in - so on a reused worktree whose local base lagged origin, the
    upstream commits read as the card's own work"""
    path, origin = repo_with_origin
    store = Store(tmp_path / "board.db")
    board = store.create_board("b")
    repo = store.create_repo(
        board["id"], "repo", str(path), "main", test_command="true", image="img:latest"
    )
    card = store.create_card(board["id"], repo["id"], "add card.py", leases=["card.py"])
    monkeypatch.setattr(
        lifecycle,
        "run_test_gate",
        lambda *a, **k: GateResult(passed=True, command="true", exit_code=0, output="ok"),
    )
    monkeypatch.setattr(
        lifecycle,
        "open_merge_request",
        lambda *a, **k: MergeRequestResult(opened=True, branch="card/x", url="https://x/pull/1"),
    )
    diffs = []

    def _review(store, card_id, diff, *a, **k):
        diffs.append(diff)
        return ReviewResult(approved=False, error="api down", runtime_reason="API_UNREACHABLE")

    monkeypatch.setattr(lifecycle, "run_review", _review)
    backend = _CommittingBackend()

    first = lifecycle.run_card_lifecycle(store, card["id"], backend=backend)
    assert first.phase == "blocked"
    _push_upstream(tmp_path, origin, "upstream.py")
    lifecycle.run_card_lifecycle(store, card["id"], backend=backend)

    # the local main never moved, origin/main did, and the reused branch merged it before review
    heads = [
        subprocess.run(
            ["git", "-C", str(path), "rev-parse", ref], capture_output=True, text=True
        ).stdout.strip()
        for ref in ("main", "origin/main")
    ]
    assert heads[0] != heads[1]
    last = diffs[-1]
    assert "card.py" in last
    assert "upstream.py" not in last
    store.close()


# -- a reviewer that failed to answer is not a verdict ------------------------------------------


@pytest.fixture
def local_card(tmp_path, monkeypatch):
    """a card on a real repo with no remote, and every gate but the reviewer stubbed to pass"""
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _identity(path)
    (path / "thing.py").write_text("x = 1\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "first")
    store = Store(tmp_path / "board.db")
    board = store.create_board("b")
    repo = store.create_repo(
        board["id"], "repo", str(path), "main", test_command="true", image="img:latest"
    )
    card = store.create_card(board["id"], repo["id"], "add card.py", leases=["card.py", "uv.lock"])
    monkeypatch.setattr(lifecycle, "image_id", lambda image: "sha256:one")

    def _gate(store, card_id, *a, **k):
        store.append_event(card_id, "test_gate", {"passed": True, "command": "true"})
        return GateResult(passed=True, command="true", exit_code=0, output="ok")

    monkeypatch.setattr(lifecycle, "run_test_gate", _gate)
    monkeypatch.setattr(
        lifecycle,
        "open_merge_request",
        lambda *a, **k: MergeRequestResult(opened=True, branch="card/x", url="https://x/pull/1"),
    )
    yield store, card["id"], path
    store.close()


def _reviews(monkeypatch, *results):
    """run_review stand-in: hands back `results` in order and records what each call was given"""
    calls = []
    queue = list(results)

    def _review(store, card_id, diff, *a, **k):
        calls.append({"diff": diff, **k})
        result = queue.pop(0)
        store.append_event(
            card_id,
            "review_gate",
            {
                "approved": result.approved,
                "error": result.error,
                "runtime_reason": result.runtime_reason,
                "head": k.get("head"),
                "findings": [],
            },
        )
        return result

    monkeypatch.setattr(lifecycle, "run_review", _review)
    return calls


_OUTAGE = ReviewResult(approved=False, error="api down", runtime_reason="API_UNREACHABLE")
_APPROVED = ReviewResult(approved=True)


def _kinds(store, card_id):
    return [e["kind"] for e in store.list_events(card_id)]


def test_a_reviewer_api_error_blocks_api_unreachable_not_review_rejected(local_card, monkeypatch):
    store, card_id, _ = local_card
    _reviews(monkeypatch, _OUTAGE)
    result = lifecycle.run_card_lifecycle(store, card_id, backend=_CommittingBackend())
    assert result.blocked_reason_code == "API_UNREACHABLE"
    assert store.get_card(card_id)["blocked_reason_code"] == "API_UNREACHABLE"
    note = store.list_comments(card_id)[-1]["body"]
    assert "not a verdict" in note and "did not approve" not in note


def test_the_retry_of_an_unchanged_head_skips_the_worker_and_goes_to_review(
    local_card, monkeypatch
):
    """measured: a reviewer outage or usage limit re-ran the whole worker (avg $1.12, up to ~$5)
    only to reach the same review again"""
    store, card_id, _ = local_card
    reviews = _reviews(monkeypatch, _OUTAGE, _APPROVED)
    backend = _CommittingBackend()
    lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    seen = []
    result = lifecycle.run_card_lifecycle(store, card_id, backend=backend, on_phase=seen.append)
    assert result.phase == "opened"
    assert backend.calls == 1  # the worker ran once, in the first attempt only
    assert seen == ["preparing", "testing", "reviewing", "opening"]
    assert len(reviews) == 2
    assert _kinds(store, card_id).count("worker_summary") == 1
    assert "worker_skipped" in _kinds(store, card_id)


def test_a_new_operator_note_still_reruns_the_worker(local_card, monkeypatch):
    from smortboard.operator import AUTHOR_KEY

    store, card_id, _ = local_card
    _reviews(monkeypatch, _OUTAGE, _APPROVED)
    backend = _CommittingBackend()
    lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    store.add_comment(card_id, AUTHOR_KEY, "also add a docstring")
    lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    assert backend.calls == 2


def test_a_changed_head_still_reruns_the_worker(local_card, monkeypatch):
    store, card_id, path = local_card
    _reviews(monkeypatch, _OUTAGE, _APPROVED)
    backend = _CommittingBackend()
    lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    tree = path / ".claude" / "worktrees" / card_id
    (tree / "card.py").write_text("by hand\n")
    _git(tree, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "by hand")
    lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    assert backend.calls == 2


def test_a_real_rejection_reruns_the_worker_to_fix_it(local_card, monkeypatch):
    """a verdict's findings are the worker's to fix - resuming at the review would only reject
    the same head again"""
    store, card_id, _ = local_card
    rejected = ReviewResult(
        approved=False,
        findings=[ReviewFinding("vulnerability", "high", "card.py", "shell injection")],
    )
    _reviews(monkeypatch, rejected, _APPROVED)
    backend = _CommittingBackend()
    first = lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    assert first.blocked_reason_code == "REVIEW_REJECTED"
    lifecycle.run_card_lifecycle(store, card_id, backend=backend)
    assert backend.calls == 2


def test_the_review_diff_leaves_lockfiles_out_and_the_prompt_names_them(local_card, monkeypatch):
    store, card_id, _ = local_card
    reviews = _reviews(monkeypatch, _APPROVED)

    class _WithLockfile(_CommittingBackend):
        def run_card(self, store, card_id, worktree_path, *a, **k):
            (worktree_path / "uv.lock").write_text("version = 1\n" * 500)
            _git(worktree_path, "add", "uv.lock")
            return super().run_card(store, card_id, worktree_path, *a, **k)

    lifecycle.run_card_lifecycle(store, card_id, backend=_WithLockfile())
    diff, left_out = reviews[0]["diff"], reviews[0]["excluded_paths"]
    assert "card.py" in diff
    assert "uv.lock" not in diff
    assert left_out == ["uv.lock"]
    prompt = _build_prompt(None, diff, left_out)
    # named just ahead of the diff, inside the untrusted markers - a path is branch data too
    named = prompt.index("left out of this diff")
    assert prompt.index(DIFF_BEGIN) < named < prompt.index("diff --git")
    assert "uv.lock" in prompt[named : prompt.index("diff --git")]


def test_a_diff_of_only_lockfiles_still_goes_to_the_reviewer(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("smortboard.review.reviewer.docker_available", lambda: True)
    monkeypatch.setattr("smortboard.review.reviewer.read_card_token", lambda p=None: "t")

    def _fake_run_process(store, card_id, cmd, **kwargs):
        calls.append(cmd)
        return RunResult("success", False, None, "s", 0.01, 1, '{"findings": []}')

    monkeypatch.setattr("smortboard.review.reviewer.run_process", _fake_run_process)
    result = run_review(
        None, "card", "", tmp_path, tmp_path / "s.json", {}, excluded_paths=["uv.lock"]
    )
    assert result.approved
    assert len(calls) == 1
    assert "uv.lock" in " ".join(calls[0])


# -- the reviewer's own parse ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("run", "reason"),
    [
        (RunResult("error", True, "API_UNREACHABLE", "s", 0.0, 1, "overloaded"), "API_UNREACHABLE"),
        (RunResult("error", True, "CRASH", "s", 0.0, 1, "boom"), "CRASH"),
        (RunResult("error", True, "AGENT_QUESTION", "s", 0.0, 1, "ok?"), "CRASH"),
        (RunResult("error_max_budget_usd", True, "CRASH", "s", 1.5, 9, None), "CRASH"),
        (RunResult("success", False, None, "s", 0.1, 1, "not json"), "CRASH"),
        (RunResult("success", False, None, "s", 0.1, 1, ""), "CRASH"),
    ],
)
def test_no_reviewer_failure_reads_as_review_rejected(run, reason):
    result = _parse(run)
    assert not result.approved
    assert result.runtime_reason == reason
    assert result.blocked_reason_code == reason


def test_a_real_verdict_still_rejects():
    payload = '{"findings": [{"category": "vulnerability", "severity": "high", "file": "a.py", "line": null, "message": "x"}]}'
    result = _parse(RunResult("success", False, None, "s", 0.1, 1, payload))
    assert result.runtime_reason is None
    assert result.blocked_reason_code == "REVIEW_REJECTED"


# -- the schema codex's --output-schema accepts --------------------------------------------------


def _objects(schema):
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            yield schema
        for value in schema.values():
            yield from _objects(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _objects(value)


def test_the_review_schema_is_strict_at_every_object_level():
    """measured, card 4c56f435 at 06:17: `invalid_json_schema ... 'additionalProperties' is
    required to be supplied and to be false` - every codex review failed on it"""
    objects = list(_objects(REVIEW_JSON_SCHEMA))
    assert len(objects) == 2
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert sorted(obj["required"]) == sorted(obj["properties"])
    finding = REVIEW_JSON_SCHEMA["properties"]["findings"]["items"]
    assert finding["properties"]["line"]["type"] == ["integer", "null"]
