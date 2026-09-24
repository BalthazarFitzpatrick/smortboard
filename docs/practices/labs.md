# Labs and usage limits

**One profile per subscription, a model per role, a fallback in another lab.** Spread work across
the subscriptions you already pay for, and pick models from measurements. A second lab buys an
independent reviewer and somewhere to go when one is rate-limited.

## How it works

### Credential profiles

`shift`+`p` holds one profile per credential: a name plus its own mode-600 token file. A lab holds
several; one is active. Tokens are model-only, never your login: Anthropic profiles use
`claude setup-token`. Existing token files and settings keep working without a migration.

> [!IMPORTANT]
> **Rotation is off by default.**
> A profile at its limit parks new starts on that lab until the window resets. Set
> `usage_limit_route` to `switch` in settings to rotate instead, so one limited account does
> not stop the board. Running cards are left alone either way.

### Adding an OpenAI profile

1. Rebuild the card image ([Install](../get-started/install.md)):
   `docker build -f docker/card.Dockerfile -t smortboard-card:latest .`
2. `shift`+`p`, choose `openai`, add a profile.
3. Pick **ChatGPT login JSON** and paste your Codex login's `auth.json`, or **API key** for an
   OpenAI key.

The board stores it in its own mode-600 file. At run time it goes over stdin into the container's
temporary memory-backed Codex home. Your host Codex home is never mounted.

### A model per role

Settings sets a lab (Claude Code or Codex) and a model per role; a card's `m` menu overrides the
worker. Labs without a usable profile are disabled in the model menu. Defaults are `opus` for
mission control and `sonnet` for worker and reviewer, because the roles want different things:

| role | does | wants |
|---|---|---|
| mission control | reads repos, argues scope, writes cards | your strongest model. It decides what gets built; the rest execute |
| worker | one card, container and lease | the cheapest model reaching pull requests without fix rounds |
| reviewer | gives a verdict on a diff | **another lab than the worker**, once you have two. An independent reader is the point of the gate; one family shares blind spots |
| fold | consolidates the backlog | mission control's class: the same judgement, smaller surface |

`i` groups finished cards by model and complexity with their cost, which shows the right worker
model. A card's complexity, not the board default, should move the worker: up for design work, low
for mechanical edits.

### Fallbacks

Each role takes an ordered fallback list, such as `openai/gpt-5.6-sol`; empty keeps it on its lab.
Profile rotation stays within a lab. A cross-lab retry needs an explicit fallback and leaves a
message on the card. Codex notes arrive on the next run.

> [!IMPORTANT]
> **A usage limit waits by default.** One `o` setting decides what happens:
> | `usage_limit_route` | on a usage limit |
> |---|---|
> | not set: wait (default) | the profile is marked limited and the board parks until the reset |
> | `attention` | the card waits in the inbox with a **retry on** control for its fallback model |
> | `switch` | the board rotates to the next free profile, then to the role's fallback model |
>
> Whichever route, a card still limited re-runs on its own model once the window resets.

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

- Add `price_per_mtok` with numeric `input`, `cached_input` and `output` rates to a model entry. The
  packaged catalog has none; supply your own.
- Estimates carry `~`; missing prices show `unknown`. An enabled cumulative spend cap refuses new
  starts while prior spend is unknown.
- The per-run watchdog acts on token usage, which the measured CLI emitted at turn completion, so it
  cannot guarantee a hard dollar ceiling mid-turn.

Measured limits: [event spike](../spikes/S4-codex-events.md),
[credential spike](../spikes/S7-codex-auth.md).
