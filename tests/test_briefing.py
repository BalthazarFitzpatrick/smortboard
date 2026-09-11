"""resume_briefing: the compact "what already happened" text a resumed card's brief carries.

Fixture events are hand-built in the real payload shapes (see tests/test_timeline.py and
tests/test_cost_telemetry.py) - no model call, no docker, ever.
"""

import pytest

from smortboard.briefing import resume_briefing
from smortboard.lifecycle import build_card_prompt
from smortboard.store.api import Store


def _assistant_tool_use(tool_use_id, name, tool_input):
    return {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}]
        },
    }


def _result(permission_denials=None):
    return {"type": "result", "permission_denials": permission_denials or []}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "b.db")
    yield s
    s.close()


@pytest.fixture
def card_id(store):
    board = store.create_board("b")
    return store.create_card(board["id"], None, "a card")["id"]


def test_no_events_gives_no_briefing(store, card_id):
    assert resume_briefing(store, card_id) is None


def test_an_attempt_that_never_reached_the_worker_gives_no_briefing(store, card_id):
    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(card_id, "result", _result())
    assert resume_briefing(store, card_id) is None


def test_a_blocked_attempt_summarises_files_commands_and_the_test_gate(store, card_id):
    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(
        card_id, "assistant", _assistant_tool_use("t1", "Edit", {"file_path": "/w/app.py"})
    )
    store.append_event(
        card_id, "assistant", _assistant_tool_use("t2", "Bash", {"command": "uv run pytest -q"})
    )
    store.append_event(
        card_id,
        "assistant",
        _assistant_tool_use("t3", "Bash", {"command": "rm -rf /"}),
    )
    store.append_event(
        card_id,
        "result",
        _result([{"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}]),
    )
    store.append_event(card_id, "worker_summary", {"text": "added the retry loop"})
    store.append_event(
        card_id,
        "test_gate",
        {"passed": False, "command": "uv run pytest -q", "exit_code": 1, "output": "boom"},
    )

    briefing = resume_briefing(store, card_id)
    assert briefing is not None
    assert "attempt 1 of 1" in briefing.lower()
    assert "app.py" in briefing
    assert "uv run pytest -q" in briefing
    assert "Commands refused" in briefing and "rm -rf /" in briefing
    assert "added the retry loop" in briefing
    assert "Ended: blocked: TESTS_FAILED" in briefing
    assert "already on this branch" in briefing


def test_review_findings_carry_file_and_message(store, card_id):
    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(card_id, "worker_summary", {"text": "first pass"})
    store.append_event(
        card_id,
        "review_gate",
        {
            "approved": False,
            "error": None,
            "findings": [
                {
                    "category": "bug",
                    "severity": "high",
                    "file": "smortboard/exec/runner.py",
                    "line": 42,
                    "message": "off by one",
                }
            ],
        },
    )

    briefing = resume_briefing(store, card_id)
    assert "smortboard/exec/runner.py:42" in briefing
    assert "off by one" in briefing
    assert "Ended: blocked: REVIEW_REJECTED" in briefing


def test_older_attempts_get_one_line_each_and_only_the_latest_worker_attempt_is_detailed(
    store, card_id
):
    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(card_id, "worker_summary", {"text": "attempt one"})
    store.append_event(
        card_id, "test_gate", {"passed": False, "command": "uv run pytest -q", "exit_code": 1}
    )

    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(card_id, "worker_summary", {"text": "attempt two, fixed it"})
    store.append_event(card_id, "review_gate", {"approved": True, "error": None, "findings": []})
    store.append_event(card_id, "merge_request", {"url": "https://example.test/pull/1"})

    briefing = resume_briefing(store, card_id)
    assert "attempt 2 of 2" in briefing.lower()
    assert "attempt two, fixed it" in briefing
    assert "attempt one" not in briefing  # only in the one-line earlier-attempts summary
    assert "Earlier attempts:" in briefing
    assert "- attempt 1: blocked: TESTS_FAILED" in briefing
    assert "Ended: pull request" in briefing


def test_the_run_asking_for_the_briefing_is_not_an_earlier_attempt(store, card_id):
    # the lifecycle records this run's own lifecycle_started before it builds the brief
    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(card_id, "worker_summary", {"text": "first pass"})
    store.append_event(card_id, "lifecycle_started", {})

    briefing = resume_briefing(store, card_id)
    assert "attempt 1 of 1" in briefing.lower()
    assert "Earlier attempts" not in briefing


def test_a_fresh_cut_after_a_reject_does_not_claim_the_old_commits(store, card_id):
    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(card_id, "worker_summary", {"text": "first pass"})

    briefing = resume_briefing(store, card_id, worktree_reused=False)
    assert "already on this branch" not in briefing
    assert "starts fresh from the base branch" in briefing


def test_a_long_briefing_is_truncated_to_the_cap(store, card_id):
    store.append_event(card_id, "lifecycle_started", {})
    store.append_event(card_id, "worker_summary", {"text": "x" * 5000})

    briefing = resume_briefing(store, card_id)
    assert len(briefing) <= 2500 + len("\n...[truncated]")
    assert briefing.endswith("...[truncated]")


def test_build_card_prompt_is_byte_identical_with_no_briefing(store, card_id):
    card = store.get_card(card_id)
    assert build_card_prompt(card) == build_card_prompt(card, None)
    assert "already ran" not in build_card_prompt(card)


def test_build_card_prompt_includes_the_briefing_under_its_own_heading(store, card_id):
    card = store.get_card(card_id)
    prompt = build_card_prompt(card, "attempt 1 of 1 (most recent that ran):\nEnded: refused")
    assert "What already happened" in prompt
    assert "Ended: refused" in prompt
