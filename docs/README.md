# smortboard docs

Start with the [README](../README.md): what it does, four-step setup, best practices. Everything
else is here.

## Get started

- [Install](get-started/install.md) - requirements, clone, credentials, the protect-main hook
- [First board](get-started/first-board.md) - repo, lab keys, `h`, the first card
- [Practice board](beta-test-board.md) - `seed-beta`: a small game repo and 15 cards to run end to end
- [Protect main](protect-main.md) - a `development` branch, the hook, the GitHub ruleset

## Best practices

Each page opens with the practice, then how it works.

- [Cards](practices/cards.md) - write the card once, completely; statuses, reason codes, edges
- [Leases](practices/leases.md) - keep leases narrow, and keep them apart; strict and soft
- [Attention and inbox](practices/attention.md) - watch one column
- [The two gates](practices/gates.md) - let your tests and a second agent judge
- [Landing and main](practices/landing.md) - main is yours; merge modes, the landing lock
- [Waiting pull requests](practices/waiting-prs.md) - review on your schedule; the rebase guard
- [Mission control and workforce](practices/mission-control.md) - plan in one chat, steer in the other
- [Labs and usage limits](practices/labs.md) - profiles, a model per role, fallbacks
- [Cost and budgets](practices/cost.md) - set a budget before you run a board
- [Runs and the event log](practices/runs.md) - re-run a blocked card instead of starting over
- [When to step in](practices/stepping-in.md) - define the work, then walk away

## Reference

- [Keyboard](reference/keyboard.md) - every key, from the app's own table
- [Configuration](reference/configuration.md) - flags, env vars, settings
- [Credential files](reference/credentials.md) - where `shift`+`p` keeps each credential
- [Backup](reference/backup.md) - export and import
- [Repos and test commands](reference/repos.md) - the test gate's command and image
- [Security](reference/security.md) - the board, the container, the guards

## Help

- [Troubleshooting](help/troubleshooting.md) - common stops and their fixes
- [Beta and reporting](help/beta.md) - what is solid, what is rough, how to report

## Internals

- [Development](internals/development.md) - the local loop, tests, CI
- [Plan](PLAN.md), [Phase 1 contracts](PHASE1-CONTRACTS.md), [Prompts](PROMPTS.md)
- Spikes: [S1-S2](spikes/S1-S2-findings.md), [S3](spikes/S3-findings.md),
  [S4](spikes/S4-codex-events.md), [S5](spikes/S5-codex-steering.md),
  [S6](spikes/S6-codex-hooks.md), [S7](spikes/S7-codex-auth.md)
