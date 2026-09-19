from smortboard.exec.commands import declares_formatter, formatter_write_form


# -- formatter_write_form: the write form of a declared check-only command part ----------


def test_formatter_write_form_drops_only_check():
    assert formatter_write_form("uv run ruff format --check .") == "uv run ruff format ."


def test_formatter_write_form_keeps_other_flags_including_no_cache():
    part = "uv run --no-sync ruff format --check --no-cache --extend-exclude .claude ."
    assert (
        formatter_write_form(part)
        == "uv run --no-sync ruff format --no-cache --extend-exclude .claude ."
    )


def test_formatter_write_form_absent_without_check():
    assert formatter_write_form("uv run ruff format .") is None


def test_formatter_write_form_absent_for_unrelated_command():
    assert formatter_write_form("uv run pytest -q") is None


def test_formatter_write_form_does_not_match_a_shared_prefix():
    # tokenised, not substring-matched: "formatter" is not "format"
    assert formatter_write_form("uv run ruff formatter --check .") is None


def test_formatter_write_form_handles_unparsable_shell_text():
    # an unterminated quote can't be tokenised - return None rather than raise
    assert formatter_write_form("ruff format --check 'unterminated") is None


# -- declares_formatter: whether a repo's declared commands name the formatter -----------


def test_declares_formatter_true_when_lint_command_checks():
    repo = {"test_command": "uv run pytest", "lint_command": "uv run ruff format --check ."}
    assert declares_formatter(repo) is True


def test_declares_formatter_true_when_test_command_checks():
    repo = {"test_command": "uv run ruff format --check . && uv run pytest"}
    assert declares_formatter(repo) is True


def test_declares_formatter_false_without_a_declared_check():
    repo = {"test_command": "uv run pytest", "lint_command": "uv run ruff check ."}
    assert declares_formatter(repo) is False


def test_declares_formatter_false_for_none_and_empty_repo():
    assert declares_formatter(None) is False
    assert declares_formatter({}) is False
