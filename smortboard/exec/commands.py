"""lab-neutral reading of a repo's declared test/lint commands - shared by any lab's runner
and its Bash allowlist, so the write-form logic is not duplicated per lab"""

import shlex


def formatter_write_form(part: str) -> str | None:
    """the write form of one declared command part, if it runs `ruff format --check` - the same
    tokens with exactly the `--check` flag dropped, else None.

    tokenised rather than substring-matched, so a command that merely shares a prefix - `ruff
    formatter --check .`, `unruffled format --check` - is correctly left alone: the grant this
    feeds must never admit a command the repo did not actually declare."""
    try:
        tokens = shlex.split(part)
    except ValueError:
        return None
    if "--check" not in tokens:
        return None
    if not any(tokens[i] == "ruff" and tokens[i + 1] == "format" for i in range(len(tokens) - 1)):
        return None
    return shlex.join(token for token in tokens if token != "--check")


def declares_formatter(repo: dict[str, object] | None) -> bool:
    """whether `repo`'s test or lint command names the formatter in its check-only form - the
    signal used to add the write-form line to the card's brief"""
    repo = repo or {}
    for command in (repo.get("test_command"), repo.get("lint_command")):
        if not command:
            continue
        if any(formatter_write_form(part.strip()) for part in str(command).split("&&")):
            return True
    return False
