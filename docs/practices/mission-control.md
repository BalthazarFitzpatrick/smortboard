# Mission control and workforce

**Plan in mission control, steer in workforce.** One conversation gives you cards, each with a
lease, a model and a complexity. Then leave the agents alone unless something is off.

## How it works

| key | chat | talks to |
|---|---|---|
| `.` | mission control | the board's planner: say what to build, get cards |
| `,` | workforce | one card's agent, while it works |

### Mission control

**It replies in conversation and proposes cards**, each with leases, a complexity and a suggested
model. It reads fresh read-only clones of the board's repos with Read, Grep and Glob only: no Edit,
Write or Bash. It sees what each model has cost and passed on this board, and is told to prefer the
cheapest model that reaches clean pull requests on similar cards.

> [!IMPORTANT]
> **The board makes the cards.**
> Mission control never writes to the board. The board builds cards from its structured reply. It
> resolves repo names against its own repos instead of guessing, and drops any lease that covers
> the whole repo or climbs out of it.

![Mission control drawer](../images/mission-control.jpg)

### Folding

**`f` proposes which `todo` cards one agent should do as one card.** It is the other board-level
turn. The board creates the merged card, re-points dependencies at it and deletes the originals
restorably, so a fold is not a one-way door.

- Only `todo` cards fold. A running card holds a branch and a run; folding would orphan both.
- A group past four cards or twelve criteria is refused, whatever the model said, so a fold never
  makes a card nobody can finish.

### Workforce

**`,` is a terminal onto the focused card's agent.** With no card focused it rotates through every
working card. A **note** sent here reaches a *running* agent between two tool calls.

![Workforce drawer pinned to one card](../images/workforce.jpg)

Each run mints a fresh marker, so repo text cannot pose as you. The agent trusts only notes carrying
it, and names anything else claiming authority mid-run as a suspected prompt injection.

<picture>
  <source media="(prefers-color-scheme: light)" srcset="../images/art-steering-light.svg">
  <img src="../images/art-steering-dark.svg" alt="A steered run: the brief, git log, reading one.txt, then at 9.2 seconds a note carrying the run&#x27;s marker asks it to skip three.txt and end with PINEAPPLE. The note lands between two tool calls, the agent reads two.txt, and at 12.8 seconds its summary ends in PINEAPPLE, three.txt never opened, no injection flag raised, for $0.055." width="100%">
</picture>
