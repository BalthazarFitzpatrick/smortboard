"""mixed-lab persistence and legacy migration are lossless across backups and exports"""

import json
import sqlite3

import pytest

from smortboard.store.api import Store
from smortboard.store.schema import _MIGRATIONS, current_version


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "board.db") as value:
        yield value


def test_v19_card_model_is_unchanged_and_reads_as_anthropic(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    for script in _MIGRATIONS[:19]:
        conn.executescript(script)
    conn.execute("PRAGMA user_version = 19")
    conn.execute("INSERT INTO boards VALUES ('b', 'legacy', 0, '2026-09-17', NULL, NULL)")
    conn.execute(
        "INSERT INTO cards (id, board_id, title, status, position, created_at, updated_at, model) "
        "VALUES ('c', 'b', 'legacy', 'todo', 0, '2026-09-17', '2026-09-17', 'sonnet')"
    )
    conn.commit()
    conn.close()
    with Store(path) as migrated:
        assert current_version(migrated._conn) == len(_MIGRATIONS)
        card = migrated.get_card("c")
        assert (card["lab"], card["model"]) == ("anthropic", "sonnet")
        assert migrated.list_cards("b")[0] == card
        assert migrated._conn.execute("SELECT lab FROM cards").fetchone()[0] is None


def test_card_model_pairs_patch_and_clear(store):
    board = store.create_board("mixed")
    card = store.create_card(board["id"], None, "c")
    assert card["lab"] is None and card["model"] is None
    changed = store.update_card(card["id"], lab="openai", model="gpt-6-astra")
    assert (changed["lab"], changed["model"]) == ("openai", "gpt-6-astra")
    with pytest.raises(ValueError, match="unknown model"):
        store.update_card(card["id"], model="unknown")
    with pytest.raises(ValueError, match="unknown lab"):
        store.update_card(card["id"], lab="unknown")
    cleared = store.update_card(card["id"], model=None)
    assert cleared["lab"] is None and cleared["model"] is None
    changed = store.update_card(card["id"], model="openai/gpt-5.6-sol")
    assert (changed["lab"], changed["model"]) == ("openai", "gpt-5.6-sol")


def test_card_effort_is_its_own_and_only_a_named_level(store):
    board = store.create_board("effort")
    card = store.create_card(board["id"], None, "c")
    assert card["effort"] is None
    assert store.update_card(card["id"], effort="high")["effort"] == "high"
    with pytest.raises(ValueError, match="effort"):
        store.update_card(card["id"], effort="unknown")
    with pytest.raises(ValueError, match="effort"):
        store.create_card(board["id"], None, "d", effort="unknown")
    # a model change leaves the effort alone; clearing it hands the worker back the role's
    assert store.update_card(card["id"], model="openai/gpt-6-astra")["effort"] == "high"
    assert store.update_card(card["id"], effort=None)["effort"] is None


def test_paired_settings_validate_atomically_and_fallback_is_opt_in(store):
    assert store.get_settings()["worker_cross_lab_fallback"] == []
    store.set_setting("worker_model", "sonnet")
    settings = store.set_settings({"worker_lab": "openai", "worker_model": "gpt-5.6-sol"})
    assert settings["worker_lab"] == "openai"
    assert settings["worker_model"] == "gpt-5.6-sol"
    with pytest.raises(ValueError, match="unknown model"):
        store.set_settings({"max_parallel": 10, "worker_model": "unknown"})
    assert store.get_settings() == settings
    settings = store.set_setting("fold_model", "openai/gpt-6-astra")
    assert (settings["fold_lab"], settings["fold_model"]) == ("openai", "gpt-6-astra")
    settings = store.set_setting("worker_cross_lab_fallback", ["sonnet", "openai/gpt-6-astra"])
    assert settings["worker_cross_lab_fallback"] == [
        {"ref": "anthropic/sonnet", "effort": None},
        {"ref": "openai/gpt-6-astra", "effort": None},
    ]
    with pytest.raises(ValueError, match="unknown fallback"):
        store.set_setting("worker_cross_lab_fallback", ["openai/unknown"])
    with pytest.raises(ValueError, match="list"):
        store.set_setting("worker_cross_lab_fallback", {})


def test_fallback_entries_carry_their_own_effort(store):
    # a list stored before fallbacks had an effort reads as the role's effort
    store._conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)",
        ("reviewer_cross_lab_fallback", json.dumps(["openai/gpt-6-astra"])),
    )
    store._conn.commit()
    assert store.get_settings()["reviewer_cross_lab_fallback"] == [
        {"ref": "openai/gpt-6-astra", "effort": None}
    ]
    settings = store.set_setting(
        "reviewer_cross_lab_fallback",
        [{"ref": "openai/gpt-6-astra", "effort": "high"}, {"ref": "sonnet", "effort": None}],
    )
    assert settings["reviewer_cross_lab_fallback"] == [
        {"ref": "openai/gpt-6-astra", "effort": "high"},
        {"ref": "anthropic/sonnet", "effort": None},
    ]
    with pytest.raises(ValueError, match="effort"):
        store.set_setting(
            "reviewer_cross_lab_fallback", [{"ref": "openai/gpt-6-astra", "effort": "ultra"}]
        )


def test_exact_card_version_and_effort_survive_reopen(tmp_path):
    path = tmp_path / "exact.db"
    with Store(path) as store:
        board = store.create_board("exact")
        card = store.create_card(
            board["id"], None, "c", model="anthropic/claude-opus-5-5", effort="max"
        )
    with Store(path) as store:
        restored = store.get_card(card["id"])
        assert (restored["lab"], restored["model"], restored["effort"]) == (
            "anthropic",
            "claude-opus-5-5",
            "max",
        )


def test_card_effort_only_patch_validates_the_existing_model(store):
    board = store.create_board("exact")
    card = store.create_card(board["id"], None, "c", model="claude-haiku-4-5")
    with pytest.raises(ValueError, match="effort"):
        store.update_card(card["id"], effort="high")
    assert store.get_card(card["id"]) == card
    with pytest.raises(ValueError, match="effort"):
        store.create_card(board["id"], None, "d", model="claude-haiku-4-5", effort="low")


@pytest.mark.parametrize("role", ["worker", "reviewer", "orchestrator", "fold"])
def test_settings_effort_only_patch_validates_the_existing_model_atomically(store, role):
    settings = store.set_settings({f"{role}_model": "claude-haiku-4-5"})
    with pytest.raises(ValueError, match="effort"):
        store.set_settings({"max_parallel": 10, f"{role}_effort": "high"})
    assert store.get_settings() == settings
    changed = store.set_settings({f"{role}_model": "claude-opus-5-5", f"{role}_effort": "max"})
    assert changed[f"{role}_model"] == "claude-opus-5-5"
    assert changed[f"{role}_effort"] == "max"
    # switching to a model without that effort clears it instead of refusing
    cleared = store.set_setting(f"{role}_model", "claude-haiku-4-5")
    assert cleared[f"{role}_model"] == "claude-haiku-4-5"
    assert cleared[f"{role}_effort"] is None


def test_fallback_effort_uses_the_fallback_model_support(store):
    with pytest.raises(ValueError, match="effort"):
        store.set_setting(
            "worker_cross_lab_fallback", [{"ref": "anthropic/claude-haiku-4-5", "effort": "high"}]
        )
    settings = store.set_setting(
        "worker_cross_lab_fallback", [{"ref": "openai/gpt-6.1-sol", "effort": "xhigh"}]
    )
    assert settings["worker_cross_lab_fallback"] == [
        {"ref": "openai/gpt-6.1-sol", "effort": "xhigh"}
    ]


def test_export_import_and_backup_preserve_mixed_labs(store, tmp_path):
    board = store.create_board("mixed")
    store.create_card(board["id"], None, "legacy", model="sonnet")
    card = store.create_card(
        board["id"], None, "new", lab="openai", model="gpt-6-astra", complexity=3, effort="high"
    )
    store.set_settings({"fold_lab": "openai", "fold_model": "gpt-6-astra"})
    spend = store.add_board_spend(board["id"], "fold", 0.5, lab="openai", model="gpt-6-astra")
    store.delete_card(card["id"])
    backup = store.list_card_backups(board["id"])[0]
    bundle = tmp_path / "bundle.json"
    store.export(bundle)
    assert json.loads(bundle.read_text())["format_version"] == 3
    with Store(tmp_path / "copy.db") as restored:
        restored.import_bundle(bundle)
        assert restored.list_cards(board["id"]) == store.list_cards(board["id"])
        assert restored.get_settings() == store.get_settings()
        assert restored.list_board_spend(board["id"]) == [spend]
        assert restored.restore_card(backup["id"]) == card
