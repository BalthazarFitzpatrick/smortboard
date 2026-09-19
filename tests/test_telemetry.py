"""roster and usage: pure projections over the store and a run registry snapshot, no model calls."""

import pytest

from smortboard.store.api import Store
from smortboard.telemetry import roster_rows, usage_projection


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "board.db") as s:
        yield s


def _assistant_event(content):
    return {"message": {"role": "assistant", "content": content}}


# -- roster --------------------------------------------------------------------


def test_roster_derives_activity_from_the_latest_tool_use(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "editing card")
    store.append_event(
        card["id"],
        "assistant",
        _assistant_event([{"type": "tool_use", "name": "Edit", "input": {"file_path": "a/b.py"}}]),
    )
    rows = roster_rows(store, [{"card_id": card["id"], "phase": "running"}])
    assert rows[0]["activity"] == "editing b.py"


def test_roster_maps_every_tool_to_its_activity_phrase(store):
    board = store.create_board("b")
    cases = [
        ("Read", {"file_path": "x/y.py"}, "reading y.py"),
        ("Bash", {"command": "uv run pytest -q " + "x" * 60}, None),  # checked separately below
        ("Grep", {}, "searching"),
        ("Glob", {}, "searching"),
        ("StructuredOutput", {}, "writing its verdict"),
    ]
    for name, tool_input, expected in cases:
        card = store.create_card(board["id"], None, f"card-{name}")
        store.append_event(
            card["id"],
            "assistant",
            _assistant_event([{"type": "tool_use", "name": name, "input": tool_input}]),
        )
        activity = roster_rows(store, [{"card_id": card["id"], "phase": "running"}])[0]["activity"]
        if expected is not None:
            assert activity == expected
        else:
            assert activity.startswith("running ") and len(activity) <= 48 + len("running ")


def test_roster_reports_thinking_when_only_text_was_seen(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "card")
    store.append_event(card["id"], "assistant", _assistant_event([{"type": "text", "text": "hm"}]))
    rows = roster_rows(store, [{"card_id": card["id"], "phase": "running"}])
    assert rows[0]["activity"] == "thinking"


def test_roster_reports_starting_with_no_events_yet(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "card")
    rows = roster_rows(store, [{"card_id": card["id"], "phase": "running"}])
    assert rows[0]["activity"] == "starting"


@pytest.mark.parametrize(
    "phase,activity",
    [
        ("testing", "running the test gate"),
        ("reviewing", "reviewing the diff"),
        ("fixing", "fixing review findings"),
        ("opening", "opening the pull request"),
    ],
)
def test_roster_phase_activity_wins_over_the_last_tool_use(store, phase, activity):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "card")
    store.append_event(
        card["id"],
        "assistant",
        _assistant_event([{"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}}]),
    )
    rows = roster_rows(store, [{"card_id": card["id"], "phase": phase}])
    assert rows[0]["activity"] == activity


def test_roster_lists_only_the_running_card_not_the_blocked_one(store):
    """a blocked card belongs to the attention inbox, not the roster - it holds no live run"""
    board = store.create_board("b")
    running = store.create_card(board["id"], None, "running")
    store.update_card(running["id"], status="doing")
    blocked = store.create_card(board["id"], None, "blocked")
    store.update_card(blocked["id"], status="doing", blocked_reason_code="USAGE_LIMIT")

    rows = roster_rows(store, [{"card_id": running["id"], "phase": "running"}])
    assert [r["card_id"] for r in rows] == [running["id"]]
    assert rows[0]["state"] == "working"


def test_roster_is_empty_when_nothing_is_running(store):
    board = store.create_board("b")
    store.create_card(board["id"], None, "idle in todo")
    blocked = store.create_card(board["id"], None, "blocked")
    store.update_card(blocked["id"], status="doing", blocked_reason_code="USAGE_LIMIT")
    assert roster_rows(store, []) == []


# -- usage ------------------------------------------------------------------


def test_usage_reads_the_current_flat_rate_limit_shape_with_no_utilisation(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "c")
    store.append_event(
        card["id"],
        "rate_limit_event",
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "allowed",
                "resetsAt": 1789078800,
                "rateLimitType": "five_hour",
                "overageStatus": "rejected",
                "isUsingOverage": False,
            },
        },
    )
    usage = usage_projection(store)
    assert usage["windows"] == [
        {
            "type": "five_hour",
            "lab": "anthropic",
            "profile": "default",
            "status": "ok",
            "resets_at": 1789078800,
            "utilization": None,
        }
    ]


def test_usage_reads_the_older_unified_windows_shape(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "c")
    store.append_event(
        card["id"],
        "rate_limit_event",
        {
            "rate_limit_info": {
                "status": "allowed",
                "unifiedWindows": {
                    "five_hour": {"utilization": 0.11, "resetsAt": 111, "status": "allowed"},
                    "seven_day": {"utilization": 0.02, "resetsAt": 222, "status": "allowed"},
                },
            }
        },
    )
    usage = usage_projection(store)
    by_type = {w["type"]: w for w in usage["windows"]}
    assert by_type["five_hour"]["utilization"] == 0.11
    assert by_type["seven_day"]["resets_at"] == 222


def test_usage_keeps_only_the_latest_event_per_window_type(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "c")
    for resets_at in (100, 200, 300):
        store.append_event(
            card["id"],
            "rate_limit_event",
            {
                "rate_limit_info": {
                    "status": "allowed",
                    "resetsAt": resets_at,
                    "rateLimitType": "five_hour",
                }
            },
        )
    usage = usage_projection(store)
    assert len(usage["windows"]) == 1
    assert usage["windows"][0]["resets_at"] == 300


def test_usage_takes_the_most_recent_event_across_cards_not_by_card_id(store):
    """regression: list_events_by_kind used to sort by (card_id, seq), which groups by card
    rather than time - a card whose id sorted alphabetically later could overwrite a genuinely
    newer event from an earlier-id card, exactly the overcount this card reported. give the
    later event to the alphabetically-first card_id to prove ordering is now by wall-clock time."""
    board = store.create_board("b")
    card_a = store.create_card(board["id"], None, "a")  # sorts before card_z's id
    card_z = store.create_card(board["id"], None, "z")
    store.append_event(
        card_z["id"],
        "rate_limit_event",
        {"rate_limit_info": {"status": "allowed", "resetsAt": 100, "rateLimitType": "five_hour"}},
    )
    store.append_event(
        card_a["id"],
        "rate_limit_event",
        {"rate_limit_info": {"status": "allowed", "resetsAt": 200, "rateLimitType": "five_hour"}},
    )
    usage = usage_projection(store)
    assert len(usage["windows"]) == 1
    assert usage["windows"][0]["resets_at"] == 200


def test_usage_windows_are_attributed_to_the_recording_profile(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "c")
    store.append_event(
        card["id"],
        "rate_limit_event",
        {
            "profile": "alt",
            "rate_limit_info": {"status": "allowed", "resetsAt": 100, "rateLimitType": "five_hour"},
        },
    )
    store.append_event(
        card["id"],
        "rate_limit_event",
        {"rate_limit_info": {"status": "allowed", "resetsAt": 200, "rateLimitType": "five_hour"}},
    )
    usage = usage_projection(store)
    by_profile = {w["profile"]: w for w in usage["windows"]}
    assert by_profile["alt"]["resets_at"] == 100
    assert by_profile["default"]["resets_at"] == 200


def test_usage_sums_model_usage_and_cost_across_results(store):
    board = store.create_board("b")
    card = store.create_card(board["id"], None, "c")
    store.append_event(
        card["id"],
        "result",
        {
            "total_cost_usd": 0.05,
            "modelUsage": {
                "claude-sonnet": {
                    "inputTokens": 100,
                    "outputTokens": 20,
                    "cacheReadInputTokens": 5000,
                    "cacheCreationInputTokens": 10,
                    "costUSD": 0.05,
                }
            },
        },
    )
    store.append_event(
        card["id"],
        "result",
        {
            "total_cost_usd": 0.02,
            "modelUsage": {
                "claude-sonnet": {
                    "inputTokens": 50,
                    "outputTokens": 10,
                    "cacheReadInputTokens": 1000,
                    "cacheCreationInputTokens": 0,
                    "costUSD": 0.02,
                }
            },
        },
    )
    usage = usage_projection(store)
    assert usage["runs"] == 2
    assert usage["total_cost_usd"] == pytest.approx(0.07)
    model = usage["models"][0]
    assert model["model"] == "anthropic/claude-sonnet"
    assert model["input_tokens"] == 150
    assert model["output_tokens"] == 30
    assert model["cache_read_tokens"] == 6000
    assert model["cost_usd"] == pytest.approx(0.07)


def test_usage_is_empty_with_no_events(store):
    usage = usage_projection(store)
    assert usage == {
        "windows": [],
        "models": [],
        "total_cost_usd": 0.0,
        "runs": 0,
        "known_cost_usd": 0,
        "unknown_costs": 0,
        "cost_estimated": False,
    }
