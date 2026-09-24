# development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest -q          # includes the js suite (tests/js/*.mjs) when node is installed
```

**the suite runs in parallel by default (`-n auto`).** it is over a thousand small i/o-bound tests:
measured 2026-09-18, 129 seconds on one process and 24 seconds under `-n auto` on ten cores. pass
`-n0` for one test's output in order, or a debugger.

## the loop

**commit as often as the work wants.**

| when | what runs |
|---|---|
| every commit | ruff only, about a second |
| while you work | the file you are changing |
| after a failure | `--lf` |
| before you push | the whole suite, once |

running the suite per commit buys nothing ci does not already guarantee. it turns an afternoon of
small commits into an afternoon of waiting.

## ci

**no test calls a model.** ci runs the same checks on every pull request, and they must pass
before anything reaches `main`. a markdown-only change skips the toolchain and the suite but still reports,
so a docs pull request never waits on a required check that never starts. a second push to a branch
cancels the run it superseded.

## where things are

| path | holds |
|---|---|
| [`tools/shoot_docs_images.py`](https://github.com/BalthazarFitzpatrick/smortboard/blob/main/tools/shoot_docs_images.py) | retakes the screenshots in `docs/images/` against the demo board |
| [`docs/plan.md`](../plan.md) | the plan and the containment reasoning |
| [`docs/phase1-contracts.md`](../phase1-contracts.md) | the schema and api |
| [`docs/prompts.md`](../prompts.md) | the prompt layering |
| `docs/spikes/` | what was proven before it was built on, [s1-s2](../spikes/s1-s2-findings.md) to [s7](../spikes/s7-codex-auth.md) |
