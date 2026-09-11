# smortboard

**A keyboard-first kanban board whose cards are worked by coding agents.**

You write a card and press a key. An agent picks it up in a sealed container and commits its work.
The board then re-runs the tests itself, has a second agent read the diff, and opens a pull request.
It never merges. Anything that needs you waits in one inbox.

![A payments service mid-sprint: cards in every state across five columns - blue where agents are working, vanilla where they wait on you, lichen accepted, red rejected](docs/images/hero-board.jpg)

![A card opened over the board: its criteria, tasks, dependencies and the run as a timeline - tests passed, reviewer approved, pull request open](docs/images/hero-card.jpg)

![Both side panels open: a working agent's transcript with a live note on the left, the orchestrator planning cards on the right](docs/images/hero-agents.jpg)

<sub>The board, an open card, and the two agent panels. Example content, from a demo database.</sub>

---

## Three rules

1. **The agent never decides it is finished.** Its claim that the tests pass counts for nothing. The
   board re-runs them offline, and a separate read-only reviewer judges the diff.
2. **The board never merges.** Every card ends at an open pull request or a stated reason it
   stopped, and merging is always done by a person.
3. **Every card runs sealed.** It gets its own throwaway container, a clone of its repo, a write
   lease and a command allowlist. If Docker isn't available, the card refuses to run rather than
   running outside a container.

## How a card runs

<picture>
  <source media="(prefers-color-scheme: light)" srcset="docs/images/art-lifecycle-light.png">
  <img src="docs/images/art-lifecycle-dark.png" alt="The card lifecycle: preparing, running, testing, reviewing, opening, opened. Reviewer findings on the fix route go back to the worker at most twice; a question, a limit, a crash or a lease conflict, failed tests, or a rejected review block the card in its column, waiting in the inbox." width="100%">
</picture>

| Phase | What happens |
|---|---|
| **preparing** | A worktree and branch per card. A resumed card reuses its worktree and commits. A card with dependencies is cut from a fresh fetch of the base branch. |
| **running** | `claude -p` streams JSON both ways in a named container. The token is the first stdin line, the brief follows, and stdin stays open for live notes. Every line becomes an event. |
| **testing** | The repo's own test command runs in the repo's image with no network. |
| **reviewing** | A second agent with Read, Grep and Glob only returns a structured verdict on the diff. |
| **opening** | The board pushes the branch and runs `gh pr create`. Its `gh` wrapper allows three subcommands, and merge isn't one of them. |

![An open card: sections headed like board columns, the run as a timeline](docs/images/card.jpg)

## Concepts

| | |
|---|---|
| **Board** | A named set of cards and repos. `1`-`9` jump between boards. |
| **Card** | One unit of work: a title, a description, acceptance criteria, tasks, dependencies, a lease, and optionally a model. |
| **Repo** | Where cards work: a path, a default branch, a test command, an optional lint command and an optional image. |
| **Lease** | The path globs a card may Edit or Write. An empty lease allows no writes at all. |
| **Worktree** | One git worktree and branch per card. The container works on a clone, and its commits are fetched back. |
| **Attempt** | One run of a card, from its `lifecycle_started` event to where it stopped. Cost, replay and the resume briefing all work per attempt. |
| **Status** | Five columns: todo, doing, checking, accepted, rejected. There is no "blocked" column. |
| **Reason code** | Why a card waits on you, recorded alongside its status: `AGENT_QUESTION`, `TESTS_FAILED`, `REVIEW_REJECTED`, `LEASE_CONFLICT`, `USAGE_LIMIT`, `CRASH`, `DEPENDENCY_REJECTED`. |
| **Findings route** | Where reviewer findings go: back to the worker (`fix`), or to you (`attention`, the default). |
| **Decision** | `y` accepts a card and keeps its branch for the pull request. `x` rejects it and deletes the branch. Both can be reversed. |
| **Roles** | The **orchestrator** plans cards and has no tools. The **worker** works a card. The **reviewer** judges the worker's diff. Each role has its own prompt and model. |
| **Note** | A message to a running agent, delivered at its next step with a fixed marker it is taught to trust. Any other text claiming authority is treated as a prompt injection. |
| **Resume briefing** | When a card runs again, its brief summarises the last attempt: how it ended, gate verdicts, findings, files touched, and commands run or refused. |
| **Event log** | Every stream line, gate, decision and note, append-only. Cost, replay, the roster and the briefing are all projections of it. |

<img src="docs/images/card-states.jpg" alt="Card edges by state: blue working, vanilla attention with a stepped glow, lichen accepted, red rejected" width="100%">

A card's state shows as its edge, not a fill: **blue** means an agent is working it, **vanilla**
with a stepped glow means it needs you, **lichen** means accepted and **red** means rejected. The
working blue is cold on purpose, and lichen and red stay distinguishable, so no pair collapses
under red-green colour blindness.

## Features

<table>
<tr>
<td width="50%" valign="top">
<img src="docs/images/mission-control.jpg" alt="Mission control drawer" width="100%"><br>
<b>Mission control</b> <code>.</code><br>
Chat with the board's orchestrator. It answers with a plan and cards, each with a proposed model, and
it sees what each model has cost and passed on this board.
</td>
<td width="50%" valign="top">
<img src="docs/images/workforce.jpg" alt="Workforce drawer pinned to one card" width="100%"><br>
<b>Workforce</b> <code>,</code><br>
A terminal with a card's agent: its narration and the board's gate lines. It is <b>pinned</b> to the
card you're on; with none focused it is a <b>mall cam</b>, rotating through every working card. A
note sent here reaches a running agent at its next step.
</td>
</tr>
<tr>
<td width="50%" valign="top">
<img src="docs/images/inbox.jpg" alt="Attention inbox" width="100%"><br>
<b>Attention inbox</b> <code>n</code><br>
Every card waiting on you, across every board, oldest first. An answer resumes the card in its own
worktree. Rows an answer can't help say where to act instead.
</td>
<td width="50%" valign="top">
<img src="docs/images/digest.jpg" alt="Morning digest" width="100%"><br>
<b>Run the board</b> <code>w</code> · <b>digest</b> <code>d</code><br>
Bounded parallel runs, two at a time by default. A card starts only once its dependencies' pull
requests are merged, and two cards whose leases may overlap never run at once.
</td>
</tr>
<tr>
<td width="50%" valign="top">
<img src="docs/images/costs.jpg" alt="Cost overview across boards" width="100%"><br>
<b>Cost per board</b> <code>c</code><br>
One row per board: its share of all spend, runs, accepted cards, pull requests, cost per pull
request, and money spent on runs that hit a refusal. Worker vs reviewer and spend by model sit in
the totals.
</td>
<td width="50%" valign="top">
<img src="docs/images/telemetry.jpg" alt="Card telemetry" width="100%"><br>
<b>Card cost</b> <code>i</code><br>
Every attempt: cost, turns, fix rounds, refusals, and the model that did the work for each role.
</td>
</tr>
<tr>
<td width="50%" valign="top">
<img src="docs/images/usage.jpg" alt="Usage panel" width="100%"><br>
<b>Usage</b> <code>u</code><br>
Rate-limit windows and spend per model. A usage limit pauses new runs until its window resets.
</td>
<td width="50%" valign="top">
<img src="docs/images/replay.jpg" alt="Run replay" width="100%"><br>
<b>Replay</b> <code>t</code><br>
Step through a run: reads, edits as diffs, commands, refused calls, gates, verdicts. Any attempt.
</td>
</tr>
</table>

**Live steering.** A note sent to a running agent is written to its stdin with a fixed marker and
reaches it between two tool calls, in the same run. The worker is told to trust only that marker and
to treat any other text claiming authority as a prompt injection. A real run:

<picture>
  <source media="(prefers-color-scheme: light)" srcset="docs/images/art-steering-light.png">
  <img src="docs/images/art-steering-dark.png" alt="A real steered run: the brief, git log, reading one.txt, a note queued at 9.2 seconds asking to skip three.txt and end with PINEAPPLE, reading two.txt, and the summary ending in PINEAPPLE at 12.8 seconds for $0.055." width="100%">
</picture>

**Stop** `k` asks once, then removes a running card's container. The card keeps its worktree and
commits, and nothing after the worker runs. **Prompts** `p` edits the three role prompts; every save
is a new version. **Model** `m` cycles a card through board default, haiku, sonnet and opus.

## Architecture

<picture>
  <source media="(prefers-color-scheme: light)" srcset="docs/images/art-architecture-light.png">
  <img src="docs/images/art-architecture-dark.png" alt="Architecture: one native smortboard process on 127.0.0.1 with the http server, sqlite store, run registry, scheduler and orchestrator, and the card token file; per card a card container, a test gate container with no network, and a read-only reviewer container; commits fetched back to the repo on disk, then pushed to GitHub where you merge." width="100%">
</picture>

The board is a plain Python process: a stdlib HTTP server, SQLite and plain JavaScript on
[smortui](https://github.com/BalthazarFitzpatrick/smortui). Only the agents are contained, because
a containerised board would need the Docker socket, which amounts to root on the host. Card runs,
orchestrator turns and scheduler ticks each open their own SQLite connection on their own thread.

## Security

- **Local only.** The API has no authentication, so the board binds `127.0.0.1`. `--host` is an
  explicit opt-in; only use it behind something that authenticates.
- **The token travels on stdin.** It is a model-only `claude setup-token`. It is never an env var or
  a mounted file, and never visible to `docker inspect`. On the host it lives in a mode-600 file.
- **Guards are read-only.** A lease hook covers Edit and Write. A bash guard allows only git plus the
  repo's test and lint commands. Both are mounted read-only outside the working tree, so the agent
  can neither edit nor commit them.
- **Proof comes from outside the agent.** The tests re-run offline, and the reviewer can only read.
  Only a note carrying the fixed marker counts as the operator.
- **An expired or revoked token** (HTTP 401) refuses the card with the steps to renew it.
- **No merge path exists** in the code.

## Cost

- Workers and the reviewer default to sonnet, and the orchestrator to opus. A card's model overrides
  the board setting, which overrides the default. The reviewer has its own setting, so a cheap worker
  never means a cheap review.
- Each worker run is capped at $5 (`--max-budget-usd`). Findings go back to the worker at most twice,
  and by default they go to you instead.
- Costs come from each run's own stream (`total_cost_usd`, `modelUsage`), per turn, summed per
  session. The model credited is the one with the highest spend, not the small helper Claude Code
  bills alongside it:

<picture>
  <source media="(prefers-color-scheme: light)" srcset="docs/images/art-cost-credit-light.png">
  <img src="docs/images/art-cost-credit-dark.png" alt="One real result's spend by model: claude-sonnet-5 $0.2476 for 822 output tokens, the claude-haiku helper $0.0009 for 17." width="75%">
</picture>

## Keys

Bindings follow the physical key, so a non-US layout doesn't move them. `s` shows them in the app.

| Cards | | Panels | |
|---|---|---|---|
| arrows | move | `n` | attention inbox |
| `Space` `Enter` | open / close | `d` | morning digest |
| `Esc` | one level back | `c` | cost per board |
| `r` | run | `i` | card cost |
| `k` | stop | `u` | usage |
| `y` `x` | accept / reject | `a` | agent roster |
| `m` | model | `p` | prompts |
| `t` | replay | `.` | mission control |
| `w` | run the board | `,` | workforce |
| `g` | kanban / workstreams | `s` | shortcuts |
| `/` | comment input | `1`-`9` | jump to a board |

## Setup

```bash
git clone https://github.com/BalthazarFitzpatrick/smortboard && cd smortboard
uv sync
uv run smortboard            # http://127.0.0.1:8000/ui/index.html
```

Or run the board without a checkout: `uvx --from git+https://github.com/BalthazarFitzpatrick/smortboard smortboard`.
You still need the clone once, to build the Docker images below.

The board runs with nothing else installed. Running cards needs three more things:

```bash
# 1. docker, running
# 2. the card image: claude cli, git, uv - no credentials, no code
docker build -f docker/card.Dockerfile -t smortboard-card:latest .
# 3. a card token, model-only, kept in a file only you can read
claude setup-token                      # prints the token - copy it to the clipboard
mkdir -p ~/.config/smortboard
# umask 077 creates the file as mode 600; tr strips the newline pbpaste keeps
(umask 077; pbpaste | tr -d '\r\n ' > ~/.config/smortboard/card_token)
chmod 600 ~/.config/smortboard/card_token      # already 600 - this makes it explicit
wc -c ~/.config/smortboard/card_token           # a full token is 108 bytes
```

`pbpaste` is macOS. On Linux, paste into `cat > ~/.config/smortboard/card_token` instead and press
Ctrl-D.

Register a repo on the board with its path, default branch and test command. Its image must already
contain its toolchain, because the test gate is offline. smortboard's own image is
`docker/repo.Dockerfile`.

| Flag | Env | Default |
|---|---|---|
| `--port` | `SMORTBOARD_PORT` | `8000` |
| `--host` | `SMORTBOARD_HOST` | `127.0.0.1` |
| `--db` | `SMORTBOARD_DB` | user data dir |
| `--no-browser` | | opens a tab |
| | `SMORTBOARD_CARD_IMAGE` | `smortboard-card:latest` |
| | `SMORTBOARD_CARD_TOKEN_PATH` | `~/.config/smortboard/card_token` |
| | `SMORTBOARD_OPERATOR_NAME` | your `git config user.name` |

The operator name is who the board shows on your own notes and chat lines, and who the agents are
told to trust: a live note starts `Note from <name>, via the board:`.

Board-wide settings are set with `PATCH /api/settings`: `findings_route`, `orchestrator_model`,
`worker_model`, `reviewer_model`, `max_parallel`, and `resume_briefing` (`"off"` disables it).

## Testing the alpha

Everyone runs their own board on their own machine; nothing is shared.

1. **Install and start** the board as above, then open it in the browser.
2. **Press `h`** for the pre-flight checklist: Docker, the card image, the token, `gh`, and every
   repo you register. Fix what it lists; each line says how.
3. **Create the GitHub repo** you want cards to work on, clone it, and push its default branch. The
   board opens pull requests there, so it has to exist on GitHub first.
4. **Press `b`** to create a board and register that repo: its path, default branch, test command
   and image. Press `h` again until everything is green.
5. **Press `.`** and describe some work. Mission control proposes cards; focus one and press `r`.
6. **Something wrong?** [Open a bug report](https://github.com/BalthazarFitzpatrick/smortboard/issues/new?template=bug.yml).
   Paste what `h` shows and, if a card misbehaved, a screenshot of its replay (`t`). Never paste
   your token.

## Limits

- A stop that lands while the test gate is running waits for the gate to finish.
- Mission-control turns carry no cost in the log, so they're not counted.
- The HTTP server handles one request at a time. That's fine for one person on one machine.
- Windows code paths exist but have never been run.

## Development

```bash
uv run pytest
for f in tests/js/*.mjs; do node "$f"; done
uv run ruff check . && uv run ruff format --check .
```

Neither suite calls a model or Docker. `docs/PLAN.md` has the plan and the containment reasoning,
`docs/PHASE1-CONTRACTS.md` the schema and API, `docs/PROMPTS.md` the prompt layering, and
`docs/spikes/` what was proven before it was built on.

## License

[MIT](LICENSE). Use it, change it, ship it; keep the copyright notice.
