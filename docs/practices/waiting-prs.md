# waiting pull requests

**review on your schedule; the board keeps the branch current.**

a waiting pull request keeps up with its base, so the diff you review is against today's base, and
you do nothing to get it. no stale diffs: a conflict surfaces as `OUTDATED` with a file list, not at
merge time.

## how it works

github tells nobody when something merges: there is no push event to subscribe to, and the board
can only ask about one named pull request at a time. so the board watches what actually changes:
the base branch's own commit.

on a background timer it fetches each repo's base and compares it to the last sha it saw:

| base | then |
|---|---|
| unchanged | nothing; no branch is touched |
| moved | every card waiting in `checking` on that repo has its own commits rebased onto the base in a fresh tree, then force-pushed with a lease, so a push someone else made is never overwritten |
| moved, and the rebase conflicts | the card blocks `OUTDATED` with the file list; the branch and pull request are left alone |

the same sweep moves your local base branch up to origin when that is a plain fast-forward (see
[landing](landing.md#the-local-base)). the board's own landings trigger the sweep directly, so a
stack moves within seconds rather than at the next poll. what answering `OUTDATED` does is under
[reason codes](cards.md#reason-codes).

measured on card `59727ba3` (pr #112): a branch cut at the start of a run and never updated drifted
**34 commits** behind main while its pull request waited, and conflicted in four files other pull
requests had since touched.
