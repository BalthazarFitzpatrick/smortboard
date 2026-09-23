import json
import subprocess

import pytest

from smortboard.exec.bash_guard import BASH_ESCAPE_PREFIX
from smortboard.exec.leases import (
    LEASE_CONFLICT_PREFIX,
    drop_legacy_guards,
    write_lease_settings,
)
from smortboard.exec.runner import (
    DEFAULT_ALLOWED_TOOLS,
    DEFAULT_CARD_BUDGET_USD,
    SYSTEM_PROMPT,
    ProcessHandle,
    allowed_tools_for_repo,
    build_command,
    classify_rate_limit,
    classify_result,
    commands_preamble,
    lease_preamble,
    parse_line,
    result_to_run_result,
)
from smortboard.exec.worktrees import (
    WorktreeError,
    branch_diverged_from_origin,
    create_worktree,
    destroy_worktree,
    list_worktrees,
)
from smortboard.store.api import Store


def _init_repo(path):
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "initial"], check=True, capture_output=True
    )


def _with_bare_origin(repo):
    bare = repo.parent / f"{repo.name}-origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(bare)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare)], check=True)
    return bare


# -- worktrees ---------------------------------------------------------------


def test_branch_diverged_from_origin_detects_a_moved_branch(tmp_path):
    """found for real: a worktree held one commit while origin had two newer ones"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    bare = _with_bare_origin(repo)
    subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "card/x"], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", "card/x"], check=True)

    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True)
    subprocess.run(["git", "-C", str(other), "checkout", "-q", "card/x"], check=True)
    subprocess.run(["git", "-C", str(other), "config", "user.email", "o@o.com"], check=True)
    subprocess.run(["git", "-C", str(other), "config", "user.name", "o"], check=True)
    (other / "extra.txt").write_text("external work\n")
    subprocess.run(["git", "-C", str(other), "add", "extra.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(other), "commit", "-q", "-m", "external"],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "card/x"], check=True)

    assert branch_diverged_from_origin(repo, "card/x") is True


def test_branch_diverged_from_origin_is_false_when_in_sync(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _with_bare_origin(repo)
    subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "card/x"], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", "card/x"], check=True)

    assert branch_diverged_from_origin(repo, "card/x") is False


def test_branch_diverged_from_origin_is_none_when_never_pushed(tmp_path):
    """a card branch on its first run, before any push, is not a divergence"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _with_bare_origin(repo)
    subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "card/x"], check=True)

    assert branch_diverged_from_origin(repo, "card/x") is None


def test_branch_diverged_from_origin_is_none_with_no_remote(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "card/x"], check=True)

    assert branch_diverged_from_origin(repo, "card/x") is None


def test_create_worktree_round_trip(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    info = create_worktree(repo, "card-1")
    assert info.path.exists()
    assert info.branch == "card/card-1"

    listed = list_worktrees(repo)
    assert [w.card_id for w in listed] == ["card-1"]

    destroy_worktree(repo, "card-1")
    assert not info.path.exists()
    assert list_worktrees(repo) == []


def test_worktree_is_already_checked_out_on_its_branch(tmp_path):
    """S1/S3: a card left on main deadlocks or invents its own branch - the handed-back checkout
    must already be on the card's branch"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    info = create_worktree(repo, "card-2")
    current = subprocess.run(
        ["git", "-C", str(info.path), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert current == "card/card-2"


def test_create_worktree_twice_raises(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    create_worktree(repo, "card-3")
    with pytest.raises(WorktreeError):
        create_worktree(repo, "card-3")


# -- leases -------------------------------------------------------------------


def _run_hook(worktree, file_path):
    settings_path = write_lease_settings(
        worktree.parent / "guards", ["src/allowed.py"], root=worktree.resolve()
    )
    settings = json.loads(settings_path.read_text())
    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    payload = json.dumps({"tool_input": {"file_path": str(file_path)}})
    return subprocess.run(
        command.split(" ", 1),
        input=payload,
        capture_output=True,
        text=True,
    )


def test_lease_hook_allows_in_lease_path(tmp_path):
    worktree = tmp_path / "wt"
    (worktree / "src").mkdir(parents=True)
    result = _run_hook(worktree, worktree / "src" / "allowed.py")
    assert result.returncode == 0


def test_lease_hook_refuses_out_of_lease_path(tmp_path):
    worktree = tmp_path / "wt"
    (worktree / "src").mkdir(parents=True)
    result = _run_hook(worktree, worktree / "src" / "other.py")
    assert result.returncode == 2
    assert LEASE_CONFLICT_PREFIX in result.stderr


def _guard(settings_path, index, tool_input):
    settings = json.loads(settings_path.read_text())
    command = settings["hooks"]["PreToolUse"][index]["hooks"][0]["command"]
    return subprocess.run(
        command.split(" ", 1),
        input=json.dumps({"tool_input": tool_input}),
        capture_output=True,
        text=True,
    )


def test_lease_hook_takes_its_root_from_the_lease_when_mounted_elsewhere(tmp_path):
    # the container case: the guards sit outside the repo, so __file__ cannot name the root
    root = (tmp_path / "workspace").resolve()
    settings_path = write_lease_settings(tmp_path / "wt", ["src/allowed.py"], root=str(root))
    assert _guard(settings_path, 0, {"file_path": str(root / "src" / "allowed.py")}).returncode == 0
    assert _guard(settings_path, 0, {"file_path": str(root / "src" / "other.py")}).returncode == 2


def test_bash_guard_takes_its_root_from_the_lease_too(tmp_path):
    root = (tmp_path / "workspace").resolve()
    settings_path = write_lease_settings(tmp_path / "wt", [], root=str(root))
    assert _guard(settings_path, 1, {"command": f"cat {root}/README.md"}).returncode == 0
    assert _guard(settings_path, 1, {"command": f"cat {tmp_path}/wt/README.md"}).returncode == 2


def test_legacy_guards_leave_the_worktree_but_a_repos_own_claude_settings_stay(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text('{"permissions": {}}')
    subprocess.run(["git", "-C", str(repo), "add", ".claude"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "own settings"], check=True)
    tree = create_worktree(repo, "card-legacy")
    for name in ("lease.json", "lease_guard.py", "bash_guard.py", "review-schema.json"):
        (tree.path / ".claude" / name).write_text("board wrote this")

    removed = drop_legacy_guards(tree.path)

    assert sorted(removed) == [
        "bash_guard.py",
        "lease.json",
        "lease_guard.py",
        "review-schema.json",
    ]
    assert [p.name for p in (tree.path / ".claude").iterdir()] == ["settings.json"]
    assert drop_legacy_guards(tree.path) == []


def test_legacy_guards_take_the_emptied_claude_dir_with_them(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    tree = create_worktree(repo, "card-legacy")
    (tree.path / ".claude").mkdir()
    (tree.path / ".claude" / "settings.json").write_text("{}")

    assert drop_legacy_guards(tree.path) == ["settings.json"]
    assert not (tree.path / ".claude").exists()


# -- runner: parsing and classification ---------------------------------------


def test_parse_line_skips_blank_and_bad_json():
    assert parse_line("") is None
    assert parse_line("   \n") is None
    assert parse_line("not json") is None
    assert parse_line('{"type": "result"}') == {"type": "result"}


def test_classify_successful_result_is_not_blocked():
    result_event = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "session_id": "abc",
        "total_cost_usd": 0.05,
        "num_turns": 5,
        "permission_denials": [],
        "result": "done",
    }
    assert classify_result(result_event) is None
    run_result = result_to_run_result(result_event)
    assert run_result.subtype == "success"
    assert run_result.blocked_reason_code is None
    assert run_result.session_id == "abc"


def test_classify_rate_limit_event_maps_to_usage_limit():
    event = {
        "rate_limit_info": {
            "status": "rejected",
            "rateLimitType": "five_hour",
            "unifiedWindows": {
                "five_hour": {"utilization": 1.0, "resetsAt": 1788909000},
                "seven_day": {"utilization": 0.9, "resetsAt": 1789401600},
            },
        }
    }
    assert classify_rate_limit(event) == "USAGE_LIMIT"


def test_classify_rate_limit_event_allowed_is_not_blocked():
    event = {"rate_limit_info": {"status": "allowed"}}
    assert classify_rate_limit(event) is None


def test_a_threshold_warning_is_not_a_usage_limit():
    """the exact event recorded on the board at 76% of the week: the run was allowed to continue"""
    event = {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": "allowed_warning",
            "resetsAt": 1789401600,
            "rateLimitType": "seven_day",
            "utilization": 0.76,
            "isUsingOverage": False,
            "surpassedThreshold": 0.75,
        },
    }
    assert classify_rate_limit(event) is None


def test_lease_conflict_is_not_a_crash():
    """S3: a lease block still ends subtype success, is_error false - a card failure it is not"""
    result_event = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "session_id": "abc",
        "total_cost_usd": 0.02,
        "num_turns": 3,
        "permission_denials": [
            {"tool_name": "Edit", "tool_use_id": "t1", "tool_input": {"file_path": "billing.py"}}
        ],
        "result": "Blocked by a PreToolUse lease-guard hook on billing.py.",
    }
    run_result = result_to_run_result(result_event)
    assert run_result.is_error is False
    assert run_result.subtype == "success"
    assert run_result.blocked_reason_code == "LEASE_CONFLICT"


def test_agent_question_is_detected():
    result_event = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "permission_denials": [
            {
                "tool_name": "Bash",
                "tool_use_id": "t1",
                "tool_input": {"command": "git checkout -b x"},
            }
        ],
        "result": "I need your approval to create a branch off main - OK to proceed?",
    }
    assert classify_result(result_event) == "AGENT_QUESTION"


def test_a_report_that_mentions_approval_is_not_a_question():
    # run 3: the agent reported its denied commands and finished; the card blocked anyway
    result_event = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "permission_denials": [
            {"tool_name": "Bash", "tool_use_id": "t1", "tool_input": {"command": "uv run ruff ."}}
        ],
        "result": "Done. Could not verify - every `uv run` was blocked with "
        '"This command requires approval". Committed as 88b49d5.',
    }
    assert classify_result(result_event) is None


def test_session_limit_text_classifies_as_usage_limit_not_crash():
    """the real card evidence: the run's only output was this refusal text and it was classified
    CRASH (is_error true), so nothing rotated"""
    result_event = {
        "type": "result",
        "subtype": "error_during_execution",
        "is_error": True,
        "permission_denials": [],
        "result": "You've hit your session limit · resets 3:40pm (UTC)",
    }
    assert classify_result(result_event) == "USAGE_LIMIT"
    run_result = result_to_run_result(result_event)
    assert run_result.blocked_reason_code == "USAGE_LIMIT"
    assert run_result.resets_at is not None


def test_session_limit_time_resets_at_is_in_the_future():
    from datetime import UTC, datetime

    result_event = {
        "type": "result",
        "is_error": True,
        "permission_denials": [],
        "result": "You've hit your session limit · resets 3:40pm (UTC)",
    }
    run_result = result_to_run_result(result_event)
    assert run_result.resets_at > datetime.now(UTC).timestamp()


def test_session_limit_date_variant_classifies_as_usage_limit():
    result_event = {
        "type": "result",
        "is_error": True,
        "permission_denials": [],
        "result": "You've hit your session limit · resets Dec 25 (UTC)",
    }
    run_result = result_to_run_result(result_event)
    assert run_result.blocked_reason_code == "USAGE_LIMIT"
    assert run_result.resets_at is not None


def test_a_real_rate_limit_event_wins_over_the_session_limit_text(tmp_path):
    """S2's own signal - a refused rate_limit_event - must not be overridden by this new text
    heuristic: the run's only recorded rate_limit_event stays the real one, its resetsAt
    untouched by a synthetic one built from the text."""
    from smortboard.exec.runner import run_process

    real_resets_at = 1999999999
    events = [
        json.dumps(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {"status": "rejected", "resetsAt": real_resets_at},
            }
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "permission_denials": [],
                "result": "You've hit your session limit · resets 3:40pm (UTC)",
                "session_id": "abc",
            }
        ),
    ]
    cmd = ["python3", "-c", "import sys; [print(line) for line in sys.argv[1:]]", *events]
    with Store(tmp_path / "board.sqlite3") as store:
        board = store.create_board("b")
        card = store.create_card(board["id"], None, "a card")
        run_result = run_process(store, card["id"], cmd)
        assert run_result.blocked_reason_code == "USAGE_LIMIT"
        recorded = store.list_events_by_kind(["rate_limit_event"])
        assert len(recorded) == 1
        assert recorded[0]["payload"]["rate_limit_info"]["resetsAt"] == real_resets_at


@pytest.mark.parametrize(
    "text",
    [
        "Unable to connect to API",
        "unable to connect to the api - please try again",
        "ConnectionRefused: [Errno 61]",
        "connection reset by peer",
        "api overloaded, please retry",
        "the api returned a 503 error",
    ],
)
def test_api_unreachable_text_classifies_as_api_unreachable_not_crash(text):
    result_event = {
        "type": "result",
        "subtype": "error_during_execution",
        "is_error": True,
        "permission_denials": [],
        "result": text,
    }
    assert classify_result(result_event) == "API_UNREACHABLE"


def test_crash_when_is_error_and_no_other_signal():
    result_event = {
        "type": "result",
        "subtype": "error_during_execution",
        "is_error": True,
        "permission_denials": [],
        "result": "something went wrong",
    }
    assert classify_result(result_event) == "CRASH"


# -- events land in the store ---------------------------------------------------


def test_events_recorded_into_store(tmp_path):
    with Store(tmp_path / "board.sqlite3") as store:
        board = store.create_board("b")
        card = store.create_card(board["id"], None, "a card")
        store.append_event(card["id"], "result", {"subtype": "success"})
        events = store.list_events(card["id"])
        assert len(events) == 1
        assert events[0]["kind"] == "result"
        assert events[0]["payload"]["subtype"] == "success"


# -- allowlist derived from a repo's test_command -----------------------------


def test_allowed_tools_falls_back_without_a_test_command():
    assert allowed_tools_for_repo(None) == DEFAULT_ALLOWED_TOOLS
    assert allowed_tools_for_repo({"test_command": None}) == DEFAULT_ALLOWED_TOOLS
    assert allowed_tools_for_repo({}) == DEFAULT_ALLOWED_TOOLS


def test_allowed_tools_scopes_bash_to_the_test_command():
    tools = allowed_tools_for_repo({"test_command": "uv run pytest"})
    assert tools == (
        "Read",
        "Edit",
        "Write",
        "Glob",
        "Grep",
        "Bash(git *)",
        "Bash(uv run pytest *)",
    )
    # nothing else in Bash - no bare "Bash" entry that would allow an unscoped command
    assert "Bash" not in tools


def test_allowed_tools_grant_the_lint_command_part_by_part():
    # a rule must match every subcommand of a compound command, so each && part is granted alone
    tools = allowed_tools_for_repo(
        {
            "test_command": "uv run pytest",
            "lint_command": "uv run ruff check . && uv run ruff format --check .",
        }
    )
    assert "Bash(uv run ruff check . *)" in tools
    assert "Bash(uv run ruff format --check . *)" in tools
    assert not any("&&" in tool for tool in tools)


def test_the_brief_names_the_exact_test_and_lint_commands():
    repo = {"test_command": "uv run pytest", "lint_command": "uv run ruff check ."}
    brief = commands_preamble(repo)
    assert "Run tests with: uv run pytest" in brief
    assert "Run lint with: uv run ruff check ." in brief
    # no declared command still names the one grant every card has, git
    bare = commands_preamble({})
    assert "- git *" in bare and "Run tests" not in bare


# -- the declared formatter's write form is admitted, scoped to what was declared -------


def test_allowed_tools_admits_the_declared_formatters_write_form():
    tools = allowed_tools_for_repo(
        {
            "test_command": "uv run pytest",
            "lint_command": "uv run ruff check . && uv run ruff format --check .",
        }
    )
    # the check form stays granted (still how a card sees it is misformatted)...
    assert "Bash(uv run ruff format --check . *)" in tools
    # ...and the write form is now granted too, so a card can also fix it
    assert "Bash(uv run ruff format . *)" in tools


def test_allowed_tools_formatter_write_form_keeps_the_repos_other_flags():
    # the write form must be the declared command with only --check dropped, not a rebuilt one
    tools = allowed_tools_for_repo(
        {
            "test_command": (
                "uv run --no-sync ruff format --check --no-cache --extend-exclude .claude . "
                "&& uv run --no-sync pytest -q"
            )
        }
    )
    assert "Bash(uv run --no-sync ruff format --no-cache --extend-exclude .claude . *)" in tools


def test_allowed_tools_formatter_write_form_absent_without_a_declared_check():
    # a repo that never declares "ruff format --check" grants no write form - a command the repo
    # never named is still refused, not admitted because it shares a word with something granted
    tools = allowed_tools_for_repo(
        {"test_command": "uv run pytest", "lint_command": "uv run ruff check ."}
    )
    assert not any("ruff format" in tool for tool in tools)


def test_allowed_tools_formatter_write_form_does_not_match_a_shared_prefix():
    # tokenised, not substring-matched: "formatter" is not "format", so this stays refused
    tools = allowed_tools_for_repo(
        {"test_command": "uv run pytest", "lint_command": "uv run ruff formatter --check ."}
    )
    assert not any("ruff format ." in tool for tool in tools)
    assert "Bash(uv run ruff formatter --check . *)" in tools


def test_allowed_tools_never_grants_a_blanket_bash():
    tools = allowed_tools_for_repo(
        {
            "test_command": "uv run pytest",
            "lint_command": "uv run ruff check . && uv run ruff format --check .",
        }
    )
    assert "Bash" not in tools
    assert "Bash(*)" not in tools
    assert not any(tool.startswith("Bash(ruff format") for tool in tools)


def test_the_brief_says_the_card_may_format_not_only_check():
    repo = {
        "test_command": "uv run pytest",
        "lint_command": "uv run ruff check . && uv run ruff format --check .",
    }
    brief = commands_preamble(repo)
    assert "write form" in brief
    assert "format and commit" in brief
    # a repo with no formatter declared gets no such promise
    plain = commands_preamble({"test_command": "uv run pytest"})
    assert "write form" not in plain


def test_a_summary_quoting_the_lease_prefix_is_not_a_lease_conflict():
    """card 48e86bcf: a finished run's summary quoted the prefix in prose and the card blocked.
    a bash-written change outside the lease is caught by the post-run committed-path check in
    exec/backends.py, which sets LEASE_CONFLICT itself - it never needed the agent's own words"""
    result_event = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "permission_denials": [],
        "result": f"Verified: the hook prints {LEASE_CONFLICT_PREFIX} when a path is refused.",
    }
    assert classify_result(result_event) is None
    assert result_to_run_result(result_event).blocked_reason_code is None


@pytest.mark.parametrize(
    "text",
    ["You've hit your session limit · resets 3:40pm (UTC)", "Request rejected (429)"],
)
def test_a_usage_limit_wins_over_an_earlier_lease_denial(text):
    """card b286983f: a Write was refused mid-run, then the session limit ended it - the limit is
    what stopped the run, so it must park as USAGE_LIMIT and resume, not wait on a lease answer"""
    result_event = {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "api_error_status": 429,
        "permission_denials": [
            {
                "tool_name": "Write",
                "tool_use_id": "t1",
                "tool_input": {"file_path": "/workspace/wowtomate/extract/restedxp.py"},
            }
        ],
        "result": text,
    }
    assert classify_result(result_event) == "USAGE_LIMIT"


def test_an_edit_denial_that_ends_in_a_question_stays_a_lease_conflict():
    # the order below the usage limit is unchanged: a lease refusal still outranks the question
    result_event = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "permission_denials": [
            {"tool_name": "Edit", "tool_use_id": "t1", "tool_input": {"file_path": "/w/x.py"}}
        ],
        "result": "x.py is outside my lease - should I widen it?",
    }
    assert classify_result(result_event) == "LEASE_CONFLICT"


# -- bash guard: refuses a command that reaches outside the worktree ----------


def _run_bash_guard(worktree, command):
    settings_path = write_lease_settings(worktree.parent / "guards", ["src/*"], root=worktree)
    settings = json.loads(settings_path.read_text())
    bash_entry = next(e for e in settings["hooks"]["PreToolUse"] if e["matcher"] == "Bash")
    hook_command = bash_entry["hooks"][0]["command"]
    payload = json.dumps({"tool_input": {"command": command}})
    return subprocess.run(
        hook_command.split(" ", 1),
        input=payload,
        capture_output=True,
        text=True,
    )


def test_bash_guard_permits_a_command_inside_the_worktree(tmp_path):
    worktree = tmp_path / "wt"
    (worktree / "src").mkdir(parents=True)
    result = _run_bash_guard(worktree, f"cat {worktree / 'src' / 'file.py'}")
    assert result.returncode == 0


def test_bash_guard_refuses_an_absolute_path_outside_the_worktree(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    outside = tmp_path / "elsewhere" / "secret.txt"
    result = _run_bash_guard(worktree, f"cat {outside}")
    assert result.returncode == 2
    assert BASH_ESCAPE_PREFIX in result.stderr


def test_bash_guard_refuses_cd_dotdot(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    result = _run_bash_guard(worktree, "cd .. && rm -rf something")
    assert result.returncode == 2
    assert BASH_ESCAPE_PREFIX in result.stderr


def test_bash_guard_permits_a_relative_path_with_a_slash(tmp_path):
    # run 3: the old regex read `/test_cli.py` out of `tests/test_cli.py` and refused the card
    # its own test command
    worktree = tmp_path / "wt"
    worktree.mkdir()
    result = _run_bash_guard(worktree, "uv run pytest -p no:cacheprovider -q tests/test_cli.py")
    assert result.returncode == 0


def test_bash_guard_permits_dev_null(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    assert _run_bash_guard(worktree, "ls smortboard 2>/dev/null").returncode == 0


def test_bash_guard_allows_a_plain_command_with_no_paths(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    result = _run_bash_guard(worktree, "git status")
    assert result.returncode == 0


# -- bash guard: refuses a git invocation that carries a global option / remote program flag ---


@pytest.mark.parametrize(
    "command",
    [
        "git -c core.sshCommand='sh -c id' fetch origin",
        "git -c core.pager='sh -c id' log",
        "git --config-env=core.sshCommand=EVIL fetch",
        "git -c alias.x='!id' x",
        "git --exec-path=/tmp/evil status",
        "git -C /tmp status",
        "git --git-dir=/tmp/other/.git status",
        "git fetch --upload-pack='sh -c id' origin",
        "git clone --upload-pack='sh -c id' url dest",
        "git push --receive-pack='sh -c id' origin",
        "git ls-remote --upload-pack='sh -c id' origin",
        "git fetch -u origin",
    ],
)
def test_bash_guard_refuses_git_invocations_that_can_run_an_arbitrary_program(tmp_path, command):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    result = _run_bash_guard(worktree, command)
    assert result.returncode == 2
    assert BASH_ESCAPE_PREFIX in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git add -A",
        "git commit -m 'x'",
        "git diff",
        "git log --oneline",
    ],
)
def test_bash_guard_permits_ordinary_git_commands(tmp_path, command):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    result = _run_bash_guard(worktree, command)
    assert result.returncode == 0


def test_a_card_run_carries_a_budget_ceiling():
    """a card that loops burns real money quietly. measured, one 13-turn card re-read 209k cached
    tokens, so a thrashing card multiplies that - the budget refuses at the limit instead."""
    cmd = build_command("do the thing", "/tmp/s.json")
    assert "--max-budget-usd" in cmd
    assert cmd[cmd.index("--max-budget-usd") + 1] == str(DEFAULT_CARD_BUDGET_USD)


def test_the_budget_can_be_lifted_deliberately():
    cmd = build_command("do the thing", "/tmp/s.json", budget_usd=None)
    assert "--max-budget-usd" not in cmd


def test_the_lease_is_told_to_the_agent_not_only_enforced():
    """the lease says exactly which files the work touches. an agent that knows them does not go
    looking, and exploration is what multiplies turns."""
    preamble = lease_preamble(["smortboard/exec/runner.py", "tests/test_exec.py"])
    assert "smortboard/exec/runner.py" in preamble
    assert "tests/test_exec.py" in preamble
    assert preamble.endswith("\n\n")  # it prefixes a brief, so it has to separate from it


def test_a_card_with_no_lease_gets_no_preamble():
    assert lease_preamble([]) == ""
    assert lease_preamble(None) == ""


def test_a_soft_lease_is_said_so_the_agent_commits_what_it_may():
    """measured on a real soft run: told only its lease, the agent wrote b/y.py past it, the hook let
    it through, and it left the file uncommitted as out of lease"""
    soft = lease_preamble(["a/**"], "soft")
    assert "a/**" in soft
    assert "SOFT LEASE" in soft
    assert "commit" in soft
    assert ".github/**" in soft and "CLAUDE.md" in soft  # the protected paths are named, readably
    assert "**/" not in soft
    strict = lease_preamble(["a/**"], "strict")
    assert "SOFT" not in strict
    assert strict == lease_preamble(["a/**"])


def test_the_default_tools_include_search():
    """without Grep and Glob an agent reaches for `Bash grep` and is refused. one measured card
    spent ten of its thirty-two turns being denied reworded shell commands."""
    assert "Grep" in DEFAULT_ALLOWED_TOOLS
    assert "Glob" in DEFAULT_ALLOWED_TOOLS


def test_the_prompt_says_a_denial_is_final():
    """the prompt told the agent not to retry a refused WRITE, and said nothing about a refused
    command - so it tried four spellings of the same denied shell call."""
    assert "REFUSED COMMAND WILL NOT SUCCEED REWORDED" in SYSTEM_PROMPT


# -- ProcessHandle: stop() terminates whatever a run is inside right now -------


class _FakeProcess:
    """stands in for subprocess.Popen - never a real process"""

    def __init__(self, dies_on_terminate=True):
        self.terminated = False
        self.killed = False
        self._dies_on_terminate = dies_on_terminate
        self._waits = 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self._waits += 1
        if self._dies_on_terminate or self.killed:
            return
        raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)


def test_terminate_sends_sigterm_first():
    process = _FakeProcess(dies_on_terminate=True)
    ProcessHandle(process).terminate()
    assert process.terminated
    assert not process.killed


def test_terminate_kills_after_the_timeout_if_sigterm_does_not_land():
    process = _FakeProcess(dies_on_terminate=False)
    ProcessHandle(process).terminate(timeout=0.01)
    assert process.terminated
    assert process.killed  # the process never exited on its own, so kill() had to follow


def test_terminate_removes_the_named_container_too(monkeypatch):
    """SIGTERM only kills the `docker run` client, not the container underneath it - `docker rm -f`
    is what actually stops the work. Docker is faked; this must never shell out for real."""
    calls = []
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **k: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0)
    )
    ProcessHandle(_FakeProcess(), container_name="smortboard-worker-abc123-def456").terminate()
    assert calls == [["docker", "rm", "-f", "smortboard-worker-abc123-def456"]]


def test_terminate_removes_the_container_before_signalling_the_client(monkeypatch):
    # the docker client ignored sigterm in a real stop; removing the container is what ends it
    order = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **k: order.append("rm") or subprocess.CompletedProcess(cmd, 0),
    )
    process = _FakeProcess()
    process.terminate = lambda: order.append("sigterm")
    ProcessHandle(process, container_name="smortboard-worker-abc123-def456").terminate()
    assert order == ["rm", "sigterm"]


def test_terminate_with_no_container_name_never_touches_docker(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: calls.append(cmd))
    ProcessHandle(_FakeProcess()).terminate()
    assert calls == []
