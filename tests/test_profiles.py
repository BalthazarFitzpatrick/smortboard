"""credential profiles: naming, rotation state, and the scheduler switching on USAGE_LIMIT.

Every test points SMORTBOARD_PROFILES_STATE_PATH and SMORTBOARD_CARD_TOKEN_PATH at a tmp_path -
smortboard.scheduler calls into this module on every USAGE_LIMIT, including from tests this card
cannot edit (tests/test_scheduler.py), so a stray real-filesystem read is tolerated (see
smortboard.preflight's own token check, which already does this) but a stray WRITE never is: a
single-profile board must never grow a real ~/.config/smortboard/profiles.json just from running
the suite. The autouse fixture below is what keeps every write in this file on a throwaway path.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from smortboard import profiles
from smortboard.scheduler import BoardScheduler
from smortboard.store.api import Store


@pytest.fixture(autouse=True)
def _isolated_profiles(monkeypatch, tmp_path):
    # XDG_CONFIG_HOME isolates profiles_dir() (named profiles' token files); the other two are
    # the same test seams backends.py already offers for the default profile and its own state
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("SMORTBOARD_PROFILES_STATE_PATH", str(tmp_path / "profiles.json"))
    monkeypatch.setenv("SMORTBOARD_CARD_TOKEN_PATH", str(tmp_path / "card_token"))


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "board.db")
    yield s
    s.close()


@pytest.fixture
def board_and_repo(store):
    board = store.create_board("b")
    repo = store.create_repo(board["id"], "r", "/tmp/r", "main")
    return board["id"], repo["id"]


class FakeRuns:
    """stands in for server.runs.RunRegistry - see tests/test_scheduler.py for the original. a
    local copy rather than a cross-file import, matching how every other scheduler-adjacent test
    file builds its own."""

    def __init__(self):
        self.started: list[str] = []
        self._callbacks: dict[str, object] = {}

    def start(self, card_id, runner=None, on_finish=None):
        self.started.append(card_id)
        if on_finish is not None:
            self._callbacks[card_id] = on_finish
        return SimpleNamespace(card_id=card_id, running=True)

    def finish(self, card_id, blocked_reason_code=None):
        callback = self._callbacks.pop(card_id, None)
        assert callback is not None, f"{card_id} was never started"
        callback(SimpleNamespace(blocked_reason_code=blocked_reason_code))


# -- default profile, unconfigured ---------------------------------------------------


def test_a_fresh_board_has_only_the_default_profile():
    rows = profiles.list_profiles()
    assert [r["name"] for r in rows] == ["default"]
    assert rows[0]["active"] is True


def test_the_default_profile_path_is_the_legacy_card_token_file(tmp_path, monkeypatch):
    from smortboard.exec.backends import card_token_path

    assert profiles.profile_path("default") == card_token_path()


def test_reading_profiles_never_writes_a_state_file():
    # list/active/has_multiple are all pure reads - a single-credential board must never grow a
    # state file just from being asked about itself
    profiles.list_profiles()
    profiles.active_profile()
    profiles.has_multiple_profiles()
    profiles.next_available()
    profiles.earliest_reset()
    assert not profiles.state_path().exists()


# -- add / remove / set active ---------------------------------------------------------


def test_add_profile_registers_a_name_and_writes_a_600_token_file():
    path = profiles.add_profile("work", token="s3cret-token")
    assert path.is_file()
    assert path.stat().st_mode & 0o777 == 0o600
    names = [r["name"] for r in profiles.list_profiles()]
    assert names == ["default", "work"]


def test_add_profile_without_a_token_just_registers_the_name():
    profiles.add_profile("work")
    row = next(r for r in profiles.list_profiles() if r["name"] == "work")
    assert row["present"] is False


def test_add_profile_rejects_a_duplicate_name():
    profiles.add_profile("work")
    with pytest.raises(profiles.ProfileError):
        profiles.add_profile("work")


@pytest.mark.parametrize("bad", ["", "has/slash", "has space", "has.dot"])
def test_add_profile_rejects_an_invalid_name(bad):
    with pytest.raises(profiles.ProfileError):
        profiles.add_profile(bad)


def test_remove_profile_refuses_to_remove_default():
    with pytest.raises(profiles.ProfileError):
        profiles.remove_profile("default")


def test_remove_profile_refuses_to_remove_the_active_profile():
    profiles.add_profile("work")
    profiles.set_active("work")
    with pytest.raises(profiles.ProfileError):
        profiles.remove_profile("work")


def test_remove_profile_drops_a_registered_inactive_profile():
    profiles.add_profile("work")
    profiles.remove_profile("work")
    assert [r["name"] for r in profiles.list_profiles()] == ["default"]


def test_set_active_refuses_an_unknown_profile():
    with pytest.raises(profiles.ProfileError):
        profiles.set_active("ghost")


def test_set_active_switches_the_active_profile():
    profiles.add_profile("work")
    profiles.set_active("work")
    assert profiles.active_profile() == "work"
    rows = {r["name"]: r for r in profiles.list_profiles()}
    assert rows["work"]["active"] is True
    assert rows["default"]["active"] is False


# -- token_path_for_run: what a run actually reads from -------------------------------


def test_an_explicit_override_always_wins():
    profiles.add_profile("work")
    profiles.set_active("work")
    assert profiles.token_path_for_run("/explicit/path") == "/explicit/path"


def test_the_default_profile_resolves_to_none_so_the_keychain_fallback_still_applies():
    # None is what backends.read_card_token needs to fall back to the OS credential store - an
    # explicit path (even the same file) would skip that fallback
    assert profiles.token_path_for_run(None) is None


def test_a_named_active_profile_resolves_to_its_own_file():
    profiles.add_profile("work")
    profiles.set_active("work")
    assert profiles.token_path_for_run(None) == profiles.profile_path("work")


# -- security: mode 600, and no token value ever surfaces -----------------------------


def test_write_token_file_lands_at_600_even_with_a_permissive_umask(tmp_path):
    import os

    previous = os.umask(0o022)
    try:
        path = profiles.add_profile("work", token="s3cret")
    finally:
        os.umask(previous)
    assert path.stat().st_mode & 0o777 == 0o600


def test_read_active_token_refuses_an_insecure_named_profile_file():
    path = profiles.add_profile("work", token="s3cret")
    profiles.set_active("work")
    path.chmod(0o644)
    with pytest.raises(profiles.ProfileError) as exc:
        profiles.read_active_token()
    assert "s3cret" not in str(exc.value)
    assert "600" in str(exc.value)


def test_a_token_value_never_appears_in_the_state_file(tmp_path):
    secret = "sk-do-not-leak-this-value"
    profiles.add_profile("work", token=secret)
    assert secret not in profiles.state_path().read_text()


def test_read_active_token_reads_the_active_profiles_file():
    profiles.add_profile("work", token="the-token")
    profiles.set_active("work")
    assert profiles.read_active_token() == "the-token"


# -- limits and rotation order ----------------------------------------------------------


def test_mark_limited_then_is_limited():
    future = time.time() + 3600
    profiles.mark_limited("default", future)
    assert profiles.is_limited("default")
    assert profiles.is_limited("default", now=future + 1) is False


def test_next_available_skips_a_limited_active_profile():
    profiles.add_profile("second", token="t")
    profiles.mark_limited("default", time.time() + 3600)
    assert profiles.next_available() == "second"


def test_next_available_is_none_once_every_profile_is_limited():
    profiles.add_profile("second", token="t")
    now = time.time()
    profiles.mark_limited("default", now + 3600)
    profiles.set_active("second")
    profiles.mark_limited("second", now + 1800)
    assert profiles.next_available() is None


def test_earliest_reset_is_the_soonest_still_future_window():
    now = time.time()
    profiles.mark_limited("default", now + 3600)
    profiles.add_profile("second", token="t")
    profiles.mark_limited("second", now + 60)
    assert profiles.earliest_reset() == pytest.approx(now + 60)


def test_earliest_reset_ignores_a_window_that_already_passed():
    now = time.time()
    profiles.mark_limited("default", now - 10)  # stale, should never be picked
    profiles.add_profile("second", token="t")
    profiles.mark_limited("second", now + 60)
    assert profiles.earliest_reset() == pytest.approx(now + 60)


# -- scheduler integration: USAGE_LIMIT rotates instead of only parking ---------------


def test_a_usage_limit_run_switches_profile_and_resumes_the_same_card(store, board_and_repo):
    board_id, repo_id = board_and_repo
    store.set_setting("max_parallel", "1")
    profiles.add_profile("second", token="second-token")
    card = store.create_card(board_id, repo_id, "a")

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    assert runs.started == [card["id"]]

    resets_at = time.time() + 3600
    store.append_event(card["id"], "rate_limit_event", {"rate_limit_info": {"resetsAt": resets_at}})
    runs.finish(card["id"], blocked_reason_code="USAGE_LIMIT")

    assert profiles.active_profile() == "second"
    assert profiles.is_limited("default")
    view = scheduler.schedule_view()
    assert view["paused_until"] is None
    # the operator touched nothing - the same card ran again on the new profile
    assert runs.started == [card["id"], card["id"]]


def test_with_every_profile_limited_the_board_parks_until_the_earliest_reset(store, board_and_repo):
    board_id, repo_id = board_and_repo
    store.set_setting("max_parallel", "1")
    profiles.add_profile("second", token="second-token")
    card = store.create_card(board_id, repo_id, "a")

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()

    first_reset = time.time() + 100
    store.append_event(
        card["id"], "rate_limit_event", {"rate_limit_info": {"resetsAt": first_reset}}
    )
    runs.finish(card["id"], blocked_reason_code="USAGE_LIMIT")
    assert profiles.active_profile() == "second"

    second_reset = time.time() + 50  # sooner than the first
    store.append_event(
        card["id"], "rate_limit_event", {"rate_limit_info": {"resetsAt": second_reset}}
    )
    runs.finish(card["id"], blocked_reason_code="USAGE_LIMIT")

    view = scheduler.schedule_view()
    assert view["paused_until"] == pytest.approx(min(first_reset, second_reset))
    # nothing left to switch to - not resumed a third time
    assert runs.started == [card["id"], card["id"]]


def test_a_single_profile_board_still_parks_and_writes_no_profile_state(store, board_and_repo):
    # parity with the pre-profiles behaviour in tests/test_scheduler.py, plus the disk-write guard
    board_id, repo_id = board_and_repo
    store.set_setting("max_parallel", "1")
    a = store.create_card(board_id, repo_id, "a")
    b = store.create_card(board_id, repo_id, "b")

    runs = FakeRuns()
    scheduler = BoardScheduler(board_id, store.path, runs)
    scheduler.start_all()
    resets_at = time.time() + 3600
    store.append_event(a["id"], "rate_limit_event", {"rate_limit_info": {"resetsAt": resets_at}})
    runs.finish(a["id"], blocked_reason_code="USAGE_LIMIT")

    view = scheduler.schedule_view()
    assert b["id"] not in runs.started
    assert view["paused_until"] == pytest.approx(resets_at)
    assert not profiles.state_path().exists()
