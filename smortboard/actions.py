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
        "answer the agent's question in the inbox (n) - the answer resumes it.",
    ),
    "TESTS_FAILED": (
        "answer it",
        "read the failing output, then answer in the inbox (n) with a hint - that re-runs it.",
    ),
    "BASE_RED": (
        "fix the base, then r",
        "the base branch already fails these tests, so this card did not cause it. fix the base "
        "or merge a fix into it, then press r to run the card again.",
    ),
    "REVIEW_REJECTED": (
        "answer it",
        "read the findings, then answer in the inbox (n) with what to change - that re-runs it.",
    ),
    "CRASH": (
        "answer it",
        "check the note for what broke, then answer in the inbox (n) to resume it.",
    ),
    "LEASE_CONFLICT": (
        "widen or answer",
        "widen its lease if it needs that file, or answer in the inbox (n) to leave the file be.",
    ),
    "USAGE_LIMIT": (
        "retry or wait",
        "retry it on a fallback model from the inbox (n), or wait for the rate-limit window to "
        "reset - its inbox note says whether it re-runs by itself then or needs r.",
    ),
    "DEPENDENCY_REJECTED": (
        "fix its dependency",
        "fix and accept the rejected card it depends on - this one un-blocks itself then.",
    ),
    "MERGE_CONFLICT": (
        "n or r",
        "its branch no longer merges cleanly with the base - resume it (n or r) and the board "
        "rebases it onto the base in a fresh tree. if that still conflicts, it turns OUTDATED.",
    ),
    "API_UNREACHABLE": (
        "retrying",
        "the api was unreachable - the board is retrying it automatically with backoff.",
    ),
    "OUTDATED": (
        "redo on fresh base",
        "the base moved and its commits no longer rebase onto it. answer in the inbox (n) to "
        "redo it on a fresh tree at the current base - the old commits stay on a backup ref.",
    ),
    "refused": ("fix, then r", "fix what the note says, then press r to run it again."),
    "stopped": ("press r", "press r to run it again - its worktree and commits are kept."),
    "review": ("y or x", "review the pull request, then press y to accept or x to reject."),
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
    return f"{note.rstrip()}\n\nnext: {action}" if action else note
