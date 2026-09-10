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

from smortboard.server.app import build_server
from smortboard.store import Store

_APP_NAME = "smortboard"
_DEFAULT_PORT = 8000


def _version() -> str:
    # installed package metadata is derived from pyproject.toml's [project].version
    return importlib.metadata.version(_APP_NAME)


def _default_db_path() -> Path:
    # user data dir, not cwd — `uvx smortboard` run from anywhere should not scatter .db files
    data_dir = Path(user_data_dir(_APP_NAME))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "smortboard.db"


def _resolve_db(cli_value: str | None) -> Path:
    if cli_value is not None:
        return Path(cli_value)
    env_value = os.environ.get("SMORTBOARD_DB")
    if env_value is not None:
        return Path(env_value)
    return _default_db_path()


def _resolve_port(cli_value: int | None) -> int:
    if cli_value is not None:
        return cli_value
    env_value = os.environ.get("SMORTBOARD_PORT")
    if env_value is not None:
        return int(env_value)
    return _DEFAULT_PORT


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
        "--db",
        type=str,
        default=None,
        help="sqlite db path (env: SMORTBOARD_DB, default: a user data dir)",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="do not open a browser tab on start"
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
    db_path = _resolve_db(args.db)
    port = _resolve_port(args.port)

    with Store(db_path) as store:
        server = build_server(store, port)
        actual_port = server.server_address[1]
        url = f"http://127.0.0.1:{actual_port}/ui/index.html"
        print(f"smortboard serving on {url} (db={db_path})")
        if not args.no_browser:
            webbrowser.open(url)
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()


if __name__ == "__main__":
    main()
