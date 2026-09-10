"""one card, start to finish: worktree, run, test gate, reviewer, pull request.

This is the chain, and nothing else. Each step already exists and already knows how to refuse; what
this module adds is the order they go in, what a refusal at each step means for the card's status,
and the single fact that every step records into the event log as it passes.

TWO RULES SHAPE ALL OF IT.

The card never decides it is finished. The agent's own report that the tests pass is the agent
grading its own homework, so the board re-runs them; the agent's own view that the code is fine is
the same, so a separate reviewer reads the diff. A card reaches a pull request by passing two things
that do not care what it thinks.

The board never merges. The chain ends with an open pull request and a link, which is the point at
which a human takes over. There is no step after this one.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smortboard.exec.backends import CardRuntimeUnavailable, require_card_runtime
from smortboard.exec.leases import write_lease_settings
from smortboard.exec.worktrees import WorktreeError, create_worktree
from smortboard.review.gates import GateUnavailable, run_test_gate
from smortboard.review.merge_request import MergeRequestUnavailable, open_merge_request
from smortboard.review.reviewer import ReviewUnavailable, run_review
from smortboard.store.api import Store

# the phases a card passes through, in order. the last three are terminal
PHASES = (
    "preparing",
    "running",
    "testing",
    "reviewing",
    "opening",
    "opened",
    "blocked",
    "refused",
)

# who a board-written comment is from. cards already carry comments, so the pull request link lands
# where a human is already looking rather than needing a column of its own
BOARD_AUTHOR = "smortboard"


@dataclass
class LifecycleResult:
    card_id: str
    phase: str
    branch: str | None = None
    worktree: str | None = None
    pr_url: str | None = None
    blocked_reason_code: str | None = None
    # why the chain stopped short of a pull request without the card being at fault: no repo, no
    # test command, no Docker. distinct from blocked_reason_code, which is about the work
    refusal: str | None = None

    @property
    def finished(self) -> bool:
        return self.phase in ("opened", "blocked", "refused")


def build_card_prompt(card: dict[str, Any]) -> str:
    """the card, as the brief the agent is handed.

    Deliberately the whole card and nothing more: title, description, acceptance criteria, tasks.
    The lease and the house conventions arrive separately (runner.SYSTEM_PROMPT and
    runner.lease_preamble), so this stays the part a human actually wrote.
    """
    lines = [f"CARD: {card['title']}", ""]
    if card.get("description"):
        lines += [str(card["description"]), ""]

    criteria = [c["text"] for c in card.get("criteria") or []]
    if criteria:
        lines.append("Acceptance criteria - the work is judged against exactly these:")
        lines += [f"- {text}" for text in criteria]
        lines.append("")

    tasks = [t for t in card.get("tasks") or [] if not t.get("done")]
    if tasks:
        lines.append("Tasks:")
        lines += [f"- {t['text']}" for t in tasks]
        lines.append("")

    lines += [
        "You are already on the correct branch. Work only inside this repository.",
        "Commit your work when it is done - an uncommitted change is a change nobody can review.",
    ]
    return "\n".join(lines)


def _lease_globs(card: dict[str, Any]) -> list[str]:
    """the card's lease as a list of globs.

    get_card returns lease rows, not strings - passing the rows straight through produced a lease
    preamble that listed python dicts at the agent, and a settings file that matched nothing.
    """
    return [row["path_glob"] for row in card.get("leases") or []]


def _diff(repo_path: str | Path, base: str, branch: str) -> str:
    """what the card actually changed, which is what the reviewer reads.

    Three dots: the diff against the merge base, so work that landed on `base` while the card was
    running does not show up as something the card did.
    """
    result = subprocess.run(
        ["git", "-C", str(repo_path), "diff", f"{base}...{branch}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else ""


def _note(store: Store, card_id: str, text: str) -> None:
    store.add_comment(card_id, author=BOARD_AUTHOR, body=text)


def _block(store: Store, state: LifecycleResult, reason_code: str, note: str) -> LifecycleResult:
    """parks the card with a reason and flags it for a human.

    THE STATUS IS NOT THROWN AWAY. A blocked card keeps the column it was in and raises
    blocked_reason_code, so the board still shows what it was doing - which is what a resume
    briefing needs and what a sixth "blocked" column would have destroyed.
    """
    store.update_card(state.card_id, blocked_reason_code=reason_code, review_flag=True)
    _note(store, state.card_id, note)
    state.phase = "blocked"
    state.blocked_reason_code = reason_code
    return state


def _refuse(store: Store, state: LifecycleResult, note: str) -> LifecycleResult:
    """the board could not run this card at all - a missing repo, image or credential.

    Not a blocked_reason_code: those describe work that went wrong, and nothing here went wrong
    with the work. It is flagged for a human because only a human can fix any of these.
    """
    store.update_card(state.card_id, review_flag=True)
    _note(store, state.card_id, note)
    state.phase = "refused"
    state.refusal = note
    return state


def run_card_lifecycle(
    store: Store,
    card_id: str,
    token_path: str | Path | None = None,
    backend: Any | None = None,
    on_phase: Any | None = None,
) -> LifecycleResult:
    """runs one card the whole way, and returns where it stopped.

    `on_phase` is called with each phase name as it starts, so a caller can show progress without
    polling the event log. `backend` is for tests; production always takes the one container
    runtime, which refuses rather than falling back.
    """

    def phase(name: str) -> None:
        state.phase = name
        if on_phase is not None:
            on_phase(name)

    state = LifecycleResult(card_id=card_id, phase="preparing")
    phase("preparing")
    card = store.get_card(card_id)
    if not card.get("repo_id"):
        return _refuse(
            store, state, "This card has no repo, so there is nowhere for an agent to work."
        )
    repo = store.get_repo(card["repo_id"])
    base = repo.get("default_branch") or "main"

    try:
        runtime = backend or require_card_runtime(token_path)
    except CardRuntimeUnavailable as exc:
        return _refuse(store, state, f"The card runtime is not ready:\n{exc}")

    try:
        tree = create_worktree(repo["path"], card_id, base=base)
    except WorktreeError as exc:
        return _refuse(store, state, f"Could not cut a worktree for this card: {exc}")
    state.branch, state.worktree = tree.branch, str(tree.path)

    settings = write_lease_settings(tree.path, _lease_globs(card))
    store.update_card(card_id, status="doing", blocked_reason_code=None)

    phase("running")
    run = runtime.run_card(
        store,
        card_id,
        tree.path,
        build_card_prompt(card),
        settings,
        repo=repo,
        token_path=token_path,
    )
    if run.blocked_reason_code:
        return _block(
            store,
            state,
            run.blocked_reason_code,
            f"The run stopped: {run.blocked_reason_code}.\n\n{run.result_text or ''}".strip(),
        )

    # from here the card is work waiting to be judged, not work in progress
    store.update_card(card_id, status="checking")

    phase("testing")
    try:
        gate = run_test_gate(store, card_id, tree.path, repo)
    except GateUnavailable as exc:
        return _refuse(store, state, f"The test gate could not run: {exc}")
    if not gate.passed:
        return _block(
            store,
            state,
            "TESTS_FAILED",
            f"`{gate.command}` exited {gate.exit_code}.\n\n```\n{gate.output}\n```",
        )

    phase("reviewing")
    try:
        review = run_review(
            store,
            card_id,
            _diff(repo["path"], base, tree.branch),
            tree.path,
            settings,
            repo=repo,
            token_path=token_path,
        )
    except ReviewUnavailable as exc:
        return _refuse(store, state, f"The reviewer could not run: {exc}")
    if not review.approved:
        findings = "\n".join(
            f"- {f.severity} {f.category} in {f.file}: {f.message}" for f in review.findings
        )
        return _block(
            store,
            state,
            "REVIEW_REJECTED",
            f"The reviewer did not approve.\n\n{findings or review.error or ''}".strip(),
        )

    phase("opening")
    try:
        request = open_merge_request(store, card_id, repo["path"], tree.branch, base=base)
    except MergeRequestUnavailable as exc:
        return _refuse(store, state, f"Could not open a pull request: {exc}")

    if not request.url:
        return _refuse(store, state, f"Both gates passed, but: {request.refusal}")

    _note(
        store,
        card_id,
        f"Tests passed, the reviewer approved, and the pull request is open:\n{request.url}\n\n"
        "Merging is yours - the board stops here.",
    )
    store.update_card(card_id, review_flag=True)
    state.phase, state.pr_url = "opened", request.url
    return state
