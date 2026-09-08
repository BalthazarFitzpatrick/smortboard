# smortboard

> a browser-based board built on ui_base, python-served, docker in the backend

## Project Mission

<!-- fill in: what this project does, who uses it, what success looks like. the brief exists as a
     transcribed set of ramblings from Fabian and has not been written up here yet - do that before
     any code lands. -->

mode: outcome-defined - the target is a stated vision to be checked and then implemented, not an
open-ended investigation. lock the output and prove unknown mechanics small before building.

## Architecture

<!-- fill in: key components, data flows, external systems, rough diagram if useful -->

## Active Context

<!-- current focus, open branches, known issues, next steps — update at end of session -->

## Tech Stack

python (uv, ruff, pytest) - `uv run ruff check . --fix && uv run ruff format .` before every commit.

not yet decided, to be settled during planning:
- browser frontend. `ui_base` (the sibling repo) supplies css and plain scripts with no build step
  and no npm, so a js toolchain may not be needed at all - decide before adding one
- docker for the backend

## Related repos

this project spans repos. `ui_base` (`../ui_base`) is the shared interface package - read its
`CLAUDE.md` before writing any interface code. all final results live here in smortboard.

ledgers: `TASKS.jsonl` and `WORKLOG.md` are symlinks into the private `../dev_ledgers` repo. they
resolve only while both repos sit side by side under the same parent.

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
