# s3 findings — enforcing a card's path lease at write time

Run 2026-09-08 against an isolated scratch repo. **Confirmed: leases are enforceable at write time,
not merely advisory.**

## the mechanism

A `PreToolUse` hook matched on `Edit|Write`, reading the tool payload on stdin, comparing
`file_path` against the card's declared lease, and exiting 2 to block:

```sh
# refuses a write outside the card's declared path lease [PreToolUse, exit 2 blocks]
target=$(printf '%s' "$payload" | sed -n 's/.*"file_path"[^"]*"\([^"]*\)".*/\1/p')
...
echo "LEASE_CONFLICT: $rel is outside this card's lease" >&2
exit 2
```

Wired through `--settings <file>` with `{"hooks": {"PreToolUse": [{"matcher": "Edit|Write", ...}]}}`.

## result

A card was asked to make two edits: one inside its lease (`toolkit/cli.py`), one outside
(`toolkit/billing.py`).

| File | In lease | Outcome |
|---|---|---|
| `toolkit/cli.py` | yes | written, `VERSION` bumped to 0.4.0 |
| `toolkit/billing.py` | no | **blocked**, file unchanged on disk |

Both of the open questions are answered:

**A hook sees the target path before the write lands.** The `PreToolUse` payload carries
`file_path`, and exit 2 prevents the edit. The file was verified unchanged afterwards.

**A lease refusal is distinguishable from any other denial.** Three independent signals:

1. `system/hook_response` with `hook_name: "PreToolUse:Edit"`, `exit_code: 2`, `outcome: "error"`,
   and our own message in `stderr` — so the board can match on the `LEASE_CONFLICT:` prefix and know
   it was the lease rather than some other hook.
2. `result.permission_denials` records `tool_name`, `tool_use_id` and the full `tool_input`
   including `file_path` — enough to name the exact path the card tried to touch and add it to the
   attention entry, or to extend the lease if nothing else holds that path.
3. The agent itself understood and reported it accurately: *"Blocked by a PreToolUse lease-guard
   hook ... That file isn't in scope for this branch/card, so the edit was not applied."*

Note the run still ended `subtype: success`, `is_error: false`. **A lease block is not a card
failure.** The board decides whether a `LEASE_CONFLICT` parks the card or extends the lease; the
runner should not infer it from the exit status.

## second finding: card agents inherit the operator's whole configuration

The first attempt at this spike never reached the lease. The agent's opening move was
`git checkout -b`, because a headless run inherits `~/.claude/CLAUDE.md` and the playbook forbids
working on main. That Bash call was outside `--allowedTools`, so it was denied, and the card
**stopped and asked for approval** instead of doing any work:

> "I need your approval to create a branch off `main` before editing — my rules block committing
> directly to main. OK to proceed?"

Three consequences:

- **This is the `AGENT_QUESTION` blocked state, and it is cleanly detectable.** The question arrives
  in `result.result` and the blocked call in `result.permission_denials`. §4.5's attention system
  can be built on exactly this, with the question rendered verbatim on the card.
- **It confirms the S1 requirement with teeth.** A card handed a checkout that is not already on its
  branch does not merely pick an unpredictable branch name — it can deadlock, burning a turn asking
  for something no one is there to grant. The board must hand over a worktree already on the right
  branch.
- **`--settings` adds to the operator's configuration, it does not replace it.** The stream showed
  `PreToolUse:Bash` hooks firing that were not in our settings file — the operator's global
  block-main-commit hook. Card runs inherit global hooks, global CLAUDE.md and global permissions.

**Recommendation for Phase 2:** card runs should not inherit the personal playbook wholesale. It is
written for an interactive operator who can answer a prompt; a card agent has nobody to ask.
`--setting-sources` and an explicit `--system-prompt` should scope what a card inherits, with the
lease hook and the card's own role prompt supplied deliberately rather than by ambient inheritance.
This wants deciding before Phase 2 rather than discovering in it.

## net effect on the plan

- Leases are enforceable at write time. The "come back to clean branches" promise holds, and the
  risk logged against it in `plan.md` is closed.
- `LEASE_CONFLICT` and `AGENT_QUESTION` both have proven, machine-readable signals.
- Phase 2 gains one decision: what a card run inherits from the operator's configuration.
