# beta and reporting

**one user so far.** the core runs daily on real repos. everything around it has seen one person,
one machine and one set of habits.

## what is solid

- the card lifecycle end to end: worktree, run, test gate, reviewer, pull request
- the containment and the guards
- the landing lock, including from outside the board
- cost accounting and the spend caps
- hours of unattended running, which is what it was built for

## what is rough

- **only macos is really used.** linux should be fine: ci runs the test suite there every push.
  the windows code paths exist but never ran. assume they are broken and tell us how.
- **setup is the least tested.** it ran a handful of times, always on a machine that already had
  everything. the pre-flight checklist (`h`) is the best defence; reports about it are
  especially useful.
- **keys follow one person's habits.** a key that surprises you is worth a report, even if it works
  as built.
- **the http server handles one request at a time.** fine for one person on one machine. it is not a
  multi-user server, by design.
- **a stop during the test gate** waits for the gate to finish.
- **task checkboxes in a pull request** are not ticked during a run.

## what will change

**anything on the rough list.** the database migrates itself forward on every open, so upgrades
keep your boards. but this is `0.x`: settings, defaults and panel shapes will move.

what will not: [cards](../practices/cards.md), [leases](../practices/leases.md),
[the two gates](../practices/gates.md), [the landing lock](../practices/landing.md), and human-only
merges into main.

## try it end to end

`uv run smortboard seed-beta <empty folder>` makes a small browser game on a private github repo
and a board of 15 cards already written: dependency chains, parallel tracks, and a test gate that
needs nothing but node. [practice board](../beta-test-board.md) walks it, with the mission control
prompt if you would rather plan it yourself, and a table of which feature each step exercises.

## sending a report

**file bugs at
[github.com/balthazarfitzpatrick/smortboard/issues](https://github.com/BalthazarFitzpatrick/smortboard/issues)**
with the bug report template. the more of it you fill in, the faster the fix:

- the version (`uv run smortboard --version`) and your operating system
- anything not green in the pre-flight checklist (`h`)
- **what you did**: the keys you pressed or the card you ran, in order
- **what you expected, and what happened instead**
- which board and card, plus evidence: a screenshot, the card's replay (`t`), its comments, or the
  output in the terminal smortboard runs in

> [!IMPORTANT]
> **keep credentials out.**
> never paste your card token, your api key, or the contents of `~/.config/smortboard/`.

read a transcript before attaching it: a card's event log holds whatever the agent saw in your repo.
`smortboard export` ([backup](../reference/backup.md)) attaches your whole board reproducibly. it
never carries credential material, so nothing needs redacting.
