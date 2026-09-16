"""the demo keeps demonstrating all three column regimes.

the regimes are decided by ui_base's pile.js computeColumnFit, so this asks THAT function rather than
restating its arithmetic in python - a node helper loads the real module and answers with a regime
per column. the counts come from smortboard/demo.py itself, so there is one source of truth: change
a board's card counts, or tune the fit maths, and this fails instead of the demo quietly flattening
into three identical columns.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from smortboard.demo import (
    DEMO_COLUMN_AVAILABLE_PX,
    DEMO_COLUMN_WIDTH_PX,
    DEMO_VIEWPORT,
    column_counts,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
HELPER = REPO_ROOT / "tests" / "js_tools" / "demo_regimes.mjs"
NODE = shutil.which("node")

# the row gap the board's own stylesheet sets between two cards (--card-gap)
COLUMN_GAP_PX = 10

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _regimes_from_columns_js() -> list[dict]:
    spec = {
        "width": DEMO_COLUMN_WIDTH_PX,
        "available": DEMO_COLUMN_AVAILABLE_PX,
        "gap": COLUMN_GAP_PX,
        "boards": column_counts(),
    }
    result = subprocess.run(
        [NODE, str(HELPER), json.dumps(spec)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        pytest.fail(f"demo_regimes.mjs failed (exit {result.returncode}):\n{result.stderr}")
    return json.loads(result.stdout)


def test_every_demo_board_shows_all_three_column_regimes():
    """at the stated viewport, each board has a column that spreads, one that fans, and one that
    fans between two piles - the whole point of the demo dataset"""
    for board in _regimes_from_columns_js():
        found = set(board["regimes"].values())
        assert {1, 2, 3} <= found, (
            f"{board['board']} at {DEMO_VIEWPORT[0]}x{DEMO_VIEWPORT[1]} draws regimes {sorted(found)}, "
            f"not all three: {board['regimes']}"
        )


def test_the_columns_named_as_a_regime_are_that_regime():
    """not just "all three appear somewhere" - the column each board spec NAMES for a regime is the
    one that actually lands in it, so the demo's own labelling cannot drift from what it draws"""
    measured = {board["board"]: board["regimes"] for board in _regimes_from_columns_js()}
    for spec in column_counts():
        for column, regime in spec["regimes"].items():
            assert measured[spec["board"]][column] == regime, (
                f"{spec['board']}'s {column} column is regime "
                f"{measured[spec['board']][column]}, not the {regime} it is specified as"
            )
