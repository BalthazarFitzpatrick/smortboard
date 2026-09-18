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
`REVIEW_REJECTED`, `DEPENDENCY_REJECTED`, `MERGE_CONFLICT`, `API_UNREACHABLE`). Blocked is a flag,
not a column: the card keeps the status it was in and the board renders it in **attention**, a
presentation column between doing and checking. Reaching Checking requires unit tests passing AND the
reviewer approving. Boards default to review-required: they open a PR and wait for accept before
landing on an unprotected base. Free-merge boards land after the gates pass. Neither mode merges
into main/master/trunk. Review mode can stack one unmerged parent, at most three cards deep.

See `docs/PLAN.md` for the full flowchart.

## Active Context

Public beta, run daily on real repos by one person on macOS. The board, mission control, the
landing lock and the demo board are built, the security audit's remediation landed, and the public
history was rewritten (a github support request covers the pull-request refs a force-push cannot
touch).

Since 2026-09-18 two things changed the shape of the board. **Multi-lab**: a run picks a lab and a
model per role, Claude Code or Codex, with credential profiles per lab and a fallback list per role -
`smortboard/labs/` holds the adapters and the catalog, and `docs/spikes/S4`-`S7` hold what was
measured about Codex before any of it was built. **Merge modes**: a board is review-required by
default and accepting a card is what lands it, with free-merge as the opt-in per board. Review mode
stacks a dependent card on its unmerged parent so the queue keeps moving while nobody is watching.

The task ledger holds the current queue.

## The local loop

**Commit as often as the work wants. Only ruff runs at commit time, and it takes about a second.**
Never run the suite per commit - it buys nothing CI does not already guarantee, and it is what turns
an afternoon of small commits into an afternoon of waiting.

| when | what runs | cost |
|---|---|---|
| while writing | nothing you start by hand - a hook formats python on save | - |
| every commit | `uv run ruff check . --fix && uv run ruff format .` | ~1s |
| the file you are changing | `uv run pytest tests/test_<thing>.py -q` | a second or two |
| after a failure | `uv run pytest --lf -q` | only what broke |
| before a push or a pull request | `uv run pytest -q` - the whole suite, parallel | ~24s |
| pushing, merging | CI, on github | not your wait |

The suite runs `-n auto` by default (`pyproject.toml`), which is what makes the last line
affordable: measured 2026-09-18, 1039 tests took 129s on one process at 37% cpu across ten cores,
and 24s in parallel. Pass `-n0` when you are reading one test's output or using a debugger.

CI is the guarantee nobody can skip: it runs ruff, the format check and the suite on every pull
request, and again on a push to `main` or `development`. A markdown-only change skips the heavy
steps but still reports, so a docs pull request stays mergeable without burning four minutes. A
second push to a branch cancels the run it superseded.

**Never point `UV_CACHE_DIR` at a directory inside the repo.** The shared `~/.cache/uv` is warm and
large; a project-local cache starts empty and makes every worktree sync re-download everything.

## Tech Stack

python (uv, ruff, pytest) - `uv run ruff check . --fix && uv run ruff format .` before every commit.
the suite is parallel by default; see `## The local loop` above for what to run when.

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

ledgers: `TASKS.jsonl` and `WORKLOG.md` live in a private ledger repo checked out beside this
one. The root files are untracked, gitignored symlinks into it; nothing about them is ever
committed here.

## Constraints and Gotchas

<!-- fill in: known limitations, required env vars, external dependencies, things that will
     surprise a new contributor -->

## Rules

This repo uses approval-required Git mode. Work in a feature worktree, open a PR against
`development`, then wait for the operator's approval before merging. A later explicit session
mode choice takes precedence.

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
