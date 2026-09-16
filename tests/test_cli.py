"""cli tests: default db path resolution, flag/env/default precedence, packaged ui assets

no browser is started in any of these — main() itself is left untested here since it blocks
on serve_forever(); parse_args and the resolver helpers cover the precedence contract instead
"""

import importlib.metadata
import importlib.resources

import pytest

from smortboard.cli import _default_db_path, _resolve_db, _resolve_port, parse_args


def test_default_db_path_is_under_user_data_dir_not_cwd(tmp_path, monkeypatch):
    # point platformdirs at a scratch home so the test never touches the real user data dir
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    db_path = _default_db_path()
    assert db_path.name == "smortboard.db"
    assert str(tmp_path) in str(db_path)
    assert db_path.parent != tmp_path  # nested under a data dir, not dumped at cwd itself


def test_db_flag_beats_env_beats_default(monkeypatch, tmp_path):
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
