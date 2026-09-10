# Prompt layering

Prompts are layered by **role**, and only role - `orchestrator`, `worker`, `reviewer`. Not per
board, not per repo, not per card.

## The three layers

1. **Seed default**, in code. Each role's module owns one: `runner.SYSTEM_PROMPT` (worker),
   `reviewer.REVIEW_PROMPT_HEADER` (reviewer), `orchestrator.ORCHESTRATOR_PROMPT` (orchestrator).
   These ship with smortboard and change only when the codebase does.
2. **Stored override**, in the `prompts` table (`role`, `version`, `body`). When one exists for a
   role, it replaces the seed default entirely - it is not appended to it. `PATCH /api/prompts/{role}`
   writes a new row; the version number always increases, so every save is a new version and history
   is kept rather than overwritten. `GET /api/prompts` reports `version: 0` for a role still on its
   seed default.
3. **Repo conventions**, separately, through `--setting-sources project` and the repo's own
   `CLAUDE.md` - not part of this layering at all. A card is handed the repo's own house style; the
   role prompt is what the card is, not what the repo already says.

`smortboard/prompts.py`'s `active_prompt(store, role, default)` is the one place that resolves
layers 1 and 2: look up the stored version, fall back to the default if there is none. Runner,
reviewer and orchestrator each pass their own default in, rather than importing one another's -
that is what keeps `prompts.py` free of an import cycle back into `exec/`, `review/` or the
orchestrator module.

## Where each layer lands in the actual `claude` invocation

- worker: `--system-prompt`, built by `backends.ContainerBackend._docker_command` from
  `active_prompt(store, "worker", runner.SYSTEM_PROMPT)`
- reviewer: the prompt text prepended to the diff, from `active_prompt(store, "reviewer",
  reviewer.REVIEW_PROMPT_HEADER)`
- orchestrator: `--system-prompt`, from `active_prompt(store, "orchestrator",
  orchestrator.ORCHESTRATOR_PROMPT)`

`runner.build_command`'s `system_prompt` parameter defaults to `SYSTEM_PROMPT` so every caller that
does not care about layering (tests, the non-container `run_card` path) keeps working unchanged.
