"""a self-contained demo dataset: three invented projects, every card state, all three column
regimes.

WHY A SEEDED THROWAWAY DB AND NOT A FIXTURE FILE: the schema migrates forward on every open
(store/schema.py), so a checked-in .db would rot the first time a migration lands. Seeding through
the public Store API instead means the demo is always built against today's schema, and the whole
dataset stays readable as code.

WHY IT CAN NEVER TOUCH THE REAL DB: build_demo_db writes only to the path it is handed, and
cli.py's --demo hands it a fresh file in a throwaway directory - it never goes near _resolve_db,
SMORTBOARD_DB or the user data dir.

Nothing here is real: every repo path, project, card and comment is invented.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from itertools import cycle
from pathlib import Path
from typing import Any

from smortboard import profiles
from smortboard.lifecycle import BOARD_AUTHOR
from smortboard.operator import AUTHOR_KEY
from smortboard.scheduler import API_UNREACHABLE_MAX_RETRIES
from smortboard.store.api import Store

# ---- the viewport the column regimes are stated at ---------------------------------------------
#
# the three regimes (ui_base pile.js's computeColumnFit) are a measured choice, so "this column
# demonstrates regime 3" is only true at a stated screen. these are the numbers measured in a real
# 1600x1000 headless chromium against this very dataset - the width of one of the six board
# columns, and the room availableColumnHeight leaves under its label. tests/test_demo_regimes.py
# feeds them to the real computeColumnFit, so a tuning pass that moves the maths fails the test
# instead of quietly flattening the demo.
DEMO_VIEWPORT = (1600, 1000)
DEMO_COLUMN_WIDTH_PX = 222
DEMO_COLUMN_AVAILABLE_PX = 886

# card counts that land in each regime at the viewport above - derived from the fit maths, not
# guessed: a card is PORTRAIT_BELOW (230) tall at that width, so 3 spread, 6 have to fan, and 12
# cannot even fan without two piles
REGIME_COUNTS = {1: 3, 2: 6, 3: 12}

# the columns the BOARD draws, which are not the five stored statuses: attention is a presentation
# column (board.js COLUMNS) holding every card that blocks or waits on a decision, wherever its own
# status sits. the demo is specified against these, since they are what a regime is visible in
DEMO_COLUMNS = ("todo", "doing", "attention", "checking", "accepted", "rejected")

# ---- the three projects ------------------------------------------------------------------------
#
# repo paths are under a made-up home so nothing points at a real checkout

_REPOS = {
    "orchard-pay": {
        "name": "orchard-pay",
        "path": "/home/demo/projects/orchard-pay",
        "default_branch": "development",
        "test_command": "uv run pytest -q",
        "lint_command": "uv run ruff check .",
    },
    "lantern-cms": {
        "name": "lantern-cms",
        "path": "/home/demo/projects/lantern-cms",
        "default_branch": "development",
        "test_command": "npm test --silent",
        "image": "lantern-cms-tests:latest",
    },
    "tideline-etl": {
        "name": "tideline-etl",
        "path": "/home/demo/projects/tideline-etl",
        "default_branch": "main",
        "test_command": "uv run pytest -q",
    },
}

# one board per project: the column whose cards spread (regime 1), the one whose cards fan
# (regime 2) and the one that fans between two piles (regime 3) are named per board, so every
# board shows all three at once and no two boards demonstrate them in the same column
_BOARDS: list[dict[str, Any]] = [
    {
        "name": "orchard-pay",
        "repo": "orchard-pay",
        "regimes": {"todo": 3, "doing": 1, "attention": 2},
        "counts": {"checking": 2, "accepted": 4, "rejected": 1},
    },
    {
        "name": "lantern-cms",
        "repo": "lantern-cms",
        "regimes": {"todo": 2, "doing": 1, "accepted": 3},
        "counts": {"attention": 4, "checking": 2, "rejected": 2},
    },
    {
        "name": "tideline-etl",
        "repo": "tideline-etl",
        "regimes": {"todo": 1, "checking": 2, "accepted": 3},
        "counts": {"doing": 2, "attention": 4, "rejected": 1},
    },
]


def column_counts() -> list[dict[str, Any]]:
    """one entry per board: its name and how many cards each status column holds.

    the single source of truth the regime test reads - it is derived from _BOARDS here rather than
    counted off the seeded db, so the test never needs to build one.
    """
    return [
        {
            "board": board["name"],
            "columns": board_column_counts(board),
            "regimes": dict(board["regimes"]),
        }
        for board in _BOARDS
    ]


# ---- card copy ---------------------------------------------------------------------------------
#
# invented work, written the way real cards read: a verb, the thing, and why. one pool per project
# so a column of nine never repeats a title

_TITLES = {
    "orchard-pay": [
        "Idempotency keys on the refund endpoint",
        "Retry a declined capture on the issuer's advice code",
        "Split the settlement report by acquirer",
        "Webhook signatures rotate without dropping a delivery",
        "Ledger entries carry the originating payment intent",
        "Refunds past 180 days quote the chargeback route instead",
        "Currency rounding follows the acquirer, not the locale",
        "Payout schedule respects bank holidays per country",
        "A failed 3DS challenge keeps the cart intact",
        "Batch capture retries in one transaction, not nine",
        "Dispute evidence bundles as one PDF",
        "Mask the PAN in every log line, including the slow-query log",
        "Backfill the payment_method_fingerprint column",
        "Rate-limit the tokenisation endpoint per merchant",
        "Sandbox keys stop working against live endpoints",
        "Partial captures leave the remainder authorised",
        "Statement descriptors truncate at the network's limit",
        "Reconcile the acquirer file against the ledger nightly",
        "Stored cards re-authenticate after a network token update",
        "Fee breakdown on every payout line",
        "Void and refund stop being the same endpoint",
        "Decline reasons map to one vocabulary across acquirers",
        "Payment intents expire instead of hanging authorised",
        "One webhook endpoint per merchant, not per event type",
        "Currency conversion quotes are held for the checkout's life",
        "Settlement file parser handles a trailing blank record",
        "Test cards stop reaching the fraud model",
        "Refund receipts carry the original payment's reference",
        "Drop the legacy /v1/charge alias",
    ],
    "lantern-cms": [
        "Draft previews expire instead of leaking a URL forever",
        "Media library keeps the original alongside each derivative",
        "Scheduled publish survives a worker restart",
        "Editor autosave every 20 seconds, conflict-aware",
        "Slug history redirects an old URL to the renamed page",
        "Block editor: pull quote block with a source line",
        "Search reindexes only the pages a save touched",
        "Roles: an editor cannot publish, only submit",
        "Image alt text is required before publish",
        "Page tree drag-and-drop writes one move, not a reorder storm",
        "Sitemap excludes noindex pages",
        "Comment moderation queue with a bulk approve",
        "Locale fallback walks up the language tag",
        "Trash keeps a page for 30 days, then purges the blobs",
        "Webhooks fire once per publish, not once per revision",
        "Revision diff shows blocks moved, not rewritten",
        "Uploads reject a file the magic bytes disagree with",
        "Menu builder keeps external links out of the page tree",
        "Preview renders with the theme the page will publish under",
        "Bulk import maps a CSV column to a field once",
        "Embeds resolve server-side so the editor never calls out",
        "Draft and published trees stay one tree, two states",
        "Asset URLs survive a storage backend swap",
        "Editor toolbar collapses instead of wrapping",
        "Page templates are data, not code",
        "Publishing writes one cache purge, not one per block",
        "Author field falls back to the account, never to blank",
        "Tag pages paginate past the first hundred",
        "Retire the v1 preview token format",
    ],
    "tideline-etl": [
        "Incremental load keyed on the source watermark",
        "Late-arriving rows reopen the affected partition only",
        "Schema drift raises before it writes a mangled column",
        "Warehouse merge is idempotent on a replayed batch",
        "Dead-letter rows land in a table, not a log line",
        "Column-level lineage in the run manifest",
        "Timezone normalisation at ingest, never at read",
        "A failed step resumes from its own checkpoint",
        "Source credentials rotate without a redeploy",
        "Row counts reconcile against the source per run",
        "Compact small files after every hourly load",
        "Nulls in the join key fail the run instead of dropping rows",
        "Backfill runs at a lower priority than the hourly load",
        "Data freshness published as a metric per table",
        "Retire the v1 extractor once v2 has run clean for a week",
        "Partition pruning survives the date-cast in the filter",
        "Secrets never reach the run manifest",
        "A schema change writes a new version, not a new table",
        "Hourly load skips a source that has not moved",
        "Unit costs per pipeline in the daily summary",
        "Replace the bespoke scheduler with a cron table",
        "One manifest per run, written once at the end",
        "Source connectors declare their own required grants",
        "Deduplicate on the natural key, not the row hash",
        "Run history keeps 90 days, then rolls up",
        "Alert on a table that stops arriving, not only on errors",
        "Test fixtures generated from the real schema",
        "Parallel extracts share one connection pool",
        "Drop the staging tables v1 left behind",
    ],
}

# a card's own paragraph. one per card rather than one sentence repeated down a column - a board
# where every description reads the same is the tell that gives a seeded demo away
_DESCRIPTIONS = [
    "{title}. Today the caller has no way to tell the two cases apart, and the logs do not record "
    "which one happened, so this starts by making the distinction visible.",
    "{title}. Raised after the third support ticket in a week about the same symptom; the cause "
    "is the shared path below, not any one caller.",
    "{title}. The current shape works for the common case and quietly loses the edge case. Make "
    "the edge case explicit rather than widening the happy path.",
    "{title}. Scoped small on purpose: one behaviour change, one migration, no rename.",
    "{title}. Blocked on nothing external - the interface already exposes everything this needs, "
    "so it is a change of rules, not of plumbing.",
    "{title}. The tests around this are thin, so the first task is the failing test that pins "
    "today's behaviour before anything moves.",
]

_CRITERIA = [
    "the new path has a test that fails without it",
    "no existing behaviour changes for the default configuration",
    "the change is documented where a reader would look for it",
]

_TASKS = [
    "write the failing test first",
    "make it pass",
    "check the neighbouring call sites",
]

_LEASES = {
    "orchard-pay": ["src/payments/**/*.py", "tests/**/*.py"],
    "lantern-cms": ["src/editor/**/*.js", "test/**/*.js"],
    "tideline-etl": ["pipelines/**/*.py", "tests/**/*.py"],
}

# what fills the attention column, drained in order across the three boards and cycled if it runs
# short. each entry is (stored status, blocked reason or None) - a card with no reason is the
# checking card waiting on a decision, which raises review_flag instead. USAGE_LIMIT, MERGE_CONFLICT
# and API_UNREACHABLE are absent on purpose: the board retries those itself, so isAttentionCard
# leaves them in their own column until the automatic attempts are spent (see attention.py's
# handled_by_board, and _spend_automatic_retries below, which is how the demo shows the spent case)
_ATTENTION_RECIPE = [
    ("doing", "AGENT_QUESTION"),
    ("doing", "TESTS_FAILED"),
    ("doing", "REVIEW_REJECTED"),
    ("checking", None),
    ("doing", "LEASE_CONFLICT"),
    ("doing", "CRASH"),
    ("todo", "DEPENDENCY_REJECTED"),
    ("doing", "MERGE_CONFLICT"),
    ("doing", "API_UNREACHABLE"),
]

# the two reasons the board handles itself, so they stay in the doing column with the gold outline
# rather than moving to attention - one per board, so the state is on show without being a backlog
_SELF_HANDLED = ["USAGE_LIMIT", None, None]

_MODELS = ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5"]


# ---- event streams -----------------------------------------------------------------------------
#
# payload shapes are the ones the runner and lifecycle actually append (see tests/test_timeline.py
# and tests/test_cost_telemetry.py) - the cost, replay, usage and digest panels are all projections
# over these, so seeding them is what makes those panels read as a real board


def _result_payload(cost: float, turns: int, model: str, denials: list | None = None) -> dict:
    if model.startswith("openai/"):
        identity = {"lab": "openai", "model": model.split("/", 1)[1], "profile": "demo"}
        return {
            **identity,
            "neutral": [
                {
                    **identity,
                    "kind": "usage",
                    "usage": {
                        "input_tokens": turns * 4200,
                        "output_tokens": turns * 380,
                        "cached_tokens": turns * 1000,
                        "cost_usd": cost,
                        "cost_estimated": True,
                    },
                },
                {
                    **identity,
                    "kind": "result",
                    "result": {
                        "ok": True,
                        "subtype": "success",
                        "num_turns": turns,
                        "text": "done",
                    },
                },
            ],
        }
    return {
        "total_cost_usd": cost,
        "num_turns": turns,
        "permission_denials": denials or [],
        "modelUsage": {
            model: {
                "inputTokens": turns * 4200,
                "outputTokens": turns * 380,
                "cacheReadInputTokens": turns * 15000,
                "costUSD": cost,
            },
            # the small helper claude code bills alongside the working model
            "claude-haiku-4-5": {"inputTokens": 900, "outputTokens": 40, "costUSD": 0.0011},
        },
        "result": "done",
    }


def _narration(store: Store, card_id: str, steps: list[tuple[str, str, dict]]) -> None:
    """one assistant tool_use plus its user tool_result per step - what replay steps through"""
    for i, (name, text, tool_input) in enumerate(steps):
        use_id = f"toolu_{card_id[:8]}_{i}"
        store.append_event(
            card_id,
            "assistant",
            {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}},
        )
        store.append_event(
            card_id,
            "assistant",
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": use_id, "name": name, "input": tool_input}
                    ]
                },
            },
        )
        store.append_event(
            card_id,
            "user",
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": use_id,
                            "content": "ok",
                            "is_error": False,
                        }
                    ]
                },
            },
        )


_WALKTHROUGH = [
    (
        "Read",
        "Reading the refund path before touching it.",
        {"file_path": "/workspace/src/payments/refund.py"},
    ),
    ("Grep", "Looking for every caller of capture().", {"pattern": "def capture"}),
    (
        "Edit",
        "The endpoint needs to key on the header, not the body.",
        {"file_path": "/workspace/src/payments/refund.py"},
    ),
    (
        "Write",
        "A test that fails without the key.",
        {"file_path": "/workspace/tests/test_refund_idempotency.py"},
    ),
    (
        "Bash",
        "Running the suite.",
        {"command": "uv run pytest -q tests/test_refund_idempotency.py"},
    ),
]


def _clean_attempt(
    store: Store, card_id: str, model: str, cost: float, pr_number: int, repo: str, slug: str
) -> None:
    """worker, test gate, reviewer, pull request - the shape of an attempt that got there"""
    store.append_event(card_id, "lifecycle_started", {})
    _narration(store, card_id, _WALKTHROUGH)
    store.append_event(card_id, "result", _result_payload(cost, 9, model))
    store.append_event(
        card_id,
        "worker_summary",
        {"text": "Keyed the endpoint on the Idempotency-Key header and covered the replay case."},
    )
    store.append_event(
        card_id, "test_gate", {"passed": True, "command": "uv run pytest -q", "exit_code": 0}
    )
    store.append_event(card_id, "result", _result_payload(cost / 4, 3, "claude-sonnet-5"))
    store.append_event(card_id, "review_gate", {"approved": True, "findings": []})
    store.append_event(
        card_id,
        "merge_request",
        {
            "opened": True,
            "url": f"https://github.com/demo/{repo}/pull/{pr_number}",
            "branch": f"card/{pr_number}-{slug}",
        },
    )


def _rejected_review_attempt(store: Store, card_id: str, model: str) -> None:
    """the attempt a reviewer stopped - one real finding, which is what the card panel shows"""
    store.append_event(card_id, "lifecycle_started", {})
    _narration(store, card_id, _WALKTHROUGH[:3])
    store.append_event(card_id, "result", _result_payload(0.62, 7, model))
    store.append_event(card_id, "worker_summary", {"text": "Widened the retry to every decline."})
    store.append_event(
        card_id, "test_gate", {"passed": True, "command": "uv run pytest -q", "exit_code": 0}
    )
    store.append_event(card_id, "result", _result_payload(0.08, 2, "claude-sonnet-5"))
    store.append_event(
        card_id,
        "review_gate",
        {
            "approved": False,
            "findings": [
                {
                    "severity": "high",
                    "category": "best practices",
                    "file": "src/payments/capture.py",
                    "line": 118,
                    "detail": (
                        "Retries every decline code, including hard declines the issuer will "
                        "never approve - retry only the soft advice codes."
                    ),
                }
            ],
        },
    )


def _failed_tests_attempt(store: Store, card_id: str, model: str) -> None:
    store.append_event(card_id, "lifecycle_started", {})
    _narration(store, card_id, _WALKTHROUGH[:2])
    store.append_event(card_id, "result", _result_payload(0.41, 5, model))
    store.append_event(card_id, "worker_summary", {"text": "Split the report per acquirer."})
    store.append_event(
        card_id,
        "test_gate",
        {
            "passed": False,
            "command": "uv run pytest -q",
            "exit_code": 1,
            "output": "FAILED tests/test_settlement.py::test_totals_per_acquirer",
        },
    )


def _refused_attempt(store: Store, card_id: str, model: str) -> None:
    """a lease conflict: the edit the agent wanted was outside what the card may write"""
    store.append_event(card_id, "lifecycle_started", {})
    _narration(store, card_id, _WALKTHROUGH[:2])
    store.append_event(
        card_id,
        "result",
        _result_payload(
            0.19,
            3,
            model,
            denials=[
                {"tool_name": "Edit", "tool_input": {"file_path": "/workspace/src/core/db.py"}}
            ],
        ),
    )


def _capped_attempt(store: Store, card_id: str, model: str, cost: float) -> None:
    """a run stopped by its worker's --max-budget-usd cap - what the cost-optimisation view's cap
    fit and waste sections read off result.subtype == error_max_budget_usd"""
    store.append_event(card_id, "lifecycle_started", {})
    _narration(store, card_id, _WALKTHROUGH[:2])
    result = _result_payload(cost, 22, model)
    result["subtype"] = "error_max_budget_usd"
    store.append_event(card_id, "result", result)


def _crashed_attempt(store: Store, card_id: str, model: str) -> None:
    """the container died mid-run - is_error with no other signal reads as CRASH"""
    store.append_event(card_id, "lifecycle_started", {})
    result = _result_payload(0.09, 1, model)
    result["is_error"] = True
    store.append_event(card_id, "result", result)


def _in_flight(store: Store, card_id: str) -> None:
    """a card an agent is working: the transcript the workforce panel shows, no gate yet.

    run_ended is written even though the run reads as live, because a doing card whose latest
    attempt started and never ended is exactly what server/runs.py's recover_orphaned_runs blocks
    as CRASH the moment a board opens - which would empty the demo's doing column on every start.
    """
    store.append_event(card_id, "lifecycle_started", {})
    _narration(store, card_id, _WALKTHROUGH[:3])
    store.append_event(card_id, "run_ended", {})


def _rate_limit_events(store: Store, card_id: str, profile: str) -> None:
    """what the usage panel draws: one window per type and profile, latest wins"""
    skew = DEMO_PROFILES.index(profile)
    for window_type, resets_in_hours in (
        ("five_hour", 2 + skew * 2),
        ("seven_day", 71 - skew * 30),
    ):
        store.append_event(
            card_id,
            "rate_limit_event",
            {
                "profile": profile,
                "rate_limit_info": {
                    "status": "allowed",
                    "rateLimitType": window_type,
                    "resetsAt": (datetime.now(UTC) + timedelta(hours=resets_in_hours)).timestamp(),
                },
            },
        )


# a 1x1 png - the smallest thing that is honestly an image attachment
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def board_column_counts(spec: dict[str, Any]) -> dict[str, int]:
    """one board spec -> how many cards each DRAWN column holds, regimes resolved to counts"""
    counts = dict(spec["counts"])
    for column, regime in spec["regimes"].items():
        counts[column] = REGIME_COUNTS[regime]
    return counts


def _seed_board(store: Store, spec: dict[str, Any], attention: Iterator) -> dict[str, Any]:
    """one board, its repo and its cards, built per drawn column.

    `attention` is drained across the three boards so every reason code lands somewhere; each card
    is tagged with the column it is meant to draw in, and _decorate_board gives it the events that
    make that true.
    """
    board = store.create_board(spec["name"], position=spec["position"])
    repo = store.create_repo(board["id"], **_REPOS[spec["repo"]])
    titles = iter(_TITLES[spec["repo"]])
    leases = _LEASES[spec["repo"]]
    counts = board_column_counts(spec)
    self_handled = _SELF_HANDLED[spec["position"] % len(_SELF_HANDLED)]

    cards: list[dict[str, Any]] = []
    position = 0
    for column in DEMO_COLUMNS:
        for index in range(counts.get(column, 0)):
            status, reason, flag = column, None, False
            if column == "attention":
                status, reason = next(attention)
                flag = reason is None
            elif column == "doing" and index == 0 and self_handled:
                # the board retries this one itself, so it keeps the doing column and only wears
                # the gold outline - the state that is easiest to misread as "waiting on you"
                reason = self_handled
            title = next(titles)
            card = store.create_card(
                board["id"],
                repo["id"],
                title,
                status=status,
                blocked_reason_code=reason,
                description=_DESCRIPTIONS[position % len(_DESCRIPTIONS)].format(title=title),
                position=position,
                review_flag=flag,
                tasks=_TASKS,
                criteria=_CRITERIA,
                leases=leases,
                model=_MODELS[position % len(_MODELS)],
            )
            cards.append({**card, "column": column})
            position += 1
    return {"board": board, "repo": repo, "cards": cards}


# the board's own note on a blocked card - what the inbox shows for every reason but a question
_BLOCK_NOTES = {
    "TESTS_FAILED": "The test gate failed: tests/test_settlement.py::test_totals_per_acquirer.",
    "REVIEW_REJECTED": "The reviewer stopped this: retries every decline, not only soft declines.",
    "LEASE_CONFLICT": "The agent asked to edit src/core/db.py, which is outside this card's lease.",
    "USAGE_LIMIT": "The five-hour window is spent. New runs resume when it resets.",
    "CRASH": "The container exited before the agent finished its first turn.",
    "MERGE_CONFLICT": "The branch no longer merges into development; the base moved underneath it.",
    "API_UNREACHABLE": "The API could not be reached. The board is retrying on its own.",
    "DEPENDENCY_REJECTED": "The card this one waits on was rejected, so it cannot start.",
}

_AGENT_QUESTION = (
    "Refunds past the 180-day window: should the endpoint refuse them outright, or accept them "
    "and mark the entry for the chargeback route? The spec reads both ways and the tests do not "
    "cover it, so I have stopped rather than pick."
)


def _spend_automatic_retries(store: Store, card_id: str, reason: str) -> None:
    """the events that prove the board already tried by itself, so handled_by_board hands the card
    over instead of hiding it - without these, MERGE_CONFLICT and API_UNREACHABLE never draw in the
    attention column at all"""
    if reason == "API_UNREACHABLE":
        for _ in range(API_UNREACHABLE_MAX_RETRIES):
            store.append_event(card_id, "api_unreachable_retry", {"retry_at": None})
    if reason == "MERGE_CONFLICT":
        store.append_event(card_id, "merge_conflict_auto_resume", {})


def _slug(title: str) -> str:
    """a branch-shaped short name from a card title, the way a real card's branch reads"""
    words = "".join(c if c.isalnum() or c == " " else "" for c in title.lower()).split()
    return "-".join(words[:4])


def _attention_card(store: Store, card: dict[str, Any], position: int, repo: str) -> None:
    """the run behind one attention card - the story its panel and the inbox both read"""
    card_id = card["id"]
    reason = card["blocked_reason_code"]
    if reason is None:  # the checking card waiting on your y or x
        _clean_attempt(
            store, card_id, "claude-sonnet-5", 1.44, 170 + position, repo, _slug(card["title"])
        )
        store.add_comment(
            card_id,
            BOARD_AUTHOR,
            "Tests passed and the reviewer approved. The pull request is open and waiting on you.",
        )
        return
    if reason == "AGENT_QUESTION":
        store.append_event(card_id, "lifecycle_started", {})
        _narration(store, card_id, _WALKTHROUGH[:3])
        result = _result_payload(0.37, 5, "claude-sonnet-5")
        result["result"] = _AGENT_QUESTION
        store.append_event(card_id, "result", result)
        store.append_event(card_id, "worker_summary", {"text": _AGENT_QUESTION})
        return
    if reason == "TESTS_FAILED":
        _failed_tests_attempt(store, card_id, "claude-opus-5")
    elif reason == "LEASE_CONFLICT":
        _refused_attempt(store, card_id, "claude-sonnet-5")
    elif reason == "REVIEW_REJECTED":
        _rejected_review_attempt(store, card_id, "claude-opus-5")
    elif reason != "DEPENDENCY_REJECTED":
        store.append_event(card_id, "lifecycle_started", {})
        _narration(store, card_id, _WALKTHROUGH[: 1 + position % 3])
    _spend_automatic_retries(store, card_id, reason)
    store.add_comment(card_id, BOARD_AUTHOR, _BLOCK_NOTES[reason])


def _by_column(cards: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        grouped.setdefault(card["column"], []).append(card)
    return grouped


def _decorate_board(store: Store, entry: dict[str, Any], index: int) -> None:
    repo = entry["repo"]["name"]
    grouped = _by_column(entry["cards"])
    todo = grouped.get("todo", [])
    doing = grouped.get("doing", [])
    checking = grouped.get("checking", [])
    accepted = grouped.get("accepted", [])
    rejected = grouped.get("rejected", [])

    # a dependency chain down the todo column: each card waits on the one before it
    for earlier, later in zip(todo[:3], todo[1:4], strict=False):
        store.set_dependencies(later["id"], [earlier["id"]])

    for card in doing:
        _in_flight(store, card["id"])
        if card["blocked_reason_code"]:
            store.add_comment(card["id"], BOARD_AUTHOR, _BLOCK_NOTES[card["blocked_reason_code"]])
    if doing:
        for profile in DEMO_PROFILES:
            _rate_limit_events(store, doing[0]["id"], profile)
        store.add_comment(
            doing[-1]["id"],
            "demo",
            "Keep the header name exactly as the spec spells it - the SDK capitalises it.",
        )

    for position, card in enumerate(grouped.get("attention", [])):
        _attention_card(store, card, position, repo)

    for i, card in enumerate(accepted):
        # the first accepted card is the board's fully dressed one: two attempts that did not get
        # there before the one that did, so telemetry and replay both have a history to show
        if i == 0:
            _failed_tests_attempt(store, card["id"], "claude-sonnet-5")
            _refused_attempt(store, card["id"], "claude-sonnet-5")
        model = _MODELS[i % len(_MODELS)]
        if i % 2:
            from smortboard.labs.catalog import load_catalog

            model_id = load_catalog()["openai"]["models"][0]["id"]
            store.update_card(card["id"], lab="openai", model=model_id)
            model = f"openai/{model_id}"
        _clean_attempt(
            store,
            card["id"],
            model,
            0.94 + i * 0.31,
            140 + i,
            repo,
            _slug(card["title"]),
        )
    for i, card in enumerate(checking):
        if i == 0:
            # this one overran its worker cap before the attempt that finally passed - the cost
            # optimisation view's cap-fit and waste sections need at least one of these to show
            _capped_attempt(store, card["id"], "claude-opus-5", 5.85)
        _clean_attempt(
            store,
            card["id"],
            "claude-sonnet-5",
            1.22 + i * 0.4,
            160 + i,
            repo,
            _slug(card["title"]),
        )
    for i, card in enumerate(rejected):
        if i == 0:
            _crashed_attempt(store, card["id"], "claude-sonnet-5")
        _rejected_review_attempt(store, card["id"], "claude-opus-5")

    # a couple of rated cards, one at each end of the scale - the rest stay unrated so the cost
    # optimisation view's cap-fit table shows both a "rated" and an "estimated" row
    if todo:
        store.update_card(todo[0]["id"], complexity=1)
    if len(accepted) > 1:
        store.update_card(accepted[1]["id"], complexity=3)

    # one fully dressed card per board: a dependency, both comments and the attachment, so there
    # is always a card whose panel shows every section filled in
    if len(accepted) > 1:
        store.set_dependencies(accepted[0]["id"], [accepted[1]["id"]])
    if accepted:
        store.add_comment(accepted[0]["id"], "demo", "Merged. The replay case is the one I wanted.")
        store.add_comment(
            accepted[0]["id"], BOARD_AUTHOR, "Tests passed, reviewer approved, pull request open."
        )
        store.add_attachment(accepted[0]["id"], "settlement-split.png", "image/png", _TINY_PNG)

    board_id = entry["board"]["id"]
    store.set_plan(board_id, _PLANS[index])
    store.add_orchestrator_message(board_id, AUTHOR_KEY, _CHATS[index][0])
    store.add_orchestrator_message(board_id, "orchestrator", _CHATS[index][1])


_PLANS = [
    "Ship the idempotency work first - everything else on the board touches the same call path "
    "and would only queue up behind it.",
    "Editor correctness before editor features: autosave and the conflict case, then the blocks.",
    "The watermark work unblocks the rest of the column; nothing else starts until it lands.",
]

_CHATS = [
    (
        "The refund endpoint is capturing twice when a client retries. What should we do?",
        "Three cards, in this order: key the endpoint on the Idempotency-Key header, then make "
        "the retry read the issuer's advice code, then split the settlement report. The first "
        "two share a lease, so the board will never run them at once.",
    ),
    (
        "Editors keep losing work when a tab crashes. Where do we start?",
        "Two cards. Autosave every 20 seconds with a conflict check, then draft previews that "
        "expire - the second is a smaller change and can run in parallel, different lease.",
    ),
    (
        "The hourly load reprocesses the whole table. Can we make it incremental?",
        "One card for the source watermark, one for reopening only the affected partition when "
        "rows arrive late. The second depends on the first and the board will hold it until the "
        "first card's pull request is merged.",
    ),
]


def build_demo_db(path: str | Path) -> Path:
    """seeds a demo board database at `path` and returns it. writes nowhere else."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # one cycle across all three boards, so the first nine attention cards are the nine distinct
    # reasons and only then does a reason repeat
    attention = cycle(_ATTENTION_RECIPE)
    with Store(path) as store:
        for i, spec in enumerate(_BOARDS):
            entry = _seed_board(store, {**spec, "position": i}, attention)
            _decorate_board(store, entry, i)
    return path


# the demo's own credential profiles. the usage and profiles panels read these off DISK, not out
# of the database, so a demo that only swapped the db would still put the operator's real profile
# names on screen - which is exactly what a demo is for not doing
DEMO_PROFILES = ["weekday", "weekend"]


def isolate_demo_config(root: Path) -> None:
    """points every config lookup at `root` and seeds it with the demo's profiles.

    process-wide on purpose: profiles.py and exec/backends.py read the environment at call time
    rather than taking a path, so this is the seam that exists. only --demo calls it, and it is
    the whole process's mode for its lifetime.
    """
    tokens = root / "smortboard" / "tokens"
    tokens.mkdir(parents=True, exist_ok=True)
    for name in DEMO_PROFILES:
        token = tokens / name
        token.write_text("demo-token-not-a-real-credential\n")
        token.chmod(0o600)
    state = root / "smortboard" / "profiles.json"
    state.write_text(
        json.dumps({"active": DEMO_PROFILES[0], "profiles": DEMO_PROFILES, "limits": {}})
    )
    # XDG_CONFIG_HOME on unix, APPDATA on windows - config_base reads one or the other
    os.environ["XDG_CONFIG_HOME"] = str(root)
    os.environ["APPDATA"] = str(root)
    os.environ[profiles.STATE_PATH_ENV] = str(state)


def make_demo_db() -> Path:
    """a fresh demo db in its own throwaway directory, with the config to match - the operator's
    own database and credential profiles are both left untouched"""
    root = Path(tempfile.mkdtemp(prefix="smortboard-demo-"))
    isolate_demo_config(root / "config")
    return build_demo_db(root / "demo.db")


if __name__ == "__main__":  # pragma: no cover - a hand check of the counts
    print(json.dumps(column_counts(), indent=2))
