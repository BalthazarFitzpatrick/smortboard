# smortboard

**A kanban board that agents work, not you.** You write the card. An agent takes it in its own
container, on its own branch. The board runs the tests, a second agent reviews the diff, and a pull
request waits for you. Nothing lands on `main` unless you merge it.

![A payments service mid-sprint: cards in every state across the columns - blue where agents are working, vanilla where they wait on you, lichen accepted, red rejected](docs/images/hero-board.jpg)

**Define the work, then walk away.** Blue cards have an agent on them. Vanilla cards wait on you,
and that column is the only one you need to watch. Everything else is the board keeping itself
busy: queueing, retrying after a rate limit, rebasing a waiting pull request.

![Both drawers open: a working agent's transcript on the left, the orchestrator planning cards on the right](docs/images/hero-agents.jpg)

**Plan in one chat, steer in the other.** Tell mission control what you want built and it drafts
the cards, with a file lease and a model for each (right). Talk to any agent while it works; your
note reaches it between two tool calls, without stopping the run (left).

<sub>Every screenshot on this page is the built-in demo board: invented projects, invented cards.</sub>

> **Public beta.** Run daily on real repos by one person on macOS. Nobody else has tested it, so the
> rough edges are the ones a single operator never hits. See [Beta](#beta) for what is solid, what
> is not, and how to file a report.

**Contents** ·
[Quick start](#quick-start) ·
[Install](#install) ·
[Concepts](#concepts) ·
[How to use it well](#how-to-use-it-well) ·
[Keyboard](#keyboard) ·
[Security](#security) ·
[Beta](#beta) ·
[Reference](#reference) ·
[Troubleshooting](#troubleshooting)

---

## Quick start

Four commands, then look before you build anything real:

```bash
git clone https://github.com/BalthazarFitzpatrick/smortboard && cd smortboard
uv sync
docker build -f docker/card.Dockerfile -t smortboard-card:latest .
uv run smortboard --demo
```

That prints and opens `http://127.0.0.1:8001/ui/index.html?key=...`: a throwaway demo board with
three invented projects and cards in every state, on port 8001 so it never touches your real one.
`--demo` ignores `--db` and `SMORTBOARD_DB`, never looks at the user data dir, and writes only to a
fresh file in a temp directory that dies with the process. Its runs and pull requests are seeded
history, so `r` there would need Docker and a real repo. Use it to learn the keys and read the
concepts below against something real.

### Your first board

`uv run smortboard` (no `--demo`) starts your actual board on port 8000. Three things before a card
can run, and the pre-flight checklist (`h`) names whichever is missing:

1. **A lab credential.** `shift`+`p`, add a profile, paste what `claude setup-token` or
   `codex login` gives you. Never your own login; [Install](#install) says exactly what to paste.
2. **Docker running**, so a card has somewhere to execute.
3. **A board and a repo.** `b` -> **from local repo** -> pick a clone of a GitHub repo with its
   default branch pushed. On the repo's row, set the command that runs its tests, e.g.
   `uv run pytest -q`. **A repo without a test command cannot finish a card**; the gate has nothing
   to run.

Then:

1. `.` and tell mission control what you want built. It answers with a plan and proposed cards.
2. Focus a card and press `r` (it asks once), or `w` to run the whole board.
3. Anything that needs you lands in the inbox, `n`. Boards default to review-required: read the
   pull request, then accept with `y` to land it on an unprotected base. Main stays yours.

---

## Install

### Requirements

| Tool | Why | Get it |
|---|---|---|
| Python 3.11+ and **uv** | runs the board | [uv install](https://docs.astral.sh/uv/getting-started/installation/) |
| **Docker** (Desktop or Engine), running | every card runs in its own container | [Docker Desktop](https://www.docker.com/products/docker-desktop/) |
| **GitHub CLI**, logged in | the board opens pull requests with it | [cli.github.com](https://cli.github.com/), then `gh auth login` |
| git | worktrees, branches, pushes | usually already there |

And at least one lab, for the agent inside the container. Both CLIs are installed in the card image
already; what you need locally is whichever one mints the credential:

| Lab | Install locally | To get a credential |
|---|---|---|
| **Claude Code** | `npm install -g @anthropic-ai/claude-code` | `claude setup-token` |
| **OpenAI Codex** | `npm install -g @openai/codex` or `brew install codex` | `codex login`, which writes `~/.codex/auth.json` |

One lab is enough to run the board. A second buys you an independent reviewer and somewhere to fall
back when the first is rate-limited.

Only Python and uv are needed to open the board. The rest is needed before a card can run, and the
pre-flight checklist (`h`) tells you exactly what is missing and how to fix each one.

### Steps

**1. Clone and build the card image** (git, uv, Claude Code and Codex, no credentials):

```bash
git clone https://github.com/BalthazarFitzpatrick/smortboard && cd smortboard
uv sync
docker build -f docker/card.Dockerfile -t smortboard-card:latest .
```

**2. Start the board:**

```bash
uv run smortboard
```

It prints and opens `http://127.0.0.1:8000/ui/index.html?key=...`. The key is swapped for a cookie
on the first load and dropped from the address bar, so a tab opened by hand has no key. Use the
printed link. Keep the terminal open: closing it stops the board and any running card.

**3. Give cards a credential**, in the board itself. Press `shift`+`p` for credential profiles, add
one, and paste. The board writes the mode-600 file for you and refuses anything a file others could
read.

Cards never use your own login. What you paste is:

- **Claude**: the token `claude setup-token` prints once. It prints it, you copy it, the board
  stores it. A model-only token, not your Claude Code session.
- **OpenAI**: either **ChatGPT login JSON**, which is the whole contents of the `~/.codex/auth.json`
  that `codex login` wrote (paste it entire; a bare access token from inside it is refused), or an
  **API key**.

At run time the credential goes into the container over stdin, into a memory-backed home that dies
with the container. Your own `~/.claude` and `~/.codex` are never mounted.

Several profiles per lab are fine; the board rotates to the next one when the active profile hits
its rate limit. The manual file layout, the OS credential store and the Windows and Linux paths are
under [Card token](#card-token).

**4. Keep `main` for people.** Agents, the board's and your own Claude Code or Codex sessions, merge
into `development`. A person merges into `main`. One hook script,
[`tools/claude-hooks/protect-main.sh`](tools/claude-hooks/protect-main.sh), refuses the rest in both
CLIs: committing on `main`, pushing to it by any refspec, and merging a pull request whose base is
not `development`. It needs `bash`, `jq` and a logged-in `gh`.

```bash
mkdir -p ~/.claude/hooks ~/.codex/hooks
cp tools/claude-hooks/protect-main.sh ~/.claude/hooks/
cp tools/claude-hooks/protect-main.sh ~/.codex/hooks/
chmod +x ~/.claude/hooks/protect-main.sh ~/.codex/hooks/protect-main.sh
```

Claude Code, in `~/.claude/settings.json` (merge into a `PreToolUse` list already there):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "bash ~/.claude/hooks/protect-main.sh"}]
      }
    ]
  }
}
```

Codex, in `~/.codex/hooks.json`. Codex sends its shell calls as `Bash` with the same payload shape
and honours the same exit code, so the script is shared:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "^Bash$",
        "hooks": [{"type": "command", "command": "bash ~/.codex/hooks/protect-main.sh", "timeout": 15}]
      }
    ]
  }
}
```

Codex only runs a hook it has recorded as trusted, under `[hooks.state]` in `~/.codex/config.toml`.
Check both: ask the agent to run `git push origin HEAD:main`. It should be refused.

The hook only sees commands an agent runs through its shell tool. Pair it with the GitHub ruleset in
[`tools/github/protect-main.json`](tools/github/protect-main.json), which protects `main` against
everything else. [docs/protect-main.md](docs/protect-main.md) has the `development` branch setup, a
per-repo variant of the hook and the ruleset commands.

No checkout at all: `uvx --from git+https://github.com/BalthazarFitzpatrick/smortboard smortboard`
runs the board, though you still need the clone once to build the image.

---

## Concepts

<picture>
  <source media="(prefers-color-scheme: light)" srcset="docs/images/art-lifecycle-light.svg">
  <img src="docs/images/art-lifecycle-dark.svg" alt="The card lifecycle: preparing, running, testing, reviewing, fixing, opening, then opened or landing. Reviewer findings on the fix route go back to the worker at most twice; a question, a limit, a crash, a lease conflict, failed tests or a rejected review block the card and wait in the inbox." width="100%">
</picture>

Every concept reads in three layers: one line to scan, a short table on how it shapes your work,
and the mechanics folded underneath for when you need them. Each one has a rule that surprises
people, called out on its own.

| Concept | In one line |
|---|---|
| [Board](#board) | a set of cards and the repos they work in |
| [Card](#card) | one feature-sized piece of work, defined up front |
| [Statuses](#statuses-and-why-there-is-no-blocked) | five stored states, with blocked as a flag on top |
| [Attention](#the-attention-column) | the one column you actually watch |
| [Leases](#leases) | the paths a card may write, and what lets cards run side by side |
| [Runs and the event log](#runs-attempts-and-the-event-log) | every attempt, kept once, projected everywhere |
| [The two gates](#the-two-gates-and-the-reviewers-verdict) | your tests and a second agent, before any pull request |
| [Landing](#the-landing-lock-and-the-push-queue) | one lock per branch, and never `main` |
| [Current pull requests](#keeping-a-waiting-pull-request-current) | waiting branches follow their base |
| [Mission control and workforce](#mission-control-and-the-workforce-chat) | plan in one chat, steer in the other |
| [Inbox](#the-inbox) | everything waiting on you, oldest first |
| [Credential profiles](#credential-profiles) | several subscriptions per lab |
| [Models and labs](#openai-models) | a lab and model per role, with fallbacks |
| [Cost](#cost-and-telemetry) | what each card and board spent, and the caps |
| [Card edges](#card-edges) | a card's state is its edge colour |

### Board

**A named set of cards and the repos they work in.** `1`-`9` jump between boards.

| | |
|---|---|
| **Work with it** | One board per repo, or per product when the work crosses repos. |
| **Encourages** | A queue you can leave running, with limits that belong to that queue. |
| **Pays off as** | One board can run hot while another stays cautious, without either touching the other. |

<details>
<summary><b>The details</b></summary>

A board can hold several repos. It carries its own parallelism cap, its own daily spend budget, its
merge mode (review required or free merge) and its lease mode (strict or soft).

</details>

### Card

**One feature-sized piece of work, defined up front.** The detail you put in is what the agent has.

| | |
|---|---|
| **Work with it** | Write it once, completely: a short title, criteria your tests can check, a lease. Then leave it. |
| **Encourages** | Work sized to one pull request and decided before it starts, not steered while it runs. |
| **Pays off as** | Cards that finish unattended, and a history that reads like a changelog. |

> [!IMPORTANT]
> A card is not a conversation. It is feature-sized, not edit-sized, and nothing you meant but did
> not write reaches the agent.

![A card opened over the board: what it is about, what it has done and what it needs down the left; its criteria, lease and history down the right](docs/images/hero-card.jpg)

Open a card and it reads top to bottom: **about** (what it is for), **done** (what the agent
delivered, with the test and review verdicts), **needs** (the one thing it wants from you, if
anything). Its criteria, lease and history sit on the right. The arrow keys move between those
sections with the same highlight frame the board uses.

<details>
<summary><b>The details</b></summary>

A card carries a title and description, **acceptance criteria**, a task list, **leases** (the paths
its agent may write), dependencies on other cards, optionally a **model**, and a **complexity**
rating of low, medium or high used for cost analysis.

**Card text is short.** A title is at most 8 words, a description at most 20, each criterion at most
12. Mission control and fold are told so, and the board leaves a note when a card runs long.
`POST /api/cards`, and a `PATCH` that changes a title or description, answer with the card plus a
`warnings` list, empty when it fits. Nothing is refused or cut.

</details>

### Statuses, and why there is no "blocked"

**Five stored statuses: `todo`, `doing`, `checking`, `accepted`, `rejected`.** Blocked is a flag.

| | |
|---|---|
| **Work with it** | Read the reason code, not the column. It says what to do next. |
| **Encourages** | Resuming over restarting. |
| **Pays off as** | A blocked card picks up exactly where it stopped, with its worktree and commits intact. |

> [!IMPORTANT]
> "Blocked" is not a status. A blocked card keeps whatever status it was in and raises a
> `blocked_reason_code` alongside it. A sixth status would throw away what the card was doing, which
> is exactly what the resume briefing needs to restart it.

<details>
<summary><b>The ten reason codes</b></summary>

| Code | What happened |
|---|---|
| `AGENT_QUESTION` | the agent stopped to ask you something |
| `TESTS_FAILED` | the board re-ran the repo's tests and they failed |
| `BASE_RED` | the tests fail, but they fail the same way on the base without the card's changes |
| `REVIEW_REJECTED` | the reviewer refused the diff |
| `LEASE_CONFLICT` | it committed a path outside its lease, or a refused write left it with nothing committed |
| `MERGE_CONFLICT` | its branch no longer merges cleanly with the base |
| `DEPENDENCY_REJECTED` | a card this one depends on was rejected |
| `USAGE_LIMIT` | the credential hit its rate limit |
| `API_UNREACHABLE` | the API was unreachable; the board retries this one itself, with backoff |
| `CRASH` | the run died, or the board was restarted mid-run |

</details>

### The attention column

**The one column you watch.** Anything that needs you lands here; everything else runs itself.

| | |
|---|---|
| **Work with it** | Glance at it, act, leave. `n` gives the same cards as a list. |
| **Encourages** | Walking away. The board, not you, tracks what is running. |
| **Pays off as** | "What is waiting for me?" answered by one look at the board, not by opening cards. |

> [!IMPORTANT]
> Attention is not a status. It is a presentation column between doing and checking, showing any
> card with a blocked reason or a review flag, whatever real status it holds underneath.

<details>
<summary><b>The details</b></summary>

The board shows **six** columns: `todo`, `doing`, `attention`, `checking`, `accepted`, `rejected`.
Accept or reject a card in attention and it leaves.

A card the board is already retrying on its own (an `API_UNREACHABLE` backoff, a `MERGE_CONFLICT`
auto-resume, a usage limit with a retry scheduled) stays out of the column until those automatic
attempts are spent, so the column never fills with things nobody needs to touch yet.

Pressing `w` shows a fourth state: a card the run-all queue is holding, whether it is next in line
or waiting on a lease clash, an unmet dependency or a limit, draws in `doing` with a grey edge -
pending, not blocked - under the cards an agent is actually running. It goes back to its own column
the moment the run stops. Nothing about the card is written; this is presentation only.

</details>

### Leases

**The paths a card may write.** Narrow leases are also what let cards run side by side.

| | |
|---|---|
| **Work with it** | Name the files the card really touches: `src/refunds/**`, `tests/test_refunds.py`. |
| **Encourages** | Small, file-scoped cards that do not step on each other. |
| **Pays off as** | Parallel runs, and a boundary no note or argument with the agent can move. |

> [!IMPORTANT]
> Two cards in one repo whose leases could touch the same file never run at the same time.
> Overlapping leases silently serialise your board. And an empty lease allows nothing, so a card
> without one is refused rather than run.

**Two modes, set per board** in `o` under *file leases*:

| Mode | A card may write | Use it when |
|---|---|---|
| **strict** (default) | its own lease, plus paths you approved for the whole repo | you want every file decided up front |
| **soft** | also any other repo path that is not protected and that no other active card holds | the card knows better than its lease where a fix belongs |

Soft never reaches the protected paths: agent settings and hooks (`.claude/`, `.codex/`, `.git/`,
`.vscode/`, `.husky/`, `.pre-commit-config.yaml`), CI (`.github/`), the next agent's orders
(`CLAUDE.md`, `AGENTS.md`), what the host builds or installs from (Dockerfiles, `docker/`,
`pyproject.toml`, `uv.lock`, `package*.json`, `requirements*.txt`, `.gitignore`, `.gitattributes`,
`.gitmodules`) and secrets (`.env*`, `*.pem`, `*.key`). A lease that names one of them explicitly
still allows it. Every path a soft card reaches beyond its lease shows on the card under needs, and
the reviewer is told about each one.

<details>
<summary><b>The details</b></summary>

A lease is a list of gitignore-style globs relative to the repo root. `*` stays inside one folder,
`**/` is any depth, a trailing `**` is everything below.

A `PreToolUse` hook checks every Edit and Write against the lease before it lands, and after the run
the board checks every committed path against it again. The hook, the Codex guard and that post-run
check share one rule, so they cannot disagree.

**A refused write does not sink committed work.** If the agent was refused a path but committed its
work, and the committed paths pass the post-run check, the card goes on to the test gate and the
reviewer. The paths it wanted wait under needs. Only a run that committed nothing, or that committed
outside its lease, blocks as `LEASE_CONFLICT`.

**Only you can widen a lease.** The guard reads the card's stored lease rows, never a note or a
message. Approving a lease from the inbox can also *remember* those paths for the whole repo, so
later cards are not asked again; remembered paths count in the hook and in the post-run check.

Leases guard writes only. Reads are not limited, and shell commands go through a separate allowlist
scoped to exactly the repo's declared `test_command` (and `lint_command`, if set) plus git - never a
bare `Bash`. If the declared command runs a formatter in check-only form (`ruff format --check ...`),
the card is also granted that formatter's write form, so it can fix what it finds rather than only
report it; an out-of-lease path it reformats is still refused by the post-run lease check. The real
boundary is the container.

</details>

### Runs, attempts, and the event log

**Every line of every attempt, stored once.** Cost, replay and the briefing are views of that log.

| | |
|---|---|
| **Work with it** | `t` replays any attempt; `i` prices it. Re-run freely, nothing is overwritten. |
| **Encourages** | Trying again after a block instead of starting a new card. |
| **Pays off as** | Cost, replay, the roster, the digest and the resume briefing can never disagree. |

<details>
<summary><b>The details</b></summary>

Every stream line, gate, decision and note is appended to a per-card **event log**, and nothing else
is stored as a second copy. An **attempt** is one run of a card: one start event and everything that
follows it. A card that was blocked, answered and re-run has several attempts, and they all stay:
cost, replay and the resume briefing all work per attempt, and a re-run never erases an earlier one.

**A re-run does not pay for the agent twice.** When the branch is exactly where the agent last left it
cleanly and nothing new was said to it, a re-run skips the agent and goes straight to the gates. A
rebuilt image or a changed test command counts as a reason to test again; the same code, the same
image and the same command after a failed gate is refused as "nothing has changed". A run silent for
20 minutes is stopped and retried as an outage, and a Codex run is capped at 60 minutes.

A card passes through these phases: `preparing`, `running`, `testing`, `reviewing`, `fixing`,
`opening`, then one of `opened`, `blocked`, `refused`, `stopped`.

| Phase | What happens |
|---|---|
| **preparing** | a git worktree and branch per card. A resumed card reuses its worktree and commits; a card with dependencies is cut from a fresh fetch of the base. |
| **running** | the agent works in a named container. The token arrives on stdin, the brief follows, and stdin stays open so a live note can reach it. Every line becomes an event. |
| **testing** | the repo's own test command runs against what the card committed, in the repo's image, with no network and no credential. |
| **reviewing** | a second, read-only agent reviews the diff. |
| **fixing** | only on the `fix` findings route: findings go back to the worker, at most **twice**, then the gates run again. |
| **opening** | the board pushes the branch and opens a pull request. |
| **opened** | on a `main` base the card ends here. On a development base the board **lands** it (see the landing lock). |

</details>

### The two gates, and the reviewer's verdict

**Your own tests, then a second agent, before any pull request.** Neither trusts the agent's word.

| | |
|---|---|
| **Work with it** | Write criteria your test command actually checks. Let findings come to you first. |
| **Encourages** | Tests as the spec. A criterion with a test behind it is enforced; one without is a note. |
| **Pays off as** | A pull request that already passed your suite and an independent read of the diff. |

> [!IMPORTANT]
> The reviewer does not judge your acceptance criteria. It judges the code. Criteria are the test
> gate's business, so they are only enforced as far as your test command checks them.

<details>
<summary><b>The details</b></summary>

**The test gate** re-runs the repo's own test command against what the card actually committed, in a
container the agent never touched. It needs no credential and no network. An agent reporting "tests
pass" counts for nothing.

**The reviewer** is a second agent with only Read, Grep and Glob, with the working tree mounted
read-only. It answers exactly four questions about the diff - **vulnerability**, **leaked
credential**, **best practice**, **efficiency** - and grades each finding `low`, `medium`, `high` or
`critical`. What blocks the card:

- any finding at `high` or `critical`
- a `leaked_credential` finding at **any** severity, because a model that rates a leaked key "low"
  is not a label to trust
- no usable verdict at all - a crash, an outage, a limit, a budget stop before it answered, or
  unparseable output. A reviewer that fails to deliver a verdict blocks rather than passing quietly,
  but with its own reason (`CRASH`, `API_UNREACHABLE`, `USAGE_LIMIT`), so it retries; only a real
  verdict is a `REVIEW_REJECTED`.

The diff is framed as untrusted data: text inside it asking to be approved is itself reported as a
finding.

Where findings go is a setting: back to the worker to fix (`fix`), or to you (`attention`). The
default is `attention`, because a card quietly fixing its own findings unattended spends a run's
worth of tokens nobody asked for.

</details>

### The landing lock and the push queue

**The board lands cards one at a time per branch, and never on `main`, `master` or `trunk`.**

| | |
|---|---|
| **Work with it** | Keep review required, accept with `y`. Switch a board to free merge with `shift+a` once you trust it. |
| **Encourages** | A `development` branch for agents and a `main` only you merge. |
| **Pays off as** | No two pushes race, a dead process cannot wedge the queue, and main stays yours. |

> [!IMPORTANT]
> `pr merge` is not on the board's `gh` allowlist and cannot be added by a caller. Protected
> branches are refused as a landing target in code, not by habit.

<details>
<summary><b>The details</b></summary>

Other bases follow the board's mode:

- **Review required** (default): a card stops at an open pull request. Accept with `y` to land it
  in the background. If you already merged it on GitHub, accept records that without merging again.
- **Free merge**: a card lands and is accepted once tests and review pass.

`shift+a` changes the focused board's mode after confirmation. A free-merge board shows a burnt-orange
pulsing frame and a text label. Reduced motion keeps a static frame.

Review mode keeps dependent work moving: a child can start on one unmerged parent's branch once
that parent reaches checking. Its PR targets that branch until the parent lands, then moves to the
repo base. Stacks are at most three cards deep. Two unmerged parents wait. A rejected parent blocks
its dependents. A child conflict never undoes a parent's successful landing.

A rejected parent keeps its local branch while undecided children need it. Those children cannot
run against rejected work, and that parent cannot rerun while they remain undecided. Decide the
dependent attempts and create fresh work; the board does not rewrite an existing stack.

The board also keeps one standing pull request from the development branch into `main`, for you
to merge. Accepting a protected-base card records your decision; it cannot merge the PR.

Landing is guarded by the **landing lock**: one holder per repo and target branch, with a FIFO queue
behind it. It is stored in the database, so it survives a board restart, and a holder that stops
heartbeating past its TTL is evicted so a dead process cannot wedge the queue. `q` shows every
holder and the queue behind them.

The lock is not only for cards. Your own agents and sessions outside the board can take the same
lock, which is what keeps them from racing the board's pushes:

```bash
uv run smortboard-land --repo . --target development -- uv run pytest -q
```

It waits for the lock, fetches, merges the target in, runs your test command, pushes, and always
releases. It exits non-zero with the reason on a conflict, a failed test or a rejected push - and it
refuses `main`, `master` and `trunk` outright.

Every `gh` call goes through an allowlist of exactly five subcommands - `pr create`, `pr list`,
`pr view`, `pr close` and `pr edit`, the last one only so a stacked child's pull request can be
re-pointed at the base when its parent lands. A push rejected because the base moved is re-synced
and retried, never forced.

</details>

### Keeping a waiting pull request current

**A pull request waiting on you keeps up with its base.** The diff you review is against today's base.

| | |
|---|---|
| **Work with it** | Nothing to do. Review when you get to it. |
| **Encourages** | Reviewing on your schedule, not the agent's. |
| **Pays off as** | No stale diffs, and conflicts surface as `MERGE_CONFLICT` with a file list, not at merge time. |

<details>
<summary><b>The details</b></summary>

GitHub tells nobody when something merges. There is no push event the board can subscribe to, and it
can only ask about one named pull request at a time. So it watches the thing that actually changes:
the base branch's own commit.

The board fetches each repo's base on a background timer and compares it to the last sha it saw.
Unchanged means there is nothing to do and no branch is touched. When it moves, every card waiting in
checking on that repo gets the base merged into its branch and pushed. A branch that now conflicts
blocks its card `MERGE_CONFLICT` with the file list instead, and the pull request is left alone. The
board's own landings trigger the same sweep directly, so a stack moves within seconds rather than
waiting for the next poll.

This matters more than it sounds. Measured on card `59727ba3` (PR #112): a branch cut at the start
of a run and never updated drifted **34 commits** behind main while its pull request waited, and
conflicted in four files other pull requests had since touched.

</details>

### Mission control and the workforce chat

**Plan in one chat, steer in the other.** They talk to different agents about different things.

| | |
|---|---|
| **Work with it** | `.` to say what you want built and get cards. `,` to talk to one working agent. |
| **Encourages** | Planning in one place, then leaving the agents alone unless something is off. |
| **Pays off as** | Cards with leases, a model and a complexity each, from one conversation. |

> [!IMPORTANT]
> The orchestrator does not create cards. The board does, from its structured reply. It resolves
> repo names against the board's own repos rather than guessing at an unknown one, and drops any
> lease that would cover the whole repo or climb out of it.

<table>
<tr>
<td width="50%" valign="top">
<img src="docs/images/mission-control.jpg" alt="Mission control drawer" width="100%"><br>
<b>Mission control</b> <code>.</code> - plan the board, get cards.
</td>
<td width="50%" valign="top">
<img src="docs/images/workforce.jpg" alt="Workforce drawer pinned to one card" width="100%"><br>
<b>Workforce</b> <code>,</code> - talk to one card's agent while it works.
</td>
</tr>
</table>

<details>
<summary><b>The details</b></summary>

**Mission control** (`.`) is the board's planner. You tell it what you want built; it replies
conversationally and proposes cards, each with leases, a complexity rating and a suggested model. It
runs against fresh read-only clones of the board's repos with Read, Grep and Glob only - it has no
Edit, no Write, no Bash, and it never writes to the board. It sees what each model has cost and
passed on this board, and is told to prefer the cheapest model that has been reaching pull requests
cleanly on cards like this one.

**Folding** (`f`) is the other board-level turn. It proposes which of a board's `todo` cards one
agent should do as a single card, and the board applies it: it creates the merged card, re-points
every dependency at it, and deletes the originals - restorably, so a fold is not a one-way door.
Only `todo` cards fold; a card that is already running holds a branch and a run, and folding it
would orphan both. A proposed group past four cards or twelve criteria is refused whatever the model
suggested, so a fold never turns a queue of small cards into one nobody can finish.

**The workforce chat** (`,`) is a terminal onto one card's agent, pinned to the card you are on. With
no card focused it rotates through every working card. A **note** sent here reaches a *running* agent
between two of its tool calls.

Because a note arrives mid-run, it has to be distinguishable from text in the repo that claims to be
from you. Every run mints its own fresh marker, and the agent is told that only a note carrying that
exact marker is genuinely yours - anything else claiming authority mid-run is to be named as a
suspected prompt injection.

</details>

### The inbox

**Every card on every board that waits on you, oldest first.** Nothing sits in it forever.

| | |
|---|---|
| **Work with it** | `n`, answer the top row, move on. The answer resumes the card. |
| **Encourages** | Short visits instead of watching runs. |
| **Pays off as** | Each row leads with why it waits and the text that matters: the question, the failing output, the findings. |

> [!IMPORTANT]
> Two reasons cannot be answered. `USAGE_LIMIT` clears itself when the window resets, so an answer
> would only confuse the agent; its row offers
> **retry on** the next usable fallback model instead, when there is one. `DEPENDENCY_REJECTED` is not
> this card's fault: fix and accept the card it depends on, and this one un-blocks itself.

<img src="docs/images/inbox.jpg" alt="Attention inbox" width="100%">

<details>
<summary><b>The details</b></summary>

Each row leads with why the card is waiting, then its title and the relevant text: for a question,
the agent's own words; for anything else, the board's note - the failing test output, the review
findings, the crash.

The focused card answers in place, and the answer resumes the card. Six reasons can be resolved by
answering: a question, failed tests, a rejected review, a crash, a lease conflict, a merge conflict.
A card in `checking` waiting for accept or reject is a decision, not a question: it belongs on the
card with `y` or `x`.

A `LEASE_CONFLICT` row is special: it lists exactly the paths the card was refused, and **approve**
adds those and only those to the lease, then resumes. Answering instead tells the agent to leave the
files alone. An answer alone never widens a lease.

</details>

### Credential profiles

**Several subscriptions per lab, one active at a time.** Model-only tokens, never your own login.

| | |
|---|---|
| **Work with it** | `shift`+`p`, one profile per credential, each under its own name. |
| **Encourages** | Spreading work across the subscriptions you already pay for. |
| **Pays off as** | A limit on one account does not stop the board, if you let it rotate. |

> [!IMPORTANT]
> Rotation is off by default. When a profile hits its limit, the board parks new starts on that lab
> until the window resets. Turn on *switch credential profiles automatically* in settings if you
> want rotation. Cards already running are left alone either way.

<details>
<summary><b>The details</b></summary>

Anthropic profiles use a model-only `claude setup-token`, separate from your own Claude login.
Existing token files and settings keep working without a migration step. Each profile is a name plus
its own mode-600 token file. Profiles belong to a lab, with one active profile per lab.

</details>

### OpenAI models

**A lab and a model per role, with a fallback list for each.** Claude Code or Codex, per role.

| | |
|---|---|
| **Work with it** | Strongest model for mission control, the cheapest one that reaches clean pull requests for the worker, another lab for the reviewer. |
| **Encourages** | Picking models from measurements: `i` groups finished cards by model and complexity with their cost. |
| **Pays off as** | An independent reviewer, and somewhere to go when one lab is rate-limited. |

> [!IMPORTANT]
> A usage limit switches a role to its fallback model on its own, by default. Tick *on a usage
> limit, ask me before switching to a fallback model* in `o` and the card waits in the inbox with a
> **retry on** control instead. Either way it re-runs on its own model once the window resets.

<details>
<summary><b>The details</b></summary>

Rebuild the card image with the command above, then open `shift`+`p`, choose `openai` and add a
profile. Choose **ChatGPT login JSON** to import the contents of the `auth.json` created by your Codex
login, or **API key** for an OpenAI key. The board stores the imported credential in its own mode-600
file. At run time it sends the credential over stdin into the container's temporary memory-backed
Codex home. It does not mount your host Codex home.

Open settings to choose a lab and model for each role: worker, reviewer, mission control and fold. A
card's `m` menu overrides the worker default. Labs without a usable profile are disabled in the model
menu.

The board ships with `opus` for mission control and `sonnet` for the worker and reviewer, and those
defaults exist because the roles want different things:

| role | what it does | what that asks for |
|---|---|---|
| mission control | reads the repos, argues about scope, writes the cards | the strongest model you have. It decides what gets built; every other role only executes it |
| worker | one card, in one container, against a stated lease | the cheapest model that has been reaching pull requests without fix rounds. The `i` view tells you which that is on your board |
| reviewer | reads a diff and gives a verdict | a model from **a different lab than the worker**, once you have two. An independent reader is the point of the gate, and two models from one family share their blind spots |
| fold | consolidates the card backlog | the same class as mission control; it is the same kind of judgement on a smaller surface |

A card's own complexity should move the worker, not the board default: raise it for genuine design
work, leave it low for mechanical edits.

Each role can have an ordered fallback list such as `openai/gpt-5.6-sol`. Leave it empty to keep the
role on its chosen lab. Automatic profile rotation stays within a lab; a cross-lab retry needs an
explicit fallback and leaves a message on the card. Codex notes are delivered on the next run.

The board makes that cross-lab switch on its own by default. Tick "on a usage limit, ask me before
switching to a fallback model" in `o` (`usage_limit_route` set to `attention`) and it asks instead:
the card waits in the inbox with a **retry on** control, and still re-runs on its own model once the
window resets.

Models come from `smortboard/labs/catalog.json`. Add or override entries in
`~/.config/smortboard/catalog.json` (under `XDG_CONFIG_HOME` when set):

```json
{
  "openai": {
    "models": [
      {"id": "your-model-id", "label": "Your model", "tier": "standard"}
    ]
  }
}
```

To estimate Codex spend, add `price_per_mtok` with numeric `input`, `cached_input` and `output`
rates to the model entry. Supply your own rates; the packaged catalog assumes none. Estimates carry
`~`; missing prices show `unknown`. An enabled cumulative spend cap refuses new starts when prior
spend is unknown. Codex's per-run watchdog acts when token usage arrives, which the measured CLI
emitted at turn completion, so it cannot guarantee a hard dollar ceiling during the turn. See the
[event spike](docs/spikes/S4-codex-events.md) and [credential spike](docs/spikes/S7-codex-auth.md)
for the measured limits.

</details>

### Cost and telemetry

**What every card and board spent, and three caps to keep it there.**

| | |
|---|---|
| **Work with it** | `i` for one card, `c` for every board, `u` for rate-limit windows. Set caps once. |
| **Encourages** | Treating model choice and card size as costs you can see. |
| **Pays off as** | Wasted spend on refused, crashed or rejected attempts shows up as its own line. |

<table>
<tr>
<td width="50%" valign="top">
<img src="docs/images/costs.jpg" alt="Cost overview across boards" width="100%"><br>
<b>Cost per board</b> <code>c</code>
</td>
<td width="50%" valign="top">
<img src="docs/images/telemetry.jpg" alt="Card telemetry" width="100%"><br>
<b>Card cost</b> <code>i</code>
</td>
</tr>
<tr>
<td width="50%" valign="top">
<img src="docs/images/usage.jpg" alt="Usage panel" width="100%"><br>
<b>Usage</b> <code>u</code>
</td>
<td width="50%" valign="top">
<img src="docs/images/replay.jpg" alt="Run replay" width="100%"><br>
<b>Replay</b> <code>t</code> - step through any attempt: reads, edits as diffs, commands, refused calls, gates and verdicts.
</td>
</tr>
</table>

<details>
<summary><b>The details</b></summary>

`i` shows what one card has cost, attempt by attempt, with turns, fix rounds, refusals and the model
behind each role. `c` shows what each board has cost, with runs, accepted cards, pull requests and
cost per pull request, plus a cost-optimisation view (cap fit by complexity, a suggested cap per
role, and spend wasted on refused, crashed, rejected or capped attempts). `u` shows rate-limit
windows and spend per model.

Three optional spend caps, each independent:

| Cap | Scope |
|---|---|
| per-run budget, set per role in settings | one run of a worker, reviewer, mission control turn or fold |
| a board's **daily budget** | that board's whole day, UTC |
| a card's **total cap** | one card across every run it has ever made, restarts included |

The daily budget counts mission control and fold turns too, including a turn that failed or hit its
own per-run cap - what it actually spent still counts against the board's day. Every path that can
start a run checks the same caps: the scheduler, a manual `r`, an inbox answer and a lease approval.
Running cards always finish.

</details>

### Card edges

**A card's state is its edge, not a fill.**

<img src="docs/images/card-states.jpg" alt="Card edges by state: blue working, vanilla attention with a stepped glow, lichen accepted, red rejected" width="100%">

**Blue** an agent is working it, **vanilla** with a stepped glow it needs you, **lichen** accepted,
**red** rejected. The working blue is cold on purpose, so no pair collapses under red-green colour
blindness.

---

## How to use it well

The board is built on one assumption: **you define work carefully, then walk away.** Everything that
rewards hovering is a design mistake in it. If you find yourself watching a card run, either the card
was underspecified or the board owes you a surface that would have let you leave.

That gives you a different job than writing code. Your job is the brief, the criteria, the lease, and
the decision at the end. Everything between those is not yours.

### What makes a good card

**A brief that stands alone.** The agent gets the card and the repo, not your afternoon. Anything
that lives only in your head - why this approach and not the obvious one, the constraint that makes
the simple version wrong, the file that looks relevant and is not - either goes in the description or
gets rediscovered expensively, or not at all.

**Criteria a machine can check.** The reviewer will not judge them, so the test command is the only
thing that does. "Handles errors gracefully" is not a criterion; "returns 400 with `{"error": ...}`
on a malformed body, covered by a test" is. If a criterion has no test behind it, decide honestly
whether you are going to verify it by hand, and expect to.

**A lease that is narrow and does not overlap.** Narrow because it is a boundary; non-overlapping
because overlapping leases serialise cards that could have run together. Cover every file the card
genuinely must touch, including its tests. A write it needed and did not have is refused: if the
card still committed the rest, that goes on to the gates and the path waits under needs; if it
committed nothing, the card stops on `LEASE_CONFLICT`. A soft board lets it reach an unprotected file
on its own.

**One feature, not one edit.** A card that is a single edit spends most of its money on the agent
reading its way in. A card that is three features has no clean point where it is done. Feature-sized
is the unit the whole thing is tuned for.

**The model matched to the work.** Sonnet for ordinary work, Opus where the card genuinely needs
design judgement, Haiku for mechanical edits. Mission control proposes one based on what has actually
been reaching pull requests cleanly on that board; `m` overrides it.

### The loop you actually run

1. **Queue a few cards.** Mission control (`.`) if you want them planned for you, by hand if you
   already know. Check the leases before you run anything - it is the cheapest moment to catch an
   overlap.
2. **Start the board** (`w`) and leave. Two cards run at once by default. A card starts only once
   every card it depends on has its pull request **merged on GitHub**, not merely accepted on the
   board.
3. **Answer the inbox** (`n`) when you come back. Oldest first; the row tells you what it needs.
4. **Merge what is ready.** `v` lists every open pull request across every board, in merge order.
   Review, merge, and accept the card with `y`.
5. **Look at cost occasionally** (`c`). Cost per accepted card by model and complexity is the number
   that tells you whether your card sizing is working.

Set a daily budget per board before you trust the board to run unattended. It is the difference
between a bad night and an expensive one.

### When to step in, and when not to

**Let it run** when a card is working, when a card is on its first fix round, and when the board is
retrying something itself - those cards say so and stay out of the inbox on purpose.

**Step in** when a card comes back a second time for the same reason. Twice is the signal that the
card is wrong, not that the agent is unlucky. Fix the card - the brief, the criteria, the lease - and
run it again, rather than answering the same note twice.

**Never step in by arguing with a running agent about permission.** The guards read stored state, not
the conversation. If it needs a file, widen the lease; if it should not have it, say so and move on.

### When a card comes back blocked

Every waiting card already tells you what to do, in the same words on the board, the card and the
inbox. In short:

| Reason | What it usually means |
|---|---|
| `AGENT_QUESTION` | the brief had a hole. Answer it, then consider whether the answer belonged in the card. |
| `TESTS_FAILED` | read the failing output before answering. A hint resumes it; a wrong hint costs another run. |
| `BASE_RED` | not the card's fault. Fix the base or merge a fix into it, then run the card again. |
| `REVIEW_REJECTED` | read the findings. Real ones mean a fix; a wrong one means the card needed more context. |
| `LEASE_CONFLICT` | almost always a lease written for a repo layout that does not exist. Approve the paths, or tell it to leave them alone. |
| `MERGE_CONFLICT` | another card landed first. Resuming makes the worker merge the base and resolve it. |
| `DEPENDENCY_REJECTED` | nothing to do here. Fix the dependency. |
| `USAGE_LIMIT` | wait, or retry it on the fallback model from its row. Answering does nothing. |
| `CRASH` | check the note, then resume - the worktree and commits are kept. |

### The mistakes that cost a day

- **A lease that overlaps a card already running.** Nothing errors. The second card just waits, and
  your parallel board is a serial one. Check leases at queue time, not when you wonder why nothing
  finished.
- **Criteria nobody can check.** The board will happily open a pull request for work that met a
  criterion no test covers, because nothing in the chain was ever going to catch it. The reviewer
  does not do this for you.
- **A repo with no test command.** The card cannot finish, full stop. There is no path to a pull
  request that skips the gate. Set it when you register the repo, and give it everything your CI
  checks.
- **A test command that writes into the working tree.** The gate mounts it read-only, so a tool
  caching into the repo fails the gate for a reason that has nothing to do with the card.
- **Cards sized like tickets.** Ten edit-sized cards cost far more than three feature-sized ones,
  because each one pays the read-in cost again.
- **Running unattended with no daily budget set.**

---

## Keyboard

The board is keyboard-first, and the model is small enough to hold in your head:

- **`space` opens a thing.** On a focused card it opens the card; on the open card it closes it again.
- **`escape` goes one level back** - out of a text field, then out of the panel, then to the board.
- **`/` types.** It is the one way into typing: it puts the cursor in the visible text field. Opening
  a card or a drawer never steals focus, so every letter stays a board key until you press `/`.

Three consequences of that model worth knowing:

- **A card must be open for its comment box to exist.** `/` has nowhere to go on a card that is only
  focused; open it with `space` first.
- **An inbox card that shows its answer box takes `/` straight from focus**, without opening
  anything.
- **Settings has many fields, so arrow to one first.** `escape` there closes the panel and the field
  you were in saves.

Bindings resolve on the physical key, so a non-US layout does not move them. A held cmd, ctrl or alt
belongs to the browser - `cmd`+`c` copies, it does not open the cost panel. The one exception is
`cmd`/`ctrl`+`f` on a board with nothing open over it: it filters the board's cards instead of
searching the page, and `esc` clears the filter.

### Confirmations, and which button is focused

Anything that starts, spends, lands or destroys asks once. The confirmation opens with one button
**already focused**, and which button that is depends on how bad the mistake would be:

- **`y`, `x` and `k` focus confirm.** Pressing enter straight after the key carries on, because a
  wrong accept or reject is undone from the card's own menu (`m`), and a stopped run can be started
  again. Being wrong costs a keypress.
- **`r`, `w`, `f`, and delete or change-model inside the card menu focus the negative.** A stray
  enter cancels rather than spending money, starting a run, or destroying something.

The rule behind it: **reversible defaults to yes; expensive or destructive defaults to no.** If a
confirmation seems to do the opposite of what you expected, that is the rule at work rather than a
bug - though it is worth a beta report if it reads wrong in practice.

**`s` shows this same list inside the app**, paged left/right; the app builds it from the same table
this list was read from, so the two cannot drift.

### Cards and the board

| Key | Action |
|---|---|
| arrows | move focus; up also exits to the board bar. In an open card: up/down within a column, left/right across |
| `enter` | open the focused card |
| `space` | open the focused card, or close the open one |
| `esc` | one level back: input -> panel -> closed |
| `r` | run the focused card, with confirmation |
| `k` | stop the focused card if it is running |
| `y` | accept the focused card, with confirmation |
| `x` | reject the focused card, with confirmation |
| `m` | menu for the focused card: edit, model, complexity, move to, delete |
| `t` | run replay: scrub the focused card's run step by step |
| `/` | type: the open card's comment, or the open chat |
| `g` | toggle kanban / workstream grouping |
| `w` | run the board: start (with confirmation) / stop the queue |
| `f` | fold: merge the board's overlapping todo cards into one, with confirmation |

### Panels and boards

| Key | Action |
|---|---|
| `n` | attention inbox: answer a blocked card, across every board |
| `d` | morning digest: pull requests and open questions |
| `v` | pull requests: every open one across every board, in merge order |
| `c` | cost overview: spend across every board |
| `i` | cost telemetry: card attempts, or the board cost table |
| `u` | usage: rate-limit windows and per-model spend |
| `a` | agent roster: jump to a card an agent is working on |
| `p` | orchestrator, worker and reviewer prompts |
| `shift`+`p` | credential profiles |
| `.` | mission control: chat with the board orchestrator |
| `,` | workforce: chat with the focused card's agent |
| `b` | boards and repos: create a board, register a repo |
| `h` | pre-flight checklist: what is missing before a card can run |
| `o` | settings |
| `q` | landing lock: who holds the push lock on each repo, and the queue behind them |
| `s` | this shortcut list |
| `1`-`9` | jump to board 1-9 |

---

## Security

The board starts agents that can spend money and push code, so it is built to keep both the browser
and the agents in their lane.

### The board itself

- **Loopback and keyed.** It binds `127.0.0.1`. Every `/api/` call needs the key from
  `~/.config/smortboard/api_key` (mode 600), as the cookie the printed link sets or an
  `X-Smortboard-Key` header. A foreign `Host` (DNS rebinding), a cross-site `Origin` and a non-json
  body are refused before any route runs. `--host` is an explicit opt-in; the key is not network
  authentication.
- **A strict Content-Security-Policy.** Scripts and styles load only from the board's own files, so
  text an agent writes can never run as code in your tab. Agent-written labels render as text,
  attachments always download, and only `http(s)` links become links.
- **Bounded requests.** JSON bodies are capped at 1 MB and uploads at 25 MB, sockets time out after
  30 s, and image builds run off the request thread.
- **Private data stays private.** The database, which holds full agent transcripts, is created mode
  600 in a mode-700 directory.

### The agents

- **Sealed containers.** Non-root, `--cap-drop=ALL`, `no-new-privileges`, memory and process limits.
  No Docker socket, no home directory, no `~/.ssh` or `~/.claude`. The test gate has no network; the
  reviewer and orchestrator mounts are read-only. Without Docker a card refuses to run rather than
  running outside a container.
- **The token travels on stdin.** Never on the `docker` command line, never mounted, never in
  `docker inspect`; inside the container it reaches only the `claude` process.
- **Guards the agent cannot touch.** The lease hook covers Edit and Write. A bash guard allows only
  git and the repo's own test and lint commands, and refuses git's option tricks (`-c`,
  `--upload-pack` and friends). Both are written to a temporary folder outside the repo and mounted
  read-only. A soft lease never reaches agent settings, CI, agent instructions, build and dependency
  files or secrets unless the card's own lease names them.
- **Only the tools a role needs.** A worker loads read, edit, write, two searches and a shell; the
  reviewer, mission control and fold load only read and the searches. Skills and slash commands are
  off for every run.
- **Proof comes from outside the agent.** Tests re-run offline, the reviewer can only read, and only
  a note carrying the run's own marker counts as you.
- **No merge path exists** in the code.

<picture>
  <source media="(prefers-color-scheme: light)" srcset="docs/images/art-architecture-light.svg">
  <img src="docs/images/art-architecture-dark.svg" alt="Architecture: one native smortboard process on 127.0.0.1 - request gate, http server, sqlite store, run registry, scheduler and orchestrator - plus the card token file; per card a hardened card container, a test gate with no network and a read-only reviewer; commits fetched back to the repo, then pushed to GitHub, where a person merges into main." width="100%">
</picture>

The board itself is one plain Python process - a stdlib HTTP server, SQLite, and plain JavaScript
built on [smortui](https://github.com/BalthazarFitzpatrick/smortui), pinned by commit, with no build
step. Only the agents are contained, because a containerised board would need the Docker socket,
which amounts to root on the host.

---

## Beta

This is a beta because it has had exactly one user. The mechanics below have been exercised daily on
real repos; the things around them have been exercised by one person, on one machine, with one set
of habits.

**What is solid.** The card lifecycle end to end - worktree, run, test gate, reviewer, pull request.
The containment and the guards. The landing lock, including from outside the board. Cost accounting
and the spend caps. The board runs itself unattended for hours without supervision, which is the
thing it was built to do.

**What is rough.**

- **Only macOS has really been used.** Linux should be fine; CI runs the test suite on Linux every
  push. The Windows code paths exist but have never been run - assume they are broken and tell us
  how.
- **Setup is the least-tested part**, because it only ever happened a handful of times and always on
  a machine that already had everything. The pre-flight checklist (`h`) is the best defence, and
  reports about it are especially useful.
- **One person's keyboard habits** shaped which key does what. If a key does something surprising,
  that is worth a report even if it is technically working as built.
- **The HTTP server handles one request at a time.** Fine for one person on one machine; it is not a
  multi-user server and is not trying to be.
- **A stop that lands while the test gate is running** waits for the gate to finish.
- **Task checkboxes in a pull request** are not ticked during a run.

**What will change.** Anything on the rough list. The database migrates itself forward on every open,
so upgrading does not cost you your boards - but this is a `0.1.x`, and settings, defaults and the
shape of individual panels are expected to move. The concepts above - cards, leases, the two gates,
the landing lock, and human-only merges into main - are the parts that are not going to.

**Try it end to end.** [docs/beta-test-board.md](docs/beta-test-board.md) builds a small browser
game from one mission-control prompt: 12-14 cards with dependency chains and parallel tracks, a
test gate that needs nothing but Node, and a table of which board feature each step exercises.

### Sending a report

Bug reports go to
**[github.com/BalthazarFitzpatrick/smortboard/issues](https://github.com/BalthazarFitzpatrick/smortboard/issues)**.
Pick the bug report template; it asks for what is actually useful, and the more of it you fill in the
faster it is fixed:

- the version (`uv run smortboard --version`) and your operating system
- anything not green in the pre-flight checklist (`h`)
- **what you did** - the keys you pressed or the card you ran, in order
- **what you expected, and what happened instead**
- which board and which card, and evidence: a screenshot, the card's replay (`t`), the card's
  comments, or the output in the terminal smortboard is running in

**Never paste your card token, your API key, or the contents of `~/.config/smortboard/`.** If a
transcript is worth attaching, read it first - a card's event log contains whatever the agent saw in
your repo. `smortboard export` (see [Backup](#backup-export-and-import)) is a reproducible way to
attach your whole board - it never carries credential material, so nothing there needs redacting.

---

## Reference

### Configuration

| Flag | Env | Default |
|---|---|---|
| `--port` | `SMORTBOARD_PORT` | `8000` (`8001` with `--demo`) |
| `--host` | `SMORTBOARD_HOST` | `127.0.0.1` |
| `--db` | `SMORTBOARD_DB` | user data dir |
| `--no-browser` | | opens a tab |
| `--demo` | | off |
| `--version` | | |
| | `SMORTBOARD_CARD_IMAGE` | `smortboard-card:latest` |
| | `SMORTBOARD_CARD_TOKEN_PATH` | `~/.config/smortboard/card_token` |
| | `SMORTBOARD_OPERATOR_NAME` | your `git config user.name` |

A flag beats the matching `SMORTBOARD_*` env var, which beats the default.

**The data directory** is the platform's user data dir - on macOS
`~/Library/Application Support/smortboard`, elsewhere the equivalent - created mode 700, holding
`smortboard.db`. It is never the working directory, so running the board from anywhere does not
scatter database files. Configuration and tokens live separately, under `~/.config/smortboard`
(`%APPDATA%\smortboard` on Windows).

**The settings panel** (`o`) is three groups: how the board behaves, which lab and model each role
runs on, and what it may spend.

<table>
<tr>
<td width="33%"><img src="docs/images/settings-general.jpg" alt="General board settings: the mouse toggle, how many cards run at once globally and per board, strict or soft file leases per board, the resume briefing, the gate timeout and the mall cam interval" width="100%"></td>
<td width="33%"><img src="docs/images/settings-labs.jpg" alt="Labs and models: automatic credential profile rotation, whether to ask before switching to a fallback model on a usage limit, and a primary model, a fallback order and an effort for the worker, reviewer, orchestrator and fold" width="100%"></td>
<td width="33%"><img src="docs/images/settings-cost.jpg" alt="Spend caps per run: a card run, a review, a mission control turn, a fold, and a per-card total across every run" width="100%"></td>
</tr>
<tr>
<td align="center"><sub>general — how it behaves</sub></td>
<td align="center"><sub>labs and models — who runs what</sub></td>
<td align="center"><sub>cost control — what it may spend</sub></td>
</tr>
</table>

Every one of those is a stored setting: `findings_route`, the per-role `*_lab` and `*_model` pairs
and their `*_cross_lab_fallback` lists, `max_parallel`, `resume_briefing`, `gate_timeout_seconds`,
`auto_switch_profiles`, `usage_limit_route`, the per-role `worker_effort`, `reviewer_effort`,
`orchestrator_effort` and `fold_effort` (unset passes no effort flag), the per-run caps
`worker_budget_usd`, `reviewer_budget_usd`, `orchestrator_budget_usd`, `fold_budget_usd`, the
per-card `card_total_budget_usd`, and `mission_control_read_paths` (absolute paths mission control may
also read). Per board: its own parallel cap, its `daily_budget_usd`, its merge mode and its lease
mode.

### Card token

Adding a profile with `shift`+`p` writes these files for you, at mode 600. This is what it writes,
for when you would rather do it by hand or script it.

| What | Where |
|---|---|
| the original single Claude token | `~/.config/smortboard/card_token`, or `%APPDATA%\smortboard\card_token` on Windows |
| any named profile | `~/.config/smortboard/tokens/<lab>/<name>` - `<lab>` being `anthropic` or `openai` |

`$XDG_CONFIG_HOME` is honoured where it is set. An OpenAI profile holds either the whole
`~/.codex/auth.json` or an API key, depending on the kind chosen when it was added; a bare access
token pulled out of that JSON is refused, which is measured in
[the credential spike](docs/spikes/S7-codex-auth.md).

- **macOS:**

  ```bash
  mkdir -p ~/.config/smortboard
  (umask 077; pbpaste | tr -d '\r\n ' > ~/.config/smortboard/card_token)
  ```

- **Linux:** the macOS command with `wl-paste` (Wayland) or `xclip -selection clipboard -o`
  (X11) in place of `pbpaste`.
- **Windows (PowerShell):**

  ```powershell
  New-Item -ItemType Directory -Force "$env:APPDATA\smortboard" | Out-Null
  (Get-Clipboard -Raw) -replace '\s', '' |
    Set-Content -NoNewline -Encoding ascii "$env:APPDATA\smortboard\card_token"
  ```

- **By hand:** create the file, `chmod 600` it, then paste the token in with any editor.
- **Elsewhere:** `SMORTBOARD_CARD_TOKEN_PATH` points at any file. Credentials are read only from
  files; a missing file requires saving a token before running a card.
- **Several subscriptions, or both labs:** `shift`+`p`, one profile per credential, each under its
  own name. The board rotates to the next profile of the same lab when the active one is
  rate-limited; crossing to the other lab needs a fallback list per role, which is deliberate.

Neither `claude setup-token` nor `codex login` hands its credential to the board. That is why the
board stores its own copy, and why it is a model-only token rather than your login.

### Backup: export and import

```bash
uv run smortboard export backup.json   # writes the board to a file
uv run smortboard import backup.json   # reads it back into an empty database
```

Both run with no server started - the whole point is a way to get your board out (or back in) when
the UI will not start. Each respects `--db` and `SMORTBOARD_DB` exactly like the server does, so a
bundle round-trips through whichever database you point it at. `--demo` is refused for both: a demo
database is a fresh throwaway made and discarded every run, never a board worth backing up.
`import` refuses a database that already has a board in it, rather than half-overwriting one -
point `--db` at a fresh path for a restore.

**What a bundle contains:** every board, repo registration and the paths remembered for it, card
and its tasks, criteria, leases, dependencies and comments, attachments, the event log, board-wide settings, saved prompts and
mission control's messages and plans - everything a board's own database holds, as one plain-text
JSON file (attachment contents included, base64-encoded).

**What a bundle never contains:** your card token, your API key, any Claude credential profile
name or token, or anything else under `~/.config/smortboard/`. None of that is ever written to the
database a bundle is built from, so there is nothing to strip on export - a bundle is safe to
attach to a public bug report as it stands.

### Repos and their test command

A repo must already exist on GitHub with its default branch pushed; the board registers it, it does
not create it. Set the default branch to `development` (pushed to the remote) and the board lands
cards there itself.

The test command is the gate, so give it everything your CI checks - if CI runs `ruff check` and the
gate does not, a card can pass its own gate and still fail CI once it lands. The gate mounts the
worktree read-only; it exports `RUFF_CACHE_DIR` and `PYTEST_ADDOPTS=-p no:cacheprovider` itself, so
a plain command does not need `--no-cache` for those two, but any other tool with its own cache still
does:

```bash
uv run --no-sync ruff format --check --no-cache --extend-exclude .claude . && uv run --no-sync ruff check --no-cache --extend-exclude .claude . && uv run --no-sync pytest -q -p no:cacheprovider
```

A repo whose tests need more than git, uv and Python gets its own image with its toolchain already
installed, since the gate is offline; `docker/repo.Dockerfile` is the template. The preflight check
(`h`) flags an image that predates the repo's `uv.lock` or its own base image, and the board rebuilds
it itself before the next card runs on that repo. Give Docker Desktop a memory limit (Settings ->
Resources) and lower `max_parallel` if it is tight.

---

## Troubleshooting

### A card stopped on `LEASE_CONFLICT`

Either the card committed a file outside its lease, or it was refused a write and committed nothing
at all; the note names the files. A card that was refused a write but committed the rest does not
stop here - it goes on to the gates and lists the wanted paths under needs. The usual cause is a
lease written for a layout the repo does not have. On a board you trust, switching it to a soft
lease in `o` lets cards reach unprotected files on their own. In the inbox (`n`), **approve** adds exactly the refused
paths and resumes the card. Or answer instead, to tell the agent to leave those files alone. An
answer alone never widens a lease.

Through the API, with the key loaded once:

```bash
K="X-Smortboard-Key: $(cat ~/.config/smortboard/api_key)"
curl -s -H "$K" 127.0.0.1:8000/api/boards                        # board ids
curl -s -H "$K" 127.0.0.1:8000/api/boards/<board-id>/cards       # card ids
curl -s -X PATCH -H "$K" -H 'content-type: application/json' 127.0.0.1:8000/api/cards/<card-id> \
  -d '{"leases": ["src/app/**", "tests/**"]}'                    # replaces the whole list
```

### Other common stops

| Symptom | Fix |
|---|---|
| `this tab has no api key` at the top of the page | Open the link the board printed on start. |
| `.../card_token is not mode 600 - refusing to read it` | `chmod 600 ~/.config/smortboard/card_token` |
| An expired or revoked token (HTTP 401) | The card says how to renew it: `claude setup-token` again. |
| The gate fails on `Read-only file system` for a tool other than ruff or pytest | Give that tool its own cache flag - `--no-cache`, `-p no:cacheprovider`, or whatever it takes. |
| A card fails a test its own diff never touches | Preflight (`h`) may show its repo image predates `uv.lock`; the next run rebuilds it, or rebuild it by hand from the repo's row. |
| A card waits with `lease conflict with card <id>` | Two leases overlap; it starts when the other card finishes. |
| No new runs start | Check the usage window (`u`) and the board's daily budget (`o`). |
| Cards were `CRASH`ed on start-up | The board was stopped mid-run; they are waiting in the inbox to be resumed. |

Anything else: [open a bug report](https://github.com/BalthazarFitzpatrick/smortboard/issues).

---

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest -q          # includes the js suite (tests/js/*.mjs) when node is installed
```

The suite runs in parallel by default (`-n auto`), which is what keeps it worth running: it is a
thousand small I/O-bound tests, and measured on ten cores it takes about 20 seconds that way against
roughly three minutes on one process. Pass `-n0` when you want one test's output in order, or a
debugger.

That shapes the loop. Commit as often as the work wants and let ruff be the only thing that runs -
about a second. Run the file you are changing while you work, `--lf` after a failure, and the whole
suite once before you push. Running the suite per commit buys nothing CI does not already guarantee
and turns an afternoon of small commits into an afternoon of waiting.

Neither suite calls a model. CI runs the same checks on every pull request and must pass before
anything reaches `main`. A markdown-only change skips the toolchain and the suite but still reports,
so a documentation pull request stays mergeable rather than waiting on a required check that never
starts. A second push to a branch cancels the run it superseded. `tools/shoot_docs_images.py` retakes this page's screenshots against the demo board.
`docs/PLAN.md` holds the plan and the containment reasoning, `docs/PHASE1-CONTRACTS.md` the schema and
API, `docs/PROMPTS.md` the prompt layering, and `docs/spikes/` what was proven before it was built on.

## License

[MIT](LICENSE). Use it, change it, ship it; keep the copyright notice.
