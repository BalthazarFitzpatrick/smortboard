"""the protect-main claude hook: agents merge into development, never write main"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "tools" / "claude-hooks" / "protect-main.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("jq") is None or shutil.which("bash") is None, reason="needs bash and jq"
)


@pytest.fixture
def env(tmp_path):
    """a repo on a feature branch, a repo on main, and a fake gh that reports a chosen base"""
    for name, branch in (("feature", "feature/x"), ("onmain", "main")):
        repo = tmp_path / name
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", branch, str(repo)], check=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh = bin_dir / "gh"
    # prints FAKE_PR_BASE as the base, or fails like gh does for an unknown pr
    fake_gh.write_text('#!/bin/sh\n[ -n "$FAKE_PR_BASE" ] || exit 1\necho "$FAKE_PR_BASE"\n')
    fake_gh.chmod(0o755)
    return tmp_path, bin_dir


def run_hook(env, command, base=""):
    tmp_path, bin_dir = env
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    result = subprocess.run(
        ["bash", str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        cwd=tmp_path / "feature",
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin",
            "FAKE_PR_BASE": base,
        },
        check=False,
    )
    return result.returncode


@pytest.mark.parametrize("command", ["gh pr merge 12 --merge", "gh pr merge -R o/r --subject x 12"])
def test_a_pull_request_into_development_may_be_merged(env, command):
    assert run_hook(env, command, base="development") == 0


@pytest.mark.parametrize("base", ["main", "master", "feature/other", ""])
def test_a_pull_request_into_anything_else_is_refused(env, base):
    assert run_hook(env, "gh pr merge 12 --merge", base=base) == 2


def test_committing_on_main_is_refused(env):
    tmp_path, _ = env
    assert run_hook(env, f"git -C {tmp_path / 'onmain'} commit -m x") == 2


def test_committing_on_a_feature_branch_is_allowed(env):
    tmp_path, _ = env
    assert run_hook(env, f"git -C {tmp_path / 'feature'} commit -m x") == 0


@pytest.mark.parametrize(
    "command",
    ["git push origin HEAD:main", "git push origin +main", "git push origin feature/x:master"],
)
def test_pushing_to_main_from_any_branch_is_refused(env, command):
    assert run_hook(env, command) == 2


@pytest.mark.parametrize(
    "command",
    [
        "git push origin development",
        "git merge origin/development",
        "git status",
        "cat <<'EOF'\ngit push origin main\nEOF",
    ],
)
def test_everyday_commands_pass(env, command):
    assert run_hook(env, command) == 0


def run_codex_hook(env, command):
    """codex's PreToolUse payload for a shell call, as measured in docs/spikes/s6-codex-hooks.md"""
    tmp_path, bin_dir = env
    payload = json.dumps(
        {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": str(tmp_path / "feature"),
            "hook_event_name": "PreToolUse",
        }
    )
    result = subprocess.run(
        ["bash", str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        cwd=tmp_path / "feature",
        env={"PATH": f"{bin_dir}:/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin"},
        check=False,
    )
    return result.returncode, result.stderr


def test_the_same_script_guards_a_codex_shell_call(env):
    code, stderr = run_codex_hook(env, "git push origin HEAD:main")
    assert code == 2
    assert "BLOCKED" in stderr
    assert run_codex_hook(env, "git status")[0] == 0
