# prompt layering

Prompts are layered by **role**, and only role - `orchestrator`, `worker`, `reviewer`. Not per
board, not per repo, not per card.

## the three layers

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

## where each layer lands in the actual `claude` invocation

- worker: `--system-prompt`, built by `backends.ContainerBackend._docker_command` from
  `active_prompt(store, "worker", runner.SYSTEM_PROMPT)`
- reviewer: the prompt text prepended to the diff, from `active_prompt(store, "reviewer",
  reviewer.REVIEW_PROMPT_HEADER)`
- orchestrator: `--system-prompt`, from `active_prompt(store, "orchestrator",
  orchestrator.ORCHESTRATOR_PROMPT)`

`runner.build_command`'s `system_prompt` parameter defaults to `SYSTEM_PROMPT` so every caller that
does not care about layering (tests, the non-container `run_card` path) keeps working unchanged.

## what the board adds that a stored prompt cannot drop

A stored override replaces the seed default whole, so anything a run depends on is appended outside
it, in code, on every run:

- **worker, system prompt:** `runner.HEADLESS_RULES` (nothing wakes a headless run, commit before
  finishing), the READ NARROW rule (grep for line numbers, then read only that range, never re-read),
  the note-marker paragraph that tells the agent which marker a genuine operator note carries, and -
  only when the repo has `smortboard/ui/` - `runner.SCREENSHOT_RULE`, since the board screenshots
  nothing but its own ui. House style is never in the worker prompt; it comes from the repo's own
  `CLAUDE.md` or `AGENTS.md`.
- **worker, first message:** `backends.WORKSPACE_PREAMBLE`, the lease (`runner.lease_preamble`: the
  paths the card may write), and `runner.commands_preamble`: "YOU CAN RUN EXACTLY" every shell
  command the run is granted, word for word, plus the facts that used to cost refused turns - no
  pipes or chains, no docker, never push, no installs, a failing test outside the lease gets noted.
  It informs; the grants themselves come from the tool allowlist.
- **reviewer:** the diff sits between `DIFF_FRAMING` and `DIFF_END` as untrusted data. File names
  the worker wrote - lockfiles left out of the diff, paths a soft lease reached - go inside that
  region, never in the trusted header. The header only carries the board's own instruction to judge
  the soft-lease paths.
- **orchestrator:** `build_system_prompt` appends the model catalog, `_LEDGER_RULES`,
  `CARD_TEXT_RULES` and `_TEST_RULES` to the system prompt, so they stay identical between turns and
  the provider can cache them. `_TEST_RULES`: tests on every card, a first test-suite card for a repo
  with none, and `test_commands` naming the runner for a repo without a test command. The per-turn prompt carries only the snapshot (compact json, message bodies capped at
  800 characters, the newest 20 finished cards as evidence) and the planning or manage mode rules.
- **fold:** `consolidate.FOLD_PROMPT` is fixed in code; there is no stored fold role. Its turn prompt
  carries `CARD_TEXT_RULES` too.

## flags that shape every run

- `--tools`: the only tools a run loads - Read, Edit, Write, Glob, Grep and Bash for the worker;
  Read, Grep and Glob for the reviewer, mission control and fold. `--allowedTools` and
  `--disallowedTools` still decide permission; `--tools` decides what is loaded at all.
- `--disable-slash-commands`: no skills or slash commands in any run.
- `--effort`, only when `{role}_effort` is set (low, medium, high). Codex gets
  `-c model_reasoning_effort` instead.
