"""one card, start to finish: worktree, run, test gate, reviewer, pull request.

This is the chain, and nothing else. Each step already exists and already knows how to refuse; what
this module adds is the order they go in, what a refusal at each step means for the card's status,
and the single fact that every step records into the event log as it passes.

TWO RULES SHAPE ALL OF IT.

The card never decides it is finished. The agent's own report that the tests pass is the agent
grading its own homework, so the board re-runs them; the agent's own view that the code is fine is
the same, so a separate reviewer reads the diff. A card reaches a pull request by passing two things
that do not care what it thinks.

The board never merges into main. On a repo whose base is main, the chain ends with an open pull
request and a link, which is the point at which a human takes over. On a repo whose base is not
protected, review mode waits for acceptance before landing; free mode lands automatically.
Review-mode dependents may stack on one waiting parent, up to three cards deep. Merging
development into main stays the operator's.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smortboard.actions import with_next
from smortboard.briefing import repeats_failed_attempt, resume_briefing
from smortboard.exec.backends import (
    CardRuntimeUnavailable,
    require_card_runtime,
    write_container_guards,
)
from smortboard.exec.runner import ProcessHandle
from smortboard.exec.worktrees import (
    WorktreeError,
    add_worktree,
    branch_diff,
    branch_diverged_from_origin,
    branch_exists,
    branch_name,
    create_worktree,
    default_branch,
    existing_worktree,
    fetch_base,
    has_remote,
    rev_parse,
    worktree_path,
)
from smortboard.labs.routing import command_model, run_ref
from smortboard.operator import AUTHOR_KEY, OPERATOR_NAME
from smortboard.repo_image import rebuild_if_stale
from smortboard.review.base_red import check_base_red
from smortboard.review.gates import GateUnavailable, NoTestCommand, run_test_gate
from smortboard.review.integrate import integrate, open_release_request
from smortboard.review.merge_request import (
    PROTECTED_BRANCHES,
    MergeRequestUnavailable,
    branch_has_commits,
    open_merge_request,
)
from smortboard.review.mergeable import sync_with_base
from smortboard.review.reviewer import (
    DEFAULT_REVIEW_BUDGET_USD,
    ReviewResult,
    ReviewUnavailable,
    run_review,
)
from smortboard.review.screenshot import diff_touches_ui, take_screenshot
from smortboard.review.stacks import (
    active_stack,
    dependency_landed,
    integration_base,
    stacked_children,
    stacking_parent,
)
from smortboard.store.api import Store

# the phases a card passes through, in order. the last four are terminal
PHASES = (
    "preparing",
    "running",
    "testing",
    "reviewing",
    "fixing",
    "opening",
    "opened",
    "blocked",
    "refused",
    "stopped",
)

# how often a card on the fix route gets its findings back before it goes to a human. the operator chose
# two on 2026-09-10 - each round is roughly one more card run, so this also caps the cost
MAX_FIX_ROUNDS = 2

# the models when neither the card nor the board's settings name one
DEFAULT_WORKER_MODEL = "sonnet"
DEFAULT_REVIEWER_MODEL = "sonnet"

# who a board-written comment is from. cards already carry comments, so the pull request link lands
# where a human is already looking rather than needing a column of its own
BOARD_AUTHOR = "smortboard"

# what the card says when the model api refused its token - it lasts a year from setup-token
TOKEN_REFUSED_NOTE = (
    "The card token was refused (HTTP 401): it has expired or been revoked. Run "
    "`claude setup-token` in your own terminal, write the new token to "
    "~/.config/smortboard/card_token (mode 600), then run this card again."
)

# what the card says when its run ended without committing - the gates would only test the base
NO_COMMITS_NOTE = (
    "The run ended without a commit on {branch}, so there is nothing to test or review - the gates "
    "were skipped. Its last words are in the timeline; anything it wrote but did not commit is "
    "gone. Run it again, with a note if it stopped early."
)

# what the card says when it has no lease - an empty one lets the agent write nowhere at all
NO_LEASE_NOTE = (
    "This card has no lease, so its agent could not Edit or Write a single file. Set the path "
    'globs it may write (PATCH /api/cards/<id> with {"leases": ["src/thing/**", "tests/**"]}), '
    "then run it again."
)

# how a refusal over missing Docker or a missing credential starts - the scheduler reads it to
# retry the card with backoff instead of leaving it for the operator on the first try
RUNTIME_NOT_READY = "The card runtime is not ready"

# a run that ends on one of these never got to say it was done - it just ran out of budget or
# turns mid-turn. with commits on the branch that is still real work, so it goes to the gates
# like a normal finish; with none, it is blocked like any other failed run, but the note names
# the actual limit instead of a bare CRASH
BUDGET_CAPPED_SUBTYPES = frozenset({"error_max_budget_usd", "error_max_turns"})

NO_COMMITS_BUDGET_NOTE = (
    "The run hit its {limit} before committing anything, so there is nothing to test or review. "
    "Its last words are in the timeline. Run it again, with a note if it stopped early."
)


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
    fix_rounds: int = 0

    @property
    def finished(self) -> bool:
        return self.phase in ("opened", "blocked", "refused", "stopped")


def build_card_prompt(card: dict[str, Any], briefing: str | None = None) -> str:
    """the card, as the brief the agent is handed.

    Deliberately the whole card and nothing more: title, description, acceptance criteria, tasks.
    The lease and the house conventions arrive separately (runner.SYSTEM_PROMPT and
    runner.lease_preamble), so this stays the part a human actually wrote.

    `briefing` is resume_briefing's output for a card that already ran - None (the default) leaves
    the prompt exactly as it was before resume briefings existed, which is what the fresh-card tests
    assert byte-for-byte.
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

    # every note so far, even ones already delivered live: a fresh session remembers none of them
    notes = [c["body"] for c in card.get("comments") or [] if c.get("author") == AUTHOR_KEY]
    if notes:
        lines.append(f"Notes from {OPERATOR_NAME}, oldest first:")
        lines += [f"- {text}" for text in notes]
        lines.append("")

    if briefing:
        lines.append("What already happened - this card ran before:")
        lines.append(briefing)
        lines.append("")

    lines += [
        "You are already on the correct branch. Work only inside this repository.",
        "Commit your work when it is done - an uncommitted change is a change nobody can review.",
    ]
    return "\n".join(lines)


def _format_findings(review: ReviewResult) -> str:
    return "\n".join(
        f"- {f.severity} {f.category} in {f.file}"
        + (f":{f.line}" if f.line is not None else "")
        + f": {f.message}"
        for f in review.findings
    )


def build_fix_prompt(card: dict[str, Any], review: ReviewResult) -> str:
    """a fix round's brief: the findings to address, with the card underneath for context.

    A fresh headless session remembers nothing of the first run, so the card goes along - but the
    instruction is to fix these findings and nothing else, not to redo the card.
    """
    return "\n".join(
        [
            "Your work on this card is already committed on this branch. A reviewer read the diff",
            "and did not approve it. Fix these findings, and nothing else, then commit the fix:",
            "",
            _format_findings(review),
            "",
            "For context, the card was:",
            "",
            build_card_prompt(card),
        ]
    )


def _lease_globs(card: dict[str, Any]) -> list[str]:
    """the card's lease as a list of globs.

    get_card returns lease rows, not strings - passing the rows straight through produced a lease
    preamble that listed python dicts at the agent, and a settings file that matched nothing.
    """
    return [row["path_glob"] for row in card.get("leases") or []]


def _note(store: Store, card_id: str, text: str) -> None:
    store.add_comment(card_id, author=BOARD_AUTHOR, body=text)


def _block(store: Store, state: LifecycleResult, reason_code: str, note: str) -> LifecycleResult:
    """parks the card with a reason and flags it for a human.

    THE STATUS IS NOT THROWN AWAY. A blocked card keeps the column it was in and raises
    blocked_reason_code, so the board still shows what it was doing - which is what a resume
    briefing needs and what a sixth "blocked" column would have destroyed.
    """
    store.update_card(state.card_id, blocked_reason_code=reason_code, review_flag=True)
    _note(store, state.card_id, with_next(note, reason_code))
    state.phase = "blocked"
    state.blocked_reason_code = reason_code
    return state


def _block_on_failed_gate(
    store: Store,
    state: LifecycleResult,
    card_id: str,
    repo: dict[str, Any],
    base: str,
    gate: Any,
    after_merging: str = "",
) -> LifecycleResult:
    """blocks TESTS_FAILED, or BASE_RED when every failing test also fails on a clean checkout of
    the base - the card did not cause those, and a worker run cannot fix them."""
    note = _tests_failed_note(gate.command, gate.exit_code, gate.output, after_merging)
    try:
        red = check_base_red(repo, base, card_id, gate.output)
    except (GateUnavailable, OSError, subprocess.SubprocessError):
        red = None
    if red is not None:
        sha, ids = red
        store.append_event(card_id, "base_red", {"base": base, "sha": sha, "tests": ids})
        note = (
            f"BASE IS RED: {len(ids)} failing test(s) also fail on {base} ({sha[:8]}) without any "
            "of this card's changes, so the card did not cause them. Fix the base or merge a fix "
            f"into it, then run the card again: {', '.join(t.rsplit('::', 1)[-1] for t in ids[:3])}"
            f"{' ...' if len(ids) > 3 else ''}\n\n{note}"
        )
    return _block(store, state, "TESTS_FAILED" if red is None else "BASE_RED", note)


def _base_for_fresh_cut(store: Store, state: LifecycleResult, repo_path: str, base: str) -> str:
    """the ref a fresh worktree is cut from, for a card that depends on another.

    review mode can cut from one ready parent's branch. once dependencies have landed, fetch
    the default base so the cut contains their commits even when the local branch is stale.
    """
    card = store.get_card(state.card_id)
    parent = stacking_parent(store, card)
    if parent:
        branch = branch_name(parent["id"])
        store.append_event(
            state.card_id, "stacked_on", {"parent_id": parent["id"], "branch": branch}
        )
        return branch
    if not has_remote(repo_path):
        return base
    if fetch_base(repo_path, base):
        return f"origin/{base}"
    _note(
        store,
        state.card_id,
        f"could not fetch '{base}' from origin before cutting this card's worktree, so its "
        "dependency's merge may not be visible yet. cutting from the local branch instead.",
    )
    return base


# pytest names a failure as "FAILED path/to/test.py::TestCase::test_name" - the test name is the
# part after the last "::", which is what a person actually scans for
_PYTEST_FAILED_RE = re.compile(r"^FAILED\s+(\S+)", re.MULTILINE)
# ruff names a finding as "path:line:col: CODE message" - path:line plus the code is enough to spot
_RUFF_ERROR_RE = re.compile(r"^(\S+\.py):(\d+):\d+: (\w+\d*)", re.MULTILINE)
# how many names the one-liner spells out before folding the rest into "(+N more)"
_HEADLINE_SHOWN = 2


def _failing_tests_headline(output: str) -> str:
    """the gate's output, boiled down to "tests failed: test_x, test_y (+3 more)" - or the bare
    "tests failed" once nothing recognisable parses, rather than guessing at a shape it doesn't
    have. pytest's FAILED lines win when present; ruff's path:line:col lines are the fallback."""
    names: list[str] = []
    for match in _PYTEST_FAILED_RE.finditer(output):
        name = match.group(1).rsplit("::", 1)[-1]
        if name not in names:
            names.append(name)
    if not names:
        for match in _RUFF_ERROR_RE.finditer(output):
            path, line, code = match.groups()
            label = f"{path}:{line} {code}"
            if label not in names:
                names.append(label)
    if not names:
        return "tests failed"
    shown = names[:_HEADLINE_SHOWN]
    extra = len(names) - len(shown)
    headline = f"tests failed: {', '.join(shown)}"
    return f"{headline} (+{extra} more)" if extra else headline


def _tests_failed_note(command: str, exit_code: int, output: str, after_merging: str = "") -> str:
    """the stored comment for a TESTS_FAILED block: the parsed headline leads, the full gate
    command and output stay in the body for whoever opens the details."""
    context = f" after merging {after_merging} in" if after_merging else ""
    return (
        f"{_failing_tests_headline(output)}{context}.\n\n"
        f"`{command}` exited {exit_code}.\n\n```\n{output}\n```"
    )


# a repo with no test_command is a board setting, not a fault in this run - the pre-flight
# checklist already explains it once per repo, so the card only needs a pointer back to it
def _gate_unavailable_note(exc: GateUnavailable) -> str:
    if isinstance(exc, NoTestCommand):
        return "repo has no test command - set it in b"
    return f"The test gate could not run: {exc}"


def _refuse(store: Store, state: LifecycleResult, note: str) -> LifecycleResult:
    """the board could not run this card at all - a missing repo, image or credential.

    Not a blocked_reason_code: those describe work that went wrong, and nothing here went wrong
    with the work. It is flagged for a human because only a human can fix any of these.
    """
    store.update_card(state.card_id, review_flag=True)
    _note(store, state.card_id, with_next(note, "refused"))
    # the attempt's own record of how it ended - a refusal after both gates otherwise read as
    # still in progress to telemetry, which only sees events
    store.append_event(state.card_id, "run_refused", {"note": note})
    state.phase = "refused"
    state.refusal = note
    return state


def _stopped(store: Store, state: LifecycleResult) -> LifecycleResult:
    """the operator pulled the run. the card keeps its worktree and commits for a later run to resume -
    this only records that it stopped short, deliberately, of gates/reviewer/pull request.

    NOT a blocked_reason_code: the schema's CHECK constraint enumerates those, and "stopped" is not
    a claim about the work - it is the operator's own decision, not something that went wrong.
    """
    store.update_card(state.card_id, review_flag=True)
    _note(store, state.card_id, with_next(f"Stopped by {OPERATOR_NAME}.", "stopped"))
    store.append_event(state.card_id, "run_stopped", {})
    state.phase = "stopped"
    return state


def _sync_and_retest(
    store: Store,
    state: LifecycleResult,
    card_id: str,
    tree: Any,
    repo: dict[str, Any],
    base: str,
    *,
    always_test: bool = False,
) -> LifecycleResult | None:
    """merges the base into the card branch: a conflict blocks the card, and anything new from the
    base reruns the tests. None means the branch is current and still passes"""
    # measured 2026-09-14 (card 59727ba3, PR #112): a branch cut once and never updated drifted 34
    # commits behind main while its PR waited, and conflicted in four files other PRs had since
    # touched. one more sync right before the PR is opened is what this closes
    merge_result = sync_with_base(tree.path, base)
    if merge_result is not None and not merge_result.clean:
        base_ref = f"origin/{base}"
        store.append_event(
            card_id,
            "merge_conflict",
            {"base_ref": base_ref, "files": merge_result.conflicting_files},
        )
        return _block(
            store,
            state,
            "MERGE_CONFLICT",
            f"Merging {base_ref} into {tree.branch} conflicts in: "
            f"{', '.join(merge_result.conflicting_files) or 'unknown files'}.\n\n"
            "Resuming this card lets the worker merge the base branch and resolve them.",
        )
    if always_test or (merge_result is not None and merge_result.merged):
        try:
            gate = run_test_gate(store, card_id, tree.path, repo)
        except GateUnavailable as exc:
            return _refuse(
                store,
                state,
                _gate_unavailable_note(exc)
                if isinstance(exc, NoTestCommand)
                else f"The test gate could not run after merging {base}: {exc}",
            )
        if not gate.passed:
            return _block_on_failed_gate(
                store, state, card_id, repo, base, gate, after_merging=base
            )
    return None


def _integrate(store, state, card, tree, repo, base, url):
    from smortboard.review.land_card import land_card

    return land_card(
        store,
        state,
        card,
        tree,
        repo,
        base,
        url,
        sync=_sync_and_retest,
        integrate_fn=integrate,
        release_fn=open_release_request,
    )


def run_card_lifecycle(
    store: Store,
    card_id: str,
    token_path: str | Path | None = None,
    backend: Any | None = None,
    on_phase: Any | None = None,
    pending_notes: Callable[[], list[dict[str, Any]]] | None = None,
    stop_requested: Callable[[], bool] | None = None,
    on_process: Callable[[ProcessHandle], None] | None = None,
) -> LifecycleResult:
    """runs one card the whole way, and returns where it stopped.

    `on_phase` is called with each phase name as it starts, so a caller can show progress without
    polling the event log. `backend` is for tests; production always takes the one container
    runtime, which refuses rather than falling back.

    `pending_notes` is live steering: passed straight through to every worker run (the initial run
    and any fix rounds) so a note the operator leaves mid-run reaches the agent at its next step, rather
    than waiting for the card's next run. None outside RunRegistry (e.g. in
    tests) means notes fall back to arriving next run only, same as before.

    `stop_requested`, checked after every step that can take a while, is what makes a stop actually
    stop the chain rather than merely killing the process underneath one step: the very next check
    ends the run at "stopped" instead of continuing to the next gate. `on_process` is handed the
    live process/container handle for whichever step is running, so RunRegistry.stop() can reach it.
    """

    def phase(name: str) -> None:
        state.phase = name
        if on_phase is not None:
            on_phase(name)

    def stopped_now() -> bool:
        return stop_requested is not None and stop_requested()

    state = LifecycleResult(card_id=card_id, phase="preparing")
    phase("preparing")
    card = store.get_card(card_id)
    if any(store.get_card(dep)["status"] == "rejected" for dep in card["depends_on"]):
        return _refuse(
            store,
            state,
            "A dependency was rejected. Reject this dependent attempt and define fresh work before running it.",
        )
    if card["status"] == "rejected" and stacked_children(store, card):
        return _refuse(
            store,
            state,
            "This rejected branch is still the base of undecided stacked cards. Decide those cards and create fresh work before rerunning it.",
        )
    # an accepted card keeps its branch for the open pull request - cutting a fresh worktree would
    # fail anyway, and recording lifecycle_started here would bury the real attempt's outcome
    if card["status"] == "accepted":
        return _refuse(
            store,
            state,
            "This card is accepted; its branch is kept for the pull request. Reject it first to "
            "run it again.",
        )
    # marks where this attempt's events begin, so the outcome of a re-run is not mixed with the last
    store.append_event(card_id, "lifecycle_started", {})
    if not card.get("repo_id"):
        return _refuse(
            store, state, "This card has no repo, so there is nowhere for an agent to work."
        )
    # the guard refuses every Edit and Write over an empty lease - measured, a run spent $1.91 and
    # 61 turns probing for a writable path before stopping to ask
    if not _lease_globs(card):
        return _refuse(store, state, NO_LEASE_NOTE)
    repo = store.get_repo(card["repo_id"])
    rebuild = rebuild_if_stale(repo)
    if rebuild is not None:
        if rebuild.ok and rebuild.tag:
            store.set_repo_image(repo["id"], rebuild.tag)
            store.append_event(card_id, "repo_image_rebuilt", {"tag": rebuild.tag})
            repo = store.get_repo(card["repo_id"])
        else:
            # do not refuse the card over a failed rebuild - it runs against the stale image,
            # same as before this existed, and the gate's own failure stays the informative one
            store.append_event(card_id, "repo_image_rebuild_failed", {"log": rebuild.log[-2000:]})
    base = default_branch(repo)
    configured = store.get_settings()
    worker_lab, worker_id = run_ref(store, "worker", card)
    reviewer_lab, reviewer_id = run_ref(store, "reviewer", card)
    worker_model = command_model(worker_lab, worker_id)
    reviewer_model = command_model(reviewer_lab, reviewer_id)

    try:
        runtime = backend or (
            require_card_runtime(token_path)
            if worker_lab == "anthropic"
            else require_card_runtime(lab=worker_lab)
        )
    except CardRuntimeUnavailable as exc:
        return _refuse(store, state, f"{RUNTIME_NOT_READY}:\n{exc}")

    try:
        # a blocked card resuming is not a fresh start - create_worktree raises on an existing
        # path, so the worktree from its first run reuses it (commits and all) rather than being
        # refused. only reached for a non-accepted card: accepted already returned above, and a
        # rejected card's decide.py step deletes the branch, so it always falls to the fresh cut
        worktree_reused = True
        if worktree_path(repo["path"], card_id).exists():
            tree = existing_worktree(repo["path"], card_id)
        elif branch_exists(repo["path"], card_id):
            tree = add_worktree(repo["path"], card_id)
        else:
            tree = None
        # someone else pushed to this card's own branch since the board last saw it - the board's
        # own push at the end of the run is never forced, so waiting until then only burns a full
        # worker/gate/review turn on work that can never land anyway
        if (
            tree is not None
            and has_remote(repo["path"])
            and branch_diverged_from_origin(repo["path"], tree.branch)
        ):
            return _refuse(
                store,
                state,
                f"{tree.branch} has commits on origin that this worktree does not - someone "
                "else pushed to this card's own branch. Fetch and rebase or reset the branch "
                "yourself, or delete the worktree and let the board recut it, then run this "
                "card again.",
            )
        if tree is None:
            worktree_reused = False
            cut_base = base
            if card.get("depends_on"):
                cut_base = _base_for_fresh_cut(store, state, repo["path"], base)
            tree = create_worktree(repo["path"], card_id, base=cut_base)
            if tree.base_commit:
                store.append_event(card_id, "worktree_created", {"base_commit": tree.base_commit})
    except (WorktreeError, ValueError) as exc:
        return _refuse(store, state, f"Could not cut a worktree for this card: {exc}")
    state.branch, state.worktree = tree.branch, str(tree.path)

    settings = write_container_guards(tree.path, _lease_globs(card))
    store.update_card(card_id, status="doing", blocked_reason_code=None, review_flag=False)

    # a reused branch is brought up to date BEFORE the worker and the gate, not only at handover:
    # measured 2026-09-19, three cards 28-59 commits behind development failed their gate on tests
    # a later base commit had already fixed, and every re-run reused the same stale tree
    conflict_note = None
    # what "the card's own commits" are counted against: once origin/<base> is merged in, the local
    # base can lag it, and the merged-in commits would read as the card's work
    commit_base = base
    if worktree_reused:
        synced = sync_with_base(tree.path, base)
        if synced is not None and synced.clean:
            commit_base = f"origin/{base}"
        if synced is not None and not synced.clean:
            base_ref = f"origin/{base}"
            store.append_event(
                card_id,
                "merge_conflict",
                {"base_ref": base_ref, "files": synced.conflicting_files},
            )
            files = ", ".join(synced.conflicting_files) or "unknown files"
            conflict_note = (
                f"Merging {base_ref} into this branch conflicts in: {files}. Merge {base_ref} "
                "yourself, resolve those files, then commit the merge - the test gate runs "
                "against this branch, so it has to contain the base first."
            )

    fingerprint = {
        "head": rev_parse(tree.path, "HEAD"),
        "base_head": rev_parse(tree.path, f"origin/{base}"),
        "notes": sum(1 for c in card.get("comments") or [] if c.get("author") == AUTHOR_KEY),
    }
    if worktree_reused and repeats_failed_attempt(store, card_id, fingerprint):
        return _block(
            store,
            state,
            "TESTS_FAILED",
            "Nothing has changed since the last attempt: same branch head, same base, no new "
            "note - and its tests failed. Running the worker again would repeat that failure. "
            "Change something first (merge the base, fix what the gate names, add a note), then "
            "run it again.",
        )
    store.append_event(card_id, "attempt_fingerprint", fingerprint)

    # the card's own model wins, then the board's worker setting, then sonnet. the reviewer has a
    # setting of its own, so a cheap worker never means a cheap review

    # the worker's own final message, kept for the screenshot step below - it is where the worker
    # names which view to open, per runner.SYSTEM_PROMPT's SCREENSHOT: instruction
    last_summary: str | None = None
    worker_started = reviewer_started = False

    def work(prompt: str) -> LifecycleResult | None:
        nonlocal last_summary, worker_started
        """one agent run in the card's worktree; returns the blocked/stopped state if it stopped
        short"""
        if not worker_started:
            run_ref(store, "worker", card, consume=True)
            worker_started = True
        run = runtime.run_card(
            store,
            card_id,
            tree.path,
            prompt,
            settings,
            repo=repo,
            token_path=token_path,
            model=worker_model,
            pending_notes=pending_notes,
            on_process=on_process,
        )
        # a stop kills the process underneath run_card, which then reports some ordinary-looking
        # blocked_reason_code (CRASH, most likely) - checked BEFORE that interpretation, so a
        # deliberate stop is never mistaken for the work having gone wrong
        if stopped_now():
            return _stopped(store, state)
        if run.auth_failed:
            from smortboard.labs.registry import get_adapter

            return _refuse(
                store,
                state,
                "The run credential was refused. " + get_adapter(worker_lab).setup_hint(),
            )
        if run.subtype in BUDGET_CAPPED_SUBTYPES:
            if branch_has_commits(repo["path"], tree.branch, commit_base):
                store.append_event(card_id, "budget_capped_with_commits", {"subtype": run.subtype})
                _note(
                    store,
                    card_id,
                    "It hit its budget after committing, so its work went to tests and review.",
                )
                store.append_event(card_id, "worker_summary", {"text": run.result_text})
                last_summary = run.result_text
                return None
            limit = "budget" if run.subtype == "error_max_budget_usd" else "turn limit"
            return _block(
                store,
                state,
                run.blocked_reason_code or "CRASH",
                NO_COMMITS_BUDGET_NOTE.format(limit=limit),
            )
        if run.blocked_reason_code:
            return _block(
                store,
                state,
                run.blocked_reason_code,
                f"The run stopped: {run.blocked_reason_code}.\n\n{run.result_text or ''}".strip(),
            )
        store.append_event(card_id, "worker_summary", {"text": run.result_text})
        last_summary = run.result_text
        return None

    # a resumed card (an inbox answer, or a re-run after a block) keeps this worktree and its
    # commits - the briefing is what lets the agent skip re-reading them to find out what it
    # already did. "off" turns it off board-wide; unset means on.
    briefing = None
    if configured.get("resume_briefing") != "off":
        briefing = resume_briefing(store, card_id, worktree_reused=worktree_reused)
    if conflict_note:
        briefing = f"{briefing}\n\n{conflict_note}" if briefing else conflict_note

    phase("running")
    if (stopped := work(build_card_prompt(card, briefing))) is not None:
        return stopped
    if stopped_now():
        return _stopped(store, state)
    # no commit means nothing to test or review - measured, a card that ended its turn early had
    # its unchanged tree run through the full gate and an approved review of an empty diff first
    if not branch_has_commits(repo["path"], tree.branch, commit_base):
        return _refuse(store, state, NO_COMMITS_NOTE.format(branch=tree.branch, base=base))

    # both gates, and on the fix route the findings go back to the worker until the reviewer
    # approves or the rounds run out. the tests re-run after every fix, since a fix can break them
    while True:
        phase("testing")
        try:
            gate = run_test_gate(store, card_id, tree.path, repo)
        except GateUnavailable as exc:
            return _refuse(store, state, _gate_unavailable_note(exc))
        if stopped_now():
            return _stopped(store, state)
        if not gate.passed:
            return _block_on_failed_gate(store, state, card_id, repo, base, gate)

        phase("reviewing")
        diff_text = branch_diff(repo["path"], base, tree.branch)
        try:
            if not reviewer_started:
                run_ref(store, "reviewer", card, consume=True)
                reviewer_started = True
            review = run_review(
                store,
                card_id,
                diff_text,
                tree.path,
                settings,
                repo=repo,
                token_path=token_path,
                model=reviewer_model,
                budget_usd=store.spend_cap("reviewer_budget_usd", DEFAULT_REVIEW_BUDGET_USD),
                on_process=on_process,
            )
        except ReviewUnavailable as exc:
            return _refuse(store, state, f"The reviewer could not run: {exc}")
        if stopped_now():
            return _stopped(store, state)
        if review.approved:
            break

        # a reviewer that failed to deliver a verdict gave the worker nothing to fix
        fixable = review.error is None and review.findings
        if (
            not fixable
            or store.findings_route(card_id) != "fix"
            or state.fix_rounds >= MAX_FIX_ROUNDS
        ):
            return _block(
                store,
                state,
                review.blocked_reason_code or "REVIEW_REJECTED",
                f"The reviewer did not approve.\n\n{_format_findings(review) or review.error or ''}"
                + (f"\n\nAfter {state.fix_rounds} fix round(s)." if state.fix_rounds else ""),
            )

        state.fix_rounds += 1
        phase("fixing")
        store.append_event(card_id, "fix_round", {"round": state.fix_rounds})
        if (stopped := work(build_fix_prompt(card, review))) is not None:
            return stopped

    if stopped_now():
        return _stopped(store, state)

    # both gates passed - a card whose diff touches smortboard/ui/ gets one screenshot, taken on
    # the host so the operator sees the change before merging. a failed screenshot is a note, never
    # a block: nothing about the work itself was wrong
    if diff_touches_ui(diff_text):
        phase("screenshotting")
        shot = take_screenshot(store, card_id, tree.path, last_summary)
        if shot.note:
            _note(store, card_id, f"Screenshot not attached: {shot.note}")

    # only now is the card work waiting on a human: checking requires both gates, not either
    phase("opening")
    from smortboard.review.land_card import retry_retarget

    target = integration_base(store, card_id, base)
    stack = active_stack(store, card_id)
    if stack and dependency_landed(store, store.get_card(stack["parent_id"])):
        target = base
    if (blocked := _sync_and_retest(store, state, card_id, tree, repo, target)) is not None:
        return blocked

    store.update_card(card_id, status="checking")
    retry_retarget(store, store.get_card(card_id))
    if store.get_card(card_id)["blocked_reason_code"]:
        state.phase = "blocked"
        state.blocked_reason_code = store.get_card(card_id)["blocked_reason_code"]
        return state
    target = integration_base(store, card_id, base)

    try:
        request = open_merge_request(store, card_id, repo["path"], tree.branch, base=target)
    except MergeRequestUnavailable as exc:
        return _refuse(store, state, f"Could not open a pull request: {exc}")

    if not request.url:
        return _refuse(store, state, f"Both gates passed, but: {request.refusal}")

    if (
        base not in PROTECTED_BRANCHES
        and store.board_merges_freely(card["board_id"])
        and target == base
    ):
        return _integrate(store, state, card, tree, repo, base, request.url)

    _note(
        store,
        card_id,
        with_next(
            f"Tests passed, the reviewer approved, and the pull request is open:\n{request.url}\n\n"
            + (
                "Merging into this protected base is yours."
                if base in PROTECTED_BRANCHES
                else "Waiting for y to accept and merge this card."
            ),
            "review",
        ),
    )
    store.update_card(card_id, review_flag=True)
    state.phase, state.pr_url = "opened", request.url
    return state
