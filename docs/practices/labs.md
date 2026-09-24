# Labs and usage limits

**One profile per subscription, a model per role, a fallback in another lab.** Spread the work over
the subscriptions you already pay for, and pick models from what they cost and passed. A second lab
gives you an independent reviewer, and somewhere to go when one lab is rate-limited.

## How it works

### Credential profiles

**`shift`+`p` holds one profile per credential.** A profile is a name plus its own mode-600 token
file. A lab holds several; one is active. Tokens are model-only, never your login: an Anthropic
profile takes a `claude setup-token` token. Token files and settings from older versions keep
working without a migration.

### Adding an OpenAI profile

1. Rebuild the card image ([Install](../get-started/install.md)):
   `docker build -f docker/card.Dockerfile -t smortboard-card:latest .`
2. `shift`+`p`, choose `openai`, add a profile.
3. Pick **ChatGPT login JSON** and paste your Codex login's `auth.json`, or **API key** for an
   OpenAI key.

The board keeps it in its own mode-600 file. At run time it goes over stdin into the container's
temporary, memory-backed Codex home. Your host Codex home is never mounted.

### A model per role

**`o` -> labs and models -> models by role** sets a lab (Claude Code or Codex) and a model per
role. A card's `m` menu overrides the worker. A lab with no usable profile is disabled in the model
menu. Defaults: `opus` for mission control, `sonnet` for worker and reviewer. The roles want
different things:

| role | does | wants |
|---|---|---|
| mission control | reads repos, argues scope, writes cards | your strongest model. It decides what gets built; the rest execute |
| worker | one card, container and lease | the cheapest model reaching pull requests without fix rounds |
| reviewer | gives a verdict on a diff | **another lab than the worker**, once you have two. An independent reader is the point of the gate; one family shares blind spots |
| fold | consolidates the backlog | mission control's class: the same judgement, smaller surface |

`i` groups finished cards by model and complexity with their cost. That is where the right worker
model shows. Let a card's complexity move the worker, not the board default: up for design work,
low for mechanical edits.

### Fallbacks

**Each role takes an ordered fallback list**, such as `openai/gpt-5.6-sol`. Empty keeps the role on
its own lab. Profile rotation stays inside one lab; a cross-lab retry needs an explicit fallback and
leaves a message on the card. Codex notes arrive on the next run.

> [!IMPORTANT]
> **A usage limit waits by default.** One setting decides what happens: `o` -> labs and models ->
> usage limits.
>
> | choice | on a usage limit |
> |---|---|
> | wait for the reset (default) | the profile is marked limited and the board parks until the window resets |
> | ask me | the card waits in the inbox `n` with a **retry on** button for its fallback model |
> | switch by itself | the board moves to your next free credential profile, then to the role's fallback model |
>
> Running cards are left alone. A card still limited re-runs on its own model once the window
> resets, whichever you chose.

### The model catalog

Models come from
[`smortboard/labs/catalog.json`](https://github.com/BalthazarFitzpatrick/smortboard/blob/main/smortboard/labs/catalog.json).
Add or override entries in `~/.config/smortboard/catalog.json` (under `XDG_CONFIG_HOME` when set):

```json
{
  "openai": {
    "models": [
      {"id": "your-model-id", "label": "Your model", "tier": "standard"}
    ]
  }
}
```

### Codex spend

- Give a model entry `price_per_mtok` with numeric `input`, `cached_input` and `output` rates. The
  packaged catalog has none; supply your own.
- Estimates carry `~`. A run with no price shows `unknown`, and the cost overview counts it as
  "+ n unknown" beside the known spend. An enabled cumulative spend cap refuses new starts while
  prior spend is unknown.
- The per-run watchdog acts on token usage, which the measured CLI emits at turn completion. It
  cannot promise a hard dollar ceiling mid-turn.

Measured limits: [event spike](../spikes/S4-codex-events.md),
[credential spike](../spikes/S7-codex-auth.md).
