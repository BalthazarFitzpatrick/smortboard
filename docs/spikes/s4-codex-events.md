# s4: codex events

Measured 2026-09-17 with codex-cli 0.154.0 and gpt-5.6-sol. These are the
recorded results from the implementation checkpoint, not a new execution.

The one-line run emitted:

```jsonl
{"type":"thread.started","thread_id":"<redacted>"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"PINEAPPLE"}}
{"type":"turn.completed","usage":{"input_tokens":12842,"cached_input_tokens":10624,"cache_write_input_tokens":0,"output_tokens":7,"reasoning_output_tokens":0}}
```

`turn.completed` marks success. `turn.failed` carries `error.message`.
The unknown-model probe emitted `error` followed by `turn.failed`; its nested
message reported HTTP 400 and `invalid_request_error`. This is a configuration
failure, not proof of an auth failure or a spent usage window.

Shell calls emitted `item.started` and `item.completed`, with
`item.type=command_execution`, `command`, `aggregated_output`, `exit_code` and
`status`. The completed `agent_message` holds the answer in `item.text`.

No dollar cost or reset/window event was observed. Usage arrived at turn
completion. The budget watchdog can only stop a process when usage arrives;
it cannot promise to interrupt a long turn before it exceeds its cap.
Missing catalog prices remain unknown. No prices are inferred from this spike.

The bogus/revoked-credential stream still needs a real probe. Auth and usage-limit
classification tests currently use synthetic fixtures and are not evidence that
the CLI emitted those exact shapes.
