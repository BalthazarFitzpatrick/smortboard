# cards

**write the card once, completely.**

the agent has only what you write: a short title, criteria your tests can check, a lease. size it to
one pull request, decide it before it starts, then leave it. it finishes unattended, and the history
reads like a changelog.

> [!IMPORTANT]
> **a card is not a conversation.**
> it is feature-sized, not edit-sized. nothing you meant but did not write reaches the agent.

## how it works

<picture>
  <source media="(prefers-color-scheme: light)" srcset="../images/art-lifecycle-light.svg">
  <img src="../images/art-lifecycle-dark.svg" alt="the card lifecycle: preparing, running, testing, reviewing, fixing, opening, then opened or landing. reviewer findings on the fix route go back to the worker at most twice; a question, a limit, a crash, a lease conflict, failed tests or a rejected review block the card and wait in the inbox." width="100%">
</picture>

### board

a named set of cards and the repos they work in, one or several. one board per repo, or per product
when the work crosses repos. `1`-`9` jump between boards.

each board has its own settings in `shift`+`o`: merge mode (review or free, see
[landing](landing.md)), file lease (strict or soft, see [leases](leases.md)), cards at once and a
daily budget. free and soft are only on offer while their switch in `o` is enabled. so each board is
a queue you can leave running, and one can run hot while another stays cautious.

### the card

![a card opened over the board as a 2x2: what it is about and what it has done on top, what it needs and its details folded to one line each below](../images/hero-card.jpg)

an open card is its title over a two-by-two grid:

| | left | right |
|---|---|---|
| **top** | **about**: what it is for | **done**: what the agent delivered, with the test and review verdicts |
| **bottom** | **needs**: the one thing it wants from you, if anything | **details**: criteria, lease, dependencies, attachments and history, one line each |

every cell is one height; a longer section fades out at the bottom. arrow keys move between sections
with the board's highlight frame. `space` on a section pops it out whole over the card, folds open.
the needs popout keeps its note field: `/` types, `enter` sends. `space` or `escape` closes the
popout; `space` on the title closes the card.

a card holds a title, a description, **acceptance criteria**, a task list, **leases** (the paths its
agent may write), dependencies on other cards, optionally a **model**, and a **complexity** of low,
medium or high for [cost analysis](cost.md).

### card text is short

| field | at most |
|---|---|
| title | 8 words |
| description | 20 words |
| each criterion | 12 words |

[mission control and fold](mission-control.md) are told so, and the board leaves a note when a card
runs long. `POST /api/cards`, and a `PATCH` that changes a title or description, return the card plus
a `warnings` list, empty when it fits. nothing is refused or cut.

### statuses

five are stored: `todo`, `doing`, `checking`, `accepted`, `rejected`. read the reason code, not the
column: it says what to do next.

> [!IMPORTANT]
> **blocked is a flag, not a status.**
> a blocked card keeps its status and raises a `blocked_reason_code`. a sixth status would throw
> away what the card was doing, which the resume briefing needs to restart it.

so a blocked card resumes exactly where it stopped, worktree and commits intact.

### reason codes

| code | what happened |
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
| `API_UNREACHABLE` | the api was unreachable; the board retries this one itself, with backoff |
| `CRASH` | the run died, or the board was restarted mid-run |

### card edges

state shows on the edge, not as a fill.

![card edges by state: blue working, vanilla attention with a stepped glow, lichen accepted, red rejected](../images/card-states.jpg)

| edge | means |
|---|---|
| **blue** | an agent is working it |
| **grey**, slow pulse | queued by `w`, not started yet |
| **vanilla**, stepped glow | it needs you |
| **lichen** | accepted |
| **red** | rejected |

the blue is cold on purpose, so no pair collapses under red-green colour blindness.

## what makes a good card

**a brief that stands alone.** the agent gets the card and the repo, not your afternoon. what lives
only in your head goes in the description: why this approach and not the obvious one, the constraint
that makes the simple version wrong, the file that looks relevant and is not. otherwise it gets
rediscovered at a cost, or not at all.

**criteria a machine can check.** the reviewer does not judge them; only the test command does (see
[the two gates](gates.md)). "handles errors gracefully" is not a criterion. "returns 400 with
`{"error": ...}` on a malformed body, covered by a test" is. for a criterion with no test, decide
honestly whether you will verify it by hand, and expect to.

**a narrow lease that does not overlap.** narrow because it is a boundary; apart because overlapping
leases serialise cards that could run together. cover every file the card must touch, tests
included: a write it needed and did not have is refused. a soft board lets it reach an unprotected
file on its own. [leases](leases.md) has what happens to a refused write.

**one feature, not one edit.** a single edit spends most of its money on the agent reading its way
in. three features have no clean point where the card is done. the board is tuned for feature-sized
cards.

**the model matched to the work.** sonnet for ordinary work, opus where the card needs design
judgement, haiku for mechanical edits. mission control proposes one from what has been reaching pull
requests cleanly on that board; `m` overrides it (see [labs](labs.md)).
