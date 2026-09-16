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
    "MERGE_CONFLICT",
    "API_UNREACHABLE",
)
# where reviewer findings go. attention is the default: a card fixing its own findings unattended
# spends a run's worth of tokens that nobody asked for
FINDINGS_ROUTES = ("fix", "attention")
DEFAULT_FINDINGS_ROUTE = "attention"

# a deleted card is kept as a backup for this many days before it is purged for good - see
# Store.delete_card, Store.restore_card and Store._purge_expired_backups
BACKUP_RETENTION_DAYS = 7

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
    # 3: the image a card on this repo runs in. SHARED TOOLS, ISOLATED CARDS: the toolchain is baked
    # into one image per repo so a fresh container per card does not reinstall dependencies every
    # run, while each card still gets its own container and its own clone. a shared package cache
    # would have been cheaper still and is deliberately not used - it is a writable surface every
    # later card reads, which is the cross-card contamination the per-card container exists to stop
    """
    ALTER TABLE repos ADD COLUMN image TEXT;
    """,
    # 4: where a card's reviewer findings go - back to the worker to fix, or to a human through
    # attention. null on a card means the default. the global value in settings forces every card
    """
    ALTER TABLE cards ADD COLUMN findings_route TEXT CHECK (findings_route IN ('fix', 'attention'));
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
    """,
    # 5: a repo's lint invocation, allowlisted for its cards like test_command. check-only by
    # convention: a write through the shell bypasses the lease, which guards only Edit and Write
    """
    ALTER TABLE repos ADD COLUMN lint_command TEXT;
    """,
    # 6: phase 4, mission control. prompts are layered by role only, and versioned: every save is
    # a new row so history is kept rather than overwritten. orchestrator_messages is the mission
    # control chat, one row per turn on either side; cards_json records which cards a reply created,
    # so the panel can link straight to them without re-deriving it. orchestrator_plans is the
    # ledger's own plan text, one row per board, reconciled against cards rather than replacing them
    # as ground truth
    """
    CREATE TABLE prompts (
        role TEXT NOT NULL CHECK (role IN ('orchestrator', 'worker', 'reviewer')),
        version INTEGER NOT NULL,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (role, version)
    );

    CREATE TABLE orchestrator_messages (
        id TEXT PRIMARY KEY,
        board_id TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
        author TEXT NOT NULL CHECK (author IN ('fabian', 'orchestrator', 'board')),
        body TEXT NOT NULL,
        cards_json TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE orchestrator_plans (
        board_id TEXT PRIMARY KEY REFERENCES boards(id) ON DELETE CASCADE,
        body TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    # 7: the model a card's worker runs on. null means the board's worker_model setting, then sonnet;
    # the orchestrator proposes one when it plans a card, and fabian can override it on the card
    """
    ALTER TABLE cards ADD COLUMN model TEXT;
    """,
    # 8: indexes for every lookup by card or board. only events had one (its unique card_id, seq),
    # so every card's tasks, comments and leases were a scan of the whole table
    """
    CREATE INDEX IF NOT EXISTS idx_cards_board ON cards (board_id, position);
    CREATE INDEX IF NOT EXISTS idx_card_tasks_card ON card_tasks (card_id, position);
    CREATE INDEX IF NOT EXISTS idx_card_criteria_card ON card_criteria (card_id, position);
    CREATE INDEX IF NOT EXISTS idx_card_leases_card ON card_leases (card_id);
    CREATE INDEX IF NOT EXISTS idx_card_deps_dependent ON card_deps (depends_on_card_id);
    CREATE INDEX IF NOT EXISTS idx_attachments_card ON attachments (card_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_comments_card ON comments (card_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_orchestrator_messages_board
        ON orchestrator_messages (board_id, created_at);
    """,
    # 9: card soft-delete. delete_card snapshots a card and its children here before removing it,
    # so a delete is reversible for BACKUP_RETENTION_DAYS days instead of destroying the card
    # outright. no foreign keys on card_id/board_id — a backup deliberately outlives the row (and
    # the board) it describes, so a FK to either would block the very deletes it exists to survive
    """
    CREATE TABLE card_backups (
        id TEXT PRIMARY KEY,
        card_id TEXT NOT NULL,
        board_id TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        deleted_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_card_backups_deleted_at ON card_backups (deleted_at);
    """,
    # 10: a card may stand for one task of its repo's TASKS.jsonl ledger, and a task is carded
    # at most once per repo - the unique index is what makes mission control's import idempotent
    """
    ALTER TABLE cards ADD COLUMN ledger_task TEXT;
    CREATE UNIQUE INDEX IF NOT EXISTS idx_cards_repo_ledger_task
        ON cards (repo_id, ledger_task) WHERE ledger_task IS NOT NULL;
    """,
    # 11: MERGE_CONFLICT joins the blocked reasons - a card whose branch no longer merges cleanly
    # with its base while its pull request waits. sqlite cannot ALTER a CHECK constraint, so the
    # table is recreated with the wider one and the data copied across; every index, the
    # ledger_task uniqueness included, is recreated after the swap rather than assumed to survive it
    """
    PRAGMA foreign_keys = OFF;

    CREATE TABLE cards_new (
        id TEXT PRIMARY KEY,
        board_id TEXT NOT NULL REFERENCES boards(id),
        repo_id TEXT REFERENCES repos(id),
        title TEXT NOT NULL,
        workstream TEXT,
        status TEXT NOT NULL CHECK (status IN
            ('todo', 'doing', 'checking', 'accepted', 'rejected')),
        blocked_reason_code TEXT CHECK (blocked_reason_code IN
            ('CRASH', 'USAGE_LIMIT', 'LEASE_CONFLICT', 'AGENT_QUESTION',
             'TESTS_FAILED', 'REVIEW_REJECTED', 'DEPENDENCY_REJECTED', 'MERGE_CONFLICT')),
        description TEXT,
        position INTEGER NOT NULL,
        review_flag INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        findings_route TEXT CHECK (findings_route IN ('fix', 'attention')),
        model TEXT,
        ledger_task TEXT
    );

    INSERT INTO cards_new SELECT
        id, board_id, repo_id, title, workstream, status, blocked_reason_code, description,
        position, review_flag, created_at, updated_at, findings_route, model, ledger_task
    FROM cards;

    DROP TABLE cards;
    ALTER TABLE cards_new RENAME TO cards;

    CREATE INDEX IF NOT EXISTS idx_cards_board ON cards (board_id, position);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_cards_repo_ledger_task
        ON cards (repo_id, ledger_task) WHERE ledger_task IS NOT NULL;

    PRAGMA foreign_keys = ON;
    """,
    # 12: API_UNREACHABLE joins the blocked reasons - a card whose worker run ended on an
    # unreachable/overloaded api endpoint, retried automatically rather than left for a human.
    # same recreate-the-table dance as migration 11, sqlite still cannot alter a CHECK in place
    """
    PRAGMA foreign_keys = OFF;

    CREATE TABLE cards_new (
        id TEXT PRIMARY KEY,
        board_id TEXT NOT NULL REFERENCES boards(id),
        repo_id TEXT REFERENCES repos(id),
        title TEXT NOT NULL,
        workstream TEXT,
        status TEXT NOT NULL CHECK (status IN
            ('todo', 'doing', 'checking', 'accepted', 'rejected')),
        blocked_reason_code TEXT CHECK (blocked_reason_code IN
            ('CRASH', 'USAGE_LIMIT', 'LEASE_CONFLICT', 'AGENT_QUESTION',
             'TESTS_FAILED', 'REVIEW_REJECTED', 'DEPENDENCY_REJECTED', 'MERGE_CONFLICT',
             'API_UNREACHABLE')),
        description TEXT,
        position INTEGER NOT NULL,
        review_flag INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        findings_route TEXT CHECK (findings_route IN ('fix', 'attention')),
        model TEXT,
        ledger_task TEXT
    );

    INSERT INTO cards_new SELECT
        id, board_id, repo_id, title, workstream, status, blocked_reason_code, description,
        position, review_flag, created_at, updated_at, findings_route, model, ledger_task
    FROM cards;

    DROP TABLE cards;
    ALTER TABLE cards_new RENAME TO cards;

    CREATE INDEX IF NOT EXISTS idx_cards_board ON cards (board_id, position);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_cards_repo_ledger_task
        ON cards (repo_id, ledger_task) WHERE ledger_task IS NOT NULL;

    PRAGMA foreign_keys = ON;
    """,
    # 13: a repo's remembered lease globs - ticked "remember for this repo" on a lease approval,
    # so a later card on the same repo can write that path without ever being asked again. always
    # an explicit operator choice (see attention.approve_lease); nothing inserts here on its own
    """
    CREATE TABLE repo_remembered_leases (
        id TEXT PRIMARY KEY,
        repo_id TEXT NOT NULL REFERENCES repos(id),
        path_glob TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (repo_id, path_glob)
    );

    CREATE INDEX IF NOT EXISTS idx_repo_remembered_leases_repo ON repo_remembered_leases (repo_id);
    """,
    # 14: a board's own parallel cap, on top of the global max_parallel setting - unset means
    # only the global limit applies, same as before this column existed
    """
    ALTER TABLE boards ADD COLUMN max_parallel INTEGER;
    """,
    # 15: the landing lock - one holder per (repo_key, target branch), FIFO queue behind it. repo_key
    # is a resolved path or remote url, not a foreign key to repos: an outside agent's repo need not
    # be registered on any board. calling POST /api/repos/.../landing again with the held lease_id is
    # the heartbeat; a holder whose heartbeat goes stale past ttl_s is evicted and the next queued
    # lease is promoted - see smortboard/review/landing.py
    """
    CREATE TABLE landing_locks (
        repo_key TEXT NOT NULL,
        target TEXT NOT NULL,
        lease_id TEXT NOT NULL,
        holder TEXT NOT NULL,
        branch TEXT NOT NULL,
        ttl_s INTEGER NOT NULL,
        since TEXT NOT NULL,
        last_heartbeat TEXT NOT NULL,
        PRIMARY KEY (repo_key, target)
    );

    CREATE TABLE landing_queue (
        id TEXT PRIMARY KEY,
        repo_key TEXT NOT NULL,
        target TEXT NOT NULL,
        lease_id TEXT NOT NULL UNIQUE,
        holder TEXT NOT NULL,
        branch TEXT NOT NULL,
        ttl_s INTEGER NOT NULL,
        queued_at TEXT NOT NULL,
        position INTEGER NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_landing_queue_repo_target
        ON landing_queue (repo_key, target, position);
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
