# The two gates

**Let the two gates judge.**

Your tests, then a second agent, check every card before its pull request, and neither trusts the
agent's word. Write criteria your test command checks: with a test behind it a criterion is
enforced; without one it is a note.

> [!IMPORTANT]
> **The reviewer does not judge your criteria.**
> It judges the code. Criteria are the test gate's business, enforced only as far as your test
> command checks them.

## How it works

The pull request that opens has already passed your suite and an independent read of the diff.
[Runs](runs.md) has the phases around the gates.

### The test gate

It re-runs the repo's own test command against what the card committed, in a container the agent
never touched, with no credential and no network. An agent reporting "tests pass" counts for
nothing.

### The reviewer

A second agent with only Read, Grep and Glob, on a working tree mounted read-only. It asks exactly
four questions of the diff: **vulnerability**, **leaked credential**, **best practice**,
**efficiency**. Each finding is graded `low`, `medium`, `high` or `critical`.

The card blocks on:

- any finding at `high` or `critical`
- a `leaked_credential` finding at **any** severity: a model that rates a leaked key "low" is not a
  label to trust
- no usable verdict: a crash, an outage, a limit, a budget stop before it answered, or unparseable
  output. That blocks rather than passing quietly, but with its own reason (`CRASH`,
  `API_UNREACHABLE`, `USAGE_LIMIT`), so it retries. Only a real verdict is a `REVIEW_REJECTED`.

The diff is framed as untrusted data: text in it asking to be approved is itself reported as a
finding.

### Where findings go

A setting: `attention` (the default) sends them to you, `fix` sends them back to the worker. Let
them come to you first: a card quietly fixing its own findings unattended spends a run's worth of
tokens nobody asked for.
