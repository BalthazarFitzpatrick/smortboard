# Beta and reporting

One user so far. The core runs daily on real repos; everything around it has seen one person, one
machine, one set of habits.

## What is solid

- the card lifecycle end to end: worktree, run, test gate, reviewer, pull request
- the containment and the guards
- the landing lock, including from outside the board
- cost accounting and the spend caps
- hours of unattended running without supervision, which is what it was built for

## What is rough

- **Only macOS is really used.** Linux should be fine: CI runs the test suite there every push.
  The Windows code paths exist but never ran. Assume they are broken and tell us how.
- **Setup is the least tested.** It ran a handful of times, always on a machine that already had
  everything. The pre-flight checklist (`h`) is the best defence; reports about it are
  especially useful.
- **Keys follow one person's habits.** A key that surprises you is worth a report, even if it works
  as built.
- **The HTTP server handles one request at a time.** Fine for one person on one machine. It is not a
  multi-user server, by design.
- **A stop during the test gate** waits for the gate to finish.
- **Task checkboxes in a pull request** are not ticked during a run.

## What will change

Anything on the rough list. The database migrates itself forward on every open, so upgrades keep
your boards. But this is `0.x`: settings, defaults and panel shapes will move.

What will not: [cards](../practices/cards.md), [leases](../practices/leases.md),
[the two gates](../practices/gates.md), [the landing lock](../practices/landing.md), and human-only
merges into main.

## Try it end to end

`uv run smortboard seed-beta <empty folder>` makes a small browser-game repo and a board of 15 cards
already written: dependency chains, parallel tracks, and a test gate that needs nothing but Node.
[Practice board](../beta-test-board.md) walks it. It also has the mission-control prompt, if you
would rather plan it yourself, and a table of which board feature each step exercises.

## Sending a report

File bugs at
**[github.com/BalthazarFitzpatrick/smortboard/issues](https://github.com/BalthazarFitzpatrick/smortboard/issues)**
with the bug report template. The more of it you fill in, the faster the fix:

- the version (`uv run smortboard --version`) and your operating system
- anything not green in the pre-flight checklist (`h`)
- **what you did**: the keys you pressed or the card you ran, in order
- **what you expected, and what happened instead**
- which board and card, plus evidence: a screenshot, the card's replay (`t`), its comments, or the
  output in the terminal smortboard runs in

> [!IMPORTANT]
> **Keep credentials out.**
> Never paste your card token, your API key, or the contents of `~/.config/smortboard/`.

Read a transcript before attaching it: a card's event log holds whatever the agent saw in your repo.
`smortboard export` ([Backup](../reference/backup.md)) attaches your whole board reproducibly. It
never carries credential material, so nothing needs redacting.
