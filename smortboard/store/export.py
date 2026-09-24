"""a portable bundle of everything: boards, repos, cards and their children, events, attachments

json on disk, blobs base64-encoded so the bundle is one plain-text file.
"""

import base64
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from smortboard.store.errors import BundleError, UnknownFieldError
from smortboard.store.schema import USAGE_LIMIT_FOLD_SQL

_TABLES = (
    "boards",
    "repos",
    "repo_remembered_leases",
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
    "board_spend",
)

_FORMAT_VERSION = 3


# what a bundle's boards bring when they are added beside boards already here, in foreign-key order:
# (table, whether its rows carry their own id, {column: the table that column points into}).
# settings and prompts are this database's own global config, and card_backups are the source's
# trash - restore_card would bring a card back under its old ids - so none of the three come along
_AS_NEW: tuple[tuple[str, bool, dict[str, str]], ...] = (
    ("boards", True, {}),
    ("repos", True, {"board_id": "boards"}),
    ("repo_remembered_leases", True, {"repo_id": "repos"}),
    ("cards", True, {"board_id": "boards", "repo_id": "repos"}),
    ("card_tasks", True, {"card_id": "cards"}),
    ("card_criteria", True, {"card_id": "cards"}),
    ("card_leases", True, {"card_id": "cards"}),
    ("card_deps", False, {"card_id": "cards", "depends_on_card_id": "cards"}),
    ("comments", True, {"card_id": "cards"}),
    ("events", True, {"card_id": "cards"}),
    ("attachments", True, {"card_id": "cards"}),
    ("orchestrator_messages", True, {"board_id": "boards"}),
    ("orchestrator_plans", False, {"board_id": "boards"}),
    ("board_spend", True, {"board_id": "boards"}),
)
# json columns that name rows by id - a stacked card's parent in its events, the cards mission
# control made. left alone, a copy's stack would resolve to the original's cards, or to nothing
_JSON_WITH_IDS = {"events": "payload_json", "orchestrator_messages": "cards_json"}


def bundle_text(conn: sqlite3.Connection) -> str:
    """the whole database as one bundle document - what a file export writes and the board serves"""
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
    return json.dumps(bundle, indent=2)


def export_bundle(conn: sqlite3.Connection, path: str | Path) -> None:
    Path(path).write_text(bundle_text(conn), encoding="utf-8")


def import_bundle(conn: sqlite3.Connection, path: str | Path) -> None:
    """restores a bundle into the current (expected-empty) database, dependency order first"""
    bundle = json.loads(Path(path).read_text(encoding="utf-8"))

    # a remembered lease path names its repo, so it lands after repos and before anything else
    for table in (
        "boards",
        "repos",
        "repo_remembered_leases",
        "cards",
        "card_tasks",
        "card_criteria",
        "card_leases",
    ):
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
    _insert_all(conn, "board_spend", bundle.get("board_spend", []))

    for record in bundle.get("attachments", []):
        record = dict(record)
        record["blob"] = base64.b64decode(record["blob"])
        _insert_all(conn, "attachments", [record])

    conn.commit()
    # an older bundle carries settings rows from before the usage-limit route became one setting
    conn.executescript(USAGE_LIMIT_FOLD_SQL)


def import_as_new(conn: sqlite3.Connection, bundle: object) -> list[str]:
    """adds a bundle's boards beside the ones already here and returns their new ids.

    Every row gets a fresh id and every reference is rewritten to match, so no existing row is
    changed or even pointed at - a row naming an id the bundle itself does not hold is refused.
    One transaction: a refusal part way through leaves the database exactly as it was.
    """
    if not isinstance(bundle, dict) or not isinstance(bundle.get("boards"), list):
        raise BundleError("not a smortboard bundle - it has no list of boards")
    if not bundle["boards"]:
        raise BundleError("the bundle holds no boards")
    fresh: dict[str, dict[str, str]] = {}
    try:
        with conn:
            for table, own_id, refs in _AS_NEW:
                rows = [
                    _fresh_row(table, record, own_id, refs, fresh)
                    for record in _bundle_rows(bundle, table)
                ]
                if table in _JSON_WITH_IDS:
                    _swap_json_ids(rows, _JSON_WITH_IDS[table], fresh)
                _insert_all(conn, table, rows)
    except sqlite3.IntegrityError as exc:
        raise BundleError(f"the bundle does not fit this database: {exc}") from exc
    return list(fresh["boards"].values())


def _bundle_rows(bundle: dict[str, Any], table: str) -> list[dict[str, Any]]:
    records = bundle.get(table, [])
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise BundleError(f"{table}: expected a list of rows")
    return records


def _fresh_row(
    table: str,
    record: dict[str, Any],
    own_id: bool,
    refs: dict[str, str],
    fresh: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """one bundle row under a new id, its references rewritten to the new ids already handed out"""
    row = dict(record)
    for column, target in refs.items():
        old = row.get(column)
        if old is None:
            continue  # a card with no repo; a column that must not be null refuses it on insert
        if not isinstance(old, str) or old not in fresh.get(target, {}):
            raise BundleError(
                f"{table}.{column} names {old!r}, which is not in the bundle's {target}"
            )
        row[column] = fresh[target][old]
    if own_id:
        issued = fresh.setdefault(table, {})
        old = row.get("id")
        if not isinstance(old, str) or old in issued:
            raise BundleError(f"{table}: a row with a missing or repeated id {old!r}")
        row["id"] = issued[old] = str(uuid.uuid4())
    if table == "attachments":
        row["blob"] = base64.b64decode(row.get("blob") or "")
    return row


def _swap_json_ids(
    rows: list[dict[str, Any]], column: str, fresh: dict[str, dict[str, str]]
) -> None:
    """rewrites every json string that is exactly an old board, repo or card id to its new id -
    a uuid never equals other text by chance, and a pr url or branch name is left as it was"""
    swap = {
        old: new
        for table in ("boards", "repos", "cards")
        for old, new in fresh.get(table, {}).items()
    }
    for row in rows:
        if isinstance(row.get(column), str):
            row[column] = json.dumps(_swapped(json.loads(row[column]), swap))


def _swapped(value: Any, swap: dict[str, str]) -> Any:
    if isinstance(value, str):
        return swap.get(value, value)
    if isinstance(value, list):
        return [_swapped(item, swap) for item in value]
    if isinstance(value, dict):
        return {key: _swapped(item, swap) for key, item in value.items()}
    return value


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
