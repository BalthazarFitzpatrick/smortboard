"""open mode without docker: the test gate and the reviewer as host processes, the base-red check
following the board's mode, and the reviewer_input setting (read the diff, or read the code).

Docker is made to fail loudly if anything in an open run reaches for it. The reviewer's model call
is replaced by a function that records what it was given.
"""

import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from smortboard.exec.runner import RunResult
from smortboard.exec.worktrees import branch_changed_files
from smortboard.review import base_red, gates, reviewer
from smortboard.review.gates import run_test_gate
from smortboard.review.reviewer import run_review
from smortboard.store.api import Store


def _no_docker(*args, **kwargs):
    raise AssertionError("an open run must not reach for docker")


@pytest.fixture(autouse=True)
def _docker_is_off(monkeypatch):
    monkeypatch.setattr(gates, "docker_available", _no_docker)
    monkeypatch.setattr(reviewer, "docker_available", _no_docker)


# -- the gate ---------------------------------------------------------------------------------


def test_the_open_gate_runs_the_suite_on_the_host(tmp_path):
    assert run_test_gate(None, "c", tmp_path, {"test_command": "true"}, mode="open").passed
    failed = run_test_gate(None, "c", tmp_path, {"test_command": "echo red; exit 3"}, mode="open")
    assert not failed.passed
    assert failed.exit_code == 3
    assert "red" in failed.output
    assert failed.blocked_reason_code == "TESTS_FAILED"


def test_the_open_gate_runs_in_the_worktree_without_host_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "gh-secret")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    (tmp_path / "marker.txt").write_text("here")
    result = run_test_gate(
        None,
        "c",
        tmp_path,
        {
            "test_command": 'ls marker.txt && printf "%s|%s" "${GH_TOKEN-unset}" "${SSH_AUTH_SOCK-unset}"'
        },
        mode="open",
    )
    assert result.passed
    assert "unset|unset" in result.output


@pytest.mark.posix_only
def test_a_timed_out_open_gate_ends_the_whole_suite(tmp_path):
    store = SimpleNamespace(
        get_settings=lambda: {"gate_timeout_seconds": 1}, append_event=lambda *a, **k: None
    )
    started = time.time()
    result = run_test_gate(
        store, "c", tmp_path, {"test_command": "sleep 30 & sleep 30"}, mode="open"
    )
    assert time.time() - started < 15, "the suite should have been killed, not waited out"
    assert result.exit_code == -1
    assert "did not finish within 1s" in result.output


def test_the_gate_is_configured_without_docker_in_open_mode(monkeypatch):
    monkeypatch.setattr(gates.shutil, "which", lambda name: None)
    repo = {"test_command": "true"}
    assert gates.gate_is_configured(repo, mode="open") is True
    assert gates.gate_is_configured(repo) is False
    assert gates.gate_is_configured({}, mode="open") is False


# -- the base-red check follows the mode ------------------------------------------------------


def test_the_base_red_check_runs_the_base_in_the_boards_mode(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@t"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    (repo / "a.txt").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "c"], check=True, capture_output=True)
    modes = []

    def _gate(store, card_id, tree, repo_row, mode="sealed"):
        modes.append(mode)
        return SimpleNamespace(output="FAILED tests/test_a.py::test_a\n1 failed in 0.1s")

    monkeypatch.setattr(base_red, "run_test_gate", _gate)
    out = "FAILED tests/test_a.py::test_a\n1 failed in 0.1s"
    verdict = base_red.check_base_red({"path": str(repo)}, "main", "abcdef12", out, mode="open")
    assert verdict is not None
    assert modes == ["open"]


# -- the reviewer ------------------------------------------------------------------------------


def _verdict(**fields):
    return RunResult(
        subtype="success",
        is_error=False,
        blocked_reason_code=None,
        session_id="s",
        total_cost_usd=0.01,
        num_turns=1,
        result_text=None,
        structured_output={"findings": []},
        **fields,
    )


def _run_open_review(tmp_path, monkeypatch, **kwargs):
    seen = {}

    def _fake(store, card_id, cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return _verdict()

    monkeypatch.setattr(reviewer, "run_process", _fake)
    monkeypatch.setenv("GH_TOKEN", "gh-secret")
    token = tmp_path / "token"
    token.write_text("test-token")
    token.chmod(0o600)
    work = tmp_path / "work"
    work.mkdir()
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    result = run_review(
        None,
        "card",
        kwargs.pop("diff", "+ def new():\n"),
        work,
        settings,
        token_path=token,
        mode="open",
        **kwargs,
    )
    return result, seen, work


def test_the_open_reviewer_is_a_host_process_with_a_clean_env(tmp_path, monkeypatch):
    result, seen, work = _run_open_review(tmp_path, monkeypatch)
    assert result.approved
    assert seen["cmd"][:2] == ["sh", "-c"]
    assert seen["cwd"] == work
    assert seen["new_session"] is True
    assert "GH_TOKEN" not in seen["env"]
    assert seen["stdin_text"] == "test-token\n"
    assert "test-token" not in seen["cmd"][2], "the token goes on stdin, never in argv"
    assert "< /dev/null" in seen["cmd"][2]


def test_reading_the_diff_puts_the_diff_in_the_prompt(tmp_path, monkeypatch):
    _, seen, _ = _run_open_review(tmp_path, monkeypatch, diff="+ SECRET_DIFF_TEXT\n")
    assert "SECRET_DIFF_TEXT" in seen["cmd"][2]
    assert "BEGIN UNTRUSTED DIFF" in seen["cmd"][2]


def test_reading_the_code_names_the_files_and_leaves_the_diff_out(tmp_path, monkeypatch):
    _, seen, _ = _run_open_review(
        tmp_path,
        monkeypatch,
        diff="+ SECRET_DIFF_TEXT\n",
        reads="code",
        files=["pkg/new.py", "pkg/other.py"],
    )
    prompt = seen["cmd"][2]
    assert "SECRET_DIFF_TEXT" not in prompt
    assert "BEGIN UNTRUSTED FILE LIST" in prompt
    assert "pkg/new.py" in prompt and "pkg/other.py" in prompt
    assert "reads the CODE" in prompt


def test_reading_the_code_with_no_files_is_clean_without_a_model_call(tmp_path, monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("nothing to read, so no model call")

    monkeypatch.setattr(reviewer, "run_process", _boom)
    token = tmp_path / "token"
    token.write_text("t")
    token.chmod(0o600)
    result = run_review(
        None, "card", "", tmp_path, tmp_path / "s.json", token_path=token, mode="open", reads="code"
    )
    assert result.approved and not result.findings


def test_a_codex_reviewer_runs_on_the_host_read_only(tmp_path, monkeypatch):
    from smortboard import profiles

    monkeypatch.setattr(profiles, "active_profile", lambda lab="anthropic": "work")
    monkeypatch.setattr(profiles, "profile_kind", lambda name, lab="anthropic": "auth_json")
    monkeypatch.setattr(profiles, "read_profile_token", lambda lab, name: '{"tokens": "x"}')
    seen = {}

    def _fake(store, card_id, cmd, **kw):
        home = Path(kw["env"]["CODEX_HOME"])
        seen.update(cmd=cmd, hooks=(home / "hooks.json").is_file(), **kw)
        return _verdict()

    monkeypatch.setattr(reviewer, "run_process", _fake)
    work = tmp_path / "work"
    work.mkdir()
    result = run_review(
        None, "card", "+ x\n", work, tmp_path / "s.json", model="openai/gpt-5.6-luna", mode="open"
    )
    assert result.approved
    script = seen["cmd"][2]
    assert "codex exec" in script and "--sandbox read-only" in script
    assert seen["hooks"] is True and seen["new_session"] is True
    assert '{"tokens"' not in script


def test_the_reviewer_is_configured_without_docker_in_open_mode(monkeypatch):
    monkeypatch.setattr(reviewer.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        "smortboard.exec.backends.card_token_available", lambda token_path=None: True
    )
    assert reviewer.reviewer_is_configured(mode="open") is True


# -- the setting and the file list --------------------------------------------------------------


def test_reviewer_input_accepts_diff_code_or_nothing(tmp_path):
    with Store(tmp_path / "board.db") as store:
        assert store.get_settings()["reviewer_input"] is None
        store.set_setting("reviewer_input", "code")
        assert store.get_settings()["reviewer_input"] == "code"
        store.set_setting("reviewer_input", "diff")
        store.set_setting("reviewer_input", None)
        with pytest.raises(ValueError, match="reviewer_input"):
            store.set_setting("reviewer_input", "everything")


def test_changed_files_lists_what_the_card_changed_minus_the_excluded(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (repo / "keep.py").write_text("a")
    git("add", "-A")
    git("commit", "-qm", "base")
    git("switch", "-qc", "card/x")
    (repo / "keep.py").write_text("b")
    (repo / "new.py").write_text("c")
    (repo / "uv.lock").write_text("lock")
    git("add", "-A")
    git("commit", "-qm", "work")
    assert sorted(branch_changed_files(repo, "main", "card/x")) == ["keep.py", "new.py", "uv.lock"]
    assert sorted(branch_changed_files(repo, "main", "card/x", ("uv.lock",))) == [
        "keep.py",
        "new.py",
    ]
