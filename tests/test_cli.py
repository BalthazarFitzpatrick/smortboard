"""cli tests: default db path resolution, flag/env/default precedence, packaged ui assets

no browser is started in any of these — main() itself is left untested here since it blocks
on serve_forever(); parse_args and the resolver helpers cover the precedence contract instead
"""

import importlib.metadata
import importlib.resources
import json

import pytest

from smortboard.cli import _default_db_path, _resolve_db, _resolve_port, main, parse_args
from smortboard.store import Store


def test_default_db_path_is_under_user_data_dir_not_cwd(tmp_path, monkeypatch):
    # point platformdirs at a scratch home so the test never touches the real user data dir
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    db_path = _default_db_path()
    assert db_path.name == "smortboard.db"
    assert str(tmp_path) in str(db_path)
    assert db_path.parent != tmp_path  # nested under a data dir, not dumped at cwd itself


def test_db_flag_beats_env_beats_default(monkeypatch, tmp_path):
    monkeypatch.setattr("smortboard.cli.user_data_dir", lambda _: str(tmp_path / "data"))
    monkeypatch.setenv("SMORTBOARD_DB", str(tmp_path / "env.db"))
    assert _resolve_db(str(tmp_path / "flag.db")) == tmp_path / "flag.db"
    assert _resolve_db(None) == tmp_path / "env.db"

    monkeypatch.delenv("SMORTBOARD_DB", raising=False)
    assert _resolve_db(None) == _default_db_path()


def test_port_flag_beats_env_beats_default(monkeypatch):
    monkeypatch.setenv("SMORTBOARD_PORT", "9999")
    assert _resolve_port(1234) == 1234
    assert _resolve_port(None) == 9999

    monkeypatch.delenv("SMORTBOARD_PORT", raising=False)
    assert _resolve_port(None) == 8000


def test_no_browser_flag_parses():
    args = parse_args(["--no-browser", "--port", "8123", "--db", "x.db"])
    assert args.no_browser is True
    assert args.port == 8123
    assert args.db == "x.db"


def test_version_flag_prints_pyproject_version_and_exits_zero(capsys):
    # argparse's version action prints and raises SystemExit(0) — no server ever starts
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["--version"])
    assert excinfo.value.code == 0
    printed = capsys.readouterr().out.strip()
    assert printed == importlib.metadata.version("smortboard")


def test_ui_assets_reachable_from_installed_package():
    # importlib.resources, not a source-tree path walk — this must pass against an
    # installed wheel where smortboard/ lives in site-packages, not a checkout
    ui_dir = importlib.resources.files("smortboard") / "ui"
    assert (ui_dir / "index.html").is_file()
    assert (ui_dir / "board.js").is_file()
    assert (ui_dir / "layout.css").is_file()


def test_the_demo_serves_beside_the_real_board_not_on_top_of_it(monkeypatch):
    """OPERATOR, 2026-09-16: "demo should go to port 8001 to not collide". The real board owns
    8000; an explicit port or the env var still wins over both defaults.
    """
    monkeypatch.delenv("SMORTBOARD_PORT", raising=False)
    assert _resolve_port(None, demo=True) == 8001
    assert _resolve_port(None) == 8000
    assert _resolve_port(8123, demo=True) == 8123
    monkeypatch.setenv("SMORTBOARD_PORT", "9999")
    assert _resolve_port(None, demo=True) == 9999


# ---- `smortboard export` / `smortboard import` - the cli half of backup, no server involved -----


def test_export_then_import_round_trips_with_no_server_running(tmp_path, capsys):
    """the whole point of the cli commands: nothing here opens a socket or a browser"""
    db_path = tmp_path / "board.sqlite3"
    with Store(db_path) as store:
        board = store.create_board("Phase 1")
        repo = store.create_repo(board["id"], "smortboard", "/repo", "main")
        card = store.create_card(
            board["id"], repo["id"], "a card", tasks=["t1"], criteria=["c1"], leases=["a/*"]
        )
        store.add_comment(card["id"], "operator", "hi")
        store.add_attachment(card["id"], "a.txt", "text/plain", b"binary\x00data")

    bundle_path = tmp_path / "bundle.json"
    main(["export", str(bundle_path), "--db", str(db_path)])
    assert bundle_path.is_file()
    printed = capsys.readouterr().out
    assert str(db_path) in printed
    assert str(bundle_path) in printed

    fresh_db_path = tmp_path / "restored.sqlite3"
    main(["import", str(bundle_path), "--db", str(fresh_db_path)])

    with Store(fresh_db_path) as restored:
        boards = restored.list_boards()
        assert len(boards) == 1
        cards = restored.list_cards(boards[0]["id"])
        assert [c["title"] for c in cards] == ["a card"]
        assert cards[0]["tasks"][0]["text"] == "t1"
        assert cards[0]["criteria"][0]["text"] == "c1"
        assert cards[0]["leases"][0]["path_glob"] == "a/*"
        assert [c["body"] for c in restored.list_comments(cards[0]["id"])] == ["hi"]


def test_export_and_import_respect_the_smortboard_db_env_var(tmp_path, monkeypatch):
    db_path = tmp_path / "env.sqlite3"
    with Store(db_path) as store:
        store.create_board("from env")

    monkeypatch.setenv("SMORTBOARD_DB", str(db_path))
    bundle_path = tmp_path / "bundle.json"
    main(["export", str(bundle_path)])  # no --db - must fall back to the env var
    assert bundle_path.is_file()

    fresh_db_path = tmp_path / "fresh.sqlite3"
    monkeypatch.setenv("SMORTBOARD_DB", str(fresh_db_path))
    main(["import", str(bundle_path)])
    with Store(fresh_db_path) as restored:
        assert [b["name"] for b in restored.list_boards()] == ["from env"]


def test_export_refuses_a_database_that_does_not_exist_yet(tmp_path):
    missing_db = tmp_path / "nope.sqlite3"
    with pytest.raises(SystemExit, match="nothing to export"):
        main(["export", str(tmp_path / "bundle.json"), "--db", str(missing_db)])


def test_import_refuses_a_database_that_already_has_a_board(tmp_path):
    db_path = tmp_path / "board.sqlite3"
    with Store(db_path) as store:
        store.create_board("already here")
        bundle_path = tmp_path / "bundle.json"
        store.export(bundle_path)

    with pytest.raises(SystemExit, match="already has a board"):
        main(["import", str(bundle_path), "--db", str(db_path)])


def test_export_with_demo_is_refused_not_silently_ignored(tmp_path):
    with pytest.raises(SystemExit, match="--demo has no board to export"):
        main(["export", str(tmp_path / "bundle.json"), "--demo"])


def test_import_with_demo_is_refused_not_silently_ignored(tmp_path):
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps({"format_version": 2}))
    with pytest.raises(SystemExit, match="--demo has no board to import into"):
        main(["import", str(bundle_path), "--demo"])
