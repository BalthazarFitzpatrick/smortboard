# smortboard

> a browser-based board built on ui_base, python-served, docker in the backend

## project mission

a convenience wrapper around coding agents. cards are defined in detail up front, handed to headless
agent sessions - claude code or codex - running in isolated git worktrees and throwaway containers,
and surfaced back only when they need a decision. single user, local-first, browser-based,
distributed via docker.

**it is not a harness.** it does not reimplement the agent loop - it schedules and observes one.

the design test for every feature: does it help the operator define work and then walk away? anything
that rewards hovering is wrong, and should be argued against rather than built.

brief: `docs/dev-board-brief.md`. approved plan: `docs/plan.md` - phases, spikes, unit contracts,
the whole-system test case and the flowchart all live there.

## architecture

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

**the hard constraint: `ui/` holds wiring, not widgets.** any visual primitive that does not exist
yet gets built in `ui_base`, never here, and must be named in domain-free vocabulary - no card,
board, kanban or agent in class names, ids, storage keys or comments.

card lifecycle: to do -> doing -> checking -> accepted / rejected, plus **blocked** with a reason
code (`CRASH`, `USAGE_LIMIT`, `LEASE_CONFLICT`, `AGENT_QUESTION`, `TESTS_FAILED`,
`REVIEW_REJECTED`, `DEPENDENCY_REJECTED`, `MERGE_CONFLICT`, `API_UNREACHABLE`, `BASE_RED`). blocked is a flag,
not a column: the card keeps the status it was in and the board renders it in **attention**, a
presentation column between doing and checking. reaching checking requires unit tests passing and the
reviewer approving. boards default to review-required: they open a pr and wait for accept before
landing on an unprotected base. free-merge boards land after the gates pass. neither mode merges
into main/master/trunk. review mode can stack one unmerged parent, at most three cards deep.

see `docs/plan.md` for the full flowchart.

## active context

public beta, run daily on real repos by one person on macOS. the board, mission control, the
landing lock and the demo board are built, the security audit's remediation landed, and the public
history was rewritten (a github support request covers the pull-request refs a force-push cannot
touch).

since 2026-09-18 two things changed the shape of the board. **multi-lab**: a run picks a lab and a
model per role, claude code or codex, with credential profiles per lab and a fallback list per role -
`smortboard/labs/` holds the adapters and the catalog, and `docs/spikes/s4`-`s7` hold what was
measured about codex before any of it was built. **merge modes**: a board is review-required by
default and accepting a card is what lands it, with free-merge as the opt-in per board. review mode
stacks a dependent card on its unmerged parent so the queue keeps moving while nobody is watching.

## the local loop

**commit as often as the work wants. only ruff runs at commit time, and it takes about a second.**
never run the suite per commit - it buys nothing ci does not already guarantee, and it is what turns
an afternoon of small commits into an afternoon of waiting.

| when | what runs | cost |
|---|---|---|
| while writing | nothing you start by hand - a hook formats python on save | - |
| every commit | `uv run ruff check . --fix && uv run ruff format .` | ~1s |
| the file you are changing | `uv run pytest tests/test_<thing>.py -q` | a second or two |
| after a failure | `uv run pytest --lf -q` | only what broke |
| before a push or a pull request | `uv run pytest -q` - the whole suite, parallel | ~24s |
| pushing, merging | ci, on github | not your wait |

the suite runs `-n auto` by default (`pyproject.toml`), which is what makes the last line
affordable: measured 2026-09-18, 1039 tests took 129s on one process at 37% cpu across ten cores,
and 24s in parallel. pass `-n0` when you are reading one test's output or using a debugger.

ci is the guarantee nobody can skip: it runs ruff, the format check and the suite on every pull
request, and again on a push to `main` or `development`. a markdown-only change skips the heavy
steps but still reports, so a docs pull request stays mergeable without burning four minutes. a
second push to a branch cancels the run it superseded.

**never point `UV_CACHE_DIR` at a directory inside the repo.** the shared `~/.cache/uv` is warm and
large; a project-local cache starts empty and makes every worktree sync re-download everything.

## tech stack

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

## related repos

this project spans repos. `ui_base` is the shared interface package, consumed as the pinned git
dependency in `pyproject.toml` and developed in its own repo - read its `CLAUDE.md` before writing
any interface code. all final results live here in smortboard.

`CLAUDE.local.md`, if it exists beside this file, is the maintainer's own working notes - untracked
and gitignored. nothing in it is needed to work on this repo.

## constraints and gotchas

- **the js tests read `ui_base` from a sibling checkout first** (`tests/js/dom_stub.mjs`), falling
  back to the pinned install. a sibling sitting on an older commit than `pyproject.toml` pins means
  the suite tests a stylesheet the app does not ship - check the pin before trusting a green run
- **the database is gitignored and lives in the platform's data dir**, never the working directory;
  `smortboard export` is the portable bundle
- **a card cannot run without docker, a credential and a repo image.** the preflight check (`h`)
  names whatever is missing rather than letting a card fail minutes in

## rules

these hold in this project even if `~/.claude/CLAUDE.md` isn't loaded (a teammate's machine, a
stripped agent). the full version — with rationale — lives in the global playbook.

**git — never commit to `main`/`master`.** not once, not for a hotfix. the only commit that lands
on main is the initial scaffold. all work happens in worktrees on feature branches, cut from
`development` if it exists, else `main` — never in the main checkout, which the human switches at will.

before every commit, in order:
1. `git status` — confirm nothing sensitive is staged
2. (python) `uv run ruff check . --fix && uv run ruff format .`

commit messages: past tense, lowercase, no trailing period. branches: `feature/`, `fix/`, `chore/`.
never commit `.env`, `*.key`, `*.pem`, `credentials*.yaml`, or anything that looks like a token.
open prs with `gh pr create` (installed, authenticated) — real lowercase title, plain lowercase bullet body.
human merges main. before work that could end in a development merge, ask this session's mode
via AskUserQuestion: free or review. record the explicit answer using
`sh ~/.claude/hooks/session-merge-mode.sh <session_id> free|review` (SessionStart supplies the id).
read the same session state after compaction. a project preference is not consent.
in review mode, finish tests and open the pr, then ask before each merge; a yes covers that one merge.
the mode mirrors the board's merge modes and is kept by the agent; the local hook guards main only.
never force-push to main.

**lowercase.** everything you produce is lowercase, in text and code: replies, docs and headings,
comments, commits, pr text, release notes, ui strings, log messages. case stays only where it
carries meaning — identifiers a language or tool dictates (class names, constants, env vars),
existing names quoted verbatim, text quoted from others.

**releases.** once the human has merged into `main`, draft a release unasked: next tag (pre-1.0:
minor for features or a changed default, patch for fixes only), notes in their voice via
`write-like-fabs` — an upgrading section first (backup, migrations, changed defaults, rebuilds),
then changes by area, every pr since the last tag. `gh release create <tag> --draft --target main`
only; never publish, never push a tag. share the draft link.

**scope.** implement exactly what was asked — no unrequested extras; propose them in one line
instead. if a second attempt at the same bug fails, stop and state the root cause before editing again.

**evidence.** only state a cause, mechanism, or metric backed by a specific log line, code path, or
test output — prefix anything else with "unverified hypothesis:".

**code.** no emojis, anywhere, in any output. comments lowercase, no trailing period, 1-3 lines max
per block, explaining intent plus the few mechanics that matter. never bare `except:`. no credential literals — env vars
or `.env`, never committed.

**python.** always `uv run <script/tool>`, never bare `python`/`python3`. ruff must pass before
every commit. if python appears in a project that has no toolchain yet, set it up rather than
asking: `pyproject.toml` with the tuned ruff config, `uv sync`, `tests/`.

**prose.** anything a person will read — a pull request body, a comment, a doc — says what happened
and what it cost, in plain words. no filler adjectives, no restating the obvious, no summary of a
summary.
