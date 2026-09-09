# smortboard

A convenience wrapper around coding agents. Define work as cards, hand them to agents, walk away,
come back to finished work.

## How to run

No install, one command:

```bash
uvx smortboard
```

Or install it once and keep the command around:

```bash
uv tool install smortboard
smortboard
```

Either way it starts the server, prints the URL it's serving, and opens a browser tab. The
database defaults to a path under your user data directory, not the current working directory,
so running it from anywhere never scatters `.db` files around.

Overrides, in order of precedence — flag, then env var, then default:

```bash
smortboard --port 8042 --db ./board.db --no-browser
# or
SMORTBOARD_DB=board.db SMORTBOARD_PORT=8042 smortboard
```

`smortboard --help` shows all flags and this precedence.

Docker is no longer how the board itself ships — the board is a plain installable CLI now.
Docker still isolates the *cards*: each one executes in its own worktree, and giving the board
container access to run further containers for that would mean handing it the Docker socket,
which is root on the host. See `docs/PLAN.md`'s locked-decisions table (Isolation, Packaging) for
the reasoning.

Running from a checkout without installing:

```bash
uv sync
uv run smortboard --db board.db --port 8042
```

A fresh board is empty — the interface says so and how to fix it. To do it by hand:

```bash
BOARD=$(curl -s -X POST http://127.0.0.1:8042/api/boards \
  -H 'content-type: application/json' -d '{"name":"smortboard"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')

curl -s -X POST http://127.0.0.1:8042/api/cards -H 'content-type: application/json' \
  -d "{\"board_id\":\"$BOARD\",\"repo_id\":null,\"title\":\"first card\",\"status\":\"todo\"}"
```

Tests:

```bash
uv run pytest
node tests/js/board_navigation.mjs
node tests/js/keyboard_bindings.mjs
```

## Keys

The board is keyboard-first; every binding resolves on physical key position, so a non-US layout
cannot move them. Press `s` for the overlay, which is generated from the same table the handlers
use and so cannot drift.

| Key | Action |
|---|---|
| arrows | move between cards and columns |
| Enter / Space | open the focused card |
| Escape | one level back: input, then panel, then closed |
| `g` | switch kanban / workstream grouping |
| `u` | usage and account |
| `a` | agent roster |
| `s` | this list |
| `/` | focus a panel's input |
| `,` / `.` | workforce / mission control panels |
| `1`-`9` | jump to a board |

## Where things are

- `docs/PLAN.md` — the phased plan, the flowchart, and the whole-system test case
- `docs/dev-board-brief.md` — the original vision brief
- `docs/PHASE1-CONTRACTS.md` — the schema, the JSON API, and the per-unit boundaries
- `docs/spikes/` — what was proven before building, and what it disproved

State lives in SQLite and is gitignored. `Store.export()` writes a portable bundle for carrying a
board between machines.
