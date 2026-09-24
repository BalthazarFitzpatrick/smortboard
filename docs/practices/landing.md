# Landing and main

**Main is yours.**

The board lands one card at a time per branch, so no two pushes race, and never on `main`, `master`
or `trunk`. Agents land on `development` and you merge `main`: keep review required, accept with
`y`, and switch a board to free merge with `shift+a` once you trust it.

> [!IMPORTANT]
> **Refused in code, not by habit.**
> `pr merge` is not on the board's `gh` allowlist and cannot be added by a caller. Protected
> branches are refused as a landing target in code.

## How it works

### Merge modes

Other bases follow the board's mode:

| Mode | A card |
|---|---|
| **review required** (default) | stops at an open pull request. `y` accepts and lands it in the background; if you already merged it on GitHub, accept records that without merging again |
| **free merge** | lands and is accepted once tests and review pass |

Free merge is off until you turn on `allow_free_merge` in `o`; until then a board refuses it, and
turning it off puts every board back to review required. `shift+a` changes the focused board's mode
after confirmation. A free-merge board shows a
burnt-orange pulsing frame and a text label; reduced motion keeps the frame static.

The board keeps one standing pull request from the development branch into `main`, for you to
merge. Accepting a protected-base card records your decision; it cannot merge the PR.
[Protect main](../protect-main.md) has the branch setup.

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

One holder per repo and target branch, with a FIFO queue behind it. It is stored in the database,
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
