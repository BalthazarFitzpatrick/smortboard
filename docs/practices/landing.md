# Landing and main

**Main is yours.**

Agents land on `development`; you merge `main`. Keep review required and accept each card with `y`.
Once you trust a board, enable **free merge** in `o`, then switch that board to free in `shift`+`o`
or with `shift`+`a`. The board lands one card at a time per branch, so no two pushes race, and never
on `main`, `master` or `trunk`.

> [!IMPORTANT]
> **Refused in code, not by habit.**
> `pr merge` is not on the board's `gh` allowlist and cannot be added by a caller. Protected
> branches are refused as a landing target in code.

## How it works

### Merge modes

Every base except the protected ones follows the board's mode:

| Mode | A card |
|---|---|
| **review required** (default) | stops at an open pull request. `y` accepts and lands it in the background; if you already merged it on GitHub, accept records that without merging again |
| **free merge** | lands and is accepted once tests and review pass |

**Free merge** in `o` is one switch for every board: enabled or disabled, disabled by default. While
it is disabled no board can pick free; disabling it puts every board back on review. While it is
enabled, each board picks review or free in `shift`+`o` under *merge mode*, and `shift`+`a` flips the
focused board after a confirmation. A free-merge board shows a burnt-orange pulsing frame and a text
label; reduced motion keeps the frame static.

The board keeps one standing pull request from the development branch into `main`, for you to
merge. Accepting a protected-base card records your decision; it cannot merge the pull request.
[Protect main](../protect-main.md) has the branch setup.

### The local base

Every card is cut from `origin/<base>`, fetched just before; a stacked child is cut from its
parent's branch instead. Work landed by another card or merged on GitHub is in it even when nobody
pulled. If the fetch fails, the card is cut from the local branch and gets a note saying so.

A landing pushes to origin only, so the board then moves your local base branch up to match. It
does this only when it is a plain fast-forward: a branch with local commits, or checked out with
uncommitted changes to tracked files, is left alone. `main`, `master` and `trunk` never move. The
[waiting pull request](waiting-prs.md) sweep, `smortboard-land` and mission control's read of the
repo do the same.

### Stacks in review mode

Dependent work keeps moving: a child can start on one unmerged parent's branch once that parent
reaches checking. Its pull request targets the parent's branch until the parent lands, then moves to
the repo base.

- stacks are at most three cards deep
- a card with two unmerged parents waits
- a rejected parent blocks its dependents
- a child conflict never undoes a parent's successful landing

A rejected parent keeps its local branch while undecided children need it. Those children cannot
run against rejected work, and the parent cannot rerun while they stay undecided. Decide the
dependent attempts and create fresh work; the board does not rewrite an existing stack.

### The landing lock

One holder per repo and target branch, with a FIFO queue behind it. The lock lives in the database,
so it survives a board restart. A holder that stops heartbeating past its TTL is evicted, so a dead
process cannot wedge the queue. `q` shows every holder and the queue behind it.

Your own agents and sessions outside the board can take the same lock, so they do not race the
board's pushes:

```bash
uv run smortboard-land --repo . --target development -- uv run pytest -q
```

It waits for the lock, fetches, merges the target in, runs your test command, pushes, and always
releases. It exits non-zero with the reason on a conflict, a failed test or a rejected push, and
refuses `main`, `master` and `trunk` outright.

### The gh allowlist

Every `gh` call goes through exactly five subcommands: `pr create`, `pr list`, `pr view`,
`pr close` and `pr edit`, the last only to re-point a stacked child's pull request at the base when
its parent lands. A push rejected because the base moved is re-synced and retried, never forced.
