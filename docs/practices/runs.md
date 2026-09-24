# Runs and the event log

**Re-run a blocked card instead of starting a new one.** Every line of every attempt is stored once
and never overwritten. Cost, replay, the roster, the digest and the resume briefing all read that
one log, so they cannot disagree.

## How it works

**Every stream line, gate, decision and note goes to the card's event log**, and nowhere else. An
**attempt** is one start event and everything after it. A re-run card keeps every attempt: `t`
replays any of them, `i` prices it, and resume briefings work per attempt.

![Run replay](../images/replay.jpg)

`t` steps through reads, edits as diffs, commands, refused calls, gates and verdicts.

### Re-runs

**A re-run does not pay for the agent twice.** If the branch is where the agent last cleanly left it
and nothing new was said, the re-run goes straight to the gates.

- A rebuilt image or a changed test command is a reason to test again.
- Same code, image and command after a failed gate is refused as "nothing has changed".
- A run silent for 20 minutes is stopped and retried as an outage. A Codex run is capped at 60
  minutes.

### Phases

A card runs through these in order, then ends `opened`, `blocked`, `refused` or `stopped`.

| Phase | What happens |
|---|---|
| `preparing` | a git worktree and branch per card, cut from a freshly fetched `origin/<base>`, or from its parent's branch when a review-required board stacks it. A resumed card reuses its worktree and commits. |
| `running` | the agent works in a named container. The token arrives on stdin, then the brief; stdin stays open so a live note can reach it. Every line becomes an event. |
| `testing` | the repo's own test command runs on the card's commits, in the repo's image, with no network and no credential. |
| `reviewing` | a second, read-only agent reviews the diff. |
| `fixing` | on the `fix` findings route only: findings go back to the worker, at most **twice**, then the gates run again. |
| `opening` | the board pushes the branch and opens a pull request. |
| `opened` | the pull request is open. A free-merge board then **lands** it on its unprotected base; a review-required board, the default, waits for your `y`. `main` is never landed ([Landing and main](landing.md)). |
