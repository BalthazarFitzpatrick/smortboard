# Cost and budgets

**Set a budget before you run a board.** Caps are set once and checked on every path that starts a
run. Model choice and card size become visible costs, and waste on refused, crashed or rejected
attempts gets its own line.

## How it works

| key | shows |
|---|---|
| `i` | one card per attempt: turns, fix rounds, refusals, the model behind each role |
| `c` | each board: runs, accepted cards, pull requests, cost per pull request, plus cost optimisation: cap fit by complexity, a suggested cap per role, and spend wasted on refused, crashed, rejected or capped attempts |
| `u` | rate-limit windows and spend per model |

![Cost overview across boards](../images/costs.jpg)

**Cost per board** `c`

![Card telemetry](../images/telemetry.jpg)

**Card cost** `i`

![Usage panel](../images/usage.jpg)

**Usage** `u`

![One result's spend by model: claude-sonnet-5 $0.2476 for 822 output tokens, claude-haiku-4-5 $0.0009 for 17.](../images/art-cost-credit-dark.svg#only-dark)
![One result's spend by model: claude-sonnet-5 $0.2476 for 822 output tokens, claude-haiku-4-5 $0.0009 for 17.](../images/art-cost-credit-light.svg#only-light)

### Three caps

Each is optional and independent.

| Cap | Scope |
|---|---|
| per-run budget, set per role in settings | one run of a worker, reviewer, mission control turn or fold |
| a board's **daily budget** | that board's whole day, UTC |
| a card's **total cap** | one card across every run it has ever made, restarts included |

- The daily budget also counts mission control and fold turns, even one that failed or hit its own
  per-run cap.
- The scheduler, a manual `r`, an inbox answer and a lease approval all check the same caps.
- Running cards always finish.
