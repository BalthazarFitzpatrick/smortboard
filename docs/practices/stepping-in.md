# when to step in

**define the work, then walk away.** your job is the brief, the criteria, the lease and the final
decision, not what happens between. if you catch yourself watching a card run, the card was
underspecified, or the board owes you a surface that would have let you leave.

## how it works

**anything on the board that rewards hovering is a design mistake.** good cards are on
[cards](cards.md).

### the loop you run

1. **queue a few cards**, with mission control (`.`) or by hand. check the leases before running
   anything: overlaps are cheapest to catch here.
2. **start the board** (`w`) and leave. two cards run at once by default. a dependent card waits for
   its dependencies: on a free-merge board until their pull requests are **merged**, on a
   review-required board it may start on one parent still in checking, stacked at most three deep.
   a queued card stays in its own column with a slow pulse until an agent picks it up.
3. **answer the inbox** (`n`) when you are back. oldest first; each row says what it needs.
4. **land what is ready.** `v` lists open pull requests across every board, in merge order.
   review one, then `y` accepts the card; on a review-required board that also lands it on its
   base. a free-merge board has landed it already.
5. **check cost now and then** (`c`). cost per accepted card by model and complexity shows whether
   your card sizing works.

**set a daily budget per board before running unattended** (`shift`+`o`, see
[cost and budgets](cost.md)). it is the difference between a bad night and an expensive one.

### when to step in, and when not to

| when | do |
|---|---|
| a card is working, or on its first fix round | let it run |
| the board is retrying on its own | let it run. those cards say so and stay out of the inbox on purpose |
| a card is back a second time for the same reason | fix the card (brief, criteria, lease) and re-run it, rather than answer the same note twice. twice means the card is wrong, not the agent unlucky |
| a running agent wants permission | do not argue. guards read stored state, not the conversation. widen the lease, or say no and move on |

### a card comes back blocked

the board, the card and the inbox ([attention and inbox](attention.md)) say what to do in the same
words. in short:

| reason | usually means |
|---|---|
| `AGENT_QUESTION` | a hole in the brief. answer, then consider whether the answer belonged in the card. |
| `TESTS_FAILED` | read the failing output first. a hint resumes it; a wrong hint costs another run. |
| `BASE_RED` | not the card's fault. fix the base or merge a fix into it, then re-run. |
| `REVIEW_REJECTED` | read the findings. real ones need a fix; a wrong one means the card lacked context. |
| `LEASE_CONFLICT` | almost always a lease for a repo layout that does not exist. approve the paths, or tell it to leave them. |
| `MERGE_CONFLICT` | another card landed first. resuming rebases it onto the base in a fresh tree; if that still conflicts, it turns `OUTDATED`. |
| `DEPENDENCY_REJECTED` | nothing to do here. fix the dependency. |
| `USAGE_LIMIT` | wait, or retry on the fallback model from its row. answering does nothing. |
| `CRASH` | check the note, then resume. worktree and commits are kept. |

### the mistakes that cost a day

| mistake | cost |
|---|---|
| a lease overlapping a running card ([leases](leases.md)) | no error. the second card waits and the board runs serially. check leases at queue time |
| criteria no test checks | a pull request for unchecked work. nothing in the chain catches it, the reviewer included |
| a repo with no test command ([repos and test commands](../reference/repos.md)) | the card cannot finish; no path to a pull request skips the gate. set one at registration, covering everything your ci checks |
| a test command that writes into the working tree | the gate mounts it read-only, so a caching tool fails the gate through no fault of the card |
| cards sized like tickets | ten edit-sized cards cost far more than three feature-sized ones: each pays the read-in again |
| no daily budget while unattended | a bad night becomes an expensive one |
