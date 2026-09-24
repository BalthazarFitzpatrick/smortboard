# Attention and inbox

**Watch one column.**

Anything that needs you lands in attention; everything else runs itself. "What is waiting for me?"
is one look at the board, not a round of opening cards. Glance, act, leave.

> [!IMPORTANT]
> **Attention is not a status.**
> It is a presentation column between doing and checking. It shows any card with a blocked reason
> or a review flag, whatever status it holds underneath.

## How it works

### The column

Six columns: `todo`, `doing`, `attention`, `checking`, `accepted`, `rejected`. Accept or reject a
card in attention and it leaves. Its [reason code](cards.md#reason-codes) says why it is there.

A card the board still retries on its own stays out until the automatic attempts are spent: an
`API_UNREACHABLE` backoff, a `MERGE_CONFLICT` auto-resume, a usage limit with a retry scheduled. The
column never fills with things nobody needs to touch yet.

**Queued is not blocked.** While the run-all queue runs (`w`), a card it holds stays in its own
column with a grey edge and a slow pulse: next in line, or waiting on a lease clash, an unmet
dependency or a limit. Doing holds only what an agent is running this instant. Stop the queue and
the card wears its own colour again. Nothing is written; this is presentation only.

### The inbox

`n` lists the same cards: every card on every board that waits on you, oldest first. Answer the top
row and move on; the answer resumes the card. Visits stay short, and nothing sits there forever.

![Attention inbox](../images/inbox.jpg)

Each row leads with why the card waits, then its title and the text that matters: for a question,
the agent's own words; for anything else, the board's note, such as the failing test output, the
review findings or the crash.

The focused card answers in place. An answer resolves eight reasons: a question, failed tests, a red
base, a rejected review, a crash, a lease conflict, a merge conflict, an outdated branch (answering
`OUTDATED` redoes the card from the current base). A card in `checking` waiting for accept or reject
is a decision, not a question: `y` or `x` on the card.

> [!IMPORTANT]
> **Two reasons cannot be answered.**
> `USAGE_LIMIT` clears itself when the window resets, so an answer would only confuse the agent.
> Its row offers **retry on** the next usable fallback model instead, when there is one (see
> [labs](labs.md)). `DEPENDENCY_REJECTED` is not this card's fault: fix and accept the card it
> depends on, and this one unblocks itself.

### Lease conflicts

A `LEASE_CONFLICT` row lists exactly the paths the card was refused. **approve** adds those, and
only those, to the lease, then resumes. An answer instead tells the agent to leave the files alone.
An answer alone never widens a lease (see [leases](leases.md)).
