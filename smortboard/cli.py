"""console entry point: `smortboard` (installed) or `uvx smortboard` (no install)

precedence for every setting is flag > env > default, in that order — flags win because
they are the most explicit thing a person can do at the terminal
"""

import argparse
import contextlib
import importlib.metadata
import os
import webbrowser
from pathlib import Path

from platformdirs import user_data_dir

from smortboard.demo import make_demo_db
from smortboard.server.app import build_server
from smortboard.store import Store

_APP_NAME = "smortboard"
_DEFAULT_PORT = 8000
# the demo's own default, so `smortboard --demo` never collides with the board already serving on
# 8000 - an explicit --port or SMORTBOARD_PORT still wins
_DEMO_PORT = 8001
# loopback only: the api has no auth and can start runs that spend the card token
_DEFAULT_HOST = "127.0.0.1"


def _version() -> str:
    # installed package metadata is derived from pyproject.toml's [project].version
    return importlib.metadata.version(_APP_NAME)


def _default_db_path() -> Path:
    # user data dir, not cwd — `uvx smortboard` run from anywhere should not scatter .db files
    data_dir = Path(user_data_dir(_APP_NAME))
    data_dir.mkdir(parents=True, exist_ok=True)
    # 0700: the board db holds the card token's blast radius (repo paths, prompts, event logs) -
    # chmod rather than rely on the mkdir mode, since exist_ok skips mode on an existing dir
    os.chmod(data_dir, 0o700)
    return data_dir / "smortboard.db"


def _resolve_db(cli_value: str | None) -> Path:
    if cli_value is not None:
        return Path(cli_value)
    env_value = os.environ.get("SMORTBOARD_DB")
    if env_value is not None:
        return Path(env_value)
    return _default_db_path()


def _resolve_host(cli_value: str | None) -> str:
    if cli_value is not None:
        return cli_value
    return os.environ.get("SMORTBOARD_HOST") or _DEFAULT_HOST


def _resolve_port(cli_value: int | None, demo: bool = False) -> int:
    if cli_value is not None:
        return cli_value
    env_value = os.environ.get("SMORTBOARD_PORT")
    if env_value is not None:
        return int(env_value)
    # a demo beside the operator's own board, not on top of it: the real one owns the default port,
    # so --demo steps one aside rather than failing to bind or, worse, looking like the real board
    return _DEMO_PORT if demo else _DEFAULT_PORT


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="smortboard",
        description="a browser-based board for handing work to coding agents",
        epilog="precedence: --flag wins over SMORTBOARD_* env var, which wins over the default",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="port to serve on (env: SMORTBOARD_PORT, default: 8000)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="interface to bind (env: SMORTBOARD_HOST, default: 127.0.0.1 - this machine only)",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="sqlite db path (env: SMORTBOARD_DB, default: a user data dir)",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="do not open a browser tab on start"
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help=f"serve a throwaway board of invented projects on port {_DEMO_PORT} "
        "(ignores --db and SMORTBOARD_DB)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=_version(),
        help="print the installed version and exit",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """cli entry point: parse args, open the store, and serve the board until interrupted."""
    args = parse_args(argv)
    # --demo deliberately bypasses _resolve_db entirely: a demo must never be able to reach the
    # operator's own board, not through --db, not through SMORTBOARD_DB, not through the data dir
    db_path = make_demo_db() if args.demo else _resolve_db(args.db)
    port = _resolve_port(args.port, demo=args.demo)

    with Store(db_path) as store:
        server = build_server(store, port, host=_resolve_host(args.host))
        actual_port = server.server_address[1]
        url = f"http://127.0.0.1:{actual_port}/ui/index.html"
        label = "demo db, invented content" if args.demo else f"db={db_path}"
        print(f"smortboard serving on {url} ({label})")
        if server.recovered:
            print(
                f"{len(server.recovered)} card(s) were left mid-run by the last board - "
                "blocked as CRASH, waiting in the inbox"
            )
        if not args.no_browser:
            webbrowser.open(url)
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()


if __name__ == "__main__":
    main()
