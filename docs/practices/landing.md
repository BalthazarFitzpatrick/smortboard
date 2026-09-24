# landing and main

**main is yours.**

agents land on `development`; you merge `main`. keep review required and accept each card with `y`.
once you trust a board, enable **free merge** in `o`, then switch that board to free in `shift`+`o`
or with `shift`+`a`. the board lands one card at a time per branch, so no two pushes race, and never
on `main`, `master` or `trunk`.

> [!IMPORTANT]
> **refused in code, not by habit.**
> `pr merge` is not on the board's `gh` allowlist and cannot be added by a caller. protected
> branches are refused as a landing target in code.

## how it works

### merge modes

every base except the protected ones follows the board's mode:

| mode | a card |
|---|---|
| **review required** (default) | stops at an open pull request. `y` accepts and lands it in the background; if you already merged it on github, accept records that without merging again |
| **free merge** | lands and is accepted once tests and review pass |

**free merge** in `o` is one switch for every board: enabled or disabled, disabled by default. while
it is disabled no board can pick free; disabling it puts every board back on review. while it is
enabled, each board picks review or free in `shift`+`o` under *merge mode*, and `shift`+`a` flips the
focused board after a confirmation. a free-merge board shows a burnt-orange pulsing frame and a text
label; reduced motion keeps the frame static.

the board keeps one standing pull request from the development branch into `main`, for you to
merge. accepting a protected-base card records your decision; it cannot merge the pull request.
[protect main](../protect-main.md) has the branch setup.

### the local base

every card is cut from `origin/<base>`, fetched just before; a stacked child is cut from its
parent's branch instead. work landed by another card or merged on github is in it even when nobody
pulled. if the fetch fails, the card is cut from the local branch and gets a note saying so.

a landing pushes to origin only, so the board then moves your local base branch up to match. it
does this only when it is a plain fast-forward: a branch with local commits, or checked out with
uncommitted changes to tracked files, is left alone. `main`, `master` and `trunk` never move. the
[waiting pull request](waiting-prs.md) sweep, `smortboard-land` and mission control's read of the
repo do the same.

### stacks in review mode

dependent work keeps moving: a child can start on one unmerged parent's branch once that parent
reaches checking. its pull request targets the parent's branch until the parent lands, then moves to
the repo base.

- stacks are at most three cards deep
- a card with two unmerged parents waits
- a rejected parent blocks its dependents
- a child conflict never undoes a parent's successful landing

a rejected parent keeps its local branch while undecided children need it. those children cannot
run against rejected work, and the parent cannot rerun while they stay undecided. decide the
dependent attempts and create fresh work; the board does not rewrite an existing stack.

### the landing lock

one holder per repo and target branch, with a fifo queue behind it. the lock lives in the database,
so it survives a board restart. a holder that stops heartbeating past its ttl is evicted, so a dead
process cannot wedge the queue. `q` shows every holder and the queue behind it.

your own agents and sessions outside the board can take the same lock, so they do not race the
board's pushes:

```bash
uv run smortboard-land --repo . --target development -- uv run pytest -q
```

it waits for the lock, fetches, merges the target in, runs your test command, pushes, and always
releases. it exits non-zero with the reason on a conflict, a failed test or a rejected push, and
refuses `main`, `master` and `trunk` outright.

### the gh allowlist

every `gh` call goes through exactly five subcommands: `pr create`, `pr list`, `pr view`,
`pr close` and `pr edit`, the last only to re-point a stacked child's pull request at the base when
its parent lands. a push rejected because the base moved is re-synced and retried, never forced.
