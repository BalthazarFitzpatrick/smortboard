"""the attention inbox: every card across every board that needs the operator, in one list.

A card needs him when it carries a blocked_reason_code, or when review_flag is set (a checking
card waiting to be accepted/rejected also raises review_flag, but that path already has its own
surface - the board's checking column - so this module only counts a card that is still short of
a decision: not accepted, not rejected).

ORDER: oldest first. The point of an inbox is that nothing sits forever - the card that has been
waiting longest surfaces first, same as any other inbox.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from smortboard import profiles
from smortboard.actions import next_action, short_action
from smortboard.budgets import spend_refusal
from smortboard.exec.worktrees import default_branch
from smortboard.labs.routing import role_ref
from smortboard.lifecycle import BOARD_AUTHOR
from smortboard.operator import AUTHOR_KEY
from smortboard.review.rebase_guard import OUTDATED, ResetRefused, reset_to_base
from smortboard.scheduler import (
    API_UNREACHABLE_MAX_RETRIES,
    card_reset,
    conflicting_run,
    latest_limit,
    record_fallback,
    usage_limit_route,
)
from smortboard.store.api import Store
from smortboard.telemetry import card_telemetry

# a lease only ever guards writes - a refused Bash call says nothing about a path to widen for
_LEASE_TOOLS = frozenset({"Edit", "Write", "NotebookEdit"})
_WORKSPACE_PREFIX = "/workspace/"

RESUMABLE_REASONS = frozenset(
    {
        "AGENT_QUESTION",
        "TESTS_FAILED",
        "BASE_RED",
        "REVIEW_REJECTED",
        "CRASH",
        "LEASE_CONFLICT",
        "MERGE_CONFLICT",
        "OUTDATED",
    }
)
# USAGE_LIMIT clears on its own once the rate-limit window resets - answering it does not change
# the model's limit, so an answer would only be spent confusing the agent on its next run. its
# inbox row offers a retry on the next fallback model instead, see fallback_run.
# DEPENDENCY_REJECTED is not this card's fault - a message to it cannot un-reject the dependency;
# accept_card already clears it automatically once the dependency comes back (see review/decide.py)


def _utc(at: float) -> str:
    return datetime.fromtimestamp(at, tz=UTC).strftime("%H:%M UTC")


def _format_retry_at(retry_at: float | None) -> str:
    if retry_at is None:
        return "once its automatic retry runs"
    return f"retry at {_utc(retry_at)}"


def handled_by_board(store: Store, card: dict[str, Any], schedule: Any = None) -> str | None:
    """the inbox note for a card the board is already retrying on its own, or None once it needs
    a real decision - a spent automatic attempt (API_UNREACHABLE's three retries, MERGE_CONFLICT's
    one resume) returns None so the card falls back into the inbox instead of hiding forever.

    `schedule` is anything shaped like SchedulerRegistry (retry_at, paused_labs). a USAGE_LIMIT
    card or a runtime refusal counts as handled only while it really holds a timer or a queue
    place there - measured, two cards relabeled on 09-14 hid from the inbox with nothing queued."""
    reason = card.get("blocked_reason_code")
    card_id = card["id"]
    if reason == "API_UNREACHABLE":
        attempts = [e for e in store.list_events(card_id) if e["kind"] == "api_unreachable_retry"]
        if len(attempts) >= API_UNREACHABLE_MAX_RETRIES:
            return None
        retry_at = attempts[-1]["payload"].get("retry_at") if attempts else None
        return _format_retry_at(retry_at)
    if reason == "USAGE_LIMIT":
        retry_at = schedule.retry_at(store, card) if schedule is not None else None
        return _format_retry_at(retry_at) if retry_at is not None else None
    if reason == "MERGE_CONFLICT":
        attempted = any(
            e["kind"] == "merge_conflict_auto_resume" for e in store.list_events(card_id)
        )
        return None if attempted else "resuming automatically"
    if reason is None and card.get("review_flag") and schedule is not None:
        retry_at = schedule.retry_at(store, card)
        if retry_at is not None and _flag_reason(store, card_id) == "refused":
            return _format_retry_at(retry_at)
    return None


def _limit_of(store: Store, card: dict[str, Any], settings: dict[str, Any]) -> tuple[str, str, str]:
    """(role, lab, model) that hit the card's limit - its profile_limited record, or the role's own
    configured model for a card relabeled from CRASH, which never had one written"""
    limit = latest_limit(store, card["id"]) or {}
    role = limit.get("role") or "worker"
    own_lab, own_model = role_ref(settings, role, card if role == "worker" else None)
    return role, limit.get("lab") or own_lab, limit.get("model") or own_model


def _next_fallback(
    settings: dict[str, Any], role: str, lab: str, paused_labs: dict[str, float]
) -> tuple[str, str, str] | None:
    now = time.time()
    return profiles.usable_fallback(
        settings.get(f"{role}_cross_lab_fallback") or [],
        lab,
        skip=lambda target_lab, _profile: paused_labs.get(target_lab, 0) > now,
    )


def usage_limit_note(
    store: Store, card: dict[str, Any], schedule: Any = None
) -> tuple[str, str | None]:
    """the inbox line for a USAGE_LIMIT card and the fallback ref its retry control would use,
    e.g. "opus limit - retry on openai/gpt-5.6-sol? or waits to 15:40 UTC" """
    settings = store.get_settings()
    role, lab, model = _limit_of(store, card, settings)
    paused = schedule.paused_labs() if schedule is not None else {}
    target = _next_fallback(settings, role, lab, paused)
    retry_at = schedule.retry_at(store, card) if schedule is not None else None
    reset = card_reset(store, card["id"])
    if retry_at is not None:
        tail = f"waits to {_utc(retry_at)}"
    elif reset is not None and reset > time.time():
        tail = f"resets {_utc(reset)}, then r"
    else:
        tail = "r runs it again"
    if target is None:
        return f"{model} limit - no usable fallback; {tail}", None
    ref = f"{target[0]}/{target[1]}"
    return f"{model} limit - retry on {ref}? or {tail}", ref


def _flag_reason(store: Store, card_id: str) -> str:
    """why a card with no reason code is flagged: its latest run was stopped or refused, or else
    it is a checking card waiting on a decision"""
    for event in reversed(store.list_events(card_id)):
        if event["kind"] == "run_stopped":
            return "stopped"
        if event["kind"] == "run_refused":
            return "refused"
        if event["kind"] == "lifecycle_started":
            break
    return "review"


def _trim(text: str | None, limit: int = 4000) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n... (trimmed)"


def _question_for(store: Store, card: dict[str, Any]) -> str:
    """the most useful text to show the operator for this blocked card.

    AGENT_QUESTION: the agent's own final result text, from the card's latest `result` stream
    event - that is what it actually said when it stopped to ask. every other reason: the latest
    board-authored comment, which is the block note the lifecycle already wrote (test output,
    review findings, the crash, the lease conflict).
    """
    if card.get("blocked_reason_code") == "AGENT_QUESTION":
        results = [e for e in store.list_events(card["id"]) if e["kind"] == "result"]
        if results:
            return _trim(results[-1]["payload"].get("result"))
    board_notes = [c for c in card.get("comments") or [] if c.get("author") == BOARD_AUTHOR]
    if board_notes:
        return _trim(board_notes[-1]["body"])
    return ""


def _needs_attention(card: dict[str, Any]) -> bool:
    if card["status"] in ("accepted", "rejected"):
        return False
    return bool(card.get("blocked_reason_code")) or bool(card.get("review_flag"))


def waiting_reason(store: Store, card: dict[str, Any]) -> str | None:
    """why this card waits on a person - its reason code, or refused/stopped/review - or None"""
    if not _needs_attention(card):
        return None
    return card.get("blocked_reason_code") or _flag_reason(store, card["id"])


def with_actions(
    store: Store, cards: list[dict[str, Any]], schedule: Any = None
) -> list[dict[str, Any]]:
    """each card gains next_action and next_action_short - None unless it waits on a person - so
    the board can say what to do on the strip itself, not only in the inbox. Also handled_by_board
    and next: set while the board is retrying the block itself, so the card view can say so instead
    of reading as unattended."""
    ask_on_limit = usage_limit_route(store.get_settings()) == "attention"
    for card in cards:
        reason = waiting_reason(store, card)
        card["next_action"] = next_action(reason)
        card["next_action_short"] = short_action(reason)
        next_note = handled_by_board(store, card, schedule)
        # the board and the inbox agree: a limit the inbox asks about draws in attention too
        asked = reason == "USAGE_LIMIT" and ask_on_limit
        card["handled_by_board"] = next_note is not None and not asked
        card["next"] = next_note
    return cards


class AnswerRefused(Exception):
    """the store's state means this card cannot be resumed by an answer right now"""


def answer_card(store: Store, runs: Any, card_id: str, message: str) -> dict[str, Any]:
    """records the operator's answer as a comment and resumes the card, or refuses and says why.

    Resumable reasons are the ones an answer can actually unstick: AGENT_QUESTION (he answers the
    question), TESTS_FAILED / BASE_RED / REVIEW_REJECTED / CRASH (a note the next run reads before trying
    again), LEASE_CONFLICT (the note says what to do instead - widening the lease itself is
    Store.set_leases, since the guard reads the card's lease rows, never a note), OUTDATED (the
    answer redoes the card on a fresh tree at the current base, see rebase_guard.reset_to_base).
    USAGE_LIMIT is not resumable here - it clears itself once the rate-limit window resets, and an
    answer changes nothing about that.
    Neither is DEPENDENCY_REJECTED - the dependency, not this card, is what needs fixing, and
    accept_card already un-blocks it automatically once that dependency is accepted after all.
    A review_flag with no reason code (a checking card waiting on accept/reject) is not a resumable
    block either - that is a decision, not a question, and belongs on the card panel's y/x.
    """
    state = runs.get(card_id)
    if state is not None and state.running:
        raise AnswerRefused("this card is still running")

    card = store.get_card(card_id)
    reason = card.get("blocked_reason_code")
    if not _needs_attention(card):
        raise AnswerRefused("this card is not waiting on an answer")
    if reason not in RESUMABLE_REASONS:
        raise AnswerRefused(f"{reason or 'this block'} cannot be resumed by answering here")
    # refused before the comment is stored, so a retry once the other card finishes is clean
    active = getattr(runs, "active", None)
    conflict = conflicting_run(store, card, [s.card_id for s in active()]) if active else None
    if conflict:
        raise AnswerRefused(f"not resumed: {conflict} - answer again once it finishes")
    refusal = spend_refusal(store, card)
    if refusal:
        raise AnswerRefused(refusal)
    # an answer to OUTDATED is a decision to redo the card: its commits no longer rebase, so it
    # restarts on a fresh tree at the current base, the old tip kept on a backup ref
    if reason == OUTDATED and card.get("repo_id"):
        repo = store.get_repo(card["repo_id"])
        try:
            reset_to_base(store, card_id, repo["path"], default_branch(repo))
        except ResetRefused as exc:
            raise AnswerRefused(f"not reset: {exc}") from exc

    store.add_comment(card_id, author=AUTHOR_KEY, body=message)
    store.update_card(card_id, blocked_reason_code=None, review_flag=False)
    return runs.start(card_id).as_dict()


def fallback_run(store: Store, runs: Any, card_id: str, schedule: Any = None) -> dict[str, Any]:
    """the inbox's retry on a USAGE_LIMIT row: the limited role runs once on its next usable
    fallback model, now, instead of waiting for the reset. Refused (AnswerRefused) while it runs,
    when it is not usage limited, when no fallback is usable, or for the lease and spend reasons
    answer_card refuses - each checked before anything is written."""
    state = runs.get(card_id)
    if state is not None and state.running:
        raise AnswerRefused("this card is still running")
    card = store.get_card(card_id)
    if card.get("blocked_reason_code") != "USAGE_LIMIT" or not _needs_attention(card):
        raise AnswerRefused("this card is not blocked on a usage limit")
    settings = store.get_settings()
    role, lab, _model = _limit_of(store, card, settings)
    paused = schedule.paused_labs() if schedule is not None else {}
    target = _next_fallback(settings, role, lab, paused)
    if target is None:
        raise AnswerRefused(f"no usable fallback for the {role} - it waits for the reset")
    active = getattr(runs, "active", None)
    conflict = conflicting_run(store, card, [s.card_id for s in active()]) if active else None
    if conflict:
        raise AnswerRefused(f"not started: {conflict}")
    refusal = spend_refusal(store, card)
    if refusal:
        raise AnswerRefused(refusal)

    record_fallback(store, card_id, role, lab, target, card_reset(store, card_id))
    store.update_card(card_id, blocked_reason_code=None, review_flag=False)
    return runs.start(card_id).as_dict()


def lease_conflict_wants(store: Store, card_id: str) -> list[str]:
    """the repo-relative paths a card's most recent attempt was refused writing to.

    Only Edit/Write/NotebookEdit refusals count - a lease guards writes, so a refused Bash call
    is never a path to widen for. The container mounts the repo at /workspace, so a target is
    made repo-relative by stripping that prefix. Deduplicated, order preserved.
    """
    attempts = card_telemetry(store, card_id)["attempts"]
    if not attempts:
        return []
    wants: list[str] = []
    for refusal in attempts[-1]["refusals"]:
        if refusal.get("tool") not in _LEASE_TOOLS:
            continue
        target = refusal.get("target") or ""
        if target.startswith(_WORKSPACE_PREFIX):
            target = target[len(_WORKSPACE_PREFIX) :]
        if target and target not in wants:
            wants.append(target)
    return wants


def approve_lease(store: Store, runs: Any, card_id: str, paths: list[str]) -> dict[str, Any]:
    """the inbox's one-click reply to a LEASE_CONFLICT: widen the lease by exactly the paths the
    card was refused, then resume it the same way answer_card does.

    Raises ValueError for an empty/invalid path list or an invalid glob (both belong as a 400),
    NotFoundError for an unknown card (store.get_card raises it), and AnswerRefused when the card
    is not blocked on LEASE_CONFLICT (409) or when the lease is widened but the resume itself is
    refused by a running conflicting card - the lease stays widened either way, since a person
    approved exactly those paths and a later resume should not have to be asked again.
    """
    if not isinstance(paths, list) or not paths:
        raise ValueError("paths must be a non-empty list of globs")

    card = store.get_card(card_id)
    if card.get("blocked_reason_code") != "LEASE_CONFLICT":
        raise AnswerRefused("this card is not blocked on a lease conflict")

    existing = [lease["path_glob"] for lease in card.get("leases") or []]
    widened = existing + [path for path in paths if path not in existing]
    store.set_leases(card_id, widened)

    message = f"lease widened to include: {', '.join(paths)}"
    try:
        return answer_card(store, runs, card_id, message)
    except AnswerRefused as exc:
        raise AnswerRefused(
            f"lease widened, but not resumed yet: {exc} - answer again once it finishes"
        ) from exc


def attention_rows(store: Store, schedule: Any = None) -> list[dict[str, Any]]:
    """one row per card across every board that is waiting on the operator, oldest first"""
    rows = []
    ask_on_limit = usage_limit_route(store.get_settings()) == "attention"
    for board in store.list_boards():
        for card in store.list_cards(board["id"]):
            reason = waiting_reason(store, card)
            if reason is None:
                continue
            # the board is already retrying this one itself - it stays on the board (the card's
            # own strip still shows handled_by_board and next) but drops out of the inbox until
            # its automatic attempts are spent. a usage limit under the "attention" route is the
            # exception: the reset retry stands, but a fallback model is the operator's call
            asked = reason == "USAGE_LIMIT" and ask_on_limit
            if not asked and handled_by_board(store, card, schedule) is not None:
                continue
            answerable = reason in RESUMABLE_REASONS
            row = {
                "card_id": card["id"],
                "board_id": board["id"],
                "board_name": board["name"],
                "title": card["title"],
                "reason": reason,
                "question": _question_for(store, card),
                "since": card["updated_at"],
                "answerable": answerable,
                # the call to action, for every row; hint repeats it where no answer box shows
                "action": next_action(reason) or "",
                "hint": "" if answerable else (next_action(reason) or ""),
            }
            if reason == "LEASE_CONFLICT":
                row["wants"] = lease_conflict_wants(store, card["id"])
            if reason == "USAGE_LIMIT":
                note, row["fallback"] = usage_limit_note(store, card, schedule)
                row["action"] = row["hint"] = note
            rows.append(row)
    rows.sort(key=lambda r: r["since"])
    return rows
