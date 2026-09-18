"""verify the capture container's boundaries before running the real screenshot test"""

import socket
from pathlib import Path

import pytest

from smortboard.review import screenshot


def main() -> int:
    active = {
        name
        for _, name in socket.if_nameindex()
        if int(Path(f"/sys/class/net/{name}/flags").read_text().strip(), 16) & 1
    }
    assert active == {"lo"}, active
    assert not Path("/proc/net/route").read_text().splitlines()[1:]
    for host in ("1.1.1.1", "8.8.8.8"):
        try:
            with socket.create_connection((host, 443), timeout=1):
                raise AssertionError("external network must be inaccessible")
        except OSError:
            pass
    for path in (
        "/Users",
        "/var/run/docker.sock",
        "/workspace/.git",
        "/home/agent/.codex/auth.json",
    ):
        assert not Path(path).exists(), path
    for folder in ("/workspace/smortboard", "/workspace/tests"):
        for path in Path(folder).rglob("*"):
            if path.is_symlink():
                assert path.resolve().is_relative_to(Path(folder)), path
    print("capture isolation verified: no external network or unrelated host mounts", flush=True)

    capture = screenshot._capture

    def save_capture(*args, **kwargs):
        png = capture(*args, **kwargs)
        Path("/artifacts/local-test.png").write_bytes(png)
        return png

    screenshot._capture = save_capture
    try:
        return pytest.main(
            [
                "tests/test_card_screenshot.py::test_capture_takes_a_real_screenshot_of_a_faked_server",
                "-q",
                "-p",
                "no:cacheprovider",
                "--basetemp=/tmp/capture-test",
            ]
        )
    finally:
        screenshot._capture = capture


if __name__ == "__main__":
    raise SystemExit(main())
