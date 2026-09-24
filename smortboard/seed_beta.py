"""`smortboard seed-beta <dir>`: a real, runnable board to beta-test smortboard front to back.

Writes the Comet Catcher scaffold (docs/beta-test-board.md) into a new folder, makes it ready the
same way a new board's folder is (repo_setup: first commit, development, a private GitHub origin,
development pushed), and adds a board for it with its cards already written, so the first run skips
mission control. Unlike --demo this goes into the operator's own database and survives a restart,
and its cards really run.

It never pushes main. Pushing main and making it GitHub's default is the operator's one step: the
command prints it.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smortboard.repo_setup import Runner, SetupRefused, SetupResult, default_runner, prepare
from smortboard.store.api import Store
from smortboard.store.repo_validation import validate_repo

BOARD_NAME = "comet-catcher"
BASE_BRANCH = "development"
TEST_COMMAND = "node --test"
DAILY_BUDGET_USD = 30.0

# the starter commit: an empty game with one passing test, so the base is green before any card
SCAFFOLD = {
    "package.json": """{
  "name": "comet-catcher",
  "private": true,
  "type": "module",
  "scripts": {"start": "node server/main.js", "test": "node --test"}
}
""",
    "public/index.html": """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Comet Catcher</title></head>
<body><canvas id="game" width="480" height="640"></canvas></body>
</html>
""",
    "test/smoke.test.js": """import { test } from "node:test";
import assert from "node:assert/strict";

test("the test runner works", () => assert.equal(1 + 1, 2));
""",
    ".gitignore": "data/\nnode_modules/\n",
    "README.md": "# Comet Catcher\n\nA browser game built card by card on smortboard.\n",
    "server/.gitkeep": "",
    "public/js/.gitkeep": "",
    "public/css/.gitkeep": "",
}

# the board in order, dependencies before dependants. three tracks start in parallel on disjoint
# leases (server, game, skills); game core -> render + input -> loop is three deep; highscores
# needs both the api and the loop; upgrades leaves core.js out of its lease on purpose
CARDS: list[dict[str, Any]] = [
    {
        "key": "store",
        "workstream": "server",
        "title": "Score store with atomic writes",
        "description": "server/scores.js keeps top 10 scores in data/scores.json. "
        "Writes go to a temp file, then rename.",
        "complexity": 1,
        "leases": ["server/scores.js", "test/scores.test.js"],
        "criteria": [
            "saving 12 scores keeps the 10 highest, sorted descending",
            "scores survive reloading from disk",
            "a write goes through a temp file and rename",
            "a missing or corrupt file reads as an empty table",
        ],
        "tasks": ["take the data dir as a parameter, tests use a temp dir"],
        "depends_on": [],
    },
    {
        "key": "server",
        "workstream": "server",
        "title": "Http server: public/ files and scores api",
        "description": "server/main.js serves public/ and GET/POST /api/scores over node:http; "
        "server/api.js holds the handlers. PORT, default 8080.",
        "complexity": 2,
        "leases": ["server/main.js", "server/api.js", "test/server.test.js"],
        "criteria": [
            "GET / serves public/index.html as text/html",
            "GET /api/scores returns the top 10, highest first",
            "POST /api/scores adds a score and answers 201",
            "paths escaping public/ answer 404",
            "tests start it on port 0 and close it",
        ],
        "tasks": [],
        "depends_on": ["store"],
    },
    {
        "key": "validate",
        "workstream": "server",
        "title": "Reject bad score posts with 400",
        "description": "POST /api/scores accepts only names of 1-12 printable characters and "
        "integer scores 0 to 9,999,999. Everything else: 400.",
        "complexity": 2,
        "leases": ["server/api.js", "server/validate.js", "test/validate.test.js"],
        "criteria": [
            "empty and 13-character names both answer 400",
            "control characters in a name answer 400",
            "negative, fractional and 10,000,000 scores answer 400",
            'a string score like "12" answers 400',
            "a rejected post never changes data/scores.json",
            "1-character name, score 0 and score 9,999,999 pass",
        ],
        "tasks": [],
        "depends_on": ["server"],
    },
    {
        "key": "core",
        "workstream": "game",
        "title": "Pure game core: ship, stars, comets, collisions",
        "description": "public/js/core.js: state, seeded spawning, tick(state, dt, input), "
        "catches, lives, score, stardust, speed ramp. No DOM.",
        "complexity": 2,
        "leases": ["public/js/core.js", "test/core.test.js"],
        "criteria": [
            "same seed and inputs give the same state after 100 ticks",
            "a caught star adds score and one stardust",
            "a comet hit costs a life; zero lives ends the game",
            "fall speed rises with elapsed time",
            "the ship stays inside the 480-pixel width",
        ],
        "tasks": [],
        "depends_on": [],
    },
    {
        "key": "render",
        "workstream": "game",
        "title": "Canvas renderer with score, lives and stardust",
        "description": "public/js/render.js draws ship, stars, comets and a HUD line from "
        "core state onto the 480x640 canvas.",
        "complexity": 1,
        "leases": ["public/js/render.js", "public/js/hud.js", "test/render.test.js"],
        "criteria": [
            "draw(ctx, state) reads state, never changes it",
            "a recording ctx stub sees one shape per star and comet",
            "the HUD shows score, lives and stardust",
        ],
        "tasks": [],
        "depends_on": ["core"],
    },
    {
        "key": "input",
        "workstream": "game",
        "title": "Keyboard input: arrows and A/D",
        "description": "public/js/input.js maps keys to left/right intent for core's tick, "
        "and attaches and detaches its listeners.",
        "complexity": 1,
        "leases": ["public/js/input.js", "test/input.test.js"],
        "criteria": [
            "ArrowLeft and KeyA give left; ArrowRight and KeyD right",
            "holding both directions gives no movement",
            "reads event.code, so layout never moves the keys",
            "detach removes every listener it added",
        ],
        "tasks": [],
        "depends_on": ["core"],
    },
    {
        "key": "loop",
        "workstream": "game",
        "title": "Game loop: title, playing, game over",
        "description": "public/js/game.js: state machine and requestAnimationFrame loop wiring "
        "core, render and input. index.html loads it.",
        "complexity": 2,
        "leases": ["public/js/game.js", "public/index.html", "test/game.test.js"],
        "criteria": [
            "Space starts play from the title screen",
            "zero lives moves playing to game over",
            "Space on game over returns to a fresh title",
            "transitions are a pure function, tested without a browser",
            "index.html loads js/game.js as a module",
        ],
        "tasks": [],
        "depends_on": ["render", "input"],
    },
    {
        "key": "skills",
        "workstream": "skills",
        "title": "Skill tree data and buy rules",
        "description": "public/js/skills.js: 8 upgrades in 3 branches (magnet 3 tiers, shield 2, "
        "thrusters 3), costs, prerequisites, canBuy, buy. Pure.",
        "complexity": 1,
        "leases": ["public/js/skills.js", "test/skills.test.js"],
        "criteria": [
            "exactly 8 nodes: magnet 3, shield 2, thrusters 3",
            "a tier needs the tier below it in its branch",
            "canBuy is false without enough stardust",
            "buy returns new owned set and stardust, never mutates",
        ],
        "tasks": [],
        "depends_on": [],
    },
    {
        "key": "storage",
        "workstream": "skills",
        "title": "Save stardust and owned skills locally",
        "description": "public/js/storage.js: versioned localStorage save and load for stardust "
        "and owned skills. Bad data loads as empty.",
        "complexity": 1,
        "leases": ["public/js/storage.js", "test/storage.test.js"],
        "criteria": [
            "save then load round-trips stardust and owned skills",
            "corrupt JSON loads as zero stardust, nothing owned",
            "an unknown version loads as empty, not a crash",
            "storage is injected, so tests pass a plain object",
        ],
        "tasks": [],
        "depends_on": ["skills"],
    },
    {
        "key": "skills-ui",
        "workstream": "skills",
        "title": "Skill tree screen from the title",
        "description": "public/js/skills-ui.js and game.css: 3 branches, costs, owned and locked "
        "nodes. Wired into game.js and the title.",
        "complexity": 2,
        "leases": [
            "public/js/skills-ui.js",
            "public/css/game.css",
            "public/js/game.js",
            "test/skills-ui.test.js",
        ],
        "criteria": [
            "K on the title screen opens it; Escape returns",
            "the title screen says K opens the skill tree",
            "opening it reloads stardust and owned skills from storage",
            "each node shows cost and owned, buyable or locked",
            "buying goes through skills.js buy and storage.js save",
            "the view model is a pure function with a test",
        ],
        "tasks": [],
        "depends_on": ["storage", "loop"],
    },
    {
        "key": "upgrades",
        "workstream": "skills",
        "title": "Upgrades applied to runs, stardust banked",
        "description": "upgrades.js: owned skills become catch radius, shield charges, ship speed. "
        "game.js uses it every run.",
        "complexity": 2,
        "leases": ["public/js/upgrades.js", "public/js/game.js", "test/upgrades.test.js"],
        "criteria": [
            "magnet tiers widen catch radius by 10, 20, 30 pixels",
            "each shield tier absorbs one comet per run",
            "each thruster tier raises ship speed 15%",
            "no skills owned plays exactly as before",
            "game.js runs each run with the skills owned at its start",
            "a game.js test shows a thruster run moves the ship faster",
            "game over adds the run's stardust to saved stardust",
        ],
        "tasks": [],
        "depends_on": ["core", "skills", "storage", "loop"],
    },
    {
        "key": "highscores",
        "workstream": "game",
        "title": "High-score entry and table on game over",
        "description": "public/js/scores-ui.js: on game over, enter a name (1-12 characters), "
        "POST it, then show the top 10.",
        "complexity": 2,
        "leases": ["public/js/scores-ui.js", "public/js/game.js", "test/scores-ui.test.js"],
        "criteria": [
            "names outside 1-12 characters never reach the server",
            "a 400 answer shows its message, keeps the entry open",
            "the table shows rank, name and score, highest first",
            "fetch is injected, so tests need no network",
        ],
        "tasks": [],
        "depends_on": ["validate", "loop"],
    },
    {
        "key": "mute",
        "workstream": "game",
        "title": "Mute toggle on the M key",
        "description": "public/js/sound.js: short WebAudio beeps on catch and hit. M mutes and "
        "unmutes, saved in localStorage.",
        "complexity": 1,
        "leases": ["public/js/sound.js", "public/js/game.js", "test/sound.test.js"],
        "criteria": [
            "M toggles muted, and the flag survives a reload",
            "muted plays nothing; unmuted beeps on catch and hit",
            "no AudioContext means silence, never an error",
        ],
        "tasks": [],
        "depends_on": ["loop"],
    },
    {
        "key": "pause",
        "workstream": "game",
        "title": "Pause and resume on the P key",
        "description": "P pauses the loop while playing and resumes it; the canvas shows PAUSED.",
        "complexity": 1,
        "leases": ["public/js/game.js", "test/pause.test.js"],
        "criteria": [
            "P while playing pauses; P again resumes",
            "no ticks run while paused",
            "P on title or game over does nothing",
        ],
        "tasks": [],
        "depends_on": ["loop"],
    },
    {
        "key": "readme",
        "workstream": "docs",
        "title": "README: how to run and play",
        "description": "README.md: run, open, controls, skill tree, stardust banking, where "
        "scores live, tests.",
        "complexity": 1,
        "leases": ["README.md"],
        "criteria": [
            "run, test and play steps work on a clean clone",
            "lists every key: arrows, A/D, Space, K, M, P, Escape",
            "says scores live in data/scores.json",
            "says stardust banks at game over, spent in the tree",
        ],
        "tasks": [],
        "depends_on": ["highscores", "skills-ui", "upgrades", "mute", "pause"],
    },
]


class SeedRefused(ValueError):
    """nothing was written - the message says why"""


@dataclass
class SeedResult:
    board: dict[str, Any]
    repo: dict[str, Any]
    cards: list[dict[str, Any]]
    setup: SetupResult


def _write_scaffold(repo_dir: Path) -> None:
    for rel, text in SCAFFOLD.items():
        path = repo_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _undo_repo(repo_dir: Path, created_dir: bool) -> None:
    if created_dir:
        shutil.rmtree(repo_dir, ignore_errors=True)
        return
    for child in repo_dir.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def seed_beta(
    store: Store,
    repo_dir: str | Path,
    board_name: str = BOARD_NAME,
    *,
    runner: Runner = default_runner,
) -> SeedResult:
    """makes the repo at `repo_dir`, its private GitHub origin, and the board for it. refuses,
    writing nothing, when the folder is not empty or a board of that name exists"""
    repo_dir = Path(repo_dir).expanduser().resolve()
    if any(board["name"] == board_name for board in store.list_boards()):
        raise SeedRefused(f"a board named {board_name!r} already exists - pass --name")
    if repo_dir.exists() and (not repo_dir.is_dir() or any(repo_dir.iterdir())):
        raise SeedRefused(f"{repo_dir} is not an empty folder - pick a new path")
    if shutil.which("git") is None:
        raise SeedRefused("git is not installed")

    created_dir = not repo_dir.exists()
    repo_dir.mkdir(parents=True, exist_ok=True)
    try:
        _write_scaffold(repo_dir)
        # the scaffold is the first commit, so the folder's files are confirmed up front
        setup = prepare(repo_dir, confirm=True, runner=runner)
    except (SetupRefused, OSError) as exc:
        _undo_repo(repo_dir, created_dir)
        raise SeedRefused(str(exc)) from exc

    path = validate_repo(repo_dir.name, str(repo_dir), BASE_BRANCH)
    position = max((board["position"] for board in store.list_boards()), default=-1) + 1
    board = store.create_board(board_name, position=position)
    store.set_board_lease_mode(board["id"], "strict")
    store.set_board_daily_budget(board["id"], DAILY_BUDGET_USD)
    repo = store.create_repo(
        board["id"], repo_dir.name, path, BASE_BRANCH, test_command=TEST_COMMAND
    )

    ids: dict[str, str] = {}
    cards = []
    for index, spec in enumerate(CARDS):
        card = store.create_card(
            board["id"],
            repo["id"],
            spec["title"],
            workstream=spec["workstream"],
            description=spec["description"],
            position=index,
            tasks=spec["tasks"],
            criteria=spec["criteria"],
            leases=spec["leases"],
            model="sonnet",
            lab="anthropic",
            depends_on=[ids[key] for key in spec["depends_on"]],
            complexity=spec["complexity"],
        )
        ids[spec["key"]] = card["id"]
        cards.append(card)
    return SeedResult(board=store.get_board(board["id"]), repo=repo, cards=cards, setup=setup)


def next_steps(result: SeedResult) -> str:
    """what the operator runs after the seed - pushing main is theirs, the board never does"""
    lines = []
    if result.setup.push_main:
        lines += ["one step is yours - run this once:", f"  {result.setup.push_main}", "", "then:"]
    return "\n".join(
        [
            *lines,
            "  1. uv run smortboard, open the board, press h until every row is green",
            "  2. w runs the whole board. docs/beta-test-board.md says what each part exercises",
        ]
    )
