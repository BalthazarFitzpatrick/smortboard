"""real git review/free flow; worker, test gate, reviewer and github are isolated fakes"""

import json
import subprocess
import time
from pathlib import Path

import pytest

from smortboard import lifecycle, scheduler
from smortboard.exec.runner import RunResult
from smortboard.exec.worktrees import branch_name, worktree_path
from smortboard.review import merge_request
from smortboard.review.gates import GateResult
from smortboard.review.reviewer import ReviewResult
from smortboard.review.stacks import active_stack
from smortboard.store.api import Store


def run_git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


class CommitWorker:
    name = "fake"

    def run_card(self, store, card_id, worktree_path, prompt, settings_path, **kwargs):
        path = Path(worktree_path)
        filename = f"{card_id}.txt"
        (path / filename).write_text(f"work for {card_id}\n")
        run_git(path, "add", filename)
        run_git(path, "commit", "-qm", f"added {card_id}")
        return RunResult(
            subtype="success",
            is_error=False,
            blocked_reason_code=None,
            session_id="fake",
            total_cost_usd=0,
            num_turns=1,
            result_text="committed",
        )


class FakeGithub:
    def __init__(self, run_command):
        self.run_command = run_command
        self.prs = {}
        self.edits = []
        self.view_error = False

    def run(self, command, cwd=None):
        if command[0] != "gh":
            return self.run_command(command, cwd=cwd)
        args = command[1:]
        output = ""
        if args[:2] == ["auth", "status"]:
            pass
        elif args[:2] == ["pr", "list"]:
            head = args[args.index("--head") + 1]
            rows = [pr for pr in self.prs.values() if pr["head"] == head]
            output = (rows[0]["url"] if rows else "") if "--jq" in args else json.dumps(rows)
        elif args[:2] == ["pr", "create"]:
            url = f"https://github.test/repo/pull/{len(self.prs) + 1}"
            self.prs[url] = {
                "url": url,
                "head": args[args.index("--head") + 1],
                "base": args[args.index("--base") + 1],
                "state": "OPEN",
                "mergedAt": None,
            }
            output = url
        elif args[:2] == ["pr", "view"]:
            if self.view_error:
                return subprocess.CompletedProcess(command, 1, "", "github unavailable")
            pr = self.prs[args[2]]
            output = json.dumps({**pr, "baseRefName": pr["base"]})
        elif args[:2] == ["pr", "edit"]:
            base = args[args.index("--base") + 1]
            self.prs[args[2]]["base"] = base
            self.edits.append((args[2], base))
        else:
            raise AssertionError(f"unexpected github command: {command}")
        return subprocess.CompletedProcess(command, 0, output, "")


@pytest.fixture
def system(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    origin.mkdir()
    run_git(origin, "init", "--bare", "-q", "-b", "main")
    repo = tmp_path / "repo"
    run_git(tmp_path, "clone", "-q", str(origin), str(repo))
    run_git(repo, "config", "user.email", "test@example.invalid")
    run_git(repo, "config", "user.name", "test")
    (repo / "seed.txt").write_text("base\n")
    run_git(repo, "add", "seed.txt")
    run_git(repo, "commit", "-qm", "seeded repo")
    run_git(repo, "push", "-q", "origin", "main", "main:development")
    run_git(repo, "branch", "development", "origin/development")

    github = FakeGithub(merge_request._run)
    monkeypatch.setattr(merge_request, "_run", github.run)
    which = merge_request.shutil.which
    monkeypatch.setattr(
        merge_request.shutil, "which", lambda name: "/fake/gh" if name == "gh" else which(name)
    )
    monkeypatch.setattr(
        lifecycle,
        "run_test_gate",
        lambda *args, **kwargs: GateResult(True, "isolated gate", 0, "passed"),
    )
    monkeypatch.setattr(
        lifecycle, "run_review", lambda *args, **kwargs: ReviewResult(approved=True)
    )
    store = Store(tmp_path / "board.db")
    board = store.create_board("merge flow")
    registered = store.create_repo(
        board["id"], "repo", str(repo), "development", test_command="true", image="fake"
    )
    yield store, board, registered, repo, origin, github
    store.close()


def wait_for_landing(registry, card_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = registry.get(card_id)
        if state is not None and not state.running:
            return state
        time.sleep(0.01)
    pytest.fail("landing thread did not finish within ten seconds")


@pytest.mark.parametrize("external_proof", ["github", "git"])
def test_review_stack_accept_external_merge_then_free_card(system, external_proof):
    from smortboard.server.landings import LandingRegistry, perform_landing

    store, board, registered, repo, origin, github = system
    board_id, repo_id = board["id"], registered["id"]
    main_before = run_git(origin, "rev-parse", "main")
    development_before = run_git(origin, "rev-parse", "development")
    parent = store.create_card(
        board_id, repo_id, "parent", tasks=["commit parent"], leases=["*.txt"]
    )
    child = store.create_card(
        board_id,
        repo_id,
        "child",
        tasks=["commit child"],
        depends_on=[parent["id"]],
        leases=["*.txt"],
    )
    assert not store.board_merges_freely(board_id)
    parent_result = lifecycle.run_card_lifecycle(store, parent["id"], backend=CommitWorker())
    assert parent_result.phase == "opened", store.list_comments(parent["id"])
    assert store.get_card(parent["id"])["status"] == "checking"
    assert run_git(origin, "rev-parse", "development") == development_before
    assert github.prs[parent_result.pr_url]["base"] == "development"

    assert scheduler._dependency_wait(store, store.get_card(child["id"]), repo) is None
    child_result = lifecycle.run_card_lifecycle(store, child["id"], backend=CommitWorker())
    assert child_result.phase == "opened"
    assert store.get_card(child["id"])["status"] == "checking"
    assert github.prs[child_result.pr_url]["base"] == branch_name(parent["id"])
    child_tree = worktree_path(repo, child["id"])
    assert (child_tree / f"{parent['id']}.txt").exists()
    assert run_git(child_tree, "rev-parse", "HEAD^") == run_git(
        repo, "rev-parse", parent_result.branch
    )
    assert active_stack(store, child["id"])["parent_id"] == parent["id"]
    assert run_git(origin, "rev-parse", "development") == development_before

    registry = LandingRegistry(store.path)
    registry.start(parent["id"])
    landing_state = wait_for_landing(registry, parent["id"])
    assert landing_state.error is None
    assert store.get_card(parent["id"])["status"] == "accepted"
    parent_landed = run_git(origin, "rev-parse", "development")
    assert parent_landed != development_before
    assert github.edits == [(child_result.pr_url, "development")]
    assert active_stack(store, child["id"]) is None
    assert not worktree_path(repo, parent["id"]).exists()

    # an outside merge advances the bare remote before the operator accepts the child
    outside = repo.parent / "outside"
    run_git(repo.parent, "clone", "-q", str(origin), str(outside))
    run_git(outside, "config", "user.email", "test@example.invalid")
    run_git(outside, "config", "user.name", "test")
    run_git(outside, "checkout", "-qb", "development", "origin/development")
    run_git(
        outside,
        "merge",
        "--no-ff",
        "-m",
        "merged child externally",
        f"origin/{child_result.branch}",
    )
    run_git(outside, "push", "-q", "origin", "development")
    github.prs[child_result.pr_url].update(state="MERGED", mergedAt="2026-09-18T12:00:00Z")
    github.view_error = external_proof == "git"
    external_sha = run_git(origin, "rev-parse", "development")
    perform_landing(store, child["id"])
    assert store.get_card(child["id"])["status"] == "accepted"
    assert run_git(origin, "rev-parse", "development") == external_sha
    integrated = [
        event for event in store.list_events(child["id"]) if event["kind"] == "integrated"
    ]
    assert len(integrated) == 1
    assert integrated[0]["payload"]["via"] == external_proof

    store.set_setting("allow_free_merge", "on")
    store.set_board_merge_mode(board_id, "free")
    third = store.create_card(
        board_id, repo_id, "free card", tasks=["commit free card"], leases=["*.txt"]
    )
    third_result = lifecycle.run_card_lifecycle(store, third["id"], backend=CommitWorker())
    assert third_result.phase == "opened"
    assert store.get_card(third["id"])["status"] == "accepted"
    free_sha = run_git(origin, "rev-parse", "development")
    assert free_sha != external_sha
    assert run_git(origin, "show", f"development:{third['id']}.txt") == f"work for {third['id']}"
    assert run_git(origin, "rev-parse", "main") == main_before


def test_conflicted_parent_landing_keeps_child_stacked(system):
    from smortboard.review.decide import DecisionRefused
    from smortboard.server.landings import perform_landing

    store, board, registered, repo, origin, github = system
    parent = store.create_card(board["id"], registered["id"], "parent", leases=["*.txt"])
    child = store.create_card(
        board["id"], registered["id"], "child", depends_on=[parent["id"]], leases=["*.txt"]
    )
    lifecycle.run_card_lifecycle(store, parent["id"], backend=CommitWorker())
    child_result = lifecycle.run_card_lifecycle(store, child["id"], backend=CommitWorker())
    parent_tree = worktree_path(repo, parent["id"])
    child_tree = worktree_path(repo, child["id"])
    parent_before = run_git(parent_tree, "rev-parse", "HEAD")
    child_before = run_git(child_tree, "rev-parse", "HEAD")

    outside = repo.parent / "conflicting"
    run_git(repo.parent, "clone", "-q", str(origin), str(outside))
    run_git(outside, "config", "user.email", "test@example.invalid")
    run_git(outside, "config", "user.name", "test")
    run_git(outside, "checkout", "-qb", "development", "origin/development")
    filename = f"{parent['id']}.txt"
    (outside / filename).write_text("conflicting outside work\n")
    run_git(outside, "add", filename)
    run_git(outside, "commit", "-qm", "added conflicting work")
    run_git(outside, "push", "-q", "origin", "development")
    base_before = run_git(origin, "rev-parse", "development")

    with pytest.raises(DecisionRefused):
        perform_landing(store, parent["id"])
    refused = store.get_card(parent["id"])
    assert refused["status"] == "checking"
    assert refused["blocked_reason_code"] == "MERGE_CONFLICT"
    assert run_git(origin, "rev-parse", "development") == base_before
    assert run_git(parent_tree, "rev-parse", "HEAD") == parent_before
    assert run_git(child_tree, "rev-parse", "HEAD") == child_before
    assert active_stack(store, child["id"])["parent_id"] == parent["id"]
    assert github.prs[child_result.pr_url]["base"] == branch_name(parent["id"])
    assert github.edits == []
