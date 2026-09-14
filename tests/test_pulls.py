"""every open pull request across every board, in merge order - GET /api/pulls's backing logic.

gh is always faked here, never invoked for real, matching the suite-wide rule (see test_scheduler.py)
- `smortboard.pulls._gh` is monkeypatched directly, the same name pulls.py imports and calls.
"""

from __future__ import annotations

import pytest

from smortboard import pulls as pulls_module
from smortboard.pulls import open_pull_requests
from smortboard.store.api import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "board.db")
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _clear_pr_state_cache():
    # module-level, so a url answered by one test must not leak into the next
    pulls_module._pr_state_cache.clear()
    yield
    pulls_module._pr_state_cache.clear()


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_gh(monkeypatch, calls, states):
    """states maps a url to (state, mergeable) - the real gh subprocess is never reached"""

    def _gh(args, cwd):
        calls.append(args[2])  # ["pr", "view", url, ...] - the url gh was asked about
        url = args[2]
        state, mergeable = states[url]
        return _Result(0, stdout=f'{{"state": "{state}", "mergeable": "{mergeable}"}}')

    monkeypatch.setattr(pulls_module, "_gh", _gh)
    monkeypatch.setattr(pulls_module.shutil, "which", lambda name: "/usr/bin/gh")


def _board_and_repo(store, name="b"):
    board = store.create_board(name)
    repo = store.create_repo(board["id"], "r", "/tmp/r", "main")
    return board["id"], repo["id"]


def _merge_request(store, card_id, url):
    store.append_event(card_id, "merge_request", {"opened": True, "branch": "x", "url": url})


def test_an_empty_store_has_no_open_pull_requests(store, monkeypatch):
    calls = []
    _fake_gh(monkeypatch, calls, {})
    assert open_pull_requests(store) == []
    assert calls == []


def test_lists_open_pull_requests_with_board_title_state_mergeable_and_position(store, monkeypatch):
    board_id, repo_id = _board_and_repo(store)
    card = store.create_card(board_id, repo_id, "fix the thing", status="checking")
    _merge_request(store, card["id"], "https://example/pr/1")

    calls = []
    _fake_gh(monkeypatch, calls, {"https://example/pr/1": ("OPEN", "MERGEABLE")})

    rows = open_pull_requests(store)
    assert len(rows) == 1
    row = rows[0]
    assert row["card_id"] == card["id"]
    assert row["board_id"] == board_id
    assert row["board_name"] == "b"
    assert row["title"] == "fix the thing"
    assert row["url"] == "https://example/pr/1"
    assert row["state"] == "open"
    assert row["mergeable"] == "mergeable"
    assert row["depends_on"] == []
    assert row["conflicting"] is False
    assert row["dependency_unmerged"] is False
    assert row["position"] == 1


def test_a_todo_or_doing_card_never_appears_even_with_a_stale_pr_event(store, monkeypatch):
    board_id, repo_id = _board_and_repo(store)
    card = store.create_card(board_id, repo_id, "not there yet", status="doing")
    _merge_request(store, card["id"], "https://example/pr/1")

    calls = []
    _fake_gh(monkeypatch, calls, {"https://example/pr/1": ("OPEN", "MERGEABLE")})
    assert open_pull_requests(store) == []
    assert calls == []  # never asked gh about a card that carries no open pull request


def test_a_pull_request_gh_reports_merged_drops_off_the_list(store, monkeypatch):
    board_id, repo_id = _board_and_repo(store)
    card = store.create_card(board_id, repo_id, "already landed", status="accepted")
    _merge_request(store, card["id"], "https://example/pr/1")

    calls = []
    _fake_gh(monkeypatch, calls, {"https://example/pr/1": ("MERGED", "UNKNOWN")})
    assert open_pull_requests(store) == []


def test_accepted_cards_stay_listed_until_merged(store, monkeypatch):
    board_id, repo_id = _board_and_repo(store)
    card = store.create_card(board_id, repo_id, "waiting on fabian to merge", status="accepted")
    _merge_request(store, card["id"], "https://example/pr/1")

    calls = []
    _fake_gh(monkeypatch, calls, {"https://example/pr/1": ("OPEN", "MERGEABLE")})
    rows = open_pull_requests(store)
    assert len(rows) == 1
    assert rows[0]["state"] == "open"


def test_a_conflicting_pull_request_is_marked(store, monkeypatch):
    board_id, repo_id = _board_and_repo(store)
    card = store.create_card(board_id, repo_id, "needs a rebase", status="checking")
    _merge_request(store, card["id"], "https://example/pr/1")

    calls = []
    _fake_gh(monkeypatch, calls, {"https://example/pr/1": ("OPEN", "CONFLICTING")})
    rows = open_pull_requests(store)
    assert rows[0]["conflicting"] is True


def test_a_dependency_not_yet_merged_is_marked(store, monkeypatch):
    board_id, repo_id = _board_and_repo(store)
    base = store.create_card(board_id, repo_id, "base", status="checking")
    dependent = store.create_card(board_id, repo_id, "dependent", status="checking")
    store.add_dependency(dependent["id"], base["id"])
    _merge_request(store, base["id"], "https://example/pr/base")
    _merge_request(store, dependent["id"], "https://example/pr/dependent")

    calls = []
    _fake_gh(
        monkeypatch,
        calls,
        {
            "https://example/pr/base": ("OPEN", "MERGEABLE"),
            "https://example/pr/dependent": ("OPEN", "MERGEABLE"),
        },
    )
    rows = {r["card_id"]: r for r in open_pull_requests(store)}
    assert rows[dependent["id"]]["dependency_unmerged"] is True
    assert rows[base["id"]]["dependency_unmerged"] is False
    # merge order: base's row still comes before its dependent's
    ordered_ids = [r["card_id"] for r in sorted(rows.values(), key=lambda r: r["position"])]
    assert ordered_ids.index(base["id"]) < ordered_ids.index(dependent["id"])


def test_a_dependency_already_merged_is_not_marked(store, monkeypatch):
    board_id, repo_id = _board_and_repo(store)
    base = store.create_card(board_id, repo_id, "base", status="accepted")
    dependent = store.create_card(board_id, repo_id, "dependent", status="checking")
    store.add_dependency(dependent["id"], base["id"])
    _merge_request(store, base["id"], "https://example/pr/base")
    _merge_request(store, dependent["id"], "https://example/pr/dependent")

    calls = []
    _fake_gh(
        monkeypatch,
        calls,
        {
            "https://example/pr/base": ("MERGED", "UNKNOWN"),
            "https://example/pr/dependent": ("OPEN", "MERGEABLE"),
        },
    )
    rows = open_pull_requests(store)
    assert len(rows) == 1  # base dropped off, already merged
    assert rows[0]["card_id"] == dependent["id"]
    assert rows[0]["dependency_unmerged"] is False


def test_a_dependency_not_yet_accepted_is_marked(store, monkeypatch):
    board_id, repo_id = _board_and_repo(store)
    base = store.create_card(board_id, repo_id, "base", status="doing")
    dependent = store.create_card(board_id, repo_id, "dependent", status="checking")
    store.add_dependency(dependent["id"], base["id"])
    _merge_request(store, dependent["id"], "https://example/pr/dependent")

    calls = []
    _fake_gh(monkeypatch, calls, {"https://example/pr/dependent": ("OPEN", "MERGEABLE")})
    rows = open_pull_requests(store)
    assert rows[0]["dependency_unmerged"] is True
    assert calls == ["https://example/pr/dependent"]  # base has no PR yet - never asked about it


def test_pull_requests_across_two_boards_are_gathered_together(store, monkeypatch):
    board_a, repo_a = _board_and_repo(store, "alpha")
    board_b, repo_b = _board_and_repo(store, "beta")
    card_a = store.create_card(board_a, repo_a, "in alpha", status="checking")
    card_b = store.create_card(board_b, repo_b, "in beta", status="checking")
    _merge_request(store, card_a["id"], "https://example/pr/a")
    _merge_request(store, card_b["id"], "https://example/pr/b")

    calls = []
    _fake_gh(
        monkeypatch,
        calls,
        {
            "https://example/pr/a": ("OPEN", "MERGEABLE"),
            "https://example/pr/b": ("OPEN", "MERGEABLE"),
        },
    )
    rows = open_pull_requests(store)
    assert {r["board_name"] for r in rows} == {"alpha", "beta"}


def test_gh_is_asked_about_a_pull_request_at_most_once(store, monkeypatch):
    board_id, repo_id = _board_and_repo(store)
    base = store.create_card(board_id, repo_id, "base", status="checking")
    dependent = store.create_card(board_id, repo_id, "dependent", status="checking")
    store.add_dependency(dependent["id"], base["id"])
    _merge_request(store, base["id"], "https://example/pr/base")
    _merge_request(store, dependent["id"], "https://example/pr/dependent")

    calls = []
    _fake_gh(
        monkeypatch,
        calls,
        {
            "https://example/pr/base": ("OPEN", "MERGEABLE"),
            "https://example/pr/dependent": ("OPEN", "MERGEABLE"),
        },
    )
    open_pull_requests(store)
    # one gh call per pull request - the dependency check reuses the same fetch, not a second one
    assert sorted(calls) == ["https://example/pr/base", "https://example/pr/dependent"]


def test_a_cached_answer_is_not_asked_again_within_the_ttl(store, monkeypatch):
    board_id, repo_id = _board_and_repo(store)
    card = store.create_card(board_id, repo_id, "fix the thing", status="checking")
    _merge_request(store, card["id"], "https://example/pr/1")

    calls = []
    _fake_gh(monkeypatch, calls, {"https://example/pr/1": ("OPEN", "MERGEABLE")})
    open_pull_requests(store)
    open_pull_requests(store)
    assert calls == ["https://example/pr/1"]  # the second poll reused the cached answer
