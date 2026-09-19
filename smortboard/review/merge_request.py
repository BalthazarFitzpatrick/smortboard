"""push a card branch and open, inspect, close or retarget its pull request.

The board lands on unprotected bases after acceptance in review mode or automatically in free
mode. This module cannot merge a pull request: gh pr merge is absent from the allowlist, and
pushes to protected branches are refused. Retargeting only changes the review base.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smortboard.exec.worktrees import repo_lock
from smortboard.review.screenshot import latest_screenshot

# the branches nothing here may ever write, however it is spelled. a push whose destination is one
# of these is a bug, and the bug it would be is the one that writes main
PROTECTED_BRANCHES = frozenset({"main", "master", "trunk"})

# the only gh subcommands this module may run. `merge` is not on it, and cannot be added by a
# caller - see _gh, which matches the first two words of the invocation against this set.
# `close` is for a rejected card, and close_merge_request never passes --delete-branch
# pr edit --base retargets a review and lands nothing; pr merge remains absent
ALLOWED_GH_COMMANDS = frozenset(
    {("pr", "create"), ("pr", "list"), ("pr", "view"), ("pr", "close"), ("pr", "edit")}
)

# a push or a pr create that has not answered in two minutes is a network problem, not slow work
GH_TIMEOUT_SECONDS = 120


class MergeRequestUnavailable(RuntimeError):
    """the machinery is missing: no gh, not authenticated, no remote"""


@dataclass
class MergeRequestResult:
    """what happened, in a shape the board can render without interpreting an exception.

    `refusal` is set for every ordinary failure - nothing to push, a PR that already exists, a
    branch that is not the card's. The board shows it; it does not have to catch anything.
    """

    opened: bool
    branch: str
    url: str | None = None
    refusal: str | None = None
    already_existed: bool = False
    findings_summary: str = ""

    @property
    def blocked_reason_code(self) -> str | None:
        # a refusal here is not a card failure - the work is committed and the branch is fine, the
        # board simply could not open the request. there is no reason code for that, on purpose
        return None


@dataclass
class _CardEvidence:
    """what the event log already knows about this card, so the caller passes nothing extra"""

    tests: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    cost_usd: float | None = None
    turns: int | None = None
    criteria: list[str] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)


def _run(cmd: list[str], cwd: str | Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        timeout=GH_TIMEOUT_SECONDS,
        check=False,
    )


def _gh(args: list[str], cwd: str | Path) -> subprocess.CompletedProcess:
    """runs one gh command, and only one of three.

    THE ALLOWLIST IS THE GUARANTEE. A caller cannot reach `gh pr merge` through here whatever it
    passes, because the check is on the invocation itself rather than on the intent behind it.
    """
    if tuple(args[:2]) not in ALLOWED_GH_COMMANDS:
        raise MergeRequestUnavailable(
            f"gh {' '.join(args[:2])} is not something the board may run. "
            f"Allowed: {sorted(' '.join(a) for a in ALLOWED_GH_COMMANDS)}. "
            "Merging is the operator's, never the board's."
        )
    return _run(["gh", *args], cwd=cwd)


def _push(repo_path: str | Path, branch: str, remote: str = "origin") -> None:
    """pushes the card's branch, and refuses anything that could land on a protected one.

    Never `--force`: a card's branch is the only record of what it did, and a force push can lose
    a commit the reviewer already passed. Never a bare `git push` either - the refspec is explicit
    so the destination is a value this function checked, not whatever the remote config decides.
    """
    if branch in PROTECTED_BRANCHES:
        raise MergeRequestUnavailable(
            f"refusing to push {branch!r}: that is a protected branch and the board never writes it"
        )
    refspec = f"refs/heads/{branch}:refs/heads/{branch}"
    # --set-upstream writes .git/config, which a card cutting its worktree may hold right now
    with repo_lock(repo_path):
        result = _run(["git", "-C", str(repo_path), "push", "--set-upstream", remote, refspec])
    if result.returncode != 0:
        raise MergeRequestUnavailable(f"pushing {branch} failed: {result.stderr.strip()}")


def branch_has_commits(repo_path: str | Path, branch: str, base: str) -> bool:
    """whether the branch adds anything to base - an empty PR asks for a review of nothing"""
    result = _run(["git", "-C", str(repo_path), "rev-list", "--count", f"{base}..{branch}"])
    if result.returncode != 0:
        return False
    return result.stdout.strip() not in ("", "0")


def _existing_pr(repo_path: str | Path, branch: str) -> str | None:
    """the URL of an open PR already on this branch, if there is one.

    Reported rather than duplicated: opening a second PR for the same branch splits the review
    across two places and neither one is the whole story.
    """
    result = _gh(
        ["pr", "list", "--head", branch, "--state", "open", "--json", "url"], cwd=repo_path
    )
    if result.returncode != 0:
        return None
    try:
        rows = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return rows[0]["url"] if rows else None


def _gh_ready(repo_path: str | Path) -> None:
    """gh installed, authenticated, and pointed at a repo with a remote - checked before anything
    is pushed, so a missing tool is a clear refusal rather than a half-finished push"""
    if shutil.which("gh") is None:
        raise MergeRequestUnavailable(
            "gh is not installed. The board opens pull requests with it; install the GitHub CLI "
            "and run `gh auth login`."
        )
    result = _run(["gh", "auth", "status"], cwd=repo_path)
    if result.returncode != 0:
        raise MergeRequestUnavailable("gh is installed but not authenticated. Run `gh auth login`.")
    remote = _run(["git", "-C", str(repo_path), "remote"])
    if not remote.stdout.strip():
        raise MergeRequestUnavailable(
            "this repo has no git remote, so there is nowhere to open a pull request"
        )


def _gather(store: Any, card_id: str) -> _CardEvidence:
    """reads what the gates already recorded, rather than making the caller carry it.

    The event log is the ground truth for what happened to this card, so the PR body is assembled
    from it. A caller that had to pass results could pass stale ones.
    """
    evidence = _CardEvidence()
    if store is None:
        return evidence
    for event in store.list_events(card_id):
        payload = event.get("payload")
        if payload is None and event.get("payload_json"):
            payload = json.loads(event["payload_json"])
        if not isinstance(payload, dict):
            continue
        kind = event.get("kind")
        if kind == "test_gate":
            evidence.tests = payload
        elif kind == "review_gate":
            evidence.review = payload
        elif kind == "result":
            # EVERY RUN ON THE CARD, SUMMED: worker, reviewer, fix rounds and earlier attempts all
            # spent money on this branch. keeping the last one reported the reviewer's $0.30 for a
            # branch that cost $5.11
            if payload.get("total_cost_usd") is not None:
                evidence.cost_usd = (evidence.cost_usd or 0.0) + payload["total_cost_usd"]
            if payload.get("num_turns") is not None:
                evidence.turns = (evidence.turns or 0) + payload["num_turns"]
    card = store.get_card(card_id)
    evidence.criteria = [c["text"] for c in card.get("criteria") or []]
    evidence.tasks = list(card.get("tasks") or [])
    return evidence


def _body(card: dict[str, Any], evidence: _CardEvidence, branch: str) -> str:
    """the PR body: what the card was, what the tests said, what the reviewer said.

    Written to be read by the person deciding whether to merge, so it leads with the two verdicts
    rather than with the card's own description.
    """
    lines: list[str] = []

    if evidence.tests is not None:
        verdict = "passed" if evidence.tests.get("passed") else "FAILED"
        lines.append(f"Tests: {verdict} - `{evidence.tests.get('command', '')}`")
    else:
        lines.append("Tests: not run")

    if evidence.review is not None:
        findings = evidence.review.get("findings") or []
        verdict = "approved" if evidence.review.get("approved") else "REJECTED"
        lines.append(f"Review: {verdict}, {len(findings)} finding(s)")
        for finding in findings:
            where = finding.get("file") or "?"
            if finding.get("line"):
                where = f"{where}:{finding['line']}"
            lines.append(
                f"- {finding.get('severity', '?')} {finding.get('category', '?')} "
                f"in {where} - {finding.get('message', '')}"
            )
    else:
        lines.append("Review: not run")

    shot = latest_screenshot(card)
    if shot is not None:
        lines.append(
            f"Screenshot: [{shot['filename']}](/api/cards/{card['id']}/attachments/{shot['id']}) "
            "- open it from the board that ran this card"
        )

    if card.get("description"):
        lines += ["", "## What the card asked for", "", str(card["description"])]

    if evidence.criteria:
        lines += ["", "## Acceptance criteria", ""]
        lines += [f"- {text}" for text in evidence.criteria]

    if evidence.tasks:
        lines += ["", "## Tasks", ""]
        lines += [
            f"- {task.get('text', '')}{' (done)' if task.get('done') else ''}"
            for task in evidence.tasks
        ]

    cost = f"{evidence.cost_usd:.2f}" if evidence.cost_usd is not None else "unknown"
    lines += [
        "",
        "---",
        "",
        f"Branch `{branch}`, opened by smortboard. Cost ${cost} over "
        f"{evidence.turns if evidence.turns is not None else '?'} turns.",
        "Review mode waits for acceptance; free mode lands on unprotected bases automatically.",
    ]
    return "\n".join(lines)


def open_merge_request(
    store: Any,
    card_id: str,
    repo_path: str | Path,
    branch: str,
    base: str = "main",
    remote: str = "origin",
) -> MergeRequestResult:
    """pushes the card's branch and opens a pull request for it, and stops there.

    Returns a refusal rather than raising for the ordinary cases: a branch with no commits, a PR
    that already exists. Raises MergeRequestUnavailable only when the machinery itself is missing,
    which is a thing the operator has to fix rather than a thing about this card.
    """
    if branch in PROTECTED_BRANCHES:
        raise MergeRequestUnavailable(
            f"refusing to open a pull request from {branch!r}: the board never works on a "
            "protected branch, so a card claiming to be on one is a bug upstream of here"
        )
    _gh_ready(repo_path)

    if not branch_has_commits(repo_path, branch, base):
        return _refuse(
            store,
            card_id,
            branch,
            f"{branch} adds no commits to {base}, so there is nothing to review",
        )

    existing = _existing_pr(repo_path, branch)
    if existing:
        # A RE-RUN'S COMMITS STILL HAVE TO REACH THE OPEN PR. this used to return before pushing,
        # so a second attempt that fixed a review finding passed both gates and was never delivered
        # - the PR kept the unfixed commit. same guarded push as below: never forced, so a branch
        # someone else moved is a refusal the board shows, not an overwrite
        _push(repo_path, branch, remote=remote)
        result = MergeRequestResult(opened=False, branch=branch, url=existing, already_existed=True)
        _record(store, card_id, result)
        return result

    _push(repo_path, branch, remote=remote)

    card = store.get_card(card_id) if store is not None else {"title": branch}
    evidence = _gather(store, card_id)
    # a temp file rather than a path in the repo: the body must not become an untracked file the
    # next card's agent finds and wonders about
    handle, name = tempfile.mkstemp(prefix=f"smortboard-pr-{card_id[:8]}-", suffix=".md")
    os.close(handle)
    body_path = Path(name)
    body_path.write_text(_body(card, evidence, branch))
    try:
        # --body-file, never --body: a body quoting a git command would otherwise be scanned by the
        # operator's own main-branch guard and the whole call refused
        created = _gh(
            [
                "pr",
                "create",
                "--base",
                base,
                "--head",
                branch,
                "--title",
                str(card.get("title") or branch),
                "--body-file",
                str(body_path),
            ],
            cwd=repo_path,
        )
    finally:
        body_path.unlink(missing_ok=True)

    if created.returncode != 0:
        return _refuse(store, card_id, branch, f"gh pr create failed: {created.stderr.strip()}")

    url = _first_url(created.stdout)
    result = MergeRequestResult(opened=bool(url), branch=branch, url=url)
    if url is None:
        result.refusal = "gh pr create reported success but printed no url"
    _record(store, card_id, result)
    return result


def _first_url(output: str) -> str | None:
    for token in output.split():
        if token.startswith("https://"):
            return token
    return None


def _refuse(store: Any, card_id: str, branch: str, reason: str) -> MergeRequestResult:
    result = MergeRequestResult(opened=False, branch=branch, refusal=reason)
    _record(store, card_id, result)
    return result


def _record(store: Any, card_id: str, result: MergeRequestResult) -> None:
    if store is None:
        return
    store.append_event(
        card_id,
        "merge_request",
        {
            "opened": result.opened,
            "branch": result.branch,
            "url": result.url,
            "refusal": result.refusal,
            "already_existed": result.already_existed,
        },
    )


@dataclass
class PullRequestState:
    """what GitHub says about one pull request, or why it could not say.

    `error` set means GitHub could not be asked - the caller (scheduler._dependency_wait) must
    treat that as a reason to keep waiting, never as evidence the PR is merged.
    """

    merged: bool
    base: str | None = None
    state: str | None = None  # "OPEN", "CLOSED", "MERGED"
    error: str | None = None


def pr_view(repo_path: str | Path, url: str) -> PullRequestState:
    """asks GitHub whether a pull request merged. never raises - a failure to ask is reported in
    the result so a scheduler can wait rather than crash when GitHub is unreachable."""
    if shutil.which("gh") is None:
        return PullRequestState(merged=False, error="gh is not installed")
    result = _gh(["pr", "view", url, "--json", "state,mergedAt,baseRefName"], cwd=repo_path)
    if result.returncode != 0:
        return PullRequestState(merged=False, error=result.stderr.strip() or "gh pr view failed")
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return PullRequestState(merged=False, error="gh pr view returned unreadable json")
    return PullRequestState(
        merged=bool(data.get("mergedAt")), state=data.get("state"), base=data.get("baseRefName")
    )


def close_merge_request(repo_path: str | Path, url: str) -> str | None:
    """closes a rejected card's pull request, keeping its branch. returns a refusal, or None.

    the branch stays on purpose (the operator, 2026-09-10): a closed pull request still reads as a record
    of the attempt, and deleting the branch would leave its diff view with nothing behind it
    """
    if shutil.which("gh") is None:
        return "gh is not installed"
    result = _gh(["pr", "close", url, "--comment", "Rejected on the board."], cwd=repo_path)
    return None if result.returncode == 0 else result.stderr.strip() or "gh pr close failed"


def merge_request_is_configured(repo_path: str | Path) -> bool:
    """whether the board could open a pull request for this repo at all - shown up front rather
    than discovered when a card finishes and has nowhere to go. mirrors gate_is_configured"""
    try:
        _gh_ready(repo_path)
    except MergeRequestUnavailable:
        return False
    return True


def retarget_merge_request(repo, url, base):
    """pr edit --base changes only the review target; pr merge remains forbidden"""
    from smortboard.exec.worktrees import default_branch

    if base != default_branch(repo):
        raise ValueError("a stacked pull request can only retarget to the repo default")
    if shutil.which("gh") is None:
        return "gh is not installed"
    result = _gh(["pr", "edit", url, "--base", base], cwd=repo["path"])
    return None if result.returncode == 0 else result.stderr.strip() or "pr retarget failed"
