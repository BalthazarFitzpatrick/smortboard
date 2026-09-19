"""base-red detection: a gate failure the base already had is not the card's."""

import subprocess

from smortboard.review import base_red
from smortboard.review.base_red import base_red_ids, check_base_red, failing_test_ids
from smortboard.review.gates import GateResult

TWO_FAILED = (
    "FAILED tests/test_a.py::test_one - AssertionError\n"
    "FAILED tests/test_a.py::test_two - AssertionError\n"
    "2 failed, 10 passed in 1.23s\n"
)


def test_a_failure_list_that_matches_the_summary_count_is_complete():
    assert failing_test_ids(TWO_FAILED) == (
        {"tests/test_a.py::test_one", "tests/test_a.py::test_two"},
        True,
    )


def test_a_truncated_failure_list_is_not_complete():
    out = "FAILED tests/test_a.py::test_one\n2 failed, 10 passed in 1.23s\n"
    assert failing_test_ids(out)[1] is False


def test_errors_in_the_summary_make_it_not_complete():
    out = "FAILED tests/test_a.py::test_one\n1 failed, 1 error in 1.0s\n"
    assert failing_test_ids(out)[1] is False


def test_output_with_no_pytest_failures_is_not_complete():
    assert failing_test_ids("smortboard/x.py:3:1: E501 line too long\n") == (set(), False)


def test_it_is_base_red_only_when_every_card_failure_also_fails_on_base():
    assert base_red_ids(TWO_FAILED, TWO_FAILED + "FAILED tests/other.py::t\n") == [
        "tests/test_a.py::test_one",
        "tests/test_a.py::test_two",
    ]


def test_a_failure_only_the_card_has_is_not_base_red():
    base_only_one = "FAILED tests/test_a.py::test_one\n1 failed, 11 passed in 1.0s\n"
    assert base_red_ids(TWO_FAILED, base_only_one) is None


def test_an_incomplete_card_list_is_never_called_base_red():
    out = "FAILED tests/test_a.py::test_one\n2 failed, 10 passed in 1.23s\n"
    assert base_red_ids(out, TWO_FAILED) is None


def _repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@t"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    (path / "a.txt").write_text("x")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "c"], check=True, capture_output=True)
    return path


def test_the_base_is_checked_out_run_and_cleaned_up(tmp_path, monkeypatch):
    repo_path = _repo(tmp_path)
    seen = {}

    def _gate(store, card_id, tree, repo):
        seen["existed"] = (tree / "a.txt").exists()
        seen["tree"] = tree
        return GateResult(passed=False, command="x", exit_code=1, output=TWO_FAILED)

    monkeypatch.setattr(base_red, "run_test_gate", _gate)
    verdict = check_base_red({"path": str(repo_path)}, "main", "abcdef123456", TWO_FAILED)
    assert verdict is not None and len(verdict[1]) == 2
    assert seen["existed"] is True
    assert not seen["tree"].exists()
    listed = subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "list"], capture_output=True, text=True
    ).stdout
    assert len(listed.strip().splitlines()) == 1


def test_no_second_suite_run_when_the_card_failure_cannot_be_compared(tmp_path, monkeypatch):
    repo_path = _repo(tmp_path)

    def _boom(*a, **k):
        raise AssertionError("must not run the suite on base")

    monkeypatch.setattr(base_red, "run_test_gate", _boom)
    assert check_base_red({"path": str(repo_path)}, "main", "abc", "ruff said no\n") is None


def test_a_base_that_passes_means_the_card_caused_it(tmp_path, monkeypatch):
    repo_path = _repo(tmp_path)
    monkeypatch.setattr(
        base_red,
        "run_test_gate",
        lambda *a, **k: GateResult(passed=True, command="x", exit_code=0, output="12 passed in 1s"),
    )
    assert check_base_red({"path": str(repo_path)}, "main", "abc", TWO_FAILED) is None
