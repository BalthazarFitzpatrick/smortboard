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

## Before a card can run

The board starts and serves the interface with nothing but the command above. **Running a card
needs two more things**, and it refuses rather than running one unisolated:

**1. Docker, running.** Every card executes in its own throwaway container, with a clone of its
repo and nothing else of yours mounted. There is no fallback mode — a card that ran as an ordinary
process would be an agent that may have read untrusted input, on your filesystem, with your
credential. A board that quietly degraded would be claiming an isolation it no longer had.

**2. A card credential**, separate from your own login:

```bash
claude setup-token          # prints a one-year token; it is not saved anywhere
```

Store it where your OS keeps secrets — Keychain on macOS, Credential Manager on Windows, Secret
Service on Linux:

```bash
security add-generic-password -s smortboard-card-token -a smortboard -w <token>
```

The board reads it from there and hands it to the container on stdin, so it is never written to
disk and never visible to `docker inspect`. That token can only make model requests, so it is
narrower than your own login by construction.

A repo can declare the image its cards run in, so the toolchain is installed once rather than on
every card run, and the command its tests use. Both live on the repo, next to its path.

Docker is not how the board ships — the board is a plain installable CLI. Giving a *containerised*
board the ability to start card containers would mean handing it the Docker socket, which is root
on the host, so the board runs natively and only the cards are contained. See "The containment
decision" in `docs/PLAN.md` for the whole argument.

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
