# smortboard

**Kanban board that agents work, not you.** Write the card, walk away. Agent takes it in its own
container, on its own branch. Board runs your tests, a second agent reviews the diff, pull request
waits for you. `main` stays yours.

![The board mid-sprint: cards in every state across the columns - blue where agents are working, vanilla where they wait on you, lichen accepted, red rejected](images/hero-board.jpg)

## What it does

**The board.** Blue: agent on it. Vanilla: waiting on you. Lichen: accepted. Queueing, retries after
a rate limit, rebasing a waiting pull request: the board does it.

**Mission control and workforce.** `.` - say what to build; the orchestrator drafts the cards, each
with a file lease, a model and its dependencies. `,` - talk to a running agent; the note lands
between two tool calls, the run keeps going.

![Both drawers open: a working agent's transcript on the left, the orchestrator planning cards on the right](images/hero-agents.jpg)

**Attention.** One column to watch. A question, failed tests, a rejected review - anything the board
cannot retry itself - lands there and in the inbox `n`, each with its next step. Answer it, the card
resumes.

![The attention inbox: blocked cards oldest first, each with its reason, its next step and an answer box](images/inbox.jpg)

**Usage and cost.** Rate-limit windows per subscription, spend per model, per card, per board. A
daily budget per board, a cap per run. Limit hit: switch to the fallback model, or ask you first.

![The usage panel: five-hour and seven-day windows per lab, spend by model](images/usage.jpg)

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
([walkthrough](beta-test-board.md)).

## Best practices

- **Write the card once, completely.** The agent gets the card and the repo, not your head.
  Criteria a test can check. [Cards](practices/cards.md)
- **Keep leases narrow, and keep them apart.** Overlapping leases serialise the board; an empty
  lease runs nothing. [Leases](practices/leases.md)
- **Watch one column.** Attention holds everything that needs you; the rest runs itself.
  [Attention and inbox](practices/attention.md)
- **Let the two gates judge.** Your tests, then a second agent, before any pull request. An agent
  saying the tests pass is not a pass. [The two gates](practices/gates.md)
- **Main is yours.** The board lands on `development`; you merge `main`. A hook keeps agents off it.
  [Landing](practices/landing.md) -
  [protect main](protect-main.md)
- **Plan in mission control, steer in workforce.**
  [Mission control](practices/mission-control.md)
- **Budget before you run.** A daily budget per board, a model per role, a fallback in another lab.
  [Cost](practices/cost.md) -
  [labs](practices/labs.md)

## More

[install in full](get-started/install.md) -
[keyboard](reference/keyboard.md) -
[configuration](reference/configuration.md) -
[troubleshooting](help/troubleshooting.md) -
[security](reference/security.md)

**Public beta.** One user, daily, on macOS. Found a rough edge?
[Beta and reporting](help/beta.md).

[MIT](https://github.com/BalthazarFitzpatrick/smortboard/blob/main/LICENSE)
