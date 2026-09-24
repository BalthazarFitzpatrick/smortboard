# smortboard

**Kanban board that agents work, not you.** Tell mission control what to build; it writes the
cards. Each card's agent works in its own container, on its own branch, and writes the tests for
what it builds. The board runs them, a second agent reviews the diff. Then your choice: the pull
request waits for you, or lands on `development` by itself once both pass. `main` is off limits to
every agent.

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
daily budget per board, a cap per run. Limit hit: wait for the reset, ask you, or switch to your
next profile and then the fallback model.

![The usage panel: rate-limit windows per credential profile, spend by model](docs/images/usage.jpg)

**Settings.** `o` for what every board shares: models per role, what a usage limit does, which
features boards may use. `shift`+`o` for one board: merge mode, file lease, parallel cap, budget.

<table>
<tr>
<td width="50%"><img src="docs/images/settings.jpg" width="100%" alt="Settings every board shares: soft file leases and free merge enabled or disabled, folders mission control can read, where new repos go, how many cards run at once"><br><b>Settings</b><br><code>o</code></td>
<td width="50%"><img src="docs/images/board-settings.jpg" width="100%" alt="One board's own settings: review or free merge, strict or soft file leases, its own cap on cards at once and its daily budget"><br><b>Board settings</b><br><code>shift+o</code></td>
</tr>
</table>

## One key per screen

Keyboard first. `s` lists the keys.

<table>
<tr>
<td width="50%"><img src="docs/images/card.jpg" width="100%" alt="An open card: what it is about, what is done with the test and review results, what it needs from you, and its criteria, lease, dependencies and history"><br><b>Open a card</b><br><code>space</code></td>
<td width="50%"><img src="docs/images/costs.jpg" width="100%" alt="The cost overview: total, accepted and refused spend per card and per pull request, a spend table per board, and spend by lab and model"><br><b>Cost across boards</b><br><code>c</code></td>
</tr>
<tr>
<td width="50%"><img src="docs/images/telemetry.jpg" width="100%" alt="One card's cost per attempt: what each run spent, on which model, and how it ended"><br><b>Card cost</b><br><code>i</code></td>
<td width="50%"><img src="docs/images/replay.jpg" width="100%" alt="Run replay: every step of an attempt, narration and tool calls, stepped through or played back"><br><b>Replay a run</b><br><code>t</code></td>
</tr>
<tr>
<td width="50%"><img src="docs/images/pulls.jpg" width="100%" alt="The pull requests panel: open pull requests across three boards in merge order, one conflicting, one waiting on a dependency"><br><b>Pull requests</b><br><code>v</code></td>
<td width="50%"><img src="docs/images/digest.jpg" width="100%" alt="The morning digest: open pull requests in merge order, then the blocked cards waiting on you"><br><b>Morning digest</b><br><code>d</code></td>
</tr>
<tr>
<td width="50%"><img src="docs/images/preflight.jpg" width="100%" alt="The pre-flight checklist: docker, the card image, credentials, gh and git, then each repo's own checks"><br><b>Pre-flight</b><br><code>h</code></td>
<td width="50%"><img src="docs/images/shortcuts.jpg" width="100%" alt="The shortcut overlay: one row per key"><br><b>Every key</b><br><code>s</code></td>
</tr>
</table>

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

2. **Repo.** `b` -> **new board** -> pick a folder, or name a new one. The board adds what is
   missing: git, a `development` branch, a private GitHub repo. It never pushes `main` - when it
   made the GitHub repo, it shows you the one command to run. Or **from online repo**: clone one of
   yours. Tests are required. Has tests: the board finds how to run them. Has none: mission control
   writes a first suite, or you point at your own command.

3. **Lab keys.** `claude setup-token` or `codex login` in your terminal. On the board: `shift`+`p`,
   add a profile, paste, activate. Remove the empty `default`.

4. **`h`.** Pre-flight checklist; a red row names its fix. All green: `.` to plan cards, `w` to run
   the board.

Look before you set up: `uv run smortboard --demo` - throwaway board, invented cards, port 8001.
Practice run: `uv run smortboard seed-beta <empty folder>` - a small game on a private GitHub repo
and 15 written cards; you run one push command ([walkthrough](docs/beta-test-board.md)).

## Best practices

- **Let mission control write the cards.** One pull request each, criteria its own tests check,
  a lease no parallel card shares. Read its plan before it creates them; steer a running card with
  `,`. [Mission control](docs/practices/mission-control.md) - [cards](docs/practices/cards.md) -
  [leases](docs/practices/leases.md)
- **Watch one column.** Attention holds everything that needs you; the rest runs itself.
  [Attention and inbox](docs/practices/attention.md)
- **Let the two gates judge.** Your tests, then a second agent, before any pull request. An agent
  saying the tests pass is not a pass. [The two gates](docs/practices/gates.md)
- **Main is yours.** The board lands on `development`: by itself on a free-merge board once tests
  and review pass, or when you accept each card on a review-required board. A hook keeps agents off
  `main`.
  [Landing](docs/practices/landing.md) -
  [protect main](docs/protect-main.md)
- **Budget before you run.** A daily budget per board, a model per role, a fallback in another lab.
  [Cost](docs/practices/cost.md) -
  [labs](docs/practices/labs.md)

## More

[All docs](docs/README.md) -
[install in full](docs/get-started/install.md) -
[keyboard](docs/reference/keyboard.md) -
[configuration](docs/reference/configuration.md) -
[troubleshooting](docs/help/troubleshooting.md) -
[security](docs/reference/security.md)

**Public beta.** One user, daily, on macOS. Found a rough edge?
[Beta and reporting](docs/help/beta.md).

[MIT](LICENSE)
