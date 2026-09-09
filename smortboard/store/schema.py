"""forward-only, idempotent migrations — applied in order on every open"""

import sqlite3

# THE FIVE KANBAN COLUMNS, AND NOTHING ELSE. blocked is not among them: a blocked card keeps the
# status it was in and raises blocked_reason_code, which is what draws the gold outline. a sixth
# status would throw away what the card was doing, which the resume briefing needs
STATUSES = ("todo", "doing", "checking", "accepted", "rejected")
BLOCKED_REASON_CODES = (
    "CRASH",
    "USAGE_LIMIT",
    "LEASE_CONFLICT",
    "AGENT_QUESTION",
    "TESTS_FAILED",
    "REVIEW_REJECTED",
    "DEPENDENCY_REJECTED",
)

_MIGRATIONS: list[str] = [
    # 1: base tables
    """
    CREATE TABLE boards (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        position INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE repos (
        id TEXT PRIMARY KEY,
        board_id TEXT NOT NULL REFERENCES boards(id),
        name TEXT NOT NULL,
        path TEXT NOT NULL,
        default_branch TEXT NOT NULL
    );

    CREATE TABLE cards (
        id TEXT PRIMARY KEY,
        board_id TEXT NOT NULL REFERENCES boards(id),
        repo_id TEXT REFERENCES repos(id),
        title TEXT NOT NULL,
        workstream TEXT,
        status TEXT NOT NULL CHECK (status IN
            ('todo', 'doing', 'checking', 'accepted', 'rejected')),
        blocked_reason_code TEXT CHECK (blocked_reason_code IN
            ('CRASH', 'USAGE_LIMIT', 'LEASE_CONFLICT', 'AGENT_QUESTION',
             'TESTS_FAILED', 'REVIEW_REJECTED', 'DEPENDENCY_REJECTED')),
        description TEXT,
        position INTEGER NOT NULL,
        review_flag INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE card_tasks (
        id TEXT PRIMARY KEY,
        card_id TEXT NOT NULL REFERENCES cards(id),
        position INTEGER NOT NULL,
        text TEXT NOT NULL,
        done INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE card_criteria (
        id TEXT PRIMARY KEY,
        card_id TEXT NOT NULL REFERENCES cards(id),
        position INTEGER NOT NULL,
        text TEXT NOT NULL
    );

    CREATE TABLE card_leases (
        id TEXT PRIMARY KEY,
        card_id TEXT NOT NULL REFERENCES cards(id),
        path_glob TEXT NOT NULL
    );

    CREATE TABLE card_deps (
        card_id TEXT NOT NULL REFERENCES cards(id),
        depends_on_card_id TEXT NOT NULL REFERENCES cards(id),
        PRIMARY KEY (card_id, depends_on_card_id)
    );

    CREATE TABLE attachments (
        id TEXT PRIMARY KEY,
        card_id TEXT NOT NULL REFERENCES cards(id),
        filename TEXT NOT NULL,
        media_type TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        blob BLOB NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE comments (
        id TEXT PRIMARY KEY,
        card_id TEXT NOT NULL REFERENCES cards(id),
        author TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE events (
        id TEXT PRIMARY KEY,
        card_id TEXT NOT NULL REFERENCES cards(id),
        seq INTEGER NOT NULL,
        kind TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (card_id, seq)
    );
    """,
    # 2: a repo's test invocation, so the runner can scope a card's Bash allowlist to it rather
    # than granting unscoped Bash. nullable - a repo with none falls back to today's narrower set
    """
    ALTER TABLE repos ADD COLUMN test_command TEXT;
    """,
]


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return row[0]


def migrate(conn: sqlite3.Connection) -> None:
    """apply any migration past the db's current version — a no-op if already current"""
    version = current_version(conn)
    for index, script in enumerate(_MIGRATIONS, start=1):
        if index <= version:
            continue
        conn.executescript(script)
        conn.execute(f"PRAGMA user_version = {index}")
    conn.commit()
