"""out-of-card claude sessions: a separate cost line, never part of card totals"""

import json

import pytest

from smortboard.store.api import Store
from smortboard.telemetry import boards_overview, out_of_card_spend


def _assistant(msg_id, inp, out, cache_read=0, cache_create=0, model="claude-sonnet-5-5"):
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "id": msg_id,
                "model": model,
                "usage": {
                    "input_tokens": inp,
                    "output_tokens": out,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_create,
                },
            },
        }
    )


@pytest.fixture
def projects(tmp_path):
    project = tmp_path / "proj-a"
    project.mkdir()
    lines = [
        json.dumps({"type": "user", "message": {"content": "x"}}),
        "{not json",
        "",
        _assistant("m1", 2, 10, cache_read=100, cache_create=50),
        _assistant("m1", 2, 20, cache_read=100, cache_create=50),
        _assistant("m2", 5, 7),
    ]
    (project / "sess-1.jsonl").write_text("\n".join(lines), encoding="utf-8")
    return tmp_path


def test_session_appears_with_deduped_tokens(projects):
    result = out_of_card_spend(projects)
    [session] = result["sessions"]
    assert session["session_id"] == "sess-1"
    assert session["messages"] == 2
    assert session["input_tokens"] == (2 + 50 + 100) + 5
    assert session["cached_tokens"] == 100
    assert session["output_tokens"] == 27
    # no operator prices for the model, so cost stays unknown rather than zero
    assert session["cost_usd"] is None
    assert result["totals"]["unknown_costs"] == 1


def test_missing_directory_is_zero(tmp_path):
    result = out_of_card_spend(tmp_path / "nope")
    assert result["sessions"] == []
    assert result["totals"]["sessions"] == 0


def test_card_totals_unchanged(projects, tmp_path, monkeypatch):
    with Store(tmp_path / "board.db") as store:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "empty"))
        before = boards_overview(store)
        (tmp_path / "cfg" / "projects" / "p").mkdir(parents=True)
        (tmp_path / "cfg" / "projects" / "p" / "s.jsonl").write_text(_assistant("m", 9, 9))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
        after = boards_overview(store)
    assert before["out_of_card"]["sessions"] == []
    assert len(after["out_of_card"]["sessions"]) == 1
    assert after["totals"] == before["totals"]
    assert after["boards"] == before["boards"]
