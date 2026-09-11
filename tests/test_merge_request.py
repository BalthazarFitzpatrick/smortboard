"""the merge request step. gh and git are faked throughout - the suite must never push anywhere,
never call the real gh, and never reach GitHub."""

import subprocess
from pathlib import Path

import pytest

from smortboard.review import merge_request as mr
from smortboard.store.api import Store

BRANCH = "card/abc123"


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake(monkeypatch, *, commits="1", existing="[]", create_out="https://github.com/o/r/pull/7"):
    """replaces the one function every git and gh call goes through, and records the argv"""
    seen: list[list[str]] = []

    def _run(cmd, cwd=None):
        seen.append(list(cmd))
        if cmd[:2] == ["gh", "auth"]:
            return _Completed(0)
        if cmd[0] == "git" and "remote" in cmd:
            return _Completed(0, stdout="origin\n")
        if cmd[0] == "git" and "rev-list" in cmd:
            return _Completed(0, stdout=commits)
        if cmd[:3] == ["gh", "pr", "list"]:
            return _Completed(0, stdout=existing)
        if cmd[:3] == ["gh", "pr", "create"]:
            return _Completed(0, stdout=create_out)
        return _Completed(0)

    monkeypatch.setattr(mr, "_run", _run)
    monkeypatch.setattr(mr.shutil, "which", lambda name: f"/usr/bin/{name}")
    return seen


def _store(tmp_path):
    store = Store(tmp_path / "board.db")
    board = store.create_board("b")
    card = store.create_card(
        board["id"],
        None,
        "added the thing",
        description="the card asked for a thing",
        criteria=["the thing exists"],
        tasks=["do the thing"],
    )
    return store, card["id"]


def test_it_pushes_the_cards_own_branch_and_never_main(tmp_path, monkeypatch):
    seen = _fake(monkeypatch)
    store, card_id = _store(tmp_path)
    result = mr.open_merge_request(store, card_id, tmp_path, BRANCH)
    assert result.opened
    pushes = [c for c in seen if c[0] == "git" and "push" in c]
    assert len(pushes) == 1
    assert pushes[0][-1] == f"refs/heads/{BRANCH}:refs/heads/{BRANCH}"
    assert "main" not in pushes[0][-1]
    store.close()


def test_it_never_force_pushes(tmp_path, monkeypatch):
    """a card's branch is the only record of what it did; a force push can lose a passed commit"""
    seen = _fake(monkeypatch)
    store, card_id = _store(tmp_path)
    mr.open_merge_request(store, card_id, tmp_path, BRANCH)
    for cmd in seen:
        assert not any(arg in ("--force", "-f", "--force-with-lease") for arg in cmd)
    store.close()


@pytest.mark.parametrize("branch", ["main", "master", "trunk"])
def test_a_card_claiming_a_protected_branch_is_refused_before_anything_runs(
    tmp_path, monkeypatch, branch
):
    seen = _fake(monkeypatch)
    store, card_id = _store(tmp_path)
    with pytest.raises(mr.MergeRequestUnavailable):
        mr.open_merge_request(store, card_id, tmp_path, branch)
    assert seen == []  # refused before it shelled out to anything at all
    store.close()


def test_push_itself_refuses_a_protected_destination(tmp_path, monkeypatch):
    """the guard is on the push, not only on the entry point, so no other caller can route past it"""
    _fake(monkeypatch)
    with pytest.raises(mr.MergeRequestUnavailable):
        mr._push(tmp_path, "main")


def test_pr_create_gets_a_real_title_and_a_body_file(tmp_path, monkeypatch):
    seen = _fake(monkeypatch)
    store, card_id = _store(tmp_path)
    store.append_event(card_id, "test_gate", {"passed": True, "command": "uv run pytest"})
    store.append_event(card_id, "review_gate", {"approved": True, "findings": []})
    result = mr.open_merge_request(store, card_id, tmp_path, BRANCH)

    create = next(c for c in seen if c[:3] == ["gh", "pr", "create"])
    assert create[create.index("--title") + 1] == "added the thing"
    assert create[create.index("--base") + 1] == "main"
    assert create[create.index("--head") + 1] == BRANCH
    # --body-file, not --body: the operator's main-branch guard scans the command string
    assert "--body-file" in create and "--body" not in create
    assert result.url == "https://github.com/o/r/pull/7"
    store.close()


def test_the_body_carries_what_the_tests_and_the_reviewer_said(tmp_path, monkeypatch):
    bodies = []
    seen = _fake(monkeypatch)
    store, card_id = _store(tmp_path)
    store.append_event(card_id, "test_gate", {"passed": True, "command": "uv run pytest"})
    store.append_event(
        card_id,
        "review_gate",
        {
            "approved": True,
            "findings": [
                {
                    "category": "efficiency",
                    "severity": "low",
                    "file": "a.py",
                    "line": 4,
                    "message": "reads the file twice",
                }
            ],
        },
    )
    store.append_event(card_id, "result", {"total_cost_usd": 0.42, "num_turns": 9})

    real_run = mr._run

    def _capture(cmd, cwd=None):
        if cmd[:3] == ["gh", "pr", "create"]:
            bodies.append(Path(cmd[cmd.index("--body-file") + 1]).read_text())
        return real_run(cmd, cwd=cwd)

    monkeypatch.setattr(mr, "_run", _capture)
    mr.open_merge_request(store, card_id, tmp_path, BRANCH)

    body = bodies[0]
    assert "Tests: passed" in body and "uv run pytest" in body
    assert "Review: approved, 1 finding(s)" in body
    assert "reads the file twice" in body and "a.py:4" in body
    assert "the thing exists" in body  # the acceptance criteria
    assert "$0.42" in body and "9 turns" in body
    assert "never merges" in body
    assert seen  # the fake was in use throughout
    store.close()


def test_an_empty_branch_refuses_rather_than_opening_an_empty_pr(tmp_path, monkeypatch):
    seen = _fake(monkeypatch, commits="0")
    store, card_id = _store(tmp_path)
    result = mr.open_merge_request(store, card_id, tmp_path, BRANCH)
    assert not result.opened
    assert "no commits" in result.refusal
    assert not any(c[:3] == ["gh", "pr", "create"] for c in seen)
    assert not any("push" in c for c in seen)  # nothing was pushed either
    store.close()


def test_the_cost_line_sums_every_run_on_the_card(tmp_path, monkeypatch):
    bodies = []
    _fake(monkeypatch)
    store, card_id = _store(tmp_path)
    # the worker, then the reviewer: the old code kept only the last - the reviewer's 0.30 over 3
    store.append_event(card_id, "result", {"total_cost_usd": 4.27, "num_turns": 37})
    store.append_event(card_id, "result", {"total_cost_usd": 0.30, "num_turns": 3})
    real_run = mr._run

    def _capture(cmd, cwd=None):
        if cmd[:3] == ["gh", "pr", "create"]:
            bodies.append(Path(cmd[cmd.index("--body-file") + 1]).read_text())
        return real_run(cmd, cwd=cwd)

    monkeypatch.setattr(mr, "_run", _capture)
    mr.open_merge_request(store, card_id, tmp_path, BRANCH)
    assert "$4.57" in bodies[0] and "40 turns" in bodies[0]
    store.close()


def test_an_existing_pr_is_reported_not_duplicated(tmp_path, monkeypatch):
    seen = _fake(monkeypatch, existing='[{"url": "https://github.com/o/r/pull/3"}]')
    store, card_id = _store(tmp_path)
    result = mr.open_merge_request(store, card_id, tmp_path, BRANCH)
    assert result.already_existed
    assert result.url == "https://github.com/o/r/pull/3"
    assert result.refusal is None, "an open PR that was pushed to is not a refusal"
    assert not any(c[:3] == ["gh", "pr", "create"] for c in seen)
    # the re-run's commits reach the open PR - before, they were never pushed
    pushes = [c for c in seen if c[0] == "git" and "push" in c]
    assert pushes and f"refs/heads/{BRANCH}:refs/heads/{BRANCH}" in pushes[0]
    assert not any("--force" in c or "-f" in c for c in pushes)
    store.close()


def test_no_code_path_anywhere_can_merge(tmp_path, monkeypatch):
    """the structural claim, tested structurally: the allowlist refuses the verb itself"""
    _fake(monkeypatch)
    assert ("pr", "merge") not in mr.ALLOWED_GH_COMMANDS
    with pytest.raises(mr.MergeRequestUnavailable):
        mr._gh(["pr", "merge", "--squash"], cwd=tmp_path)
    with pytest.raises(mr.MergeRequestUnavailable):
        mr._gh(["pr", "merge"], cwd=tmp_path)


def test_the_source_mentions_no_merge_and_no_auto_merge():
    """a grep, deliberately: a later edit that adds `--auto` to pr create would pass every other
    test in this file, because it would still open exactly one pull request"""
    source = Path(mr.__file__).read_text()
    code = "\n".join(line for line in source.splitlines() if not line.strip().startswith("#"))
    assert "--auto" not in code
    assert '"merge"' not in code.replace('("pr", "merge")', "")


def test_a_run_that_opens_a_pr_records_it_as_an_event(tmp_path, monkeypatch):
    _fake(monkeypatch)
    store, card_id = _store(tmp_path)
    mr.open_merge_request(store, card_id, tmp_path, BRANCH)
    events = [e for e in store.list_events(card_id) if e["kind"] == "merge_request"]
    assert len(events) == 1
    assert events[0]["payload"]["url"] == "https://github.com/o/r/pull/7"
    assert events[0]["payload"]["branch"] == BRANCH
    store.close()


def test_missing_gh_is_a_refusal_with_instructions(tmp_path, monkeypatch):
    _fake(monkeypatch)
    monkeypatch.setattr(mr.shutil, "which", lambda name: None)
    store, card_id = _store(tmp_path)
    with pytest.raises(mr.MergeRequestUnavailable, match="gh auth login"):
        mr.open_merge_request(store, card_id, tmp_path, BRANCH)
    store.close()


def test_unauthenticated_gh_refuses_before_pushing(tmp_path, monkeypatch):
    seen = _fake(monkeypatch)

    def _run(cmd, cwd=None):
        seen.append(list(cmd))
        return _Completed(1, stderr="not logged in")

    monkeypatch.setattr(mr, "_run", _run)
    monkeypatch.setattr(mr.shutil, "which", lambda name: "/usr/bin/gh")
    store, card_id = _store(tmp_path)
    with pytest.raises(mr.MergeRequestUnavailable, match="not authenticated"):
        mr.open_merge_request(store, card_id, tmp_path, BRANCH)
    assert not any("push" in c for c in seen)
    store.close()


def test_a_repo_with_no_remote_refuses(tmp_path, monkeypatch):
    _fake(monkeypatch)

    def _run(cmd, cwd=None):
        if cmd[:2] == ["gh", "auth"]:
            return _Completed(0)
        return _Completed(0, stdout="")  # no remotes

    monkeypatch.setattr(mr, "_run", _run)
    store, card_id = _store(tmp_path)
    with pytest.raises(mr.MergeRequestUnavailable, match="no git remote"):
        mr.open_merge_request(store, card_id, tmp_path, BRANCH)
    store.close()


def test_gh_pr_create_failing_is_a_refusal_not_an_exception(tmp_path, monkeypatch):
    _fake(monkeypatch)
    real_run = mr._run

    def _run(cmd, cwd=None):
        if cmd[:3] == ["gh", "pr", "create"]:
            return _Completed(1, stderr="pull request already exists")
        return real_run(cmd, cwd=cwd)

    monkeypatch.setattr(mr, "_run", _run)
    store, card_id = _store(tmp_path)
    result = mr.open_merge_request(store, card_id, tmp_path, BRANCH)
    assert not result.opened
    assert "already exists" in result.refusal
    store.close()


def test_a_real_repo_with_no_commits_ahead_is_seen_as_empty(tmp_path):
    """the one test that uses real git rather than the fake, so _branch_has_commits is checked
    against git's actual output rather than against a string this file invented"""
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(a, cwd=repo, capture_output=True, check=True)  # noqa: E731
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (repo / "f.txt").write_text("one")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "first")
    run("git", "branch", BRANCH)

    assert not mr._branch_has_commits(repo, BRANCH, "main")

    run("git", "checkout", "-q", BRANCH)
    (repo / "f.txt").write_text("two")
    run("git", "commit", "-qam", "second")
    assert mr._branch_has_commits(repo, BRANCH, "main")
