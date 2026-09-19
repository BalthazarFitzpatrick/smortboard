"""proves an export bundle is safe to attach to a public bug report: it holds board content
(boards, cards, tasks, comments, settings, ...) and nothing from the credential files under
~/.config/smortboard - the api key and the claude token(s) never pass through sqlite at all, so
export_bundle (which only ever reads the tables in store/export.py's _TABLES) cannot leak them.
"""

import json

from smortboard.exec.backends import CARD_TOKEN_PATH_ENV
from smortboard.profiles import write_token_file
from smortboard.server.access import API_KEY_PATH_ENV, load_or_create_api_key
from smortboard.store import Store

_API_KEY_MARKER = "sk-api-key-should-never-leave-config-nnnnnnnnnnnnnnnnnnnnn"
_CLAUDE_TOKEN_MARKER = "sk-claude-token-should-never-leave-config-mmmmmmmmmmmmmmmmm"


def test_export_bundle_has_no_credential_or_token_material(tmp_path, monkeypatch):
    # both secrets live under a config dir the bundle must never read from
    config_dir = tmp_path / "config"
    api_key_path = config_dir / "api_key"
    token_path = config_dir / "card_token"
    monkeypatch.setenv(API_KEY_PATH_ENV, str(api_key_path))
    monkeypatch.setenv(CARD_TOKEN_PATH_ENV, str(token_path))
    write_token_file(api_key_path, _API_KEY_MARKER)
    write_token_file(token_path, _CLAUDE_TOKEN_MARKER)
    # sanity: the fixtures actually landed where the bundle must not look
    assert load_or_create_api_key() == _API_KEY_MARKER

    db_path = tmp_path / "board.sqlite3"
    with Store(db_path) as store:
        board = store.create_board("Phase 1")
        repo = store.create_repo(board["id"], "smortboard", "/repo", "main")
        card = store.create_card(board["id"], repo["id"], "a card", tasks=["t1"])
        store.add_comment(card["id"], "operator", "hi")
        store.set_setting("auto_switch_profiles", "on")

        bundle_path = tmp_path / "bundle.json"
        store.export(bundle_path)

    raw = bundle_path.read_text(encoding="utf-8")
    assert _API_KEY_MARKER not in raw
    assert _CLAUDE_TOKEN_MARKER not in raw
    assert str(config_dir) not in raw
    assert ".config/smortboard" not in raw

    # not just string-absence - the bundle's shape has nowhere a secret would even fit: no
    # config-path tables, and no settings key that looks like it holds credential material
    bundle = json.loads(raw)
    settings_keys = {row["key"] for row in bundle.get("settings", [])}
    assert not any(
        word in key.lower()
        for key in settings_keys
        for word in ("token", "secret", "key", "password")
    )
