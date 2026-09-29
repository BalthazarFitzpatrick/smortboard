# landing and main

**main is yours, unless you say otherwise.**

agents land on `development`; you merge `main`. keep review required and accept each card with `y`.
once you trust a board, enable **free merge** in `o`, then switch that board to free in `shift`+`o`
or with `shift`+`a`. the board lands one card at a time per branch, so no two landings race, and
never on the board's **off-limit branches**.

> [!IMPORTANT]
> **refused in code, not by habit.**
> every board has a list of off-limit branches, `main`, `master` and `trunk` unless you change it in
> `shift`+`o`. the board refuses them as a landing target in code. the **off-limit branches** switch
> in `o` lifts every list at once; disabled, a board may land on any branch, `main` included.
> nothing is ever force-pushed either way.

## how it works

### merge modes

every base that is not off limits follows the board's mode:

| mode | a card |
|---|---|
| **review required** (default) | stops at an open pull request. `y` accepts and lands it in the background; if you already merged it on github, accept records that without merging again |
| **free merge** | lands and is accepted once tests and review pass |

**free merge** in `o` is one switch for every board: enabled or disabled, disabled by default. while
it is disabled no board can pick free; disabling it puts every board back on review. while it is
enabled, each board picks review or free in `shift`+`o` under *merge mode*, and `shift`+`a` flips the
focused board after a confirmation. a free-merge board shows a burnt-orange pulsing frame and a text
label; reduced motion keeps the frame static.

a landing merges the card's own pull request: `gh pr merge --merge --match-head-commit`, pinned to
the commit the tests and the reviewer saw. github refuses it if anything reached the branch after
that, and a ruleset that only accepts pull requests, like [protect main](../protect-main.md), accepts
it. if github refuses for another reason, a required check or a token without merge rights, the card
stays in checking with github's words on it.

the board keeps one standing pull request from the development branch into `main`, for you to
merge. accepting a card whose base is off limits records your decision; it cannot merge the pull
request.

### the local base

every card is cut from `origin/<base>`, fetched just before; a stacked child is cut from its
parent's branch instead. work landed by another card or merged on github is in it even when nobody
pulled. if the fetch fails, the card is cut from the local branch and gets a note saying so.

a landing pushes to origin only, so the board then moves your local base branch up to match. it
does this only when it is a plain fast-forward: a branch with local commits, or checked out with
uncommitted changes to tracked files, is left alone. an off-limit branch never moves. the
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
refuses a target the board holds off limits: the board answers for every board that has the repo,
and for a repo no board knows, `main`, `master` and `trunk`.

### the gh allowlist

every `gh` call goes through exactly six subcommands: `pr create`, `pr list`, `pr view`,
`pr close`, `pr edit`, only to re-point a stacked child's pull request at the base when its parent
lands, and `pr merge`, only to land a card, never with `--auto`, `--admin` or `--delete-branch`. a
base that moved since the sync is re-synced and retested, never merged untested and never forced.
