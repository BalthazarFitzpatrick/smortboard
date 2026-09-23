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

---

## 1. Create the repo (5 minutes)

```bash
mkdir -p ~/Documents/dev/smort-arcade && cd ~/Documents/dev/smort-arcade
git init -b main
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

git add -A && git commit -m "initial project scaffold"
gh repo create smort-arcade --private --source . --push
git switch -c development && git push -u origin development
```

## 2. Set up the board

1. Start the board from the latest `development` of smortboard: `uv run smortboard`.
2. `b` -> **from local repo** -> pick `~/Documents/dev/smort-arcade`.
3. On the repo's row: default branch **`development`**, test command **`node --test`**. No lint
   command.
4. `o`:
   - **file leases**: leave this board on **strict** for now; step 5 switches it.
   - **budgets and spend caps**: a daily budget for this board, e.g. `$30`.
   - **models by role**: worker sonnet. If you have a Codex profile, put the reviewer on OpenAI.
     A reviewer from another lab is the point of the gate.
   - **usage limits**: tick "ask me before switching to a fallback model" to see the inbox route.
5. `h` until everything is green.

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
