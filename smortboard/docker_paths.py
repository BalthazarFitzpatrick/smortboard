r"""how a host path is written into a docker -v argument

docker desktop on windows takes a native path (C:\Users\me\x) as is. docker running inside wsl
wants the same folder as /mnt/c/Users/me/x. every bind mount goes through `mount_arg`, so a second
way of reaching docker is one more translator here and no change at the call sites.

the choice is the SMORTBOARD_DOCKER_PATHS env var: "docker-desktop" (the default) or "wsl". it is
not wired to a setting yet.
"""

import os
from collections.abc import Callable
from pathlib import PurePath, PureWindowsPath

PATHS_ENV = "SMORTBOARD_DOCKER_PATHS"
DEFAULT_TRANSLATOR = "docker-desktop"


def docker_desktop_path(host_path: str | PurePath) -> str:
    """docker desktop resolves the host path itself, so it passes through unchanged"""
    return str(host_path)


def wsl_path(host_path: str | PurePath) -> str:
    """C:\\Users\\me\\x -> /mnt/c/Users/me/x; a path with no drive letter is already posix"""
    path = PureWindowsPath(host_path)
    if not path.drive:
        return PurePath(host_path).as_posix()
    drive = path.drive.rstrip(":").lower()
    return "/".join(["/mnt", drive, *path.parts[1:]])


TRANSLATORS: dict[str, Callable[[str | PurePath], str]] = {
    "docker-desktop": docker_desktop_path,
    "wsl": wsl_path,
}


def to_mount_path(host_path: str | PurePath) -> str:
    name = os.environ.get(PATHS_ENV) or DEFAULT_TRANSLATOR
    translator = TRANSLATORS.get(name)
    if translator is None:
        raise ValueError(f"{PATHS_ENV}={name} is not one of {', '.join(TRANSLATORS)}")
    return translator(host_path)


def mount_arg(host_path: str | PurePath, container_path: str, mode: str | None = None) -> str:
    """the value of one `-v` flag: host:container, with an optional :ro or :rw"""
    spec = f"{to_mount_path(host_path)}:{container_path}"
    return f"{spec}:{mode}" if mode else spec
