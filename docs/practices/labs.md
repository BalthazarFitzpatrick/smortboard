# labs and usage limits

**one profile per subscription, a model per role, a fallback in another lab.** spread the work over
the subscriptions you already pay for, and pick models from what they cost and passed. a second lab
gives you an independent reviewer, and somewhere to go when one lab is rate-limited.

## how it works

### credential profiles

**`shift`+`p` holds one profile per credential.** a profile is a name plus its own mode-600 token
file. a lab holds several; one is active. tokens are model-only, never your login: an anthropic
profile takes a `claude setup-token` token. token files and settings from older versions keep
working without a migration.

### adding an openai profile

1. rebuild the card image ([install](../get-started/install.md)):
   `docker build -f docker/card.Dockerfile -t smortboard-card:latest .`
2. `shift`+`p`, choose `openai`, add a profile.
3. pick **chatgpt login json** and paste your codex login's `auth.json`, or **api key** for an
   openai key.

the board keeps it in its own mode-600 file. at run time it goes over stdin into the container's
temporary, memory-backed codex home. your host codex home is never mounted.

### a model per role

**`o` -> labs and models -> models by role** sets a lab (claude code or codex) and a model per
role. a card's `m` menu overrides the worker. a lab with no usable profile is disabled in the model
menu. defaults: `opus` for mission control, `sonnet` for worker and reviewer. the roles want
different things:

| role | does | wants |
|---|---|---|
| mission control | reads repos, argues scope, writes cards | your strongest model. it decides what gets built; the rest execute |
| worker | one card, container and lease | the cheapest model reaching pull requests without fix rounds |
| reviewer | gives a verdict on a diff | **another lab than the worker**, once you have two. an independent reader is the point of the gate; one family shares blind spots |
| fold | consolidates the backlog | mission control's class: the same judgement, smaller surface |

`i` groups finished cards by model and complexity with their cost. that is where the right worker
model shows. let a card's complexity move the worker, not the board default: up for design work,
low for mechanical edits.

### fallbacks

**each role takes an ordered fallback list**, such as `openai/gpt-5.6-sol`. empty keeps the role on
its own lab. profile rotation stays inside one lab; a cross-lab retry needs an explicit fallback and
leaves a message on the card. codex notes arrive on the next run.

> [!IMPORTANT]
> **a usage limit waits by default.** one setting decides what happens: `o` -> labs and models ->
> usage limits.
>
> | choice | on a usage limit |
> |---|---|
> | wait for the reset (default) | the profile is marked limited and the board parks until the window resets |
> | ask me | the card waits in the inbox `n` with a **retry on** button for its fallback model |
> | switch by itself | the board moves to your next free credential profile, then to the role's fallback model |
>
> running cards are left alone. a card still limited re-runs on its own model once the window
> resets, whichever you chose.

### the model catalog

models come from
[`smortboard/labs/catalog.json`](https://github.com/BalthazarFitzpatrick/smortboard/blob/main/smortboard/labs/catalog.json).
add or override entries in `~/.config/smortboard/catalog.json` (under `XDG_CONFIG_HOME` when set):

```json
{
  "openai": {
    "models": [
      {
        "id": "gpt-6.1-sol",
        "label": "gpt-6.1 sol",
        "tier": "standard",
        "effort_levels": ["low", "medium", "high", "xhigh", "max"],
        "default_effort": "medium"
      }
    ]
  }
}
```

each `id` is passed unchanged to the lab cli. versioned entries let you choose opus 5 or 5.5,
sonnet 5 or 5.5, and gpt-6 sol or gpt-6.1 sol separately. entries marked `(alias)` follow the
cli's family alias. existing role defaults stay unchanged; choose a version to pin your work.

the card, role and fallback pickers show that model's `effort_levels`. changing model clears an
effort the new model cannot use. direct api writes with an unsupported pair are refused. mission
control can also assign a card's model and effort together.

`default` stores null: card effort inherits the worker role, and a role without an effort sends
no cli flag. `default_effort` records the model/runtime default; it does not override inheritance.
if an inherited effort is unsupported by the selected model, the run sends no effort flag.
an empty `effort_levels` means no explicit effort support. older custom entries without metadata
retain low, medium and high. a `none` effort, where supported, is an explicit value, not null.

to maintain the list, edit the packaged catalog for a release or the local override for one
installation. overrides merge by lab and model id and reload on reads; reopen the picker after
changing the file. partial overrides retain other fields. include a version in new labels and
set `default_effort` to null when the cli default is unknown. adding a catalog entry does not
grant account access or upgrade the installed cli.

versioned model metadata checked on 2026-10-02 against the
[openai model catalog](https://developers.openai.com/api/docs/models),
[claude effort reference](https://platform.claude.com/docs/en/build-with-claude/effort), and
[claude code model configuration](https://code.claude.com/docs/en/model-config).
claude code defaults can differ from the api defaults. legacy gpt-5.6 entries retain their
previous effort range until their runtime capabilities are refreshed.

### codex spend

- give a model entry `price_per_mtok` with numeric `input`, `cached_input` and `output` rates. the
  packaged catalog has none; supply your own.
- estimates carry `~`. a run with no price shows `unknown`, and the cost overview counts it as
  "+ n unknown" beside the known spend. an enabled cumulative spend cap refuses new starts while
  prior spend is unknown.
- the per-run watchdog acts on token usage, which the measured cli emits at turn completion. it
  cannot promise a hard dollar ceiling mid-turn.
- adding prices also recovers historical codex estimates when model and complete input, cached
  and output token counts were recorded. read projections carry the rates and historical-estimate
  provenance; raw records and known actual costs stay unchanged. incomplete records stay unknown.

measured limits: [event spike](../spikes/s4-codex-events.md),
[credential spike](../spikes/s7-codex-auth.md).
