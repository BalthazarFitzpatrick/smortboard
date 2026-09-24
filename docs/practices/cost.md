# cost and budgets

**set a budget before you run a board.** caps are set once and checked on every path that starts a
run. model choice and card size show up as cost, and spend wasted on refused, crashed or rejected
attempts gets its own line.

## how it works

| key | shows |
|---|---|
| `i` | one card per attempt: turns, fix rounds, refusals, the model behind each role |
| `c` | each board: runs, accepted cards, pull requests, cost per pull request, plus cost optimisation: cap fit by complexity, a suggested cap per role, and spend wasted on refused, crashed, rejected or capped attempts |
| `u` | rate-limit windows per credential profile, and spend per model |

**unpriced runs are counted, not guessed.** every figure in `c` shows the known spend, with
"+ n unknown" beside it for runs that reported no price. a figure with no price at all reads
`unknown`.

**`u` counts only smortboard's own runs.** one section per credential profile, one line per window:
percent used, when it resets, time left. it adds up what smortboard's runs reported, not your lab
account; use outside smortboard does not show.

![cost overview across boards](../images/costs.jpg)

**cost per board** `c`

![card telemetry](../images/telemetry.jpg)

**card cost** `i`

![usage panel](../images/usage.jpg)

**usage** `u`

<picture>
  <source media="(prefers-color-scheme: light)" srcset="../images/art-cost-credit-light.svg">
  <img src="../images/art-cost-credit-dark.svg" alt="one result&#x27;s spend by model: claude-sonnet-5 $0.2476 for 822 output tokens, claude-haiku-4-5 $0.0009 for 17." width="100%">
</picture>

### three caps

each is optional and independent.

| cap | scope | where |
|---|---|---|
| per-run budget, one per role | one run of a worker, reviewer, mission control turn or fold | `o` -> cost control -> spend caps |
| a board's **daily budget** | that board's whole day, utc | `shift`+`o` |
| a card's **total cap** | one card across every run it has ever made, restarts included | `o` -> cost control -> spend caps |

- the daily budget also counts mission control and fold turns, even one that failed or hit its own
  per-run cap. once today's spend reaches it, new cards stop starting.
- the scheduler, a manual `r`, an inbox answer and a lease approval all check the same caps.
- running cards always finish.
