# smortboard

**Kanban board that agents work, not you.** Write the card, walk away. Agent takes it in its own
container, on its own branch. Board runs your tests, a second agent reviews the diff, pull request
waits for you. `main` stays yours.

![The board mid-sprint: cards in every state across the columns - blue where agents are working, vanilla where they wait on you, lichen accepted, red rejected](docs/images/hero-board.jpg)

## What it does

**The board.** Blue: agent on it. Vanilla: waiting on you. Lichen: accepted. Queueing, retries after
a rate limit, rebasing a waiting pull request: the board does it.

**Mission control and workforce.** `.` - say what to build; the orchestrator drafts the cards, each
with a file lease, a model and its dependencies. `,` - talk to a running agent; the note lands
between two tool calls, the run keeps going.

![Both drawers open: a working agent's transcript on the left, the orchestrator planning cards on the right](docs/images/hero-agents.jpg)

**Attention.** One column to watch. A question, failed tests, a rejected review - anything the board
cannot retry itself - lands there and in the inbox `n`, each with its next step. Answer it, the card
resumes.

![The attention inbox: blocked cards oldest first, each with its reason, its next step and an answer box](docs/images/inbox.jpg)

**Usage and cost.** Rate-limit windows per subscription, spend per model, per card, per board. A
daily budget per board, a cap per run. Limit hit: switch to the fallback model, or ask you first.

![The usage panel: five-hour and seven-day windows per lab, spend by model](docs/images/usage.jpg)

## Setup: four steps

Needs Python 3.11+ with [uv](https://docs.astral.sh/uv/getting-started/installation/), Docker
running, the [GitHub CLI](https://cli.github.com/) logged in, git, and Claude Code or Codex locally
to mint a key.

1. **Clone.**

   ```bash
   git clone https://github.com/BalthazarFitzpatrick/smortboard && cd smortboard
   uv sync
   docker build -f docker/card.Dockerfile -t smortboard-card:latest .
   uv run smortboard
   ```

   Opens the board at the printed link. Keep the terminal open.

2. **Repo.** `b` -> from local repo. Needs a GitHub `origin` (private is fine) with its default
   branch pushed. Set the repo's test command, e.g. `uv run pytest -q` - no test command, no
   finished card.

3. **Lab keys.** `claude setup-token` or `codex login` in your terminal. On the board: `shift`+`p`,
   add a profile, paste, activate. Remove the empty `default`. Never your own login.

4. **`h`.** Pre-flight checklist; a red row names its fix. All green: `.` to plan cards, `w` to run
   the board.

Look before you set up: `uv run smortboard --demo` - throwaway board, invented cards, port 8001.
Practice run: `uv run smortboard seed-beta <empty folder>` - a small game repo and 15 written cards
([walkthrough](https://balthazarfitzpatrick.github.io/smortboard/beta-test-board/)).

## Best practices

- **Write the card once, completely.** The agent gets the card and the repo, not your head.
  Criteria a test can check. [Cards](https://balthazarfitzpatrick.github.io/smortboard/practices/cards/)
- **Keep leases narrow, and keep them apart.** Overlapping leases serialise the board; an empty
  lease runs nothing. [Leases](https://balthazarfitzpatrick.github.io/smortboard/practices/leases/)
- **Watch one column.** Attention holds everything that needs you; the rest runs itself.
  [Attention and inbox](https://balthazarfitzpatrick.github.io/smortboard/practices/attention/)
- **Let the two gates judge.** Your tests, then a second agent, before any pull request. An agent
  saying the tests pass is not a pass. [The two gates](https://balthazarfitzpatrick.github.io/smortboard/practices/gates/)
- **Main is yours.** The board lands on `development`; you merge `main`. A hook keeps agents off it.
  [Landing](https://balthazarfitzpatrick.github.io/smortboard/practices/landing/) -
  [protect main](https://balthazarfitzpatrick.github.io/smortboard/protect-main/)
- **Plan in mission control, steer in workforce.**
  [Mission control](https://balthazarfitzpatrick.github.io/smortboard/practices/mission-control/)
- **Budget before you run.** A daily budget per board, a model per role, a fallback in another lab.
  [Cost](https://balthazarfitzpatrick.github.io/smortboard/practices/cost/) -
  [labs](https://balthazarfitzpatrick.github.io/smortboard/practices/labs/)

## More

[Docs](https://balthazarfitzpatrick.github.io/smortboard/) -
[install in full](https://balthazarfitzpatrick.github.io/smortboard/get-started/install/) -
[keyboard](https://balthazarfitzpatrick.github.io/smortboard/reference/keyboard/) -
[configuration](https://balthazarfitzpatrick.github.io/smortboard/reference/configuration/) -
[troubleshooting](https://balthazarfitzpatrick.github.io/smortboard/help/troubleshooting/) -
[security](https://balthazarfitzpatrick.github.io/smortboard/reference/security/)

**Public beta.** One user, daily, on macOS. Found a rough edge?
[Beta and reporting](https://balthazarfitzpatrick.github.io/smortboard/help/beta/).

[MIT](LICENSE)
