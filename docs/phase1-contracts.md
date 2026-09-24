# phase 1 contracts

> **The Phase 1 contract, kept as the record.** The board has grown past it, and where this document
> disagrees with the code or the README, they win. The main differences: eleven blocked reason codes, not
> seven (`MERGE_CONFLICT`, `API_UNREACHABLE`, `BASE_RED` and `OUTDATED` joined); many more routes (see
> `smortboard/server/app.py`); `POST /api/cards` and a `PATCH` that changes a title or description
> answer with the card plus a `warnings` list about long card text, never refusing or cutting;
> `GET /api/cards/{id}` carries the same `next_action`, `next_action_short` and `handled_by_board` as
> the board list; and `PATCH /api/boards/{id}` takes `merge_mode` and `lease_mode` besides the caps.

Phase 1 is "the board, no agents". No agent executes anything; this is the surface plus its store.
Three units are built independently against the contracts below. **A unit may not reach past these
boundaries** — if a contract is wrong, say so rather than working around it.

File ownership is strict, so the three can run at once:

| Unit | Owns | Never touches |
|---|---|---|
| A — store | `smortboard/store/`, `tests/test_store.py` | server, ui |
| B — server | `smortboard/server/`, `tests/test_server.py`, `docker/` | store internals, ui |
| C — interface | `smortboard/ui/`, `tests/js/` | any python |

## vocabulary

`status` is one of `todo`, `doing`, `checking`, `accepted`, `rejected` — the five kanban columns
and nothing else.

**`blocked` is not a status.** A blocked card keeps the status it was in and raises a nullable
`blocked_reason_code`, one of `CRASH`, `USAGE_LIMIT`, `LEASE_CONFLICT`, `AGENT_QUESTION`,
`TESTS_FAILED`, `REVIEW_REJECTED`, `DEPENDENCY_REJECTED`. That code draws the gold outline and feeds
the attention count, exactly as the brief's §4.3 describes.

A sixth status was the original plan and it was wrong: it gives a blocked card no column to live in,
and it throws away what the card was doing, which the Phase 5 resume briefing needs. A reason code
rides alongside any status — a queued card blocks on `DEPENDENCY_REJECTED` just as a working one
blocks on `USAGE_LIMIT`.

## unit a — store

```
unit:     store
expects:  a path to a sqlite file
does:     owns the schema, its migrations, and every read and write of board state; exposes
          functions, never raw sql or a connection, to its callers
outputs:  python dicts matching the json shapes below; an export bundle on demand
verifies: a card created, read back, moved through every status, and exported round-trips to an
          identical database. A card blocked without a reason code is refused.
```

Tables. `id` is a uuid string everywhere; timestamps are ISO-8601 UTC strings.

- `boards(id, name, position, created_at)`
- `repos(id, board_id, name, path, default_branch)` — a board declares the repos its cards may use
- `cards(id, board_id, repo_id, title, workstream, status, blocked_reason_code, description,
  position, review_flag, created_at, updated_at)`
- `card_tasks(id, card_id, position, text, done)`
- `card_criteria(id, card_id, position, text)` — acceptance criteria
- `card_leases(id, card_id, path_glob)` — the paths a card may write, per S3
- `card_deps(card_id, depends_on_card_id)` — both directions are derived from this one table
- `attachments(id, card_id, filename, media_type, size_bytes, blob, created_at)`
- `comments(id, card_id, author, body, created_at)`
- `events(id, card_id, seq, kind, payload_json, created_at)` — **append-only, never updated**

Required behaviour:

- `blocked` without a `blocked_reason_code` is a rejected write, and so is a reason code on any
  other status.
- `card_deps` is the single source for dependencies. "depends on" and "depended on by" are two
  queries against it, not two tables.
- `events` is append-only. Expose an append and a read; no update or delete.
- Migrations run forward on open and are idempotent — opening an up-to-date database is a no-op.
- `export(path)` writes a portable bundle (cards, boards, repos, events, attachments) and
  `import_bundle(path)` restores it. The round-trip must be lossless.

## unit b — server

```
unit:     server
expects:  a store instance and a port
does:     serves the json api below and the interface's assets, its own files first with the
          ui_base package as fallback
outputs:  http responses; 404 for an asset outside both roots
verifies: every endpoint below returns its documented shape; a request for `../pyproject.toml`
          is a 404, not a file; an asset present in both roots is served from the local one.
```

Endpoints. All bodies are JSON. Errors are `{"error": "<message>"}` with a 4xx status.

```
GET    /health                      -> {"ok": true, "version": "..."}
GET    /api/boards                  -> [board]
POST   /api/boards                  <- {name} -> board
GET    /api/boards/{id}/cards       -> [card]
POST   /api/cards                   <- {board_id, repo_id, title, ...} -> card
GET    /api/cards/{id}              -> card (with tasks, criteria, leases, deps, comments)
PATCH  /api/cards/{id}              <- any subset of writable fields -> card
DELETE /api/cards/{id}              -> 204
POST   /api/cards/{id}/comments     <- {author, body} -> comment
POST   /api/cards/{id}/attachments  <- multipart -> attachment (no blob in the response)
GET    /api/cards/{id}/attachments/{aid} -> the bytes, with its media type
GET    /api/cards/{id}/events       -> [event]
GET    /ui/{name}                   -> asset bytes
```

Asset serving follows the pattern documented in `ui_base/CLAUDE.md` verbatim: resolve the local
path, check containment with `.resolve()` and `in .parents`, fall back to `read_asset(name)`, and
map `UiBaseError` to a 404. **Do not reimplement the traversal check** — call `read_asset`.
`Content-Type` from the extension, `Cache-Control: no-store`.

## unit c — interface

```
unit:     interface
expects:  the json api above, and ui_base's assets at /ui/
does:     renders one board as kanban buckets of card strips, opens a strip into a panel, and owns
          the keyboard model
outputs:  api calls; nothing persisted locally beyond ui_base's own tab memory
verifies: five status buckets render; arrow keys move focus across buckets and rows; enter opens a
          card and escape closes it; `g`, `u`, `s`, `a`, `/`, `,`, `.` and `1`-`9` are bound.
```

**Configuration only.** Every visual primitive comes from ui_base — `makeBuckets`, `makeExpander`,
`indicateBadge`, `indicateFocus`, `Menu`, `initShell`, `.hazard-stripes`. If something is missing,
**stop and report it**; do not build a primitive here. That is the project's hard constraint.

Keyboard model, all bound on `event.code`, never `event.key`:

| Key | Action |
|---|---|
| arrows | move focus across buckets and rows; `exit-top` returns to the board bar |
| Enter / Space | open the focused card |
| Escape | exactly one level back: input -> panel -> closed |
| arrows, card open | move between the open card's sections |
| `g` | toggle kanban / workstream grouping (workstream mode ships post-v1; the toggle exists) |
| `u` | usage dropdown (placeholder in phase 1) |
| `a` | agent roster (placeholder in phase 1) |
| `s` | shortcut overlay, built from the binding table so it cannot drift |
| `/` | focus the panel's input |
| `,` / `.` | workforce / mission control panels (hazard-striped placeholders in phase 1) |
| `1`-`9` | jump directly to a board |

Placeholders use `.hazard-stripes` with a label, per the brief's §4.4.
