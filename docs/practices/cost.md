# Cost and budgets

**Set a budget before you run a board.** Caps are set once and checked on every path that starts a
run. Model choice and card size show up as cost, and spend wasted on refused, crashed or rejected
attempts gets its own line.

## How it works

| key | shows |
|---|---|
| `i` | one card per attempt: turns, fix rounds, refusals, the model behind each role |
| `c` | each board: runs, accepted cards, pull requests, cost per pull request, plus cost optimisation: cap fit by complexity, a suggested cap per role, and spend wasted on refused, crashed, rejected or capped attempts |
| `u` | rate-limit windows per credential profile, and spend per model |

**Unpriced runs are counted, not guessed.** Every figure in `c` shows the known spend, with
"+ n unknown" beside it for runs that reported no price. A figure with no price at all reads
`unknown`.

**`u` counts only smortboard's own runs.** One section per credential profile, one line per window:
percent used, when it resets, time left. It adds up what smortboard's runs reported, not your lab
account; use outside smortboard does not show.

![Cost overview across boards](../images/costs.jpg)

**Cost per board** `c`

![Card telemetry](../images/telemetry.jpg)

**Card cost** `i`

![Usage panel](../images/usage.jpg)

**Usage** `u`

<picture>
  <source media="(prefers-color-scheme: light)" srcset="../images/art-cost-credit-light.svg">
  <img src="../images/art-cost-credit-dark.svg" alt="One result&#x27;s spend by model: claude-sonnet-5 $0.2476 for 822 output tokens, claude-haiku-4-5 $0.0009 for 17." width="100%">
</picture>

### Three caps

Each is optional and independent.

| Cap | Scope | Where |
|---|---|---|
| per-run budget, one per role | one run of a worker, reviewer, mission control turn or fold | `o` -> cost control -> spend caps |
| a board's **daily budget** | that board's whole day, UTC | `shift`+`o` |
| a card's **total cap** | one card across every run it has ever made, restarts included | `o` -> cost control -> spend caps |

- The daily budget also counts mission control and fold turns, even one that failed or hit its own
  per-run cap. Once today's spend reaches it, new cards stop starting.
- The scheduler, a manual `r`, an inbox answer and a lease approval all check the same caps.
- Running cards always finish.
