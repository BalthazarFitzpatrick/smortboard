# Repos and test commands

## The repo

A repo must already exist on GitHub with its default branch pushed. The board registers it; it
never creates it. Make the default branch `development` (pushed to the remote) and the board lands
cards there itself. See [Protect main](../protect-main.md).

## The test command

The test command is the gate, so give it everything your CI checks. If CI runs `ruff check` and the
gate does not, a card can pass its gate and still fail CI once it lands.

The gate mounts the worktree read-only and exports `RUFF_CACHE_DIR` and
`PYTEST_ADDOPTS=-p no:cacheprovider` itself. Those two need no `--no-cache`; any other tool with its
own cache does:

```bash
uv run --no-sync ruff format --check --no-cache --extend-exclude .claude . && uv run --no-sync ruff check --no-cache --extend-exclude .claude . && uv run --no-sync pytest -q -p no:cacheprovider
```

## The repo image

The gate is offline. A repo whose tests need more than git, uv and Python gets its own image with
its toolchain already installed.
[`docker/repo.Dockerfile`](https://github.com/BalthazarFitzpatrick/smortboard/blob/main/docker/repo.Dockerfile)
is the template.

Preflight (`h`) flags an image older than the repo's `uv.lock` or its own base image. The board
rebuilds it itself before the next card runs on that repo.

Give Docker Desktop a memory limit (Settings -> Resources), and lower
[`max_parallel`](configuration.md) if it is tight.
