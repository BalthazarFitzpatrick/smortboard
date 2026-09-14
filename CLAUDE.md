# smortboard

> a browser-based board built on ui_base, python-served, docker in the backend

## Project Mission

A convenience wrapper around coding agents. Cards are defined in detail up front, handed to
headless Claude Code sessions running in isolated git worktrees, and surfaced back only when they
need a decision. Single user, local-first, browser-based, distributed via Docker.

**It is not a harness.** It does not reimplement the agent loop - it schedules and observes one.

The design test for every feature: does it help the operator define work and then walk away? Anything
that rewards hovering is wrong, and should be argued against rather than built.

mode: outcome-defined - the target is a stated vision to be checked and then implemented, not an
open-ended investigation. lock the output and prove unknown mechanics small before building.

Brief: `docs/dev-board-brief.md`. Approved plan: `docs/PLAN.md` - phases, spikes, unit contracts,
the whole-system test case and the flowchart all live there.

## Architecture

```
smortboard/
  store/        sqlite schema, migrations, export
  exec/         worktree manager, lease store, claude runner, event log
  review/       test runner, reviewer agent, merge request builder
  orchestrate/  mission control session, role prompts, orchestrator ledger
  server/       http api, asset serving (own files first, ui_base fallback)
  ui/           configuration of ui_base components only - no new UI primitives
```

**The hard constraint: `ui/` holds wiring, not widgets.** Any visual primitive that does not exist
yet gets built in `ui_base`, never here, and must be named in domain-free vocabulary - no card,
board, kanban or agent in class names, ids, storage keys or comments.

Card lifecycle: To do -> Doing -> Checking -> Accepted / Rejected, plus **Blocked** with a reason
code (`CRASH`, `USAGE_LIMIT`, `LEASE_CONFLICT`, `AGENT_QUESTION`, `TESTS_FAILED`,
`REVIEW_REJECTED`, `DEPENDENCY_REJECTED`). Reaching Checking requires unit tests passing AND the
reviewer approving. The board pushes and links a PR; it never merges.

See `docs/PLAN.md` for the full flowchart.

## Active Context

Plan approved 2026-09-08. **All three spikes are done and confirmed** - see `docs/spikes/`. The
plan is no longer provisional; phase 2 can be built.

Phases 2 and 6 shrank: session lifecycle, worktree creation, commit detection, telemetry and quota
all come from the CLI and its event stream rather than from code we write. Two requirements were
added instead. The board must hand each agent a worktree **already on its branch** - a card left on
main either names its own branch or deadlocks asking permission to make one. And phase 2 must decide
explicitly what a card run inherits from the operator's global config, because `--settings` adds to
it rather than replacing it.

Next action is phase 0 - the ui_base components. No application code written yet.

Branches: `feature/setup` here, `chore/smortboard-ledger` in private-ledgers.

## Tech Stack

python (uv, ruff, pytest) - `uv run ruff check . --fix && uv run ruff format .` before every commit.

frontend: plain css and script globals from `ui_base` - no build step, no npm, no framework. a js
toolchain is deliberately not being added; ui_base is designed to avoid one. all keyboard bindings
resolve on `event.code`, never `event.key`, so layout cannot move them.

backend: python http server, sqlite (gitignored, with an on-demand `smortboard export`), docker for
distribution.

ui_base is consumed as a pinned git dependency - a sha while co-developing a component in lockstep,
a tag once it lands. PyPI whenever the pinning starts to chafe; it is a one-line change per
consumer, so there is no need to pay for it up front.

## Related repos

this project spans repos. `ui_base` (`../ui_base`) is the shared interface package - read its
`CLAUDE.md` before writing any interface code. all final results live here in smortboard.

ledgers: `TASKS.jsonl` and `WORKLOG.md` live in `dev_ledger/` and are tracked in this repo; the
root files are relative symlinks into that directory.

## Constraints and Gotchas

<!-- fill in: known limitations, required env vars, external dependencies, things that will
     surprise a new contributor -->

## Rules

These hold in this project even if `~/.claude/CLAUDE.md` isn't loaded (a teammate's machine, a
stripped agent). The full version — with rationale — lives in the global playbook.

**Git — never commit to `main`/`master`.** Not once, not for a hotfix. The only commit that lands
on main is the initial scaffold. The human merges PRs and then works from `main`, so the checkout
can change under you mid-turn: run `git branch --show-current` before *every* edit and commit, not
just once at the start, and branch out if it says `main`.

Before every commit, in order:
1. `git branch --show-current` — if `main`/`master`, stop and branch first
2. `git status` — confirm nothing sensitive is staged
3. (Python) `uv run ruff check . --fix && uv run ruff format .`

Commit messages: past tense, lowercase, no trailing period. Branches: `feature/`, `fix/`, `chore/`.
Never commit `.env`, `*.key`, `*.pem`, `credentials*.yaml`, or anything that looks like a token.
After `git push`, surface the create-pull-request URL from the remote output. `gh` is often
unavailable — if `gh pr create` fails, don't retry, print the compare URL
(`https://github.com/<owner>/<repo>/compare/main...<branch>`) and a plain-text PR summary.
Human approves all merges. Never force-push to main.

**Scope.** Implement exactly what was asked — no unrequested extras; propose them in one line
instead. If a second attempt at the same bug fails, stop and state the root cause before editing again.

**Evidence.** Only state a cause, mechanism, or metric backed by a specific log line, code path, or
test output — prefix anything else with "Unverified hypothesis:".

**Code.** No emojis, anywhere, in any output. Comments lowercase, no trailing period, 1-3 lines max
per block, explaining intent not mechanics. Never bare `except:`. No credential literals — env vars
or `.env`, never committed.

**Python.** Always `uv run <script/tool>`, never bare `python`/`python3`. Ruff must pass before
every commit. If python appears in a project that has no toolchain yet, set it up rather than
asking: `pyproject.toml` with the tuned ruff config, `uv sync`, `tests/`.

**Other languages.** Suggest, don't install — name the conventional linter, formatter and test
runner, and ask once. Record whatever is agreed under `## Tech Stack`, including its lint command:
step 3 of the pre-commit list above names only the python one, so a second language is silently
skipped unless it is written down there.

**Work.** Any task with two or more independent parts → parallel subagents, not serial work. Keep
replies short and plain; normal eloquence only where real complexity earns it. Mask default AI tone
in anything others will read (docs, PRs, client reports) — load the `write-like-fabs` skill first.

## Handover notes

`TASKS.jsonl` is the task ledger, not a changelog. One JSON object per line, one line per task, in
priority order. Fields: `id`, `title`, `status` (`queued`/`in_progress`/`blocked`/`done`),
`acceptance_criteria` (concrete, testable), `verify_command` (the exact shell command proving it's
done), `blocked_reason_code`, `last_verified_commit`, `notes` (quirks, rejected approaches and why,
exact errors — what a fresh context can't rediscover). Mutate a line **in place** as its task
changes; never append a second line for the same `id`. The next action is the first entry whose
status isn't `done` — read it first when resuming.

`WORKLOG.md` is the changelog the ledger isn't: **append** a short dated entry per session or stop.
Never rewrite or delete a past entry.

Update both BEFORE stopping whenever a session heads toward a forced break, not after.
