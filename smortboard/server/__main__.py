"""entrypoint: `python -m smortboard.server` — reads DB path and port from env for docker"""

import os

from smortboard.server.app import build_server
from smortboard.store import Store

if __name__ == "__main__":
    db_path = os.environ.get("SMORTBOARD_DB", "smortboard.db")
    port = int(os.environ.get("SMORTBOARD_PORT", "8000"))
    with Store(db_path) as store:
        server = build_server(store, port)
        print(f"smortboard serving on :{port}, db={db_path}")
        server.serve_forever()
