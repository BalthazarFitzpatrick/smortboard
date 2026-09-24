# Waiting pull requests

**Review on your schedule; the board keeps the branch current.**

A waiting pull request keeps up with its base, so the diff you review is against today's base, and
you do nothing to get it. No stale diffs; conflicts surface as `OUTDATED` with a file list, not at
merge time.

## How it works

GitHub tells nobody when something merges: there is no push event to subscribe to, and the board
can only ask about one named pull request at a time. So it watches what actually changes: the base
branch's own commit.

On a background timer the board fetches each repo's base and compares it to the last sha it saw:

| Base | Then |
|---|---|
| unchanged | nothing; no branch is touched |
| moved | every card waiting in `checking` on that repo has its own commits rebased onto the base in a fresh tree, then force-pushed with a lease, so a push someone else made is never overwritten |
| moved, and the rebase conflicts | the card blocks `OUTDATED` with the file list; the branch and pull request are left alone |

The board's own landings trigger the same sweep directly, so a stack moves within seconds rather
than at the next poll. What answering `OUTDATED` does is under [reason codes](cards.md#reason-codes).

Measured on card `59727ba3` (PR #112): a branch cut at the start of a run and never updated drifted
**34 commits** behind main while its pull request waited, and conflicted in four files other pull
requests had since touched.
