# When to step in

**Define the work, then walk away.** Your job is the brief, the criteria, the lease and the final
decision, not what happens between. If you catch yourself watching a card run, the card was
underspecified, or the board owes you a surface that would have let you leave.

## How it works

Anything on the board that rewards hovering is a design mistake. Good cards are on
[Cards](cards.md).

### The loop you run

1. **Queue a few cards**, with mission control (`.`) or by hand. Check the leases before running
   anything: overlaps are cheapest to catch here.
2. **Start the board** (`w`) and leave. Two cards run at once by default. A dependent card waits for
   its dependencies. On a free-merge board: until their pull requests are **merged**. On a
   review-required board it may start on one parent still in checking, stacked at most three deep.
3. **Answer the inbox** (`n`) when you are back. Oldest first; each row says what it needs.
4. **Merge what is ready.** `v` lists open pull requests across every board, in merge order.
   Review, merge, accept the card with `y`.
5. **Check cost now and then** (`c`). Cost per accepted card by model and complexity shows whether
   your card sizing works.

Set a daily budget per board ([Cost and budgets](cost.md)) before running unattended. It is the
difference between a bad night and an expensive one.

### When to step in, and when not to

| when | do |
|---|---|
| a card is working, or on its first fix round | let it run |
| the board is retrying on its own | let it run. Those cards say so and stay out of the inbox on purpose |
| a card is back a second time for the same reason | fix the card (brief, criteria, lease) and re-run it, rather than answer the same note twice. Twice means the card is wrong, not the agent unlucky |
| a running agent wants permission | do not argue. Guards read stored state, not the conversation. Widen the lease, or say no and move on |

### A card comes back blocked

The board, the card and the inbox ([Attention and inbox](attention.md)) say what to do in the same
words. In short:

| Reason | Usually means |
|---|---|
| `AGENT_QUESTION` | a hole in the brief. Answer, then consider whether the answer belonged in the card. |
| `TESTS_FAILED` | read the failing output first. A hint resumes it; a wrong hint costs another run. |
| `BASE_RED` | not the card's fault. Fix the base or merge a fix into it, then re-run. |
| `REVIEW_REJECTED` | read the findings. Real ones need a fix; a wrong one means the card lacked context. |
| `LEASE_CONFLICT` | almost always a lease for a repo layout that does not exist. Approve the paths, or tell it to leave them. |
| `MERGE_CONFLICT` | another card landed first. Resuming rebases it onto the base in a fresh tree; if that still conflicts, it turns `OUTDATED`. |
| `DEPENDENCY_REJECTED` | nothing to do here. Fix the dependency. |
| `USAGE_LIMIT` | wait, or retry on the fallback model from its row. Answering does nothing. |
| `CRASH` | check the note, then resume. Worktree and commits are kept. |

### The mistakes that cost a day

| mistake | cost |
|---|---|
| a lease overlapping a running card ([Leases](leases.md)) | no error. The second card waits and the board runs serially. Check leases at queue time |
| criteria no test checks | a pull request for unchecked work. Nothing in the chain catches it, the reviewer included |
| a repo with no test command ([Repos and test commands](../reference/repos.md)) | the card cannot finish; no path to a pull request skips the gate. Set one at registration, covering everything your CI checks |
| a test command that writes into the working tree | the gate mounts it read-only, so a caching tool fails the gate through no fault of the card |
| cards sized like tickets | ten edit-sized cards cost far more than three feature-sized ones: each pays the read-in again |
| no daily budget while unattended | a bad night becomes an expensive one |
