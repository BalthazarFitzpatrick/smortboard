"""every script index.html loads shares one global scope, so a top-level name declared twice kills the
second script at load - replay.js and settings.js both declaring `rp` silently disabled settings"""

import re
from collections import defaultdict
from pathlib import Path

from smortboard.server.assets import resolve_asset

UI = Path(__file__).resolve().parent.parent / "smortboard" / "ui"

# a declaration at column 0 is top level in these files; nested code is always indented
_DECLARATION = re.compile(r"^(?:const|let|class)\s+([A-Za-z_$][\w$]*)", re.MULTILINE)


def _page_scripts() -> list[str]:
    html = (UI / "index.html").read_text()
    return re.findall(r'<script src="/ui/([^"]+)"', html)


def test_the_page_loads_scripts():
    assert "board.js" in _page_scripts()


def test_no_top_level_name_is_declared_by_two_scripts():
    owners = defaultdict(list)
    for name in _page_scripts():
        source = resolve_asset(name).decode()
        for declared in _DECLARATION.findall(source):
            owners[declared].append(name)
    clashes = {name: files for name, files in owners.items() if len(files) > 1}
    assert clashes == {}, f"declared at top level in more than one script: {clashes}"
