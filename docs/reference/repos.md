# repos and test commands

## the repo

**`b` sets a repo up; you only push `main`.** **new board** takes any folder and adds what is
missing: git and a starter commit, a `development` branch, a private github repo as origin, and
`development` pushed there. it never pushes `main`: when it created the github repo, it shows the one
command that does. **from online repo** clones one of yours instead. both folder pickers open in
where new repos go (`o`).

each repo row in `b` shows its origin, or "no origin" (cards cannot open pull requests without
one), and its tests, lint and image fields. lint is editable there. **add a repo by hand** is for a
second repo on a board. cards land on `development`; see [protect main](../protect-main.md).

## the test command

**tests are required.** the cards write them; this command runs them, on every card, in the gate.
when a repo is added the board detects it from the committed files (python: `uv run --no-sync pytest -q`;
node: `npm test` with a test script, else `node --test`). a repo with none gets a first card from
mission control that adds a test suite, and a brand-new repo gets its command named by mission
control's first card. mission control's proposal is stored only when the repo has no command yet,
and only a plain test-runner call (no shell syntax) is accepted.

the test command is the gate, so give it everything your ci checks. if ci runs `ruff check` and the
gate does not, a card can pass its gate and still fail ci once it lands.

the gate mounts the worktree read-only and exports `RUFF_CACHE_DIR` and
`PYTEST_ADDOPTS=-p no:cacheprovider` itself. those two need no `--no-cache`; any other tool with its
own cache does:

```bash
uv run --no-sync ruff format --check --no-cache --extend-exclude .claude . && uv run --no-sync ruff check --no-cache --extend-exclude .claude . && uv run --no-sync pytest -q -p no:cacheprovider
```

## the repo image

**the gate is offline.** a repo whose tests need more than git, uv and python gets its own image
with its toolchain already installed.
[`docker/repo.Dockerfile`](https://github.com/BalthazarFitzpatrick/smortboard/blob/main/docker/repo.Dockerfile)
is the template.

preflight (`h`) flags an image older than the repo's `uv.lock` or its own base image. the board
rebuilds it itself before the next card runs on that repo.

give docker desktop a memory limit (settings -> resources), and lower
[`max_parallel`](configuration.md) if it is tight.
