"""lease modes: strict keeps a card inside its lease, soft lets it reach unprotected paths nobody holds

One rule, lease_permits, answers for the claude hook, the codex guard and the post-run check, so
each is exercised here against the same cases.
"""

import json
import sqlite3
import subprocess
import sys

import pytest

from smortboard.exec.leases import (
    PROTECTED_GLOBS,
    lease_permits,
    lease_policy,
    split_outside_lease,
    write_lease_settings,
)
from smortboard.labs.base import BashPolicy
from smortboard.labs.codex_guards import write_guard_files
from smortboard.review.reviewer import _build_prompt
from smortboard.store.api import Store
from smortboard.store.schema import _MIGRATIONS

SOFT = {
    "path_globs": ["src/owned/**"],
    "remembered_globs": ["docs/remembered.md"],
    "mode": "soft",
    "protected_globs": list(PROTECTED_GLOBS),
    "held_globs": ["src/theirs/**"],
}
STRICT = {**SOFT, "mode": "strict"}


@pytest.mark.parametrize(
    ("path", "soft", "strict"),
    [
        ("src/owned/a.py", True, True),
        ("docs/remembered.md", True, True),
        ("src/new.py", True, False),
        ("tests/test_new.py", True, False),
        ("src/theirs/b.py", False, False),
        (".github/workflows/ci.yml", False, False),
        (".claude/settings.json", False, False),
        ("sub/.claude/settings.json", False, False),
        ("CLAUDE.md", False, False),
        ("pkg/AGENTS.md", False, False),
        ("Dockerfile", False, False),
        ("docker/card.Dockerfile", False, False),
        ("pyproject.toml", False, False),
        ("uv.lock", False, False),
        ("web/package.json", False, False),
        ("requirements-dev.txt", False, False),
        (".gitignore", False, False),
        (".envrc", False, False),
        ("config/.env.local", False, False),
        ("certs/server.pem", False, False),
        (".vscode/tasks.json", False, False),
        (".pre-commit-config.yaml", False, False),
        ("/tmp/scratch.py", False, False),
        ("../sibling/x.py", False, False),
    ],
)
def test_one_rule_decides_every_path_in_both_modes(path, soft, strict):
    assert lease_permits(path, SOFT) is soft
    assert lease_permits(path, STRICT) is strict


def test_a_lease_that_names_a_protected_path_allows_it():
    lease = {**SOFT, "path_globs": [".github/workflows/ci.yml"]}
    assert lease_permits(".github/workflows/ci.yml", lease)


def test_split_keeps_soft_expansions_and_refuses_the_rest():
    paths = ["src/new.py", ".github/ci.yml", "src/theirs/b.py"]
    assert split_outside_lease(paths, SOFT) == (
        ["src/new.py"],
        [".github/ci.yml", "src/theirs/b.py"],
    )
    assert split_outside_lease(paths, STRICT) == ([], paths)


def _hook(out_dir, file_path):
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": file_path}})
    return subprocess.run(
        [sys.executable, str(out_dir / "lease_guard.py")],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    ).returncode


@pytest.mark.parametrize(
    ("mode", "path", "code"),
    [
        ("soft", "src/new.py", 0),
        ("soft", ".github/x.yml", 2),
        ("soft", "src/theirs/b.py", 2),
        ("strict", "src/new.py", 2),
        ("strict", "src/owned/a.py", 0),
    ],
)
def test_the_claude_hook_follows_the_mode(tmp_path, mode, path, code):
    root = tmp_path / "repo"
    root.mkdir()
    policy = {**SOFT, "mode": mode}
    write_lease_settings(
        tmp_path / "guards",
        policy["path_globs"],
        root=root,
        remembered_globs=policy["remembered_globs"],
        policy=policy,
    )
    assert _hook(tmp_path / "guards", str(root / path)) == code


def test_the_claude_hook_refuses_outside_the_repo_even_when_soft(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    write_lease_settings(tmp_path / "guards", SOFT["path_globs"], root=root, policy=SOFT)
    assert _hook(tmp_path / "guards", str(tmp_path / "elsewhere.py")) == 2


def _codex(out_dir, patch):
    payload = json.dumps({"tool_name": "apply_patch", "tool_input": {"command": patch}})
    return subprocess.run(
        [sys.executable, str(out_dir / "codex_guard.py")],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    ).returncode


@pytest.mark.parametrize(
    ("mode", "path", "code"),
    [("soft", "src/new.py", 0), ("soft", ".github/x.yml", 2), ("strict", "src/new.py", 2)],
)
def test_the_codex_guard_follows_the_mode(tmp_path, mode, path, code):
    root = tmp_path / "repo"
    root.mkdir()
    policy = {key: value for key, value in {**SOFT, "mode": mode}.items() if key != "path_globs"}
    write_guard_files(
        SOFT["path_globs"],
        BashPolicy(
            out_dir=tmp_path / "guards", root=str(root), python=sys.executable, lease_policy=policy
        ),
    )
    patch = f"*** Begin Patch\n*** Add File: {path}\n+x\n*** End Patch"
    assert _codex(tmp_path / "guards", patch) == code


def _two_cards(store, repo_path):
    b = store.create_board("b")
    r = store.create_repo(b["id"], "repo", str(repo_path), "main", test_command="true")
    mine = store.create_card(b["id"], r["id"], "mine", leases=["src/owned/**"])
    theirs = store.create_card(b["id"], r["id"], "theirs", leases=["src/theirs/**"])
    return b, mine, theirs


def test_policy_is_strict_until_the_board_says_soft(tmp_path):
    store = Store(tmp_path / "db")
    b, mine, _ = _two_cards(store, tmp_path)
    assert lease_policy(store, store.get_card(mine["id"]))["mode"] == "strict"
    store.set_board_lease_mode(b["id"], "soft")
    policy = lease_policy(store, store.get_card(mine["id"]))
    assert policy["mode"] == "soft"
    assert policy["protected_globs"] == list(PROTECTED_GLOBS)
    store.close()


def test_soft_policy_holds_back_what_another_active_card_leases(tmp_path):
    store = Store(tmp_path / "db")
    b, mine, theirs = _two_cards(store, tmp_path)
    store.set_board_lease_mode(b["id"], "soft")
    # a todo card holds nothing yet
    assert lease_policy(store, store.get_card(mine["id"]))["held_globs"] == []
    store.update_card(theirs["id"], status="doing")
    policy = lease_policy(store, store.get_card(mine["id"]))
    assert policy["held_globs"] == ["src/theirs/**"]
    assert not lease_permits("src/theirs/b.py", policy)
    store.close()


def test_the_store_accepts_only_known_modes(tmp_path):
    store = Store(tmp_path / "db")
    b = store.create_board("b")
    assert store.get_board(b["id"])["lease_mode"] is None
    assert store.set_board_lease_mode(b["id"], "soft")["lease_mode"] == "soft"
    assert store.set_board_lease_mode(b["id"], None)["lease_mode"] is None
    with pytest.raises(ValueError):
        store.set_board_lease_mode(b["id"], "loose")
    store.close()


def test_an_existing_board_migrates_to_strict(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    for script in _MIGRATIONS[:22]:
        conn.executescript(script)
    conn.execute("PRAGMA user_version = 22")
    conn.execute(
        "INSERT INTO boards (id, name, position, created_at) VALUES ('old', 'old', 0, 'now')"
    )
    conn.commit()
    conn.close()
    store = Store(path)
    assert store.get_board("old")["lease_mode"] is None
    store.close()


def test_the_reviewer_is_told_which_paths_the_soft_lease_reached():
    """the instruction is trusted, the path names are worker-written, so they sit in the data"""
    from smortboard.review.reviewer import DIFF_FRAMING

    prompt = _build_prompt(None, "diff --git a/x b/x", expanded=["src/new.py", "tests/test_new.py"])
    trusted, _, data = prompt.partition(DIFF_FRAMING)
    assert "soft lease" in trusted
    assert "src/new.py" not in trusted
    assert "src/new.py, tests/test_new.py" in data
    assert "soft lease" not in _build_prompt(None, "diff --git a/x b/x")
