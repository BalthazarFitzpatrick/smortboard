"""what a person does next about a card that waits on them - one table, read by every surface.

The inbox row, the card strip, the card panel and the board's closing note on a card all say the
same action in the same words, so a card that needs someone reads the same wherever it is seen.
"""

from __future__ import annotations

# reason -> (short, full). short fits a card strip's footer; full is the sentence everywhere else.
# the reasons are the store's blocked_reason_codes plus attention._flag_reason's three
_ACTIONS = {
    "AGENT_QUESTION": (
        "answer it",
        "Answer the agent's question in the inbox (n) - the answer resumes it.",
    ),
    "TESTS_FAILED": (
        "answer it",
        "Read the failing output, then answer in the inbox (n) with a hint - that re-runs it.",
    ),
    "BASE_RED": (
        "fix the base, then r",
        "The base branch already fails these tests, so this card did not cause it. Fix the base "
        "or merge a fix into it, then press r to run the card again.",
    ),
    "REVIEW_REJECTED": (
        "answer it",
        "Read the findings, then answer in the inbox (n) with what to change - that re-runs it.",
    ),
    "CRASH": (
        "answer it",
        "Check the note for what broke, then answer in the inbox (n) to resume it.",
    ),
    "LEASE_CONFLICT": (
        "widen or answer",
        "Widen its lease if it needs that file, or answer in the inbox (n) to leave the file be.",
    ),
    "USAGE_LIMIT": (
        "wait, then r",
        "Wait for the rate-limit window to reset, then press r to run it again.",
    ),
    "DEPENDENCY_REJECTED": (
        "fix its dependency",
        "Fix and accept the rejected card it depends on - this one un-blocks itself then.",
    ),
    "MERGE_CONFLICT": (
        "resolve, then r",
        "Its branch no longer merges cleanly with the base - resume it (n or r) so the worker "
        "merges the base branch and resolves the conflict, then commits the result.",
    ),
    "API_UNREACHABLE": (
        "retrying",
        "The api was unreachable - the board is retrying it automatically with backoff.",
    ),
    "refused": ("fix, then r", "Fix what the note says, then press r to run it again."),
    "stopped": ("press r", "Press r to run it again - its worktree and commits are kept."),
    "review": ("y or x", "Review the pull request, then press y to accept or x to reject."),
}


def next_action(reason: str | None) -> str | None:
    """the full sentence for what to do about a card waiting for `reason`, or None"""
    return _ACTIONS[reason][1] if reason in _ACTIONS else None


def short_action(reason: str | None) -> str | None:
    """the two or three words a card strip has room for, or None"""
    return _ACTIONS[reason][0] if reason in _ACTIONS else None


def with_next(note: str, reason: str) -> str:
    """a board note ending in its call to action, so the card's last comment says what to do"""
    action = next_action(reason)
    return f"{note.rstrip()}\n\nNext: {action}" if action else note
