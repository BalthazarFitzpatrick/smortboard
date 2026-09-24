# attention and inbox

**watch one column.**

anything that needs you lands in attention; everything else runs itself. "what is waiting for me?"
is one look at the board, not a round of opening cards. glance, act, leave.

> [!IMPORTANT]
> **attention is not a status.**
> it is a presentation column between doing and checking. it shows any card with a blocked reason
> or a review flag, whatever status it holds underneath.

## how it works

### the column

six columns: `todo`, `doing`, `attention`, `checking`, `accepted`, `rejected`. accept or reject a
card in attention and it leaves. its [reason code](cards.md#reason-codes) says why it is there.

a card the board still retries on its own stays out until the automatic attempts are spent: an
`API_UNREACHABLE` backoff, a `MERGE_CONFLICT` auto-resume, a usage limit with a retry scheduled. the
column never fills with things nobody needs to touch yet.

**queued is not blocked.** while the run-all queue runs (`w`), a card it holds stays in its own
column with a grey edge and a slow pulse: next in line, or waiting on a lease clash, an unmet
dependency or a limit. doing holds only what an agent is running this instant. stop the queue and
the card wears its own colour again. nothing is written; this is presentation only.

### the inbox

`n` lists the same cards: every card on every board that waits on you, oldest first. answer the top
row and move on; the answer resumes the card. visits stay short, and nothing sits there forever.

![attention inbox](../images/inbox.jpg)

each row leads with why the card waits, then its title and the text that matters: for a question,
the agent's own words; for anything else, the board's note, such as the failing test output, the
review findings or the crash.

the focused card answers in place. an answer resolves eight reasons: a question, failed tests, a red
base, a rejected review, a crash, a lease conflict, a merge conflict, an outdated branch (answering
`OUTDATED` redoes the card from the current base). a card in `checking` waiting for accept or reject
is a decision, not a question: `y` or `x` on the card.

> [!IMPORTANT]
> **two reasons cannot be answered.**
> `USAGE_LIMIT` clears itself when the window resets, so an answer would only confuse the agent.
> its row offers **retry on** the next usable fallback model instead, when there is one (see
> [labs](labs.md)). `DEPENDENCY_REJECTED` is not this card's fault: fix and accept the card it
> depends on, and this one unblocks itself.

### lease conflicts

a `LEASE_CONFLICT` row lists exactly the paths the card was refused. **approve** adds those, and
only those, to the lease, then resumes. an answer instead tells the agent to leave the files alone.
an answer alone never widens a lease (see [leases](leases.md)).
