"""the js half of the test gate: every tests/js/*.mjs run with node.

without this the gate only ever proved the python side, so a card could break board.js and still
reach a pull request (#82, #90 both did). node resolves ui_base's assets from a sibling ../smortui
checkout when one exists, else from the installed ui_base package - the same fallback assets.py
uses via read_asset, so the suite runs unchanged inside the gate container and on a fresh clone.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from contextlib import ExitStack
from importlib.resources import as_file, files
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
JS_DIR = REPO_ROOT / "tests" / "js"
JS_TESTS = sorted(p for p in JS_DIR.glob("*.mjs") if p.name != "dom_stub.mjs")

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.fixture(scope="session")
def ui_base_assets_dir():
    """a real directory of ui_base's assets - as_file extracts one if the install is zipped"""
    with ExitStack() as stack:
        yield stack.enter_context(as_file(files("ui_base") / "assets"))


def test_js_suite(ui_base_assets_dir):
    """runs each tests/js/*.mjs with node, stopping at and naming the first one that fails"""
    env = {**os.environ, "UI_BASE_ASSETS_DIR": str(ui_base_assets_dir)}
    for js_test in JS_TESTS:
        result = subprocess.run(
            [NODE, str(js_test)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        if result.returncode != 0:
            pytest.fail(
                f"{js_test.relative_to(REPO_ROOT)} failed (exit {result.returncode}):\n"
                f"{result.stdout}\n{result.stderr}"
            )
