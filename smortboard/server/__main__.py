"""entrypoint: `python -m smortboard.server` — reads DB path, host and port from env for docker"""

import os

from smortboard.server.access import KEY_QUERY, load_or_create_api_key
from smortboard.server.app import build_server
from smortboard.store import Store

if __name__ == "__main__":
    db_path = os.environ.get("SMORTBOARD_DB", "smortboard.db")
    host = os.environ.get("SMORTBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("SMORTBOARD_PORT", "8000"))
    with Store(db_path) as store:
        api_key = load_or_create_api_key()
        server = build_server(store, port, host=host, api_key=api_key)
        print(f"smortboard serving on {host}:{port}, db={db_path}")
        print(f"open /ui/index.html?{KEY_QUERY}={api_key}")
        server.serve_forever()
