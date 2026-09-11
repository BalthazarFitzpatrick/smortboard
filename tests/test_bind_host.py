"""the api has no auth and can start runs that spend the card token, so it listens on this
machine only unless told otherwise"""

from smortboard.cli import _resolve_host, parse_args
from smortboard.server.app import build_server
from smortboard.store.api import Store


def test_build_server_binds_loopback_by_default(tmp_path):
    with Store(tmp_path / "b.db") as store:
        server = build_server(store, 0)
        try:
            assert server.server_address[0] == "127.0.0.1"
        finally:
            server.server_close()


def test_host_precedence_is_flag_then_env_then_loopback(monkeypatch):
    monkeypatch.delenv("SMORTBOARD_HOST", raising=False)
    assert _resolve_host(None) == "127.0.0.1"
    monkeypatch.setenv("SMORTBOARD_HOST", "0.0.0.0")
    assert _resolve_host(None) == "0.0.0.0"
    assert _resolve_host(parse_args(["--host", "192.168.1.5"]).host) == "192.168.1.5"
