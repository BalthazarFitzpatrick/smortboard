# S1 / S2 findings — headless Claude Code as a card runner

Run 2026-09-08 against an isolated scratch repo. Raw streams are not committed; every claim below
points at a field in the captured `stream-json` output.

## S1 — CONFIRMED, and the stream carries more than the plan assumed

One card ("add a `--version` flag"), run headless, completed in 15s and met its acceptance criteria:
the CLI printed the version and exited 0, committed as `b95013d`.

Command shape that worked:

```
claude -p "<card prompt>" \
  --output-format stream-json --verbose \
  --permission-mode acceptEdits \
  --allowedTools "Bash(git *)" "Edit" "Read" "Write" \
  --model sonnet < /dev/null
```

`< /dev/null` is required. Without it the process waits 3s for stdin and warns before proceeding —
harmless once, wasteful across every card.

### What the final `result` event gives us

| Need | Field |
|---|---|
| Completion vs crash | `subtype` (`success`), `is_error`, `terminal_reason` (`completed`), `stop_reason` |
| Telemetry (§7) | `usage` — input, output, thinking, cache read/creation — and `modelUsage` keyed by model |
| Cost | `total_cost_usd` |
| Resume | `session_id` |
| Blocked detection | `permission_denials` — empty here; a populated array is the signal |
| Turn count | `num_turns` |

So §7's telemetry section needs no instrumentation of our own. It is a projection of one event.

### Two events the plan did not know about

- **`system/vcs_state_changed`** — `{kind: "commit", branch: "...", cwd: "..."}`. Commit and branch
  detection arrives in the stream; the board does not need to poll git to know a card committed.
- **`rate_limit_event`** — see S2.

### The agent branched by itself

It committed to `feature/cli-version-flag`, a branch it created, because a headless run **inherits
`~/.claude/CLAUDE.md`** and the playbook forbids committing to main. Good compliance, but the branch
name is model-chosen and therefore unpredictable.

**Consequence for Phase 2:** the board must cut the worktree and branch itself and hand the agent a
checkout that is already on the right branch. Otherwise card-to-branch mapping is a guess.

## S2 — contention CONFIRMED clean; quota question RESOLVED, and my earlier claim was wrong

### Concurrency

Two headless sessions run simultaneously against the one interactive subscription seat, each in its
own worktree. Both returned `subtype: success`, `is_error: false`, 5 turns each. No degradation, no
interference, no corruption of the interactive session.

**Caveat, stated honestly:** this proves two concurrent sessions work. It does not prove behaviour
*at* the limit — five-hour utilisation was 0.11 throughout, nowhere near it. What it does prove is
that the limit *signal* exists and is readable (below), so the board can react rather than guess.

Parallelism (Phase 5) is therefore a scheduling problem, not a credentials problem.

### Quota — correcting the plan

The plan says, citing the global playbook, that the status line is the only surface exposing
subscription usage, and that §4.6's quota display might reduce to token counts only. **That is
wrong.** Every card's own stream carries `rate_limit_event`:

```json
{"rate_limit_info": {
  "status": "allowed",
  "rateLimitType": "five_hour",
  "unifiedWindows": {
    "five_hour": {"utilization": 0.11, "resetsAt": 1788909000},
    "seven_day": {"utilization": 0.07, "resetsAt": 1789401600}}}}
```

Both windows, utilisation and reset time, plus a `status` field and overage state. §4.6 is fully
buildable, including the quota meter, with no scraping and no status-line dependency. The usage
panel is a projection of these events exactly as telemetry is a projection of `result`.

`status: "allowed"` is the healthy value; the board maps any other value to `USAGE_LIMIT`.

## `--worktree` — usable, but the board should not rely on it

`claude --worktree <name>` creates `.claude/worktrees/<name>` on branch `worktree-<name>`, git-locked
— the same convention wowtomate already runs by hand. `claude rm <id>` deletes a background session
and its worktree "when that is safe".

Recommendation: **the board cuts worktrees itself.** The naming is fixed, the branch name is derived
from the worktree name rather than the card, and cleanup semantics on rejection (destroy the tree,
keep a diff) need to be ours. Use the CLI's worktree only as a fallback.

## S3 — mechanism identified, not yet proven

`--include-hook-events` puts hook lifecycle events in the stream, and `permission_denials` in the
result records refusals. A `PreToolUse` hook is therefore the natural lease enforcer, refusing a
write outside the card's declared paths at the moment it is attempted — the same mechanism the
global playbook already uses to block commits to main. This makes leases enforceable at write time
rather than advisory, which is what the "come back to clean branches" promise needs.

Still to prove: that a hook can see the target path of an `Edit`/`Write` before it lands, and that a
refusal surfaces distinguishably from other denials.

## Net effect on the plan

- Phase 2 shrinks. Session lifecycle (`--background`, `agents`, `logs`, `stop`, `rm`), worktree
  creation and commit detection are CLI features, not code we write.
- Phase 6 shrinks. Telemetry and the usage panel are projections of two event types.
- Phase 2 gains one requirement: cut the branch ourselves, do not let the agent choose it.
- No phase is invalidated. The Agent SDK fallback is not needed.
