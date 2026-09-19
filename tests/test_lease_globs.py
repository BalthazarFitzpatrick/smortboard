"""the lease glob rule: gitignore-style, shared by the board and the in-container guard"""

import json
import subprocess

import pytest

from smortboard.exec.leases import lease_allows, write_lease_settings


def test_post_run_lease_uses_card_cut_and_reports_both_sides_of_rename(tmp_path):
    from smortboard.exec.leases import changed_paths_outside_lease
    from smortboard.exec.worktrees import create_worktree

    def git(path, *args):
        return subprocess.run(
            ["git", "-C", str(path), *args], capture_output=True, text=True, check=True
        ).stdout.strip()

    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "test")
    git(tmp_path, "config", "user.email", "test@example.test")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("x = 1\n")
    git(tmp_path, "add", "src")
    git(tmp_path, "commit", "-m", "initial")
    git(tmp_path, "checkout", "-b", "dependency")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/inherited.md").write_text("dependency\n")
    git(tmp_path, "add", "docs")
    git(tmp_path, "commit", "-m", "dependency")
    tree = create_worktree(tmp_path, "child", base="dependency")
    (tree.path / "src/a.py").write_text("x = 2\n")
    git(tree.path, "add", "src")
    git(tree.path, "commit", "-m", "allowed")
    assert changed_paths_outside_lease(tmp_path, tree.base_commit, tree.branch, ["src/**"]) == []
    git(tree.path, "mv", "src/a.py", "docs/moved.py")
    git(tree.path, "commit", "-m", "outside")
    assert changed_paths_outside_lease(tmp_path, tree.base_commit, tree.branch, ["src/**"]) == [
        "docs/moved.py"
    ]


@pytest.mark.parametrize(
    ("glob", "path", "allowed"),
    [
        # the refusal that stopped two cards: **/ must also mean "no folder in between"
        ("smortboard/**/*.py", "smortboard/scheduler.py", True),
        ("smortboard/**/*.py", "smortboard/server/app.py", True),
        ("smortboard/**/*.py", "smortboard/ui/board.js", False),
        ("tests/**/*.py", "tests/test_scheduler.py", True),
        # * stays inside one folder, so a narrow lease stays narrow
        ("smortboard/*.py", "smortboard/scheduler.py", True),
        ("smortboard/*.py", "smortboard/server/app.py", False),
        ("smortboard/ui/*", "smortboard/ui/board.js", True),
        ("smortboard/ui/*", "smortboard/ui/sub/x.js", False),
        # a trailing ** is everything below, at any depth
        ("tests/js/**", "tests/js/mission_control.mjs", True),
        ("tests/js/**", "tests/js/deep/nested.mjs", True),
        ("tests/js/**", "tests/test_js.py", False),
        # literals, one character, classes
        ("README.md", "README.md", True),
        ("README.md", "docs/README.md", False),
        ("docs/?.md", "docs/a.md", True),
        ("docs/?.md", "docs/ab.md", False),
        ("tests/test_[ab].py", "tests/test_a.py", True),
        ("tests/test_[!ab].py", "tests/test_a.py", False),
        ("tests/test_[!ab].py", "tests/test_c.py", True),
        # dots and other regex characters are literal
        ("smortboard/ui/settings.*", "smortboard/ui/settings.js", True),
        ("smortboard/ui/settings.*", "smortboard/ui/settingsXjs", False),
        ("a+b/(c).py", "a+b/(c).py", True),
    ],
)
def test_lease_glob_rule(glob, path, allowed):
    assert lease_allows(path, [glob]) is allowed


def test_the_guard_in_the_container_uses_the_same_rule(tmp_path):
    """the hook script runs with no smortboard importable, so it carries the rule as source"""
    root = (tmp_path / "workspace").resolve()
    settings_path = write_lease_settings(tmp_path / "wt", ["smortboard/**/*.py"], root=str(root))
    settings = json.loads(settings_path.read_text())
    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]

    def guard(rel):
        payload = json.dumps({"tool_input": {"file_path": str(root / rel)}})
        return subprocess.run(
            command.split(" ", 1), input=payload, capture_output=True, text=True
        ).returncode

    assert guard("smortboard/scheduler.py") == 0
    assert guard("smortboard/server/app.py") == 0
    assert guard("smortboard/ui/board.js") == 2
