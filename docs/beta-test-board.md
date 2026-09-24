# Beta-test a board front to back: Comet Catcher

A small browser game, built by the board from one mission-control prompt: a canvas game, a server
that keeps a high-score table, and a skill tree you buy upgrades from. It is sized to produce 12-14
cards with real dependencies and real parallelism, so one evening exercises most of the board.

**Why this game.** It needs no npm, no Python and no custom image. The card image is
`node:22-slim`, and Node 22 has a test runner built in, so `node --test` runs in the offline test
gate as it is. Everything is plain JavaScript modules and Node's standard library.

**What it costs.** Measured on the board before the token trim landed: a worker run averaged
$1.12 on sonnet and a review $0.16. Twelve cards that pass first time are about $15; plan for $20-30
with fix rounds and retries. Keep the worker on sonnet and set a board daily budget before you start
(`o` -> budgets and spend caps).

## Fast path: seed it (2 minutes)

One command makes the repo, its private GitHub origin and a board with 15 cards already written. You
skip sections 1 to 3 and test running rather than planning:

```bash
uv run smortboard seed-beta ~/Documents/dev/comet-catcher
# run the one command it prints, then
uv run smortboard
```

The seed commits the scaffold on `main`, branches `development`, creates a private GitHub repo named
after the folder and pushes `development` to it. `gh` has to be logged in (`gh auth login`). It
never pushes `main`. That is the one command it prints for you: it pushes `main` and makes it the
repo's default branch on GitHub. Run it once.

Then `h` until every row is green, and `w`. The seed sets the repo's base to `development`, its test
command to `node --test`, strict leases, a $30 daily budget and sonnet on every card. A folder that
is not empty, or a board already named `comet-catcher`, is refused before anything is written
(`--name` picks another name). When `gh` refuses, because it is not logged in or the repo name is
taken, the seed takes its folder back out and adds nothing to the board.

The seeded cards have the shape section 3 asks mission control for. Take sections 1 to 3 instead
when mission control's planning is what you want to test.

---

## 1. Create the files (5 minutes)

```bash
mkdir -p ~/Documents/dev/smort-arcade && cd ~/Documents/dev/smort-arcade
mkdir -p server public/js public/css test data

cat > package.json <<'EOF'
{
  "name": "smort-arcade",
  "private": true,
  "type": "module",
  "scripts": {"start": "node server/main.js", "test": "node --test"}
}
EOF

cat > public/index.html <<'EOF'
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Comet Catcher</title></head>
<body><canvas id="game" width="480" height="640"></canvas></body>
</html>
EOF

cat > test/smoke.test.js <<'EOF'
import { test } from "node:test";
import assert from "node:assert/strict";

test("the test runner works", () => assert.equal(1 + 1, 2));
EOF

printf 'data/\nnode_modules/\n' > .gitignore
printf '# Comet Catcher\n\nA browser game built card by card on smortboard.\n' > README.md
touch server/.gitkeep public/js/.gitkeep public/css/.gitkeep

```

## 2. Set up the board

1. Start the board from the latest `development` of smortboard: `uv run smortboard`.
2. `b` -> **new board** -> pick `~/Documents/dev/smort-arcade`. It has files and no git, so the
   board lists what the first commit would hold: confirm. It commits, makes `development`, creates
   the private GitHub repo `smort-arcade`, pushes `development`, and shows the one push-main command -
   run it.
3. On the repo's row: base **`development`**, test command **`npm test`** (found from
   `package.json`). No lint command.
4. `o`, the settings every board shares:
   - **models by role**: worker sonnet. If you have a Codex profile, put the reviewer on OpenAI.
     A reviewer from another lab is the point of the gate.
   - **usage limits**: **ask me**, to see the inbox route.
   - **soft file leases**: **on**, so section 4 can switch this board to soft later.
5. `shift`+`o`, this board's own settings:
   - **file lease**: leave it on **strict** for now; the table in section 4 says when to switch.
   - **daily budget**: e.g. `30`.
6. `h` until everything is green.

## 3. The prompt for mission control

Open mission control with `.` on the new board and paste this:

```text
Plan Comet Catcher, a small browser game, as 12 to 14 cards on this board's one repo.

THE GAME
- canvas 480x640. The player's ship moves left and right along the bottom (arrow keys, A/D).
- stars fall and are caught for points; comets fall and cost a life. 3 lives. Speed rises over time.
- every caught star also drops stardust, the currency for the skill tree. Stardust persists between runs.
- skill tree: 8 upgrades in 3 branches, each with a cost and prerequisites:
  magnet (catch radius, 3 tiers), shield (absorbs one comet, 2 tiers), thrusters (ship speed, 3 tiers).
  a node is buyable only when its prerequisite is owned and there is enough stardust.
  the title screen says K opens it; a run's stardust is banked at game over; owned skills apply
  from the next run. the upgrades card owns that wiring in game.js, so the tree is playable end to end.
- high scores: top 10 across all players, kept by the server in data/scores.json, surviving a restart.
  on game over the player enters a name (1-12 characters) and sees the table.

HOW IT IS BUILT - hard constraints
- no npm dependencies, no build step, no framework. plain ES modules in the browser, Node 22 stdlib on
  the server (node:http, node:fs, node:path, node:test).
- every piece of logic that can be pure is pure and tested with node:test under test/. the test gate is
  offline and runs `node --test`; nothing may need the network or a browser to pass.
- server/: node:http server that serves public/ and a JSON api: GET /api/scores, POST /api/scores.
  POST validates name (1-12 printable characters) and score (non-negative integer below 10 million),
  rejects everything else with 400, and writes scores atomically (temp file, then rename).
- public/js/: one module per concern - core.js (entities, spawning, collision, tick; pure), render.js
  (canvas drawing), input.js (keyboard), game.js (states: title, playing, game over; the loop),
  hud.js, skills.js (tree data and buy rules; pure), skills-ui.js, scores-ui.js, storage.js
  (localStorage for stardust and owned skills, versioned). public/css/game.css for the screens.

CARD SHAPE - I want these properties across the set, to test the board itself
- three tracks that can run in parallel from the start, with disjoint leases: the server track, the
  game-core track, the skill-tree data track.
- real dependency chains, at least one three cards deep (e.g. core -> render+input -> game loop).
- one card that needs another track's output: the high-score screen depends on both the scores api and
  the game-over state.
- one card whose natural change reaches a file outside its obvious lease: applying skill upgrades to
  gameplay (its own module, plus small hooks in core.js).
- two small cards that could be one: a mute toggle and a pause key. leave them separate.
- one card that is mostly visual: the skill tree screen.
- the api input validation card must be explicit about rejecting bad input, so the reviewer has
  something real to check.
- a last card: README with how to run and play.

For each card: complexity low or medium, sonnet as the model, a narrow lease over the real paths above,
and acceptance criteria a node:test can check where the card has logic.
```

## 4. What to watch, and what each part exercises

| Step | Do | It exercises |
|---|---|---|
| mission control | read the plan, then let it create the cards | planning, card text limits (8-word titles), leases over real paths, complexity, dependencies |
| `w` | run the whole board | parallel runs on disjoint leases, the grey pending edge in doing, lease-overlap serialisation |
| the three-deep chain | watch a child start on an unmerged parent | review-mode stacks (up to three deep), a pull request re-targeted when its parent lands |
| the api validation card | read the reviewer's verdict | the reviewer's four questions, severity grading, findings routed to you |
| the skill-tree screen card | open it in checking | a mostly visual change judged by tests and review alone. The board's own screenshot step runs only on smortboard's own `ui/`, so no image here |
| the upgrades card | watch it want core.js | strict: a refused write, then either lease approve in the inbox or, if it committed, the gates run and the wanted path waits under needs. then switch the board to **soft** in `o` and re-run: it may reach core.js unless another active card holds it |
| mute + pause | `f` on the board | fold: two todo cards into one, dependencies re-pointed, originals restorable |
| any blocked card | `n` | the inbox, answering, lease approve, retry on fallback after a usage limit |
| open any card | `space`, then the arrow keys | about / done / needs, section navigation, the next action |
| the board | `cmd`/`ctrl`+`f` | the card filter |
| cost | `i`, `c`, `u` | per-card and per-board cost, the cap fit, wasted spend |
| accept | `y` on a checking card | landing on `development` through the landing lock; `main` stays yours |

When a card surprises you, file it from `beta: submit issue` on the board. Note the card's short id
and what you expected instead.
