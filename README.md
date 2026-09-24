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

![The usage panel: five-hour and seven-day windows per lab, spend by model](docs/images/usage.jpg)

## One key per screen

Every screen is one key. `s` lists them all, read from the same table the keys run on. Letters are
commands, `/` starts typing. Clicks always work. Hover-focus and right-click stay off until you turn
the mouse on in `o` (general -> mouse). Keys sit on the physical key, so a non-US layout does not
move them. `n` inbox and `u` usage are above.

<table>
<tr>
<td width="33%"><img src="docs/images/costs.jpg" width="100%" alt="The cost overview: total, accepted and refused spend per card and per pull request, a spend table per board, and spend by lab and model"><br><code>c</code> cost across boards</td>
<td width="33%"><img src="docs/images/settings.jpg" width="100%" alt="The settings panel: the mouse set to disabled, folders mission control can read, how many cards run at once globally and per board, and the file lease mode per board"><br><code>o</code> settings</td>
<td width="33%"><img src="docs/images/digest.jpg" width="100%" alt="The morning digest: open pull requests in merge order, then the blocked cards waiting on you, each with its reason and the board's note"><br><code>d</code> morning digest</td>
</tr>
<tr>
<td width="33%"><img src="docs/images/pulls.jpg" width="100%" alt="The pull requests panel: open pull requests across three boards in merge order, one marked conflicting, one waiting on a dependency that has not merged"><br><code>v</code> pull requests in merge order</td>
<td width="33%"><img src="docs/images/shortcuts.jpg" width="100%" alt="The shortcut overlay on its panels page: one row per key, from u for usage to 1 .. 9 for jumping between boards"><br><code>s</code> every key</td>
<td width="33%"><img src="docs/images/preflight.jpg" width="100%" alt="The pre-flight checklist: 30 of 31 ready, green rows for docker, the card image, the token, both credential profiles, gh and git, then each repo's own checks"><br><code>h</code> pre-flight checklist</td>
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
