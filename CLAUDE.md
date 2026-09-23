# smortboard

> a browser-based board built on ui_base, python-served, docker in the backend

## Project Mission

A convenience wrapper around coding agents. Cards are defined in detail up front, handed to headless
agent sessions - Claude Code or Codex - running in isolated git worktrees and throwaway containers,
and surfaced back only when they need a decision. Single user, local-first, browser-based,
distributed via Docker.

**It is not a harness.** It does not reimplement the agent loop - it schedules and observes one.

The design test for every feature: does it help the operator define work and then walk away? Anything
that rewards hovering is wrong, and should be argued against rather than built.

Brief: `docs/dev-board-brief.md`. Approved plan: `docs/PLAN.md` - phases, spikes, unit contracts,
the whole-system test case and the flowchart all live there.

## Architecture

```
smortboard/
  store/        sqlite schema, migrations, export
  labs/         one adapter per lab (claude code, codex), the model catalog, neutral events
  exec/         worktree manager, lease store, agent runner, event log
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
`REVIEW_REJECTED`, `DEPENDENCY_REJECTED`, `MERGE_CONFLICT`, `API_UNREACHABLE`, `BASE_RED`). Blocked is a flag,
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

this project spans repos. `ui_base` is the shared interface package, consumed as the pinned git
dependency in `pyproject.toml` and developed in its own repo - read its `CLAUDE.md` before writing
any interface code. all final results live here in smortboard.

`CLAUDE.local.md`, if it exists beside this file, is the maintainer's own working notes - untracked
and gitignored. Nothing in it is needed to work on this repo.

## Constraints and Gotchas

- **the js tests read `ui_base` from a sibling checkout first** (`tests/js/dom_stub.mjs`), falling
  back to the pinned install. a sibling sitting on an older commit than `pyproject.toml` pins means
  the suite tests a stylesheet the app does not ship - check the pin before trusting a green run
- **the database is gitignored and lives in the platform's data dir**, never the working directory;
  `smortboard export` is the portable bundle
- **a card cannot run without Docker, a credential and a repo image.** the preflight check (`h`)
  names whatever is missing rather than letting a card fail minutes in

## Rules

These hold in this project even if `~/.claude/CLAUDE.md` isn't loaded (a teammate's machine, a
stripped agent). The full version — with rationale — lives in the global playbook.

**Git — never commit to `main`/`master`.** Not once, not for a hotfix. The only commit that lands
on main is the initial scaffold. All work happens in worktrees on feature branches, cut from
`development` if it exists, else `main` — never in the main checkout, which the human switches at will.

Before every commit, in order:
1. `git status` — confirm nothing sensitive is staged
2. (Python) `uv run ruff check . --fix && uv run ruff format .`

Commit messages: past tense, lowercase, no trailing period. Branches: `feature/`, `fix/`, `chore/`.
Never commit `.env`, `*.key`, `*.pem`, `credentials*.yaml`, or anything that looks like a token.
Open PRs with `gh pr create` (installed, authenticated) — real lowercase title, plain lowercase bullet body.
Human merges main. Before work that could end in a development merge, ask this session's mode
via AskUserQuestion: free or review. Record the explicit answer using
`sh ~/.claude/hooks/session-merge-mode.sh <session_id> free|review` (SessionStart supplies the id).
Read the same session state after compaction. A project preference is not consent.
In review mode, finish tests and open the PR, then ask before each merge. Record only an explicit
approval with `session-merge-mode.sh <session_id> approve <absolute_session_cwd> '<exact_command>'`.
That approval is consumed before execution; a failed command needs fresh approval. Never force-push to main.

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

**Prose.** Anything a person will read — a pull request body, a comment, a doc — says what happened
and what it cost, in plain words. No filler adjectives, no restating the obvious, no summary of a
summary.
