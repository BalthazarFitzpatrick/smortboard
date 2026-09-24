# the two gates

**let the two gates judge.**

your tests, then a second agent, check every card before its pull request, and neither takes the
agent's word. write criteria your test command checks: with a test behind it a criterion is
enforced; without one it is a note.

> [!IMPORTANT]
> **the reviewer does not judge your criteria.**
> it judges the code. criteria are the test gate's business, enforced only as far as your test
> command checks them.

## how it works

a pull request only opens once the card passed your suite and an independent read of the diff.
[runs](runs.md) has the phases around the gates.

### the test gate

it re-runs the repo's own test command against what the card committed, in a container the agent
never touched, with no credential and no network. an agent reporting "tests pass" counts for
nothing.

### the reviewer

a second agent with only `Read`, `Grep` and `Glob`, on a working tree mounted read-only. it asks four
questions of the diff: **vulnerability**, **leaked credential**, **best practice**, **efficiency**.
each finding is graded `low`, `medium`, `high` or `critical`.

the card blocks on:

- any finding at `high` or `critical`
- a `leaked_credential` finding at **any** severity: a model that rates a leaked key "low" is not a
  label to trust
- no usable verdict: a crash, an outage, a limit, a budget stop before it answered, or unparseable
  output. that blocks rather than passing quietly, but with its own reason (`CRASH`,
  `API_UNREACHABLE`, `USAGE_LIMIT`), so it retries. only a real verdict is a `REVIEW_REJECTED`.

the diff is framed as untrusted data: text in it asking to be approved is itself reported as a
finding.

### where findings go

a setting: `attention` (the default) sends them to you, `fix` sends them back to the worker. keep
the default. a card fixing its own findings unattended spends a run's worth of tokens nobody asked
for.
