"""a portable bundle of everything: boards, repos, cards and their children, events, attachments

json on disk, blobs base64-encoded so the bundle is one plain-text file.
"""

import base64
import json
import sqlite3
from pathlib import Path
from typing import Any

from smortboard.store.errors import UnknownFieldError

_TABLES = (
    "boards",
    "repos",
    "cards",
    "card_tasks",
    "card_criteria",
    "card_leases",
    "card_deps",
    "card_backups",
    "comments",
    "events",
    "settings",
    "prompts",
    "orchestrator_messages",
    "orchestrator_plans",
)

_FORMAT_VERSION = 2


def export_bundle(conn: sqlite3.Connection, path: str | Path) -> None:
    bundle: dict[str, Any] = {"format_version": _FORMAT_VERSION}
    for table in _TABLES:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        bundle[table] = [dict(r) for r in rows]

    attachments = []
    for row in conn.execute("SELECT * FROM attachments").fetchall():
        record = dict(row)
        record["blob"] = base64.b64encode(record["blob"]).decode("ascii")
        attachments.append(record)
    bundle["attachments"] = attachments

    Path(path).write_text(json.dumps(bundle, indent=2), encoding="utf-8")


def import_bundle(conn: sqlite3.Connection, path: str | Path) -> None:
    """restores a bundle into the current (expected-empty) database, dependency order first"""
    bundle = json.loads(Path(path).read_text(encoding="utf-8"))

    for table in ("boards", "repos", "cards", "card_tasks", "card_criteria", "card_leases"):
        _insert_all(conn, table, bundle.get(table, []))

    # card_deps references two cards, so it must come after every card exists
    _insert_all(conn, "card_deps", bundle.get("card_deps", []))
    # card_backups has no foreign key (a backup can outlive the card and board it describes),
    # so it has no ordering requirement - grouped here with the rest of the cards' data
    _insert_all(conn, "card_backups", bundle.get("card_backups", []))
    _insert_all(conn, "comments", bundle.get("comments", []))
    _insert_all(conn, "events", bundle.get("events", []))
    _insert_all(conn, "settings", bundle.get("settings", []))
    # prompts have no foreign key; the other two reference boards, already inserted above
    _insert_all(conn, "prompts", bundle.get("prompts", []))
    _insert_all(conn, "orchestrator_messages", bundle.get("orchestrator_messages", []))
    _insert_all(conn, "orchestrator_plans", bundle.get("orchestrator_plans", []))

    for record in bundle.get("attachments", []):
        record = dict(record)
        record["blob"] = base64.b64decode(record["blob"])
        _insert_all(conn, "attachments", [record])

    conn.commit()


def _insert_all(conn: sqlite3.Connection, table: str, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    # bundle keys become column names in raw sql, so refuse anything not a real column of
    # this table rather than interpolating it - a crafted key could otherwise inject sql
    real_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    columns = list(records[0].keys())
    unknown = [c for c in columns if c not in real_columns]
    if unknown:
        raise UnknownFieldError(f"{table}: not a real column: {sorted(unknown)}")
    placeholders = ", ".join("?" for _ in columns)
    column_list = ", ".join(columns)
    conn.executemany(
        f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})",
        [tuple(record[c] for c in columns) for record in records],
    )
