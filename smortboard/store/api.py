"""the store's public surface — callers get dicts, never sql, a cursor or a connection"""

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from smortboard.store.errors import BlockedReasonInvalidError, NotFoundError, UnknownFieldError
from smortboard.store.schema import (
    BACKUP_RETENTION_DAYS,
    BLOCKED_REASON_CODES,
    DEFAULT_FINDINGS_ROUTE,
    FINDINGS_ROUTES,
    STATUSES,
    migrate,
)

CARD_WRITABLE_FIELDS = {
    "title",
    "workstream",
    "status",
    "blocked_reason_code",
    "description",
    "position",
    "review_flag",
    "repo_id",
    "findings_route",
    "model",
}

# board-wide values, one settings row per key. unset means no row.
# the three models are stored only when fabian set them - callers apply the defaults (opus for the
# orchestrator, sonnet for workers and the reviewer), so a changed default reaches unset boards.
# max_parallel is the scheduler's cap on cards run-all starts at once - unset means 2, see
# smortboard.scheduler.DEFAULT_MAX_PARALLEL
# resume_briefing gates lifecycle.py's resume briefing - "off" disables it, unset means on
# gate_timeout_seconds caps the test gate - unset means review.gates.GATE_TIMEOUT_SECONDS (600)
# auto_switch_profiles gates BoardScheduler's USAGE_LIMIT rotation - "off" parks the board until
# the reset instead (the pre-profiles behaviour), unset means on
_SETTING_KEYS = (
    "findings_route",
    "orchestrator_model",
    "worker_model",
    "reviewer_model",
    "max_parallel",
    "resume_briefing",
    "gate_timeout_seconds",
    "auto_switch_profiles",
)


def _check_findings_route(value: str | None) -> None:
    if value is not None and value not in FINDINGS_ROUTES:
        raise ValueError(f"findings_route must be one of {FINDINGS_ROUTES} or null, not {value!r}")


def _check_lease_glob(value: Any) -> str:
    """one lease glob, relative to the repo root - the guard matches paths made relative to it,
    so an absolute or climbing glob could never match anything it was meant to allow"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"a lease glob must be a non-empty string, not {value!r}")
    glob = value.strip()
    if glob.startswith("/") or ".." in glob.split("/"):
        raise ValueError(f"a lease glob is relative to the repo root, not {glob!r}")
    return glob


# card_criteria has deliberately no entry here and no update_criteria method anywhere in this
# file. acceptance criteria are the contract a card is judged against; a card agent that could
# edit its own criteria could move its own goalposts. add_task/remove_task/set_task_done exist
# for tasks (progress checkboxes) but there is no equivalent for criteria, on purpose.


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


class Store:
    """owns one sqlite file: its schema, migrations, and every read/write of board state"""

    def __init__(self, path: str | Path) -> None:
        # kept because a card run happens on its own thread with its own connection, and it needs
        # to know which file to open - sqlite refuses a connection shared across threads
        self.path = Path(path)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        migrate(self._conn)
        self._purge_expired_backups()

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

    def create_repo(
        self,
        board_id: str,
        name: str,
        path: str,
        default_branch: str,
        test_command: str | None = None,
        image: str | None = None,
        lint_command: str | None = None,
    ) -> dict:
        # no validation here on purpose - the http layer (app.py's repos POST route) validates a
        # person's input with validate_repo before this is ever called; the store itself stays the
        # thing every other test builds a repo row against without needing a real git checkout
        self.get_board(board_id)  # raises NotFoundError on a bad board id
        repo_id = _new_id()
        self._conn.execute(
            """
            INSERT INTO repos
                (id, board_id, name, path, default_branch, test_command, image, lint_command)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (repo_id, board_id, name, path, default_branch, test_command, image, lint_command),
        )
        self._conn.commit()
        return self.get_repo(repo_id)

    def set_repo_image(self, repo_id: str, image: str | None) -> dict[str, Any]:
        """sets (or clears, with None) the container image cards on this repo run in.

        one image per repo with the toolchain already installed, so a fresh container per card does
        not pay for `uv sync` or `npm install` on every run. cleared, cards fall back to the
        default image.
        """
        self.get_repo(repo_id)  # raises NotFoundError on a bad id
        self._conn.execute("UPDATE repos SET image = ? WHERE id = ?", (image, repo_id))
        self._conn.commit()
        return self.get_repo(repo_id)

    def set_repo_default_branch(self, repo_id: str, default_branch: str) -> dict[str, Any]:
        """moves the branch cards on this repo base on and open their PRs into.

        unvalidated here like create_repo - the http layer checks the branch exists first. the case
        it exists for: a base branch that merged into main keeps collecting card PRs that never land
        """
        self.get_repo(repo_id)  # raises NotFoundError on a bad id
        self._conn.execute(
            "UPDATE repos SET default_branch = ? WHERE id = ?", (default_branch, repo_id)
        )
        self._conn.commit()
        return self.get_repo(repo_id)

    def set_repo_test_command(self, repo_id: str, test_command: str | None) -> dict[str, Any]:
        """sets (or clears, with None) the shell invocation the runner may add to a card's Bash
        allowlist for this repo - see allowed_tools_for_repo in smortboard.exec.runner"""
        self.get_repo(repo_id)  # raises NotFoundError on a bad id
        self._conn.execute(
            "UPDATE repos SET test_command = ? WHERE id = ?", (test_command, repo_id)
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

    def delete_board(self, board_id: str) -> None:
        """removes a board, its repos, and every card on it.

        there was no way to delete a board at all, so a board created by mistake stayed on the bar
        for good. cards go through delete_card so their children go with them.
        """
        self.get_board(board_id)  # raises NotFoundError, so deleting twice is honest
        for card in self.list_cards(board_id):
            self.delete_card(card["id"])
        self._conn.execute("DELETE FROM repos WHERE board_id = ?", (board_id,))
        self._conn.execute("DELETE FROM boards WHERE id = ?", (board_id,))
        self._conn.commit()

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
        model: str | None = None,
        ledger_task: str | None = None,
    ) -> dict[str, Any]:
        self._check_blocked_invariant(status, blocked_reason_code)
        card_id = _new_id()
        now = _now()
        self._conn.execute(
            """
            INSERT INTO cards (id, board_id, repo_id, title, workstream, status,
                blocked_reason_code, description, position, review_flag, model, ledger_task,
                created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                model,
                ledger_task,
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

    def ledger_links(self, repo_id: str) -> dict[str, str]:
        """which of this repo's ledger tasks already have a card: task id -> card id"""
        rows = self._conn.execute(
            "SELECT ledger_task, id FROM cards WHERE repo_id = ? AND ledger_task IS NOT NULL",
            (repo_id,),
        ).fetchall()
        return {row["ledger_task"]: row["id"] for row in rows}

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
        # the panel lists these; without the key its attachments section always said "none", even
        # over a rejected card's attempt diff
        card["attachments"] = self.list_attachments(card_id)
        card["comments"] = self.list_comments(card_id)
        return card

    def list_cards(self, board_id: str) -> list[dict[str, Any]]:
        """every card on the board, each exactly as get_card shapes it - but one query per child
        table for the whole board instead of eight per card, since the inbox polls this"""
        cards = [
            _row_to_dict(r)
            for r in self._conn.execute(
                "SELECT * FROM cards WHERE board_id = ? ORDER BY position", (board_id,)
            ).fetchall()
        ]
        on_board = "SELECT id FROM cards WHERE board_id = ?"

        def grouped(sql: str, key: str = "card_id", value: str | None = None) -> dict[str, list]:
            by_card: dict[str, list] = {card["id"]: [] for card in cards}
            for row in self._conn.execute(sql, (board_id,)).fetchall():
                by_card[row[key]].append(row[value] if value else _row_to_dict(row))
            return by_card

        # the same ORDER BY as each get_card query, so every list comes back in the same order
        tasks = grouped(
            f"SELECT * FROM card_tasks WHERE card_id IN ({on_board}) ORDER BY card_id, position"
        )
        criteria = grouped(
            f"SELECT * FROM card_criteria WHERE card_id IN ({on_board}) ORDER BY card_id, position"
        )
        leases = grouped(
            f"SELECT * FROM card_leases WHERE card_id IN ({on_board}) ORDER BY card_id, rowid"
        )
        depends_on = grouped(
            f"SELECT card_id, depends_on_card_id FROM card_deps WHERE card_id IN ({on_board}) "
            "ORDER BY card_id, depends_on_card_id",
            value="depends_on_card_id",
        )
        depended_on_by = grouped(
            f"SELECT card_id, depends_on_card_id FROM card_deps "
            f"WHERE depends_on_card_id IN ({on_board}) ORDER BY rowid",
            key="depends_on_card_id",
            value="card_id",
        )
        attachments = grouped(
            "SELECT id, card_id, filename, media_type, size_bytes, created_at FROM attachments "
            f"WHERE card_id IN ({on_board}) ORDER BY created_at"
        )
        comments = grouped(
            f"SELECT * FROM comments WHERE card_id IN ({on_board}) ORDER BY created_at"
        )
        for card in cards:
            card_id = card["id"]
            card["tasks"] = tasks[card_id]
            card["criteria"] = criteria[card_id]
            card["leases"] = leases[card_id]
            card["depends_on"] = depends_on[card_id]
            card["depended_on_by"] = depended_on_by[card_id]
            card["attachments"] = attachments[card_id]
            card["comments"] = comments[card_id]
        return cards

    def update_card(self, card_id: str, **fields: Any) -> dict[str, Any]:
        unknown = set(fields) - CARD_WRITABLE_FIELDS
        if unknown:
            raise UnknownFieldError(f"not writable: {sorted(unknown)}")
        current = self._card_row(card_id)
        next_status = fields.get("status", current["status"])
        next_reason = fields.get("blocked_reason_code", current["blocked_reason_code"])
        self._check_blocked_invariant(next_status, next_reason)
        if "findings_route" in fields:
            _check_findings_route(fields["findings_route"])

        merged = {**fields, "status": next_status, "blocked_reason_code": next_reason}
        assignments = ", ".join(f"{key} = ?" for key in merged)
        values = [*merged.values(), _now(), card_id]
        self._conn.execute(
            f"UPDATE cards SET {assignments}, updated_at = ? WHERE id = ?",
            values,
        )
        self._conn.commit()
        return self.get_card(card_id)

    def set_leases(self, card_id: str, globs: list[str]) -> dict[str, Any]:
        """replaces a card's whole lease. the lease guard compiles it for the card's next run, so
        this is how a lease is widened - a note to the agent cannot change what the hook allows"""
        self._card_row(card_id)
        cleaned = [_check_lease_glob(glob) for glob in globs]
        self._conn.execute("DELETE FROM card_leases WHERE card_id = ?", (card_id,))
        for glob in cleaned:
            self._conn.execute(
                "INSERT INTO card_leases (id, card_id, path_glob) VALUES (?, ?, ?)",
                (_new_id(), card_id, glob),
            )
        self._conn.execute("UPDATE cards SET updated_at = ? WHERE id = ?", (_now(), card_id))
        self._conn.commit()
        return self.get_card(card_id)

    # -- settings -------------------------------------------------------------

    def get_settings(self) -> dict[str, Any]:
        rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        stored = {r["key"]: r["value"] for r in rows}
        return {key: stored.get(key) for key in _SETTING_KEYS}

    def set_setting(self, key: str, value: str | None) -> dict[str, Any]:
        """sets a board-wide value, or clears it with None"""
        if key not in _SETTING_KEYS:
            raise UnknownFieldError(f"no setting {key!r}")
        if key == "findings_route":
            _check_findings_route(value)
        if value is None:
            self._conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            self._conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
            )
        self._conn.commit()
        return self.get_settings()

    def findings_route(self, card_id: str) -> str:
        """where this card's reviewer findings go.

        the global value FORCES every card when set - fabian's reading of "global override",
        2026-09-10. unset, the card's own value decides, and a card with none gets the default
        """
        forced = self.get_settings()["findings_route"]
        if forced:
            return forced
        return self._card_row(card_id)["findings_route"] or DEFAULT_FINDINGS_ROUTE

    # -- tasks: progress checkboxes, distinct from criteria (see the comment above) -------------

    def set_task_done(self, task_id: str, done: bool) -> dict[str, Any]:
        """the one thing a card agent may record about its own tasks: ticking progress.

        does not touch text or position, and there is no equivalent for card_criteria.
        """
        row = self._conn.execute("SELECT * FROM card_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"no task {task_id}")
        self._conn.execute("UPDATE card_tasks SET done = ? WHERE id = ?", (int(done), task_id))
        self._conn.commit()
        updated = self._conn.execute("SELECT * FROM card_tasks WHERE id = ?", (task_id,)).fetchone()
        return _row_to_dict(updated)

    def add_task(self, card_id: str, text: str) -> dict[str, Any]:
        """for a human editing a card's checklist, not for a card agent - hence store-only,
        with no HTTP route."""
        self._card_row(card_id)  # raises NotFoundError on a bad id
        next_position_row = self._conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS next_position FROM card_tasks "
            "WHERE card_id = ?",
            (card_id,),
        ).fetchone()
        task_id = _new_id()
        self._conn.execute(
            "INSERT INTO card_tasks (id, card_id, position, text, done) VALUES (?, ?, ?, ?, 0)",
            (task_id, card_id, next_position_row["next_position"], text),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM card_tasks WHERE id = ?", (task_id,)).fetchone()
        return _row_to_dict(row)

    def remove_task(self, task_id: str) -> None:
        """for a human editing a card's checklist, not for a card agent - hence store-only,
        with no HTTP route."""
        row = self._conn.execute("SELECT * FROM card_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"no task {task_id}")
        self._conn.execute("DELETE FROM card_tasks WHERE id = ?", (task_id,))
        self._conn.commit()

    # every table that points at a card, so deleting one takes its children with it. named
    # explicitly rather than relying on ON DELETE CASCADE, which the schema does not declare and
    # which sqlite only honours when foreign_keys is on - a silent orphan is worse than a loud list
    _CARD_CHILDREN = (
        "card_tasks",
        "card_criteria",
        "card_leases",
        "attachments",
        "comments",
        "events",
    )

    def delete_card(self, card_id: str) -> None:
        """removes a card and everything hanging off it, in one transaction.

        this used to be a single DELETE against cards, which raised FOREIGN KEY constraint failed
        for any card that had ever been commented on, given a task, or run - so it worked in a test
        that deleted a bare card and failed on every real one.

        the card is snapshotted into card_backups first (see _backup_card), so this delete is
        reversible through restore_card for BACKUP_RETENTION_DAYS days rather than destructive.
        """
        card = self.get_card(card_id)  # raises NotFoundError; doubles as the backup snapshot
        self._backup_card(card)
        for table in self._CARD_CHILDREN:
            self._conn.execute(f"DELETE FROM {table} WHERE card_id = ?", (card_id,))
        # a dependency edge names a card at either end, so both directions have to go
        self._conn.execute(
            "DELETE FROM card_deps WHERE card_id = ? OR depends_on_card_id = ?",
            (card_id, card_id),
        )
        self._conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        self._conn.commit()

    def _backup_card(self, card: dict[str, Any]) -> None:
        """writes one card_backups row holding everything restore_card needs to recreate the card
        exactly: its own fields plus tasks, criteria, leases, comments and dependency edges."""
        deps = self._conn.execute(
            "SELECT card_id, depends_on_card_id FROM card_deps "
            "WHERE card_id = ? OR depends_on_card_id = ?",
            (card["id"], card["id"]),
        ).fetchall()
        card_fields = (
            "id",
            "board_id",
            "repo_id",
            "title",
            "workstream",
            "status",
            "blocked_reason_code",
            "description",
            "position",
            "review_flag",
            "model",
            "findings_route",
            "created_at",
            "updated_at",
        )
        payload = {
            "card": {field: card[field] for field in card_fields},
            "tasks": card["tasks"],
            "criteria": card["criteria"],
            "leases": card["leases"],
            "comments": card["comments"],
            "deps": [dict(row) for row in deps],
        }
        self._conn.execute(
            "INSERT INTO card_backups (id, card_id, board_id, payload_json, deleted_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (_new_id(), card["id"], card["board_id"], json.dumps(payload), _now()),
        )

    def restore_card(self, backup_id: str) -> dict[str, Any]:
        """undoes delete_card: recreates the card with its original id, fields, tasks, criteria,
        leases, comments and dependencies, then removes the backup - a card that has been restored
        is live again, not still pending purge."""
        row = self._conn.execute("SELECT * FROM card_backups WHERE id = ?", (backup_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"no card backup {backup_id}")
        payload = json.loads(row["payload_json"])
        card = payload["card"]
        self._conn.execute(
            """
            INSERT INTO cards (id, board_id, repo_id, title, workstream, status,
                blocked_reason_code, description, position, review_flag, model, findings_route,
                created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                card["id"],
                card["board_id"],
                card["repo_id"],
                card["title"],
                card["workstream"],
                card["status"],
                card["blocked_reason_code"],
                card["description"],
                card["position"],
                card["review_flag"],
                card["model"],
                card["findings_route"],
                card["created_at"],
                card["updated_at"],
            ),
        )
        for task in payload["tasks"]:
            self._conn.execute(
                "INSERT INTO card_tasks (id, card_id, position, text, done) VALUES (?, ?, ?, ?, ?)",
                (task["id"], task["card_id"], task["position"], task["text"], task["done"]),
            )
        for criterion in payload["criteria"]:
            self._conn.execute(
                "INSERT INTO card_criteria (id, card_id, position, text) VALUES (?, ?, ?, ?)",
                (criterion["id"], criterion["card_id"], criterion["position"], criterion["text"]),
            )
        for lease in payload["leases"]:
            self._conn.execute(
                "INSERT INTO card_leases (id, card_id, path_glob) VALUES (?, ?, ?)",
                (lease["id"], lease["card_id"], lease["path_glob"]),
            )
        for comment in payload["comments"]:
            self._conn.execute(
                "INSERT INTO comments (id, card_id, author, body, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    comment["id"],
                    comment["card_id"],
                    comment["author"],
                    comment["body"],
                    comment["created_at"],
                ),
            )
        for dep in payload["deps"]:
            # the other end of the edge may itself be gone (deleted and not restored) - restore
            # what still resolves rather than fail the whole card over one missing edge
            both_exist = (
                self._conn.execute(
                    "SELECT COUNT(*) FROM cards WHERE id IN (?, ?)",
                    (dep["card_id"], dep["depends_on_card_id"]),
                ).fetchone()[0]
                == 2
            )
            if both_exist:
                self._conn.execute(
                    "INSERT OR IGNORE INTO card_deps (card_id, depends_on_card_id) VALUES (?, ?)",
                    (dep["card_id"], dep["depends_on_card_id"]),
                )
        self._conn.execute("DELETE FROM card_backups WHERE id = ?", (backup_id,))
        self._conn.commit()
        return self.get_card(card["id"])

    def list_card_backups(self, board_id: str) -> list[dict[str, Any]]:
        """every backup still within its retention window for this board, newest first - without
        the payload, which is only ever read back whole by restore_card"""
        rows = self._conn.execute(
            "SELECT id, card_id, board_id, deleted_at FROM card_backups "
            "WHERE board_id = ? ORDER BY deleted_at DESC",
            (board_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def _purge_expired_backups(self) -> None:
        """drops backups past BACKUP_RETENTION_DAYS. run on every store open, since there is no
        separate background scheduler for it here - see the phase 2 plan"""
        cutoff = (datetime.now(UTC) - timedelta(days=BACKUP_RETENTION_DAYS)).isoformat()
        self._conn.execute("DELETE FROM card_backups WHERE deleted_at < ?", (cutoff,))
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

    # -- prompts: layered by role, every save a new version, history kept -----

    def get_prompt(self, role: str) -> dict[str, Any] | None:
        """the latest stored version for `role`, or None if it has never been saved -
        the caller falls back to that role's seed default (see smortboard.prompts.active_prompt)"""
        row = self._conn.execute(
            "SELECT * FROM prompts WHERE role = ? ORDER BY version DESC LIMIT 1", (role,)
        ).fetchone()
        return _row_to_dict(row) if row is not None else None

    def set_prompt(self, role: str, body: str) -> dict[str, Any]:
        """stores a new version for `role`. never overwrites - the old version stays in history"""
        next_version_row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 AS next_version FROM prompts WHERE role = ?",
            (role,),
        ).fetchone()
        version = next_version_row["next_version"]
        self._conn.execute(
            "INSERT INTO prompts (role, version, body, created_at) VALUES (?, ?, ?, ?)",
            (role, version, body, _now()),
        )
        self._conn.commit()
        return self.get_prompt(role)

    def list_prompt_versions(self, role: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM prompts WHERE role = ? ORDER BY version", (role,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # -- orchestrator: messages and plan, one board's mission control session ----

    def add_orchestrator_message(
        self, board_id: str, author: str, body: str, cards: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        message_id = _new_id()
        created_at = _now()
        self._conn.execute(
            """
            INSERT INTO orchestrator_messages (id, board_id, author, body, cards_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (message_id, board_id, author, body, json.dumps(cards) if cards else None, created_at),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM orchestrator_messages WHERE id = ?", (message_id,)
        ).fetchone()
        return self._orchestrator_message_dict(row)

    def _orchestrator_message_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        message = _row_to_dict(row)
        cards_json = message.pop("cards_json")
        message["cards"] = json.loads(cards_json) if cards_json else []
        return message

    def list_orchestrator_messages(
        self, board_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM orchestrator_messages WHERE board_id = ? ORDER BY created_at"
        params: tuple[Any, ...] = (board_id,)
        if limit is not None:
            # the tail, not the head - the most recent `limit` messages, oldest first
            query = (
                "SELECT * FROM (SELECT * FROM orchestrator_messages WHERE board_id = ? "
                "ORDER BY created_at DESC LIMIT ?) ORDER BY created_at"
            )
            params = (board_id, limit)
        rows = self._conn.execute(query, params).fetchall()
        return [self._orchestrator_message_dict(r) for r in rows]

    def get_plan(self, board_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT body FROM orchestrator_plans WHERE board_id = ?", (board_id,)
        ).fetchone()
        return row["body"] if row is not None else None

    def set_plan(self, board_id: str, body: str) -> None:
        self._conn.execute(
            """
            INSERT INTO orchestrator_plans (board_id, body, updated_at) VALUES (?, ?, ?)
            ON CONFLICT (board_id) DO UPDATE SET body = excluded.body, updated_at = excluded.updated_at
            """,
            (board_id, body, _now()),
        )
        self._conn.commit()

    # -- telemetry projections: reads only, see smortboard/telemetry.py for the shaping ----

    def list_events_by_kind(self, kinds: list[str]) -> list[dict[str, Any]]:
        """every event of these kinds, across every card - usage is board-agnostic, see the
        /api/usage contract. ordered oldest first, same as list_events"""
        placeholders = ", ".join("?" for _ in kinds)
        rows = self._conn.execute(
            f"SELECT * FROM events WHERE kind IN ({placeholders}) ORDER BY card_id, seq", kinds
        ).fetchall()
        events = []
        for row in rows:
            event = _row_to_dict(row)
            event["payload"] = json.loads(event.pop("payload_json"))
            events.append(event)
        return events

    def list_doing_cards_blocked(self) -> list[dict[str, Any]]:
        """cards in 'doing' with a blocked_reason_code set, across every board - half of the
        roster (the other half is whatever RunRegistry.active() says is actually running)"""
        rows = self._conn.execute(
            "SELECT id FROM cards WHERE status = 'doing' AND blocked_reason_code IS NOT NULL"
        ).fetchall()
        return [self.get_card(r["id"]) for r in rows]

    # -- export / import --------------------------------------------------

    def export(self, path: str | Path) -> None:
        from smortboard.store.export import export_bundle

        export_bundle(self._conn, path)

    def import_bundle(self, path: str | Path) -> None:
        from smortboard.store.export import import_bundle as _import

        _import(self._conn, path)
