# s5: codex prompt and steering

Measured 2026-09-17 with codex-cli 0.154.0 and gpt-5.6-sol.

`-c 'developer_instructions="End every answer with PINEAPPLE."'` worked.
A three-command run ended with `Done. PINEAPPLE.` The adapter uses that override
for the board's role prompt.

`codex queue --thread <ephemeral-id> --message ...` failed with
`no rollout found for thread id`. Live steering is therefore disabled for the
board's ephemeral Codex runs. Notes stay available for the next run, and the
API must report that delivery mode.

A non-ephemeral queue run was not proven. This result does not establish that
queue is unsupported in every Codex mode.
