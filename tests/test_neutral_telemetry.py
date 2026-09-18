"""all labs share accounting and unknown prices cannot silently become free runs"""

import pytest

from smortboard.budgets import spend_refusal
from smortboard.digest import board_digest
from smortboard.store import Store
from smortboard.telemetry import (
    board_costs,
    board_evidence,
    board_spend_today,
    boards_overview,
    card_telemetry,
    cost_optimisation,
    estimate_complexity,
    roster_rows,
    usage_projection,
)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "board.db") as value:
        yield value


def _run(store, board, lab, cost, *, model="same-name"):
    card = store.create_card(board["id"], None, lab, complexity=2)
    store.append_event(card["id"], "lifecycle_started", {})
    identity = {"lab": lab, "model": model, "profile": "work"}
    store.append_event(
        card["id"],
        "provider.finished",
        {
            "neutral": [
                {
                    **identity,
                    "kind": "usage",
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 10,
                        "cost_usd": cost,
                        "cost_estimated": cost is not None,
                    },
                },
                {**identity, "kind": "result", "result": {"ok": True, "num_turns": 1}},
            ]
        },
    )
    store.append_event(card["id"], "worker_summary", {"text": "finished"})
    store.append_event(card["id"], "merge_request", {"url": "https://example.test/pr/1"})
    store.update_card(card["id"], status="accepted")
    return card


def test_mixed_labs_keep_model_evidence_and_windows_separate(store):
    board = store.create_board("mixed")
    first = _run(store, board, "anthropic", 0.2)
    _run(store, board, "openai", 0.3)
    for lab in ("anthropic", "openai"):
        store.append_event(
            first["id"],
            "provider.limit",
            {
                "neutral": [
                    {
                        "kind": "rate_limit",
                        "lab": lab,
                        "profile": "work",
                        "model": "same-name",
                        "rate_limit": {
                            "window": "five_hour",
                            "status": "warning",
                            "resets_at": 123,
                        },
                    }
                ]
            },
        )
    usage = usage_projection(store)
    assert usage["total_cost_usd"] == 0.5
    assert usage["cost_estimated"] is True
    assert {row["model"] for row in usage["models"]} == {"anthropic/same-name", "openai/same-name"}
    assert {row["lab"] for row in usage["windows"]} == {"anthropic", "openai"}
    evidence = board_evidence(store, board["id"])
    assert len(evidence["model_scorecard"]) == 2
    assert len(cost_optimisation(store)["model_fit"]) == 2
    assert board_spend_today(store, board["id"]) == 0.5
    assert board_digest(store, board["id"], 0)["runs_and_spend"]["total_cost_usd"] == 0.5
    overview = boards_overview(store)
    assert overview["totals"]["cost_estimated"]
    assert overview["cost_groups"]["accepted"]["cost_estimated"]
    assert all(row["cost_estimated"] for row in overview["totals"]["spend_by_model"])


def test_unknown_prices_propagate_through_every_aggregate_and_spend_guard(store):
    board = store.create_board("unknown")
    card = _run(store, board, "openai", None)
    usage = usage_projection(store)
    assert usage["total_cost_usd"] is None
    assert usage["unknown_costs"] == 1
    assert usage["models"][0]["cost_usd"] is None
    assert card_telemetry(store, card["id"])["totals"]["cost_usd"] is None
    assert board_costs(store, board["id"])[0]["cost_usd"] is None
    assert boards_overview(store)["totals"]["cost_usd"] is None
    assert boards_overview(store)["cost_groups"]["accepted"]["cost_usd"] is None
    assert board_spend_today(store, board["id"]) is None
    assert board_evidence(store, board["id"])["model_scorecard"][0]["avg_cost_usd"] is None
    assert cost_optimisation(store)["model_fit"][0]["cost_per_accepted_card_usd"] is None
    assert board_digest(store, board["id"], 0)["runs_and_spend"]["total_cost_usd"] is None
    store.set_board_daily_budget(board["id"], 10)
    assert "unknown" in spend_refusal(store, card)
    store.set_board_daily_budget(board["id"], None)
    store.set_setting("card_total_budget_usd", 10)
    assert "unknown" in spend_refusal(store, card)


def test_unknown_board_turn_spend_survives_export_and_blocks_budget(store, tmp_path):
    board = store.create_board("unknown turn")
    card = store.create_card(board["id"], None, "waiting")
    store.add_board_spend(board["id"], "fold", None, lab="openai", model="gpt-6-astra")
    store.set_board_daily_budget(board["id"], 10)
    assert board_spend_today(store, board["id"]) is None
    assert "unknown" in spend_refusal(store, card)
    assert boards_overview(store)["totals"]["turn_cost_usd"] is None


def test_complexity_uses_catalog_tier_across_labs():
    assert estimate_complexity(2, 2, 0, False, "openai/gpt-6-astra") == 2
    assert estimate_complexity(2, 2, 0, False, "openai/gpt-5.6-luna") == 1


@pytest.mark.parametrize(
    ("item", "activity"),
    [
        ({"type": "command_execution", "command": "git status"}, "running git status"),
        (
            {"type": "file_change", "changes": [{"path": "src/app.py", "kind": "update"}]},
            "editing app.py",
        ),
        (
            {
                "type": "file_change",
                "changes": [
                    {"path": "src/app.py", "kind": "update"},
                    {"path": "src/main.py", "kind": "add"},
                ],
            },
            "editing files",
        ),
    ],
)
def test_codex_tool_activity_is_visible_in_roster(store, item, activity):
    from smortboard.labs.codex import CodexAdapter

    board = store.create_board("Codex")
    card = store.create_card(board["id"], None, "working")
    raw = {"type": "item.completed", "item": {"id": "item_1", "status": "completed", **item}}
    store.append_event(
        card["id"],
        raw["type"],
        {"lab": "openai", "neutral": [event.to_dict() for event in CodexAdapter().normalize(raw)]},
    )
    rows = roster_rows(store, [{"card_id": card["id"], "phase": "running"}])
    assert rows[0]["activity"] == activity
