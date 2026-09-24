# Repos and test commands

## The repo

**`b` sets a repo up; you only push `main`.** **new board** takes any folder and adds what is
missing: git and a starter commit, a `development` branch, a private GitHub repo as origin, and
`development` pushed there. It never pushes `main`: when it created the GitHub repo, it shows the one
command that does. **from online repo** clones one of yours instead. Both folder pickers open in
where new repos go (`o`).

Each repo row in `b` shows its origin, or "no origin" (cards cannot open pull requests without
one), and its tests, lint and image fields. Lint is editable there. **add a repo by hand** is for a
second repo on a board. Cards land on `development`; see [Protect main](../protect-main.md).

## The test command

**Tests are required.** The cards write them; this command runs them, on every card, in the gate.
When a repo is added the board detects it from the committed files (Python: `uv run --no-sync pytest -q`;
Node: `npm test` with a test script, else `node --test`). A repo with none gets a first card from
mission control that adds a test suite, and a brand-new repo gets its command named by mission
control's first card. Mission control's proposal is stored only when the repo has no command yet,
and only a plain test-runner call (no shell syntax) is accepted.

The test command is the gate, so give it everything your CI checks. If CI runs `ruff check` and the
gate does not, a card can pass its gate and still fail CI once it lands.

The gate mounts the worktree read-only and exports `RUFF_CACHE_DIR` and
`PYTEST_ADDOPTS=-p no:cacheprovider` itself. Those two need no `--no-cache`; any other tool with its
own cache does:

```bash
uv run --no-sync ruff format --check --no-cache --extend-exclude .claude . && uv run --no-sync ruff check --no-cache --extend-exclude .claude . && uv run --no-sync pytest -q -p no:cacheprovider
```

## The repo image

**The gate is offline.** A repo whose tests need more than git, uv and Python gets its own image
with its toolchain already installed.
[`docker/repo.Dockerfile`](https://github.com/BalthazarFitzpatrick/smortboard/blob/main/docker/repo.Dockerfile)
is the template.

Preflight (`h`) flags an image older than the repo's `uv.lock` or its own base image. The board
rebuilds it itself before the next card runs on that repo.

Give Docker Desktop a memory limit (Settings -> Resources), and lower
[`max_parallel`](configuration.md) if it is tight.
