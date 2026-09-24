# First board

## Look at the demo first

Before you build anything real:

```bash
git clone https://github.com/BalthazarFitzpatrick/smortboard && cd smortboard
uv sync
docker build -f docker/card.Dockerfile -t smortboard-card:latest .
uv run smortboard --demo
```

That prints and opens `http://127.0.0.1:8001/ui/index.html?key=...`: a throwaway board with three
invented projects and cards in every state. Port 8001 keeps it off your real board. `--demo` ignores
`--db` and `SMORTBOARD_DB`, never reads the user data dir, and writes only to a fresh file in a
temp directory that dies with the process.

Its runs and pull requests are seeded history: `r` there would need Docker and a real repo. Use it
to learn the [keys](../reference/keyboard.md) and to read the [best practices](../practices/cards.md)
against something real.

## Your real board

`uv run smortboard` (no `--demo`) starts it on port 8000. A card needs three things before it runs.

### 1. A credential for the cards

Never your own login. Get one in your terminal:

- **Claude:** `claude setup-token` prints a token once.
- **Codex:** `codex login` writes `~/.codex/auth.json` (paste all of it). Or use an OpenAI API key.

On the board: `shift`+`p`, add a profile, paste, press **activate**. Remove the empty `default`
profile once yours is active. The board writes the file itself, at mode 600.

### 2. Docker, running

A card executes in it.

### 3. A board and a repo

`b` -> **from local repo** -> pick a clone of a GitHub repo with its default branch pushed. Private
is enough. The board pushes card branches and opens pull requests there, so a repo with no GitHub
`origin` stops every card at hand-over.

On the repo's row, set its test command, e.g. `uv run pytest -q`. **Without one a card cannot
finish**: the gate has nothing to run. See [Repos and test commands](../reference/repos.md).

### Then press `h`

> [!IMPORTANT]
> **Press `h` before your first card.**
> The pre-flight checklist is one screen of green and red rows:
>
> - git, `gh` signed in, Docker, the card image, your lab credentials
> - per repo on the board: its path, an `origin` on GitHub, its default branch pushed, `gh` able
>   to see it, a test command, and a repo image no older than its lockfile
>
> A red row says what is missing and how to fix it. Skip it and a card finds the same gap minutes
> into its run.

## Your first card

1. `.` and tell [mission control](../practices/mission-control.md) what you want built. It answers
   with a plan and proposed cards.
2. Focus a card and press `r` (it asks once), or `w` to run the whole board.
3. Anything that needs you lands in the [inbox](../practices/attention.md), `n`. Boards default to
   review-required: read the pull request, then `y` accepts it and lands it on an unprotected base.
   Main stays yours.

## Practice run

`uv run smortboard seed-beta <empty folder>` makes a small browser-game repo and a board of 15 cards
written for it. [Practice board](../beta-test-board.md) takes it to a green `h` and `w`.
