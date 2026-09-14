"""repo-wide test isolation.

every test runs against a throwaway credential-profiles state, never the operator's real
~/.config/smortboard - profiles.py is consulted on every scheduler tick (see its module
docstring), so a test file that forgets to isolate it silently reads real profile state instead of
the tmp one it thinks it has. tests/test_profiles.py and tests/test_profiles_http.py already set
these three per-test; this autouse fixture makes that the default for every OTHER test file too,
so a test that never touches profiles.py directly still cannot see real state through the
scheduler.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolated_profiles_state(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("SMORTBOARD_PROFILES_STATE_PATH", str(tmp_path / "profiles.json"))
    monkeypatch.setenv("SMORTBOARD_CARD_TOKEN_PATH", str(tmp_path / "card_token"))
