# first board

## look at the demo first

before you build anything real:

```bash
git clone https://github.com/BalthazarFitzpatrick/smortboard && cd smortboard
uv sync
docker build -f docker/card.Dockerfile -t smortboard-card:latest .
uv run smortboard --demo
```

it prints and opens `http://127.0.0.1:8001/ui/index.html?key=...`: a throwaway board with three
invented projects and cards in every state. port 8001 keeps it off your real board. `--demo` ignores
`--db` and `SMORTBOARD_DB`, never reads the user data dir, and writes only to a fresh file in a
temp directory that dies with the process.

**its runs and pull requests are seeded history.** `r` there would need docker and a real repo. use
it to learn the [keys](../reference/keyboard.md) and to read the
[best practices](../practices/cards.md) against something real.

## your real board

`uv run smortboard` (no `--demo`) starts it on port 8000. a card needs three things before it runs.

### 1. a credential for the cards

in your terminal:

- **claude:** `claude setup-token` prints a token once.
- **codex:** `codex login` writes `~/.codex/auth.json` (paste all of it). or use an openai api key.

on the board: `shift`+`p`, add a profile, paste, **activate**. remove the empty `default` profile
once yours is active. the board writes the file itself, at mode 600.

### 2. docker, running

a card executes in it.

### 3. a board and a repo

`b` -> **new board** -> pick a folder, or name a new one. the picker opens in **where new repos go**
(`o`, general), or your home folder when that is unset. the board looks at the folder and adds only
what is missing:

| the folder | the board | you |
|---|---|---|
| new or empty | git, a starter commit on `main`, a `development` branch, a private github repo, `development` pushed | run the one push-main command it shows |
| has files, no git | lists what the first commit would hold and waits for your confirm; a `.gitignore` keeps `.env` and keys out; then as above | confirm, then the command |
| a repo without github | `development` if missing, a private github repo, `development` pushed | the command |
| a repo on github | fetches or creates `development`, pushes it if missing | nothing |

**the board never pushes `main`.** the command it shows (`git push -u origin main && gh repo edit
--default-branch main`) is yours to run once. Cards land on `development`. **from online repo**
clones one of your github repos into a folder you pick, starting in the same place, and does the
same. `gh` must be logged in (`gh auth login`).

each repo row then shows its base branch, its folder and its `origin`, or "no origin" when it has
none, with labelled **tests**, **lint** and **image** fields you can edit. **add a repo by hand**,
folded behind its button, is for a second repo on a board.

**tests are required.** cards write them, one per criterion, and the gate runs them. when you add
the repo, the board reads its committed files:

- **has tests:** it stores the command that runs them: `uv run --no-sync pytest -q` for python,
  `npm test` or `node --test` for node. fix it in the repo's **tests** field if it guessed wrong.
- **has none:** it says so, with two ways forward. **ask mission control** opens `.` with a request
  for a first card that adds a test suite; `/` then `enter` sends it. **use my own command** puts
  you in the **tests** field.
- **new repo, nothing to detect:** mission control's first card sets up the tests and names the
  command, and the board stores it.

there is no switch to turn tests off: the gate refuses a card on a repo without a test command. see
[repos and test commands](../reference/repos.md).

### then press `h`

> [!IMPORTANT]
> **press `h` before your first card.**
> the pre-flight checklist is one screen of green and red rows:
>
> - git, `gh` signed in, docker, the card image, your lab credentials
> - per repo on the board: its path, an `origin` on github, its default branch pushed, `gh` able
>   to see it, a test command, and a repo image no older than its lockfile
>
> a red row says what is missing and how to fix it. skip it and a card finds the same gap minutes
> into its run.

## your first card

1. `.` and tell [mission control](../practices/mission-control.md) what to build. it answers with a
   plan and proposed cards.
2. focus a card and `r` (it asks once), or `w` to run the whole board.
3. anything that needs you lands in the [inbox](../practices/attention.md), `n`. boards default to
   review-required: read the pull request, then `y` accepts it and lands it on the unprotected base.
   main stays yours.

## practice run

`uv run smortboard seed-beta <empty folder>` makes a small browser game on a private github repo
and a board of 15 cards written for it. same setup as new board, so you run the one push-main
command it prints. [practice board](../beta-test-board.md) takes it to a green `h` and `w`.
