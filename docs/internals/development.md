# Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest -q          # includes the js suite (tests/js/*.mjs) when node is installed
```

**The suite runs in parallel by default (`-n auto`).** It is over a thousand small I/O-bound tests:
measured 2026-09-18, 129 seconds on one process and 24 seconds under `-n auto` on ten cores. Pass
`-n0` for one test's output in order, or a debugger.

## The loop

**Commit as often as the work wants.**

| When | What runs |
|---|---|
| every commit | ruff only, about a second |
| while you work | the file you are changing |
| after a failure | `--lf` |
| before you push | the whole suite, once |

Running the suite per commit buys nothing CI does not already guarantee. It turns an afternoon of
small commits into an afternoon of waiting.

## CI

**No test calls a model.** CI runs the same checks on every pull request, and they must pass
before anything reaches `main`. A markdown-only change skips the toolchain and the suite but still reports,
so a docs pull request never waits on a required check that never starts. A second push to a branch
cancels the run it superseded.

## Where things are

| Path | Holds |
|---|---|
| [`tools/shoot_docs_images.py`](https://github.com/BalthazarFitzpatrick/smortboard/blob/main/tools/shoot_docs_images.py) | retakes the screenshots in `docs/images/` against the demo board |
| [`docs/PLAN.md`](../PLAN.md) | the plan and the containment reasoning |
| [`docs/PHASE1-CONTRACTS.md`](../PHASE1-CONTRACTS.md) | the schema and API |
| [`docs/PROMPTS.md`](../PROMPTS.md) | the prompt layering |
| `docs/spikes/` | what was proven before it was built on, [S1-S2](../spikes/S1-S2-findings.md) to [S7](../spikes/S7-codex-auth.md) |
