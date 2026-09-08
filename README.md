# smortboard

A convenience wrapper around coding agents. Define work as cards, hand them to agents, walk away,
come back to finished work.

## How to run

Docker, the way it is meant to be distributed:

```bash
docker compose -f docker/docker-compose.yml up -d --build
open http://127.0.0.1:8000/ui/index.html
```

Stop it, keeping the data:

```bash
docker compose -f docker/docker-compose.yml down
```

Locally, without Docker, on a scratch database:

```bash
uv sync
SMORTBOARD_DB=board.db SMORTBOARD_PORT=8042 uv run python -m smortboard.server
open http://127.0.0.1:8042/ui/index.html
```

A fresh board is empty. Create one and give it a card:

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
