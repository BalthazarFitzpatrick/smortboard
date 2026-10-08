"""repo-wide test isolation.

every test runs against a throwaway credential-profiles state, never the operator's real
~/.config/smortboard - profiles.py is consulted on every scheduler tick (see its module
docstring), so a test file that forgets to isolate it silently reads real profile state instead of
the tmp one it thinks it has. tests/test_profiles.py and tests/test_profiles_http.py already set
these three per-test; this autouse fixture makes that the default for every OTHER test file too,
so a test that never touches profiles.py directly still cannot see real state through the
scheduler.
"""

import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_profiles_state(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("SMORTBOARD_PROFILES_STATE_PATH", str(tmp_path / "profiles.json"))
    monkeypatch.setenv("SMORTBOARD_CARD_TOKEN_PATH", str(tmp_path / "card_token"))


# the test files that touch code with an os branch: docker mount paths, preflight text, host
# backend, profile file modes, repo setup commands. the windows ci job runs only these (-m windows)
# because the rest is os-independent and linux already runs it
WINDOWS_SENSITIVE_FILES = {
    "test_backends.py",
    "test_docker_paths.py",
    "test_exec.py",
    "test_host_backend.py",
    "test_host_gate_reviewer.py",
    "test_orchestrator_ledger.py",
    "test_preflight.py",
    "test_profiles.py",
    "test_profiles_http.py",
    "test_repo_setup.py",
    "test_request_gate.py",
    "test_run_watchdogs.py",
    "test_seed_beta.py",
    "test_store.py",
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.path.name in WINDOWS_SENSITIVE_FILES:
            item.add_marker(pytest.mark.windows)
    # posix_only tests assert file modes, signals or shell paths windows does not have
    if os.name != "nt":
        return
    skip = pytest.mark.skip(reason="posix only")
    for item in items:
        if "posix_only" in item.keywords:
            item.add_marker(skip)
