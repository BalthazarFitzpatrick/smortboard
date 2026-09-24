# runs and the event log

**re-run a blocked card instead of starting a new one.** every line of every attempt is stored once
and never overwritten. cost, replay, the roster, the digest and the resume briefing all read that
one log, so they cannot disagree.

## how it works

**every stream line, gate, decision and note goes to the card's event log**, and nowhere else. an
**attempt** is one start event and everything after it. a re-run card keeps every attempt: `t`
replays any of them, `i` prices it, and resume briefings work per attempt.

![run replay](../images/replay.jpg)

`t` steps through reads, edits as diffs, commands, refused calls, gates and verdicts.

### re-runs

**a re-run does not pay for the agent twice.** if the branch is where the agent last cleanly left it
and nothing new was said, the re-run goes straight to the gates.

- a rebuilt image or a changed test command is a reason to test again.
- same code, image and command after a failed gate is refused as "nothing has changed".
- a run silent for 20 minutes is stopped and retried as an outage. a codex run is capped at 60
  minutes.

### phases

a card runs through these in order, then ends `opened`, `blocked`, `refused` or `stopped`.

| phase | what happens |
|---|---|
| `preparing` | a git worktree and branch per card, cut from a freshly fetched `origin/<base>`, or from its parent's branch when a review-required board stacks it. a resumed card reuses its worktree and commits. |
| `running` | the agent works in a named container. the token arrives on stdin, then the brief; stdin stays open so a live note can reach it. every line becomes an event. |
| `testing` | the repo's own test command runs on the card's commits, in the repo's image, with no network and no credential. |
| `reviewing` | a second, read-only agent reviews the diff. |
| `fixing` | on the `fix` findings route only: findings go back to the worker, at most **twice**, then the gates run again. |
| `opening` | the board pushes the branch and opens a pull request. |
| `opened` | the pull request is open. a free-merge board then **lands** it on its unprotected base; a review-required board, the default, waits for your `y`. `main` is never landed ([landing and main](landing.md)). |
