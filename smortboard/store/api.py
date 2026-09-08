"""the store's public surface — callers get dicts, never sql, a cursor or a connection"""

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from smortboard.store.errors import BlockedReasonInvalidError, NotFoundError, UnknownFieldError
from smortboard.store.schema import BLOCKED_REASON_CODES, STATUSES, migrate

_CARD_WRITABLE_FIELDS = {
    "title",
    "workstream",
    "status",
    "blocked_reason_code",
    "description",
    "position",
    "review_flag",
    "repo_id",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


class Store:
    """owns one sqlite file: its schema, migrations, and every read/write of board state"""

    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        migrate(self._conn)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- boards ------------------------------------------------------------

    def create_board(self, name: str, position: int = 0) -> dict[str, Any]:
        board_id = _new_id()
        created_at = _now()
        self._conn.execute(
            "INSERT INTO boards (id, name, position, created_at) VALUES (?, ?, ?, ?)",
            (board_id, name, position, created_at),
        )
        self._conn.commit()
        return self.get_board(board_id)

    def get_board(self, board_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM boards WHERE id = ?", (board_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"no board {board_id}")
        return _row_to_dict(row)

    def list_boards(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM boards ORDER BY position").fetchall()
        return [_row_to_dict(r) for r in rows]

    # -- repos ---------------------------------------------------------------

    def create_repo(self, board_id: str, name: str, path: str, default_branch: str) -> dict:
        repo_id = _new_id()
        self._conn.execute(
            "INSERT INTO repos (id, board_id, name, path, default_branch) VALUES (?, ?, ?, ?, ?)",
            (repo_id, board_id, name, path, default_branch),
        )
        self._conn.commit()
        return self.get_repo(repo_id)

    def get_repo(self, repo_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"no repo {repo_id}")
        return _row_to_dict(row)

    def list_repos(self, board_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM repos WHERE board_id = ? ORDER BY name", (board_id,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # -- cards -----------------------------------------------------------------

    def _check_blocked_invariant(self, status: str, blocked_reason_code: str | None) -> None:
        if status not in STATUSES:
            raise BlockedReasonInvalidError(f"unknown status {status!r}")
        if blocked_reason_code is not None and blocked_reason_code not in BLOCKED_REASON_CODES:
            raise BlockedReasonInvalidError(f"unknown blocked_reason_code {blocked_reason_code!r}")
        # a reason code rides alongside ANY status - a queued card can block on
        # DEPENDENCY_REJECTED just as a working one can block on USAGE_LIMIT

    def create_card(
        self,
        board_id: str,
        repo_id: str | None,
        title: str,
        workstream: str | None = None,
        status: str = "todo",
        blocked_reason_code: str | None = None,
        description: str | None = None,
        position: int = 0,
        review_flag: bool = False,
        tasks: list[str] | None = None,
        criteria: list[str] | None = None,
        leases: list[str] | None = None,
    ) -> dict[str, Any]:
        self._check_blocked_invariant(status, blocked_reason_code)
        card_id = _new_id()
        now = _now()
        self._conn.execute(
            """
            INSERT INTO cards (id, board_id, repo_id, title, workstream, status,
                blocked_reason_code, description, position, review_flag, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                card_id,
                board_id,
                repo_id,
                title,
                workstream,
                status,
                blocked_reason_code,
                description,
                position,
                int(review_flag),
                now,
                now,
            ),
        )
        for i, text in enumerate(tasks or []):
            self._conn.execute(
                "INSERT INTO card_tasks (id, card_id, position, text, done) VALUES (?, ?, ?, ?, 0)",
                (_new_id(), card_id, i, text),
            )
        for i, text in enumerate(criteria or []):
            self._conn.execute(
                "INSERT INTO card_criteria (id, card_id, position, text) VALUES (?, ?, ?, ?)",
                (_new_id(), card_id, i, text),
            )
        for glob in leases or []:
            self._conn.execute(
                "INSERT INTO card_leases (id, card_id, path_glob) VALUES (?, ?, ?)",
                (_new_id(), card_id, glob),
            )
        self._conn.commit()
        return self.get_card(card_id)

    def _card_row(self, card_id: str) -> sqlite3.Row:
        row = self._conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"no card {card_id}")
        return row

    def get_card(self, card_id: str) -> dict[str, Any]:
        card = _row_to_dict(self._card_row(card_id))
        card["tasks"] = [
            _row_to_dict(r)
            for r in self._conn.execute(
                "SELECT * FROM card_tasks WHERE card_id = ? ORDER BY position", (card_id,)
            ).fetchall()
        ]
        card["criteria"] = [
            _row_to_dict(r)
            for r in self._conn.execute(
                "SELECT * FROM card_criteria WHERE card_id = ? ORDER BY position", (card_id,)
            ).fetchall()
        ]
        card["leases"] = [
            _row_to_dict(r)
            for r in self._conn.execute(
                "SELECT * FROM card_leases WHERE card_id = ?", (card_id,)
            ).fetchall()
        ]
        card["depends_on"] = self.get_dependencies(card_id)
        card["depended_on_by"] = self.get_dependents(card_id)
        card["comments"] = self.list_comments(card_id)
        return card

    def list_cards(self, board_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id FROM cards WHERE board_id = ? ORDER BY position", (board_id,)
        ).fetchall()
        return [self.get_card(r["id"]) for r in rows]

    def update_card(self, card_id: str, **fields: Any) -> dict[str, Any]:
        unknown = set(fields) - _CARD_WRITABLE_FIELDS
        if unknown:
            raise UnknownFieldError(f"not writable: {sorted(unknown)}")
        current = self._card_row(card_id)
        next_status = fields.get("status", current["status"])
        next_reason = fields.get("blocked_reason_code", current["blocked_reason_code"])
        self._check_blocked_invariant(next_status, next_reason)

        merged = {**fields, "status": next_status, "blocked_reason_code": next_reason}
        assignments = ", ".join(f"{key} = ?" for key in merged)
        values = [*merged.values(), _now(), card_id]
        self._conn.execute(
            f"UPDATE cards SET {assignments}, updated_at = ? WHERE id = ?",
            values,
        )
        self._conn.commit()
        return self.get_card(card_id)

    def delete_card(self, card_id: str) -> None:
        self._card_row(card_id)
        self._conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        self._conn.commit()

    # -- dependencies (card_deps is the single source, queried both ways) ------

    def add_dependency(self, card_id: str, depends_on_card_id: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO card_deps (card_id, depends_on_card_id) VALUES (?, ?)",
            (card_id, depends_on_card_id),
        )
        self._conn.commit()

    def remove_dependency(self, card_id: str, depends_on_card_id: str) -> None:
        self._conn.execute(
            "DELETE FROM card_deps WHERE card_id = ? AND depends_on_card_id = ?",
            (card_id, depends_on_card_id),
        )
        self._conn.commit()

    def get_dependencies(self, card_id: str) -> list[str]:
        """cards that card_id depends on"""
        rows = self._conn.execute(
            "SELECT depends_on_card_id FROM card_deps WHERE card_id = ?", (card_id,)
        ).fetchall()
        return [r["depends_on_card_id"] for r in rows]

    def get_dependents(self, card_id: str) -> list[str]:
        """cards that depend on card_id"""
        rows = self._conn.execute(
            "SELECT card_id FROM card_deps WHERE depends_on_card_id = ?", (card_id,)
        ).fetchall()
        return [r["card_id"] for r in rows]

    # -- comments -----------------------------------------------------------

    def add_comment(self, card_id: str, author: str, body: str) -> dict[str, Any]:
        comment_id = _new_id()
        created_at = _now()
        self._conn.execute(
            "INSERT INTO comments (id, card_id, author, body, created_at) VALUES (?, ?, ?, ?, ?)",
            (comment_id, card_id, author, body, created_at),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
        return _row_to_dict(row)

    def list_comments(self, card_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM comments WHERE card_id = ? ORDER BY created_at", (card_id,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # -- attachments ----------------------------------------------------------

    def add_attachment(
        self, card_id: str, filename: str, media_type: str, data: bytes
    ) -> dict[str, Any]:
        attachment_id = _new_id()
        created_at = _now()
        self._conn.execute(
            """
            INSERT INTO attachments (id, card_id, filename, media_type, size_bytes, blob, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (attachment_id, card_id, filename, media_type, len(data), data, created_at),
        )
        self._conn.commit()
        return self.get_attachment_meta(attachment_id)

    def get_attachment_meta(self, attachment_id: str) -> dict[str, Any]:
        """attachment fields without the blob"""
        row = self._conn.execute(
            "SELECT id, card_id, filename, media_type, size_bytes, created_at "
            "FROM attachments WHERE id = ?",
            (attachment_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"no attachment {attachment_id}")
        return _row_to_dict(row)

    def get_attachment_blob(self, attachment_id: str) -> bytes:
        row = self._conn.execute(
            "SELECT blob FROM attachments WHERE id = ?", (attachment_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"no attachment {attachment_id}")
        return row["blob"]

    def list_attachments(self, card_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, card_id, filename, media_type, size_bytes, created_at "
            "FROM attachments WHERE card_id = ? ORDER BY created_at",
            (card_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # -- events: append-only, never updated -----------------------------------

    def append_event(self, card_id: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = _new_id()
        created_at = _now()
        next_seq_row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM events WHERE card_id = ?",
            (card_id,),
        ).fetchone()
        seq = next_seq_row["next_seq"]
        self._conn.execute(
            """
            INSERT INTO events (id, card_id, seq, kind, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_id, card_id, seq, kind, json.dumps(payload), created_at),
        )
        self._conn.commit()
        return self._event_dict(event_id)

    def _event_dict(self, event_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        event = _row_to_dict(row)
        event["payload"] = json.loads(event.pop("payload_json"))
        return event

    def list_events(self, card_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE card_id = ? ORDER BY seq", (card_id,)
        ).fetchall()
        events = []
        for row in rows:
            event = _row_to_dict(row)
            event["payload"] = json.loads(event.pop("payload_json"))
            events.append(event)
        return events

    # -- export / import --------------------------------------------------

    def export(self, path: str | Path) -> None:
        from smortboard.store.export import export_bundle

        export_bundle(self._conn, path)

    def import_bundle(self, path: str | Path) -> None:
        from smortboard.store.export import import_bundle as _import

        _import(self._conn, path)
