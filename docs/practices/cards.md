# Cards

**Write the card once, completely.**

The agent has only what you write: a short title, criteria your tests can check, a lease. Size it to
one pull request, decide it before it starts, then leave it; it finishes unattended, and the history
reads like a changelog.

!!! warning "A card is not a conversation"
    It is feature-sized, not edit-sized. Nothing you meant but did not write reaches the agent.

## How it works

![The card lifecycle: preparing, running, testing, reviewing, fixing, opening, then opened or landing. Reviewer findings on the fix route go back to the worker at most twice; a question, a limit, a crash, a lease conflict, failed tests or a rejected review block the card and wait in the inbox.](../images/art-lifecycle-dark.svg#only-dark)
![The card lifecycle: preparing, running, testing, reviewing, fixing, opening, then opened or landing. Reviewer findings on the fix route go back to the worker at most twice; a question, a limit, a crash, a lease conflict, failed tests or a rejected review block the card and wait in the inbox.](../images/art-lifecycle-light.svg#only-light)

### Board

A named set of cards and the repos they work in, one or several. Use one board per repo, or per
product when the work crosses repos. `1`-`9` jump between boards.

Each board has its own parallelism cap, daily spend budget, merge mode (review required or free
merge, see [landing](landing.md)) and lease mode (strict or soft, see [leases](leases.md)). So each
board is a queue you can leave running, and one can run hot while another stays cautious.

### The card

![A card opened over the board, one column: what it is about, what it has done, what it needs, then its details folded to one line each](../images/hero-card.jpg)

An open card reads top to bottom:

| Section | Holds |
|---|---|
| **about** | what it is for |
| **done** | what the agent delivered, with the test and review verdicts |
| **needs** | the one thing it wants from you, if anything |
| **details** | criteria, lease, dependencies, attachments and history, one line each |

The panel is as tall as its content. Arrow keys move between sections with the board's highlight
frame.

A card holds a title, a description, **acceptance criteria**, a task list, **leases** (the paths its
agent may write), dependencies on other cards, optionally a **model**, and a **complexity** of low,
medium or high for [cost analysis](cost.md).

### Card text is short

| Field | At most |
|---|---|
| title | 8 words |
| description | 20 words |
| each criterion | 12 words |

[Mission control and fold](mission-control.md) are told so, and the board leaves a note when a card
runs long. `POST /api/cards`, and a `PATCH` that changes a title or description, return the card plus
a `warnings` list, empty when it fits. Nothing is refused or cut.

### Statuses

Five are stored: `todo`, `doing`, `checking`, `accepted`, `rejected`. Read the reason code, not the
column: it says what to do next.

!!! warning "Blocked is a flag, not a status"
    A blocked card keeps its status and raises a `blocked_reason_code`. A sixth status would throw
    away what the card was doing, which the resume briefing needs to restart it.

So a blocked card resumes exactly where it stopped, worktree and commits intact.

### Reason codes

| Code | What happened |
|---|---|
| `AGENT_QUESTION` | the agent stopped to ask you something |
| `TESTS_FAILED` | the board re-ran the repo's tests and they failed |
| `BASE_RED` | the tests fail, but they fail the same way on the base without the card's changes |
| `REVIEW_REJECTED` | the reviewer refused the diff |
| `LEASE_CONFLICT` | it committed a path outside its lease, or a refused write left it with nothing committed |
| `MERGE_CONFLICT` | its branch no longer merges cleanly with the base |
| `OUTDATED` | the base moved under it and its commits no longer rebase onto it; answering redoes it on a fresh tree, the old commits kept on a backup ref |
| `DEPENDENCY_REJECTED` | a card this one depends on was rejected |
| `USAGE_LIMIT` | the credential hit its rate limit |
| `API_UNREACHABLE` | the API was unreachable; the board retries this one itself, with backoff |
| `CRASH` | the run died, or the board was restarted mid-run |

### Card edges

State shows on the edge, not as a fill.

![Card edges by state: blue working, vanilla attention with a stepped glow, lichen accepted, red rejected](../images/card-states.jpg)

| Edge | Means |
|---|---|
| **blue** | an agent is working it |
| **vanilla**, stepped glow | it needs you |
| **lichen** | accepted |
| **red** | rejected |

The blue is cold on purpose, so no pair collapses under red-green colour blindness.

## What makes a good card

**A brief that stands alone.** The agent gets the card and the repo, not your afternoon. What lives
only in your head goes in the description: why this approach and not the obvious one, the constraint
that makes the simple version wrong, the file that looks relevant and is not. Otherwise it gets
rediscovered expensively, or not at all.

**Criteria a machine can check.** The reviewer does not judge them; only the test command does (see
[the two gates](gates.md)). "Handles errors gracefully" is not a criterion. "Returns 400 with
`{"error": ...}` on a malformed body, covered by a test" is. For a criterion with no test, decide
honestly whether you will verify it by hand, and expect to.

**A narrow lease that does not overlap.** Narrow because it is a boundary; apart because overlapping
leases serialise cards that could run together. Cover every file the card must touch, tests
included: a write it needed and did not have is refused. A soft board lets it reach an unprotected
file on its own. [Leases](leases.md) has what happens to a refused write.

**One feature, not one edit.** A single edit spends most of its money on the agent reading its way
in. Three features have no clean point where the card is done. The board is tuned for feature-sized
cards.

**The model matched to the work.** Sonnet for ordinary work, Opus where the card needs design
judgement, Haiku for mechanical edits. Mission control proposes one from what has been reaching pull
requests cleanly on that board; `m` overrides it (see [labs](labs.md)).
