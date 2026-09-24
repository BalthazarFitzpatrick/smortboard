# smortboard docs

start with the [readme](../README.md): what it does, four-step setup, best practices. everything
else is here.

## get started

- [install](get-started/install.md) - requirements, clone, credentials, the protect-main hook
- [first board](get-started/first-board.md) - repo, lab keys, `h`, the first card
- [practice board](beta-test-board.md) - `seed-beta`: a small game repo and 15 cards to run end to end
- [protect main](protect-main.md) - a `development` branch, the hook, the github ruleset

## best practices

each page opens with the practice, then how it works.

- [cards](practices/cards.md) - write the card once, completely; statuses, reason codes, edges
- [leases](practices/leases.md) - keep leases narrow, and keep them apart; strict and soft
- [attention and inbox](practices/attention.md) - watch one column
- [the two gates](practices/gates.md) - let your tests and a second agent judge
- [landing and main](practices/landing.md) - main is yours; merge modes, the landing lock
- [waiting pull requests](practices/waiting-prs.md) - review on your schedule; the rebase guard
- [mission control and workforce](practices/mission-control.md) - plan in one chat, steer in the other
- [labs and usage limits](practices/labs.md) - profiles, a model per role, fallbacks
- [cost and budgets](practices/cost.md) - set a budget before you run a board
- [runs and the event log](practices/runs.md) - re-run a blocked card instead of starting over
- [when to step in](practices/stepping-in.md) - define the work, then walk away

## reference

- [keyboard](reference/keyboard.md) - every key, from the app's own table
- [configuration](reference/configuration.md) - flags, env vars, settings
- [credential files](reference/credentials.md) - where `shift`+`p` keeps each credential
- [backup](reference/backup.md) - export and import
- [repos and test commands](reference/repos.md) - the test gate's command and image
- [security](reference/security.md) - the board, the container, the guards

## help

- [troubleshooting](help/troubleshooting.md) - common stops and their fixes
- [beta and reporting](help/beta.md) - what is solid, what is rough, how to report

## internals

- [development](internals/development.md) - the local loop, tests, ci
- [plan](plan.md), [phase 1 contracts](phase1-contracts.md), [prompts](prompts.md)
- spikes: [s1-s2](spikes/s1-s2-findings.md), [s3](spikes/s3-findings.md),
  [s4](spikes/s4-codex-events.md), [s5](spikes/s5-codex-steering.md),
  [s6](spikes/s6-codex-hooks.md), [s7](spikes/s7-codex-auth.md)
