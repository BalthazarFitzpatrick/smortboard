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
needs three more things**, and it refuses rather than running one unisolated:

**1. Docker, running.** Every card executes in its own throwaway container, with a clone of its
repo and nothing else of yours mounted. There is no fallback mode — a card that ran as an ordinary
process would be an agent that may have read untrusted input, on your filesystem, with your
credential. A board that quietly degraded would be claiming an isolation it no longer had.

**2. The card image, built once.** Nothing builds it for you, and a card started without it fails
minutes in with an opaque crash — so the board checks for it up front and says this:

```bash
docker build -f docker/card.Dockerfile -t smortboard-card:latest .
```

It carries the `claude` CLI, git and uv, and nothing of yours: no GitHub credential, no token, no
copy of a repo. A repo can name its own image instead, with its toolchain already installed, so a
fresh container per card costs a second rather than a dependency install.

**3. A card credential**, separate from your own login:

```bash
claude setup-token          # prints a one-year token; it is not saved anywhere
```

Store it in a file only you can read. The board looks there first:

```bash
mkdir -p ~/.config/smortboard
(umask 077; cat > ~/.config/smortboard/card_token)   # paste the token, Enter, then Ctrl-D
```

On Windows the file is `%APPDATA%\smortboard\card_token`; `SMORTBOARD_CARD_TOKEN_PATH` points it
anywhere else. With no file, the board falls back to the OS credential store (service
`smortboard-card-token`, account `smortboard`). On macOS every read from the Keychain raised a
prompt, which is why the file comes first.

The board hands the token to the container on stdin, so it never lands on the container's disk and
is never visible to `docker inspect`. That token can only make model requests, so it is narrower
than your own login by construction.

A repo can declare the image its cards run in, so the toolchain is installed once rather than on
every card run, and the commands its tests and its linter use. They live on the repo, next to its
path, and they are the only test and lint invocations a card is allowed - so keep the lint command
check-only (`ruff check`, `ruff format --check`): a card's own edits go through its lease guard, a
shell command's writes do not.

The test gate runs with no network, so a repo's image must already hold its toolchain - the stock
card image has no python. smortboard's own is `docker/repo.Dockerfile`, built on top of the card
image; point the smortboard repo's `image` at `smortboard-repo:latest` and give it
`uv run --no-sync python -m pytest -p no:cacheprovider -q` style commands, which never sync:

```bash
docker build -f docker/repo.Dockerfile -t smortboard-repo:latest .
```

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

# a repo is what a card runs against: where it lives, what its tests are, what image it runs in
REPO=$(curl -s -X POST http://127.0.0.1:8042/api/boards/$BOARD/repos \
  -H 'content-type: application/json' \
  -d '{"name":"smortboard","path":"/path/to/repo","default_branch":"main",
       "test_command":"uv run pytest","lint_command":"uv run ruff check . && uv run ruff format --check .",
       "image":"smortboard-card:latest"}' \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')

curl -s -X POST http://127.0.0.1:8042/api/cards -H 'content-type: application/json' \
  -d "{\"board_id\":\"$BOARD\",\"repo_id\":\"$REPO\",\"title\":\"first card\",\"status\":\"todo\"}"
```

## Running a card

Focus a card and press `r`. If the board cannot run one it says which of Docker, the card image
and the card credential is missing, rather than doing nothing.

What happens then is the whole of it, in order:

1. a worktree and a branch are cut for the card, off the repo's default branch
2. the agent runs in its own container, with a clone and its path lease, and commits
3. **the board re-runs the repo's tests** in a second container, with no network - the agent
   reporting that the tests pass is the agent grading its own homework
4. **a separate reviewer reads the diff** and answers four questions: vulnerabilities, leaked
   credentials, best practices, efficient coding. It has no Edit, no Write and no Bash
5. if both pass, the board pushes the branch and opens a pull request, and stops

The card's foot shows the phase while it runs and where it landed when it stops - a link to the
pull request, or the reason code that blocked it. The full reason is a comment on the card.

**The board never merges.** There is no code path that can: every `gh` call goes through a
three-command allowlist that does not contain `merge`, and every push refuses a protected
destination. The pull request is where the board's job ends and yours begins.

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
| Enter | open the focused card |
| Space | open the focused card, or close the open one |
| Escape | one level back: input, then panel, then closed |
| `g` | switch kanban / workstream grouping |
| `u` | usage: rate-limit windows and per-model spend |
| `a` | agent roster: pick a row to jump to that card |
| `r` | run the focused card |
| `m` | change the focused card's model: board default, haiku, sonnet, opus |
| `t` | run replay: scrub the focused card's run step by step |
| `s` | this list |
| `p` | edit the orchestrator, worker and reviewer prompts |
| `/` | focus a panel's input |
| `,` | workforce: chat with the focused card's agent |
| `.` | mission control: chat with the board's orchestrator |
| `1`-`9` | jump to a board |

## Where things are

- `docs/PLAN.md` — the phased plan, the flowchart, and the whole-system test case
- `docs/dev-board-brief.md` — the original vision brief
- `docs/PHASE1-CONTRACTS.md` — the schema, the JSON API, and the per-unit boundaries
- `docs/spikes/` — what was proven before building, and what it disproved

State lives in SQLite and is gitignored. `Store.export()` writes a portable bundle for carrying a
board between machines.
