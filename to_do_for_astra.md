# To do for astra: multi-lab support

status: proposed 2026-09-17, not approved. written for the executing agent; every file reference
is to `development` at 1d2489a.

## 1. Outcome

Done looks like this:

- a credential profile belongs to a **lab** (`anthropic`, `openai`). the profiles panel groups
  them by lab, and each lab has its own active profile, rotation and usage windows.
- every place a model runs picks **lab > model**: the worker (board default and per card), the
  reviewer, mission control, and the fold. any mix is allowed, e.g. an openai model plans while
  anthropic sonnet executes, or the reverse.
- a card run on an openai model goes through the same pipeline as today: own container, own
  clone, lease guard, bash guard, test gate, reviewer, PR. it reaches Checking or Blocked with the
  same reason codes.
- cost, usage windows, budgets and the evidence mission control plans from are recorded per
  lab and model, and shown that way.
- a board that only ever used claude keeps working with no config change, no migration surprise
  and no new files on disk (the same invariant `profiles.py` already keeps).

Not in scope: API-key billing dashboards, labs beyond these two (the seam must allow a third,
nothing more), and any change to the card lifecycle states.

## 2. Where claude is hard-wired today

Every item below is a seam this plan replaces or parameterises.

| concern | where | what is claude-specific |
|---|---|---|
| command line | `exec/runner.py` `build_command` | `claude -p`, `--output-format stream-json`, `--setting-sources`, `--system-prompt`, `--max-budget-usd`, `--allowedTools`, `--disallowedTools`, `--json-schema` |
| event parsing | `exec/runner.py` `classify_result`, `classify_rate_limit`, `result_to_run_result`, `_structured_output` | claude stream-json shapes: `result`, `rate_limit_event`, `StructuredOutput` tool call, `api_error_status` 401, session-limit text |
| live steering | `exec/runner.py` `_NoteFeeder`, `user_message_line` | `--input-format stream-json` user turns on stdin |
| container handoff | `exec/backends.py` `_docker_command`, `orchestrator.py` `_real_runner`, `review/reviewer.py` | `read -r CLAUDE_CODE_OAUTH_TOKEN` |
| credential | `exec/backends.py` `read_card_token`, `card_token_path`, keychain service name | one token kind; `claude setup-token` in every fix message |
| profiles | `profiles.py` | one flat list, one `active`, one `limits` map |
| rotation | `scheduler.py` `_handle_usage_limit`, `_latest_reset` | reads the newest `rate_limit_event` board-wide, rotates the one profile list |
| guards | `exec/leases.py`, `exec/bash_guard.py` | claude `--settings` file with `PreToolUse` hooks |
| model choice | `lifecycle.py:88-89,544-545`, `orchestrator.py:129,638`, `consolidate.py:285`, `server/app.py:784`, `store/api.py` `_SETTING_KEYS` | bare alias strings, defaults `sonnet`/`opus` |
| model validation | `orchestrator.py` `_MODEL_NAME`, `_clean_model` | regex only, no catalog |
| orchestrator prompt | `orchestrator.py:63-67` | names `sonnet`, `opus`, `haiku` |
| cost | `telemetry.py` `_add_model_spend`, `usage_projection`, `budgets.py`, `digest.py` | `total_cost_usd` and `modelUsage[*].costUSD` from the claude result event |
| complexity heuristic | `telemetry.py:67`, `ui/card_panel.js:517` | `"opus" in model` |
| card model picker | `ui/card_panel.js:564` `CARD_MODELS` | `[null, 'haiku', 'sonnet', 'opus']` |
| usage overlay | `ui/board.js:756-872` | windows grouped by profile only |
| image | `docker/card.Dockerfile` | only `@anthropic-ai/claude-code` installed |
| preflight | `preflight.py` `_token_check`, `_profile_checks` | one token, 108-byte length check, `claude setup-token` fixes |
| demo | `demo.py:280` `_MODELS` | claude ids only |

## 3. What codex offers (checked on this machine, codex-cli 0.154.0)

From `codex exec --help`, `codex login --help` and `~/.codex/hooks.json`:

- `codex exec [PROMPT]`, prompt from argv or stdin (`-`)
- `--json` prints events as JSONL
- `-m/--model`, `-s/--sandbox read-only|workspace-write|danger-full-access`
- `--output-schema <FILE>` for a structured final answer, `-o/--output-last-message <FILE>`
- `--ephemeral`, `--ignore-user-config`, `--ignore-rules`, `--skip-git-repo-check`, `-C/--cd`
- `-c key=value` config overrides, `-p` config profile layer
- `codex exec resume <id>` and `fork`
- `codex queue` - "queue a message for an existing session"
- `codex login --with-api-key` and `--with-access-token`, both reading stdin; `codex login status`
- hooks: `hooks.json` with `PreToolUse`, a regex `matcher`, `type: command`, a `timeout`. there is
  a hook trust step (`--dangerously-bypass-hook-trust` skips it for one invocation)
- **no** budget flag, **no** tool allowlist flag

Everything else codex-related in this plan is unverified until the spikes in section 6 land.

## 4. Design

### 4.1 Vocabulary

- **lab**: who serves the model. ids `anthropic`, `openai`. a lab names one **adapter**.
- **adapter**: the code that drives one CLI (`claude-code`, `codex`). all CLI knowledge lives
  here and nowhere else.
- **model ref**: `<lab>/<model>`, e.g. `anthropic/sonnet`, `openai/<model id>`. stored as two
  columns, shown and accepted as one string. a bare legacy value (`sonnet`) reads as
  `anthropic/sonnet`.
- **role**: `worker`, `reviewer`, `orchestrator`, `fold`. each has a board-level model ref.
- **profile**: a named credential inside one lab.

### 4.2 The adapter seam

One protocol in a new `smortboard/labs/` package. Nothing outside `labs/` may build a CLI
command or read a CLI event shape again.

```python
class LabAdapter(Protocol):
    lab: str                       # "anthropic"
    cli: str                       # "claude-code"
    capabilities: Capabilities     # see below

    def build_command(self, req: RunRequest) -> list[str]: ...
    def auth_shell(self, profile: Profile) -> str: ...        # the `read -r ...` prefix
    def container_env(self) -> list[str]: ...                 # e.g. tmpfs for CODEX_HOME
    def normalize(self, raw: dict) -> list[LabEvent]: ...     # raw line -> neutral events
    def classify(self, final: LabEvent) -> str | None: ...    # -> blocked_reason_code
    def steering_line(self, text: str) -> str | None: ...     # None when unsupported
    def guard_files(self, lease: list[str], bash: BashPolicy) -> GuardFiles: ...
    def preflight(self, profile: Profile) -> list[dict]: ...
    def setup_hint(self) -> str: ...                          # "claude setup-token" / "codex login ..."
```

`RunRequest` carries everything `build_command` takes today plus the role: prompt, system prompt,
model, allowed tools (as a neutral policy, section 4.7), budget, json schema, stream input,
read-only flag.

`Capabilities` is a frozen dataclass the rest of the board branches on, never on the lab id:

| capability | claude-code | codex (to confirm in spikes) |
|---|---|---|
| `live_steering` | yes (stream-json stdin) | S5 decides: `codex queue`, or no |
| `native_budget` | yes (`--max-budget-usd`) | no - board watchdog |
| `reports_cost_usd` | yes | assumed no - priced from tokens |
| `pre_tool_hooks` | yes | yes per `hooks.json`; S6 checks it covers file edits |
| `tool_allowlist` | yes | no - sandbox + hook |
| `structured_output` | yes (`--json-schema`) | yes (`--output-schema`) |
| `rate_limit_windows` | yes (`rate_limit_event`) | S4 decides |

`LabEvent` is the neutral event the store records next to the raw one:

```
kind: assistant_text | tool_use | tool_denied | usage | rate_limit | result
lab, model, profile
usage:      {input_tokens, output_tokens, cached_tokens, cost_usd | null, cost_estimated: bool}
rate_limit: {window: "five_hour" | "seven_day" | <lab name>, status: ok | warning | refused,
             utilization: float | null, resets_at: float | null}
result:     {ok, subtype, session_id, num_turns, text, structured_output, auth_failed}
```

The raw event is still appended (the timeline and replay read it), with `lab` added to the
payload. telemetry, budgets, digest, scheduler and the usage overlay read only neutral fields.

### 4.3 Data model (migration 20)

- `cards`: add `lab TEXT`. null with a null `model` means "board default". a non-null `model`
  with a null `lab` is a pre-migration row and reads as `anthropic`.
- settings: add `worker_lab`, `reviewer_lab`, `orchestrator_lab`, `fold_lab` to
  `_SETTING_KEYS`, plus `fold_model` (fold currently borrows `orchestrator_model`, which stops
  working once the two can be different labs). unset lab = `anthropic`, so today's settings mean
  what they meant.
- settings: `cross_lab_fallback` per role, a list of model refs, unset = none (section 4.6).
- `board_spend`: add `lab`, `model`.
- events: no schema change, the payload gains `lab`, `model`, `profile` and the neutral block.
- `smortboard export` and card backups carry the new columns; the export round-trip test gets a
  mixed-lab card.

### 4.4 Model catalog

A packaged `smortboard/labs/catalog.json`, overridable by
`~/.config/smortboard/catalog.json` (merged by `lab/model`):

```json
{
  "anthropic": {
    "adapter": "claude-code",
    "models": [
      {"id": "sonnet", "label": "Sonnet", "tier": "standard", "prefer_for": ["worker", "reviewer"]},
      {"id": "opus",   "label": "Opus",   "tier": "deep"},
      {"id": "haiku",  "label": "Haiku",  "tier": "light"}
    ]
  },
  "openai": {
    "adapter": "codex",
    "models": [
      {"id": "<model id>", "label": "...", "tier": "standard",
       "price_per_mtok": {"input": 0.0, "cached_input": 0.0, "output": 0.0}}
    ]
  }
}
```

- the operator adds any model the CLI accepts; the board never hard-codes a model name again
  outside this file. "astra", "sol" or any other name is just a catalog row.
- `tier` (`light`, `standard`, `deep`) replaces the `"opus" in model` heuristic in
  `telemetry.py:67` and `ui/card_panel.js:517`.
- `price_per_mtok` is only used when the adapter reports no cost. prices are operator data, not
  something the board ships as truth; a missing price shows cost as "unknown", never 0.
- `_clean_model` validates a proposed ref against the catalog: unknown lab or model gets null
  plus the existing "uses the board default" note.
- `GET /api/catalog` serves the merged catalog plus, per lab, whether a usable profile exists.

### 4.5 Credentials and profiles

- files: `~/.config/smortboard/tokens/<lab>/<name>`, mode 600, dir mode 700. anthropic's
  `default` stays mapped onto the legacy `card_token` file and keychain fallback, untouched.
- `profiles.json` v2:
  ```json
  {"version": 2,
   "labs": {"anthropic": {"active": "default", "profiles": ["default"], "limits": {}},
            "openai":    {"active": "work",    "profiles": ["work"],    "limits": {}}}}
  ```
  a v1 file (top-level `active`/`profiles`/`limits`) loads as `labs.anthropic`, and is only
  rewritten as v2 on the next real write. a missing file still means "anthropic default only"
  and is never created by a read.
- every `profiles.py` function takes `lab` (default `"anthropic"` so existing callers and tests
  keep passing). `_STATE_LOCK` stays one lock for the whole file.
- profile kind per lab: anthropic = oauth setup-token. openai = `access_token` (chatgpt plan) or
  `api_key`, chosen when adding. the kind is stored in state, the value only in the file.
- handoff into the container, token never in argv, env flags or `docker inspect`:
  - claude: unchanged, `IFS= read -r CLAUDE_CODE_OAUTH_TOKEN && export ...`
  - codex: `IFS= read -r T && printf %s "$T" | codex login --with-access-token` (or
    `--with-api-key`) with `CODEX_HOME` on a `--tmpfs`, so the `auth.json` codex writes lives in
    container memory only. S7 confirms the tmpfs path and that a headless access token works.
- `read_card_token` becomes `read_profile_token(lab, name)`; the 108-byte length check in
  preflight applies to anthropic only.
- api routes: `GET /api/profiles` returns rows with `lab` and `kind`;
  `POST /api/profiles {lab, name, kind, token}`; `POST /api/profiles/<lab>/<name>/activate`;
  `DELETE /api/profiles/<lab>/<name>`. the old unscoped routes stay as anthropic aliases for one
  release, then go.

### 4.6 Usage limits and rotation

- `USAGE_LIMIT` marks the **run's** profile limited (lab + name come from the run record, not
  from "whatever is active now" - with several labs running at once the latter is wrong).
- `_latest_reset` filters `rate_limit` events by the run's lab and profile, not board-wide.
- `auto_switch_profiles` rotates inside the same lab only.
- once every profile of a lab is limited, the role's `cross_lab_fallback` list is tried in
  order, only if the operator set one. no fallback = the cards on that lab park until reset,
  while cards on other labs keep running. the scheduler's single `_paused_until` becomes a
  per-lab map.
- a fallback run is recorded on the card as a board message ("ran on openai/x, anthropic was
  limited until 14:00") so the evidence table never silently mixes models.
- the usage overlay groups by lab, then window, then profile. codex windows show only once S4
  proves they exist; until then an openai lab shows spend only.

### 4.7 Guards, tools, sandbox

- lease guard and bash guard stay the same python hook scripts; `guard_files` writes them in
  each CLI's config shape (claude `--settings` json, codex `hooks.json` plus
  `--dangerously-bypass-hook-trust`, which the help text names as meant for automation that
  vets its own hooks - the board writes these hooks itself, read-only mounted).
- a neutral `ToolPolicy {read, edit, search, bash_allow: [...], read_only: bool}` replaces the
  raw claude tool strings. claude maps it to `--allowedTools/--disallowedTools` as today. codex
  maps it to `--sandbox workspace-write` (or `read-only` for orchestrator and fold) plus the bash
  guard hook enforcing `bash_allow`.
- **new for every lab**: a post-run lease check. after `_fetch_back`, diff the card branch
  against its base and refuse any path outside the lease as `LEASE_CONFLICT`, with the paths
  listed. today a write that dodges the hook would go unnoticed; with two CLIs this is the one
  check that does not depend on either CLI's hook coverage.
- user and project config: the container already has no host `~/.claude` or `~/.codex`. codex
  also gets `--ignore-user-config`; `--ignore-rules` is not used (the board may ship its own
  execpolicy rules later).
- project instructions: claude reads `CLAUDE.md`, codex reads `AGENTS.md`. the board does not
  translate them. the worker brief already carries the lease and the exact commands, which is
  what matters; a repo that wants both keeps both.

### 4.8 System prompts

- `prompts.active_prompt(store, role)` stays lab-neutral text. `HEADLESS_RULES` and the note
  marker paragraph stay appended outside it.
- claude passes it as `--system-prompt`. codex has no such flag in `exec --help`; S5 picks the
  mechanism (`-c` config override for developer or base instructions, or prepending it to the
  first user turn as a fallback). the adapter owns that choice.
- the orchestrator prompt's model paragraph (`orchestrator.py:63-67`) is generated from the
  catalog: the labs with a usable profile, their models, tier and `prefer_for`. the reply schema
  gains `lab` next to `model`.
- evidence (`telemetry.py` per-model scorecard) keys by model ref, so mission control compares
  `openai/x` and `anthropic/sonnet` on the same board with real numbers.

### 4.9 Cost and budgets

- `usage.cost_usd` from the adapter when it has one, else tokens x catalog price with
  `cost_estimated: true`, else null.
- chatgpt-plan runs cost the operator nothing per call; they still get the priced estimate so
  budgets and the evidence table mean the same thing across labs. the ui marks estimated values
  with a `~`.
- budgets for adapters without `native_budget`: a watchdog in `run_process` sums neutral usage
  as events arrive and calls `ProcessHandle.terminate()` once the cap is passed, then records the
  result as subtype `error_max_budget_usd` so every existing budget path (reviewer keeps its
  findings, `_failure_detail`, cap-fit telemetry) works unchanged.
- a lab whose runs have no price and no reported cost cannot enforce a budget: preflight warns,
  and the per-board daily cap counts it as unknown rather than 0.

### 4.10 Interface (ui/ is wiring only)

- profiles panel (shift+p): one section per lab, a lab picker and a kind picker on the add row.
- card model (`m`): a two-level menu, lab then model, from `/api/catalog`. labs with no usable
  profile show greyed with the reason. `CARD_MODELS` goes away.
- settings: one lab > model picker per role, plus the per-role fallback list.
- usage overlay and costs view: grouped by lab.
- card panel status line: `model: openai/x` instead of `model: x`.
- if the two-level menu needs a primitive ui_base does not have (a nested or grouped menu),
  it gets built in ui_base first, in domain-free words, and pinned here - never in `ui/`.

### 4.11 Image

One card image with both CLIs, each pinned (`@anthropic-ai/claude-code@2.1.273` as today,
`@openai/codex@<resolved version>` next to it), because a repo image is already built per repo
and doubling that by lab buys nothing. The gate never runs a model, so it is unaffected.
`repo.Dockerfile` inherits from the card image and needs no change beyond the rebuild.

## 5. Units

Execution runs units in the order given; units with the same number prefix are independent and
can run in parallel. Anything a spike falsifies is re-planned before its dependents start.

```
unit:     S4-codex-events
expects:  codex 0.154.0, one throwaway repo, one openai credential, run on the host
does:     runs `codex exec --json` on a one-line task and records every event type
outputs:  docs/spikes/S4-codex-events.md: the event names and fields for assistant text, tool
          calls, token usage, rate limits, final result, errors (bogus token, unknown model)
verifies: the doc names the exact field carrying (a) token counts, (b) a completion vs crash
          signal, (c) any rate-limit window with a reset time, or states that one does not exist,
          each with a pasted event line
```

```
unit:     S5-codex-steering-and-prompt
expects:  S4 done
does:     tests whether `codex queue` reaches a running `codex exec` between tool calls, and which
          `-c` key sets a system/developer prompt that the model obeys
outputs:  docs/spikes/S5-codex-steering.md, and the Capabilities row for live_steering
verifies: a queued note sent during a three-command turn shows up in the transcript before the
          last command, or the doc records that it arrived only after; a system prompt saying
          "end every answer with PINEAPPLE" produces that word
```

```
unit:     S6-codex-hooks
expects:  S4 done
does:     runs the existing lease hook script under codex hooks.json and tries an in-lease edit,
          an out-of-lease edit and an out-of-lease shell write
outputs:  docs/spikes/S6-codex-hooks.md: which tool names the matcher must cover (shell,
          apply_patch or equivalent), what a refusal looks like in the event stream
verifies: the out-of-lease file is unchanged on disk and the refusal is visible in the JSONL;
          if an edit path bypasses the hook, the doc says so and the post-run lease check (U7)
          becomes the primary guard for codex
```

```
unit:     S7-codex-container-auth
expects:  S4 done, the card image with codex installed
does:     hands a chatgpt access token over stdin into `codex login --with-access-token` with
          CODEX_HOME on --tmpfs, then runs one exec
outputs:  docs/spikes/S7-codex-auth.md
verifies: the run succeeds; `docker inspect` shows no token; no auth.json exists on any host
          path after the container exits; a revoked token produces a recognisable error line
```

```
unit:     U1-catalog
expects:  nothing
does:     adds labs/catalog.json, the user override merge, model ref parsing (legacy bare value =
          anthropic) and GET /api/catalog
outputs:  labs/catalog.py: load_catalog(), parse_ref(), resolve_ref(), tier_of()
verifies: parse_ref("sonnet") == ("anthropic", "sonnet"); parse_ref("openai/x") == ("openai",
          "x"); an override file adding openai/y shows y in /api/catalog; an unknown ref resolves
          to None
```

```
unit:     U2-adapter-protocol-and-claude
expects:  U1
does:     adds labs/base.py (LabAdapter, Capabilities, RunRequest, LabEvent, ToolPolicy) and
          labs/claude_code.py, moving build_command, the classifiers, the token shell prefix and
          the guard settings writer behind it with no behaviour change
outputs:  runner.py, backends.py, orchestrator.py, reviewer.py call the adapter; claude strings
          appear only under labs/
verifies: the whole existing suite passes unchanged; `grep -rn '"claude"' smortboard --include=*.py`
          outside labs/ returns nothing; a golden test asserts the adapter's command for a worker
          run is byte-identical to today's build_command output
```

```
unit:     U3-neutral-events
expects:  U2
does:     records the neutral block on every event and moves telemetry, budgets, digest,
          scheduler._latest_reset and the usage projection onto it
outputs:  events carry lab, model, profile; readers ignore raw shapes
verifies: a replayed claude fixture stream gives the same usage_projection, budgets and digest
          numbers as before; an old event without the block still reads (as anthropic, default)
```

```
unit:     U4-migration-20
expects:  U1
does:     adds cards.lab, board_spend.lab/model, the new settings keys, export/backup support
outputs:  migration 20, store api accepts and returns lab, settings validation checks refs
          against the catalog
verifies: migrating a copy of a v19 db keeps every card's model and reads it as anthropic;
          PATCH /api/cards/<id> {"lab": "openai", "model": "x"} round-trips; export then import
          of a mixed-lab board is lossless
```

```
unit:     U5-profiles-v2
expects:  U1
does:     lab-scoped profiles: state v2 with v1 read compatibility, per-lab token dirs, kind,
          lab-scoped routes with the old ones as anthropic aliases, preflight rows per lab
outputs:  profiles.py and server routes take lab; read_profile_token(lab, name)
verifies: a v1 profiles.json loads unchanged and is not rewritten by a read; a single-profile
          board still never creates profiles.json; adding openai/work writes
          tokens/openai/work at mode 600; removing the last profile of a lab that cards still
          reference is refused, naming the cards
```

```
unit:     U6-codex-adapter
expects:  U2, U3, S4-S7
does:     labs/codex.py: command, auth prefix, tmpfs, normalizer, classifier, steering per S5,
          hooks per S6, sandbox mapping from ToolPolicy
outputs:  an openai card, reviewer or orchestrator run works end to end in a container
verifies: fixture streams recorded in S4 classify to: clean -> None, bogus token ->
          auth_failed, refused window -> USAGE_LIMIT, killed on budget -> error_max_budget_usd;
          one real card on a throwaway repo reaches Checking
```

```
unit:     U7-post-run-lease-check
expects:  nothing (independent of labs)
does:     after fetch-back, diffs the card branch against its base and blocks LEASE_CONFLICT on
          any path outside the lease
outputs:  lifecycle blocks with the offending paths in the card message
verifies: a card commit touching docs/x.md with a lease of src/** blocks LEASE_CONFLICT naming
          docs/x.md; an in-lease commit passes
```

```
unit:     U8-budget-watchdog
expects:  U3
does:     enforces the per-role cap from neutral usage for adapters without native_budget
outputs:  a capped run ends as subtype error_max_budget_usd with its partial output kept
verifies: a fake process emitting usage events worth $0.60 against a $0.50 cap is terminated,
          and the reviewer path keeps a structured answer given before the cap
```

```
unit:     U9-role-routing
expects:  U4, U5, U6
does:     lifecycle, orchestrator, consolidate and server resolve lab > model per role and pick
          the adapter and profile from it
outputs:  every run records the ref it actually used
verifies: settings orchestrator=openai/x, worker=anthropic/sonnet: a mission control turn
          starts the codex adapter and the card it proposes runs on claude; a card with its own
          ref overrides the worker default
```

```
unit:     U10-per-lab-limits
expects:  U5, U9
does:     per-lab pause map, rotation inside the lab, optional per-role cross-lab fallback
outputs:  scheduler parks only the limited lab
verifies: two queued cards, one per lab; the anthropic one blocks USAGE_LIMIT with one profile;
          the openai card still starts; with fallback [openai/x] set, the anthropic card
          re-queues on openai/x and the card gets a message saying so
```

```
unit:     U11-orchestrator-prompt-and-evidence
expects:  U1, U9
does:     generates the model paragraph from the catalog, adds lab to the reply schema,
          validates proposed refs, keys evidence and model_fit by ref, replaces the opus
          heuristic with tier
outputs:  mission control proposes lab > model per card
verifies: with only anthropic configured the paragraph lists no openai model; a proposed
          "openai/unknown" becomes the board default with a note; model_fit shows separate rows
          for anthropic/sonnet and openai/x
```

```
unit:     U12-ui
expects:  U4, U5, U9, and any ui_base primitive it needs landed first
does:     profiles panel by lab, two-level model menu, role pickers with fallback, usage and
          costs grouped by lab, ~ on estimated costs
outputs:  wiring in ui/ only
verifies: js suite covers the menu: picking openai then x PATCHes {"lab":"openai","model":"x"};
          a lab with no profile is not selectable; the keyboard binding count test is updated
          only if a key is added (none is planned)
```

```
unit:     U13-image-preflight-demo-docs
expects:  U6
does:     installs pinned codex in card.Dockerfile, adds per-lab preflight rows and setup hints,
          adds openai rows to the demo board, documents setup in README
outputs:  `smortboard --demo` shows both labs
verifies: preflight on a board with an openai profile and no codex in the image reports a fail
          row naming the image; the demo usage overlay shows two lab sections
```

Order: S4 first, then S5/S6/S7 in parallel, alongside U1, U4, U5, U7 (none of those need a
spike). Then U2, U3, U8. Then U6. Then U9, U10, U11. Then U12, U13.

## 6. Whole-system test

On a fresh board with a throwaway repo, one anthropic profile, one openai profile:

1. settings: orchestrator = openai/<model>, worker = anthropic/sonnet, reviewer =
   openai/<model>.
2. ask mission control for two cards; it proposes one with `anthropic/sonnet` and one with
   `openai/<model>`.
3. both run in parallel, each in its own container with its own CLI.
4. both reach Checking with PRs; each card's attempts show the right ref, cost (the openai one
   marked estimated if S4 finds no cost field) and profile.
5. mark the anthropic profile limited by hand; queue a third anthropic card. it parks, the
   openai lab keeps running, the usage overlay shows the anthropic lab limited.

Pass = all five hold and `usage_projection` totals equal the sum of the two cards' attempts.

## 7. Decisions for the operator

1. cross-lab fallback: opt-in per role as designed, or never cross labs automatically.
   recommendation: opt-in, off by default - a silent model swap changes what the evidence means.
2. reviewer on a different lab than the worker by default: recommendation yes once openai is
   configured - an independent reviewer is the point of the gate - but only as a suggestion in
   settings, never an automatic change.
3. openai credential kind: chatgpt access token, api key, or both. both is designed; S7 decides
   whether the access token works headless at all.
4. one image with both CLIs (recommended) or one image per lab.
