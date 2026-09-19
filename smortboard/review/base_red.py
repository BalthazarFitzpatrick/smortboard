"""tells a gate failure the card caused from one the base already had.

Measured 2026-09-19: three cards failed the same four tests in test_repo_image.py that a later
commit on the base had already fixed - nothing the cards changed. The gate cannot tell that from a
real failure, so a card is blocked TESTS_FAILED and someone reads the output to find out.

The check runs the repo's own test command on a clean checkout of the base and compares failing
test ids. It only claims "base is red" when the card's failure list is COMPLETE (the parsed FAILED
lines add up to the count pytest's summary line reports) and every id on it also fails on the base.
Anything less certain - ruff findings, collection errors, a truncated list - returns None and the
card is blocked exactly as before.
"""

from __future__ import annotations

import contextlib
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from smortboard.exec.worktrees import repo_lock, rev_parse
from smortboard.review.gates import run_test_gate

_FAILED = re.compile(r"^FAILED\s+(\S+)", re.MULTILINE)
# pytest -q's last line: "4 failed, 1079 passed, 15 skipped in 101.85s (0:01:41)"
_SUMMARY = re.compile(r"^.*\b(\d+) failed\b.* in [\d.]+s.*$", re.MULTILINE)
_ERRORS = re.compile(r"\b\d+ errors?\b")


def failing_test_ids(output: str) -> tuple[set[str], bool]:
    """the failing pytest ids in `output`, and whether that list is complete"""
    ids = {match.group(1) for match in _FAILED.finditer(output)}
    summaries = list(_SUMMARY.finditer(output))
    if not ids or not summaries:
        return ids, False
    line = summaries[-1].group(0)
    if _ERRORS.search(line):
        return ids, False
    return ids, len(ids) == int(summaries[-1].group(1))


def base_red_ids(card_output: str, base_output: str) -> list[str] | None:
    """the card's failing ids when ALL of them also fail on the base, else None"""
    card_ids, complete = failing_test_ids(card_output)
    if not complete:
        return None
    base_ids, _ = failing_test_ids(base_output)
    return sorted(card_ids) if card_ids <= base_ids else None


@contextlib.contextmanager
def _base_checkout(repo_path: str | Path, sha: str) -> Iterator[Path]:
    """a detached checkout of `sha` in a throwaway directory, removed on the way out"""
    root = Path(tempfile.mkdtemp(prefix="smortboard-base-"))
    tree = root / "tree"
    try:
        with repo_lock(repo_path):
            added = subprocess.run(
                ["git", "-C", str(repo_path), "worktree", "add", "--detach", str(tree), sha],
                capture_output=True,
                text=True,
                check=False,
            )
        if added.returncode != 0:
            raise OSError(f"could not check out {sha}: {added.stderr.strip()}")
        yield tree
    finally:
        with repo_lock(repo_path):
            subprocess.run(
                ["git", "-C", str(repo_path), "worktree", "remove", "--force", str(tree)],
                capture_output=True,
                check=False,
            )
        shutil.rmtree(root, ignore_errors=True)


def check_base_red(
    repo: dict[str, Any], base: str, card_id: str, card_output: str
) -> tuple[str, list[str]] | None:
    """(base sha, failing ids) when the card's whole failure is already on the base, else None.

    Raises whatever the gate raises (docker down, no test command) - the caller treats any of it as
    "could not tell" and blocks the card the ordinary way.
    """
    if not failing_test_ids(card_output)[1]:
        return None  # nothing to compare, so do not pay for a second suite run
    sha = rev_parse(repo["path"], f"origin/{base}") or rev_parse(repo["path"], base)
    if sha is None:
        return None
    with _base_checkout(repo["path"], sha) as tree:
        gate = run_test_gate(None, f"base-{card_id[:8]}", tree, repo)
    ids = base_red_ids(card_output, gate.output)
    return (sha, ids) if ids else None
