"""host paths in docker -v arguments: docker desktop as is, wsl translated, nothing else touched"""

import pytest

from smortboard.docker_paths import PATHS_ENV, mount_arg, to_mount_path, wsl_path


def test_docker_desktop_passes_a_native_path_through(monkeypatch):
    monkeypatch.delenv(PATHS_ENV, raising=False)
    assert to_mount_path(r"C:\a\b") == r"C:\a\b"


def test_a_mount_arg_joins_host_container_and_mode(monkeypatch):
    monkeypatch.delenv(PATHS_ENV, raising=False)
    assert mount_arg(r"C:\a\b", "/workspace", "ro") == r"C:\a\b:/workspace:ro"
    assert mount_arg("/srv/x", "/data") == "/srv/x:/data"


def test_the_wsl_translator_maps_a_drive_letter_to_mnt():
    assert wsl_path(r"C:\a\b") == "/mnt/c/a/b"
    assert wsl_path(r"D:\Users\me\repo") == "/mnt/d/Users/me/repo"
    assert wsl_path("/already/posix") == "/already/posix"


def test_the_env_var_switches_every_mount_to_wsl_without_touching_callers(monkeypatch):
    monkeypatch.setenv(PATHS_ENV, "wsl")
    assert mount_arg(r"C:\a\b", "/workspace", "ro") == "/mnt/c/a/b:/workspace:ro"


def test_an_unknown_translator_is_refused(monkeypatch):
    monkeypatch.setenv(PATHS_ENV, "podman")
    with pytest.raises(ValueError, match="docker-desktop, wsl"):
        to_mount_path("/x")
