"""`smortboard-land`: the landing lock for agents outside the board.

Queues for the push lock on one repo's target branch, heartbeats while it holds it, then fetches,
merges the target in, runs the given test command, and pushes - the same shape as the board's own
integrate path (smortboard/lifecycle.py, smortboard/review/integrate.py), for anyone who is not a
card. Always releases the lock, including on ctrl-c.

    uv run smortboard-land --repo . -- pytest -q
    uv run smortboard-land --repo ~/work/wowtomate --target development -- uv run pytest -q
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import FrameType

_DEFAULT_URL = "http://127.0.0.1:8000"
_DEFAULT_TTL_S = 600
_HEARTBEAT_INTERVAL_S = 30
_POLL_INTERVAL_S = 2
# the board refuses these as an integrate() base; the client refuses them before it ever asks
PROTECTED_BRANCHES = frozenset({"main", "master", "trunk"})


class LandingError(Exception):
    """a reason to exit non-zero, already printed to stderr by the caller"""


def _post(url: str, path: str, body: dict) -> dict:
    payload = json.dumps(body).encode()
    request = urllib.request.Request(
        f"{url}{path}", data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise LandingError(f"POST {path} failed: {exc.code} {exc.read().decode()}") from exc
    except urllib.error.URLError as exc:
        raise LandingError(f"could not reach the board at {url}: {exc.reason}") from exc


def _delete(url: str, path: str) -> None:
    request = urllib.request.Request(f"{url}{path}", method="DELETE")
    try:
        with urllib.request.urlopen(request, timeout=30):
            pass
    except urllib.error.URLError:
        pass  # releasing best-effort - a lease past its ttl frees itself anyway


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _current_branch(repo: Path) -> str:
    result = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if result.returncode != 0:
        raise LandingError(f"could not read the current branch: {result.stderr.strip()}")
    return result.stdout.strip()


def _acquire(url: str, repo: str, holder: str, branch: str, target: str, ttl_s: int) -> str:
    """blocks until granted, printing queue position changes, and returns the lease id"""
    result = _post(
        url,
        f"/api/repos/{repo}/landing",
        {
            "holder": holder,
            "branch": branch,
            "target": target,
            "ttl_s": ttl_s,
        },
    )
    lease_id = result["lease_id"]
    last_position = None
    while not result["granted"]:
        position = result.get("position")
        if position != last_position:
            print(f"landing lock: queued behind {position - 1} other(s)", file=sys.stderr)
            last_position = position
        time.sleep(_POLL_INTERVAL_S)
        result = _post(
            url,
            f"/api/repos/{repo}/landing",
            {
                "holder": holder,
                "branch": branch,
                "target": target,
                "ttl_s": ttl_s,
                "lease_id": lease_id,
            },
        )
    print("landing lock: granted", file=sys.stderr)
    return lease_id


def _heartbeat_loop(
    url: str,
    repo: str,
    holder: str,
    branch: str,
    target: str,
    ttl_s: int,
    lease_id: str,
    stop: threading.Event,
) -> None:
    while not stop.wait(_HEARTBEAT_INTERVAL_S):
        _post(
            url,
            f"/api/repos/{repo}/landing",
            {
                "holder": holder,
                "branch": branch,
                "target": target,
                "ttl_s": ttl_s,
                "lease_id": lease_id,
            },
        )


def run(args: argparse.Namespace) -> int:
    repo_path = Path(args.repo).expanduser().resolve()
    if args.target in PROTECTED_BRANCHES:
        print(f"refusing to land on {args.target} - that is the operator's branch", file=sys.stderr)
        return 1
    if not args.test_command:
        print("no test command given - pass it after `--`", file=sys.stderr)
        return 1

    branch = _current_branch(repo_path)
    holder = args.holder or f"smortboard-land@{branch}"
    lease_id: str | None = None
    stop_heartbeat = threading.Event()
    heartbeat_thread: threading.Thread | None = None

    def _release(*_: object) -> None:
        stop_heartbeat.set()
        if lease_id is not None:
            _delete(args.url, f"/api/landing/{lease_id}")

    def _on_signal(signum: int, frame: FrameType | None) -> None:  # noqa: ARG001
        _release()
        sys.exit(130)

    signal.signal(signal.SIGINT, _on_signal)

    repo_url_key = urllib.parse.quote(args.repo, safe="")
    try:
        lease_id = _acquire(args.url, repo_url_key, holder, branch, args.target, args.ttl_s)
        heartbeat_thread = threading.Thread(
            target=_heartbeat_loop,
            args=(
                args.url,
                repo_url_key,
                holder,
                branch,
                args.target,
                args.ttl_s,
                lease_id,
                stop_heartbeat,
            ),
            daemon=True,
        )
        heartbeat_thread.start()

        fetched = _git(repo_path, "fetch", "origin")
        if fetched.returncode != 0:
            print(f"fetch failed: {fetched.stderr.strip()}", file=sys.stderr)
            return 1

        merged = _git(repo_path, "merge", f"origin/{args.target}", "--no-edit")
        if merged.returncode != 0:
            print(
                f"merge conflict pulling origin/{args.target} in: {merged.stderr.strip()}",
                file=sys.stderr,
            )
            return 1

        tested = subprocess.run(args.test_command, cwd=repo_path, shell=False)
        if tested.returncode != 0:
            print(f"tests failed: exit {tested.returncode}", file=sys.stderr)
            return 1

        pushed = _git(repo_path, "push", "origin", f"HEAD:refs/heads/{args.target}")
        if pushed.returncode != 0:
            print(f"push rejected: {pushed.stderr.strip()}", file=sys.stderr)
            return 1

        print(f"landed on {args.target}", file=sys.stderr)
        return 0
    finally:
        _release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smortboard-land")
    parser.add_argument("--repo", required=True, help="repo path, or a board repo id")
    parser.add_argument("--target", default="development", help="base branch to land on")
    parser.add_argument("--url", default=_DEFAULT_URL, help="the board's own base url")
    parser.add_argument("--holder", default=None, help="defaults to smortboard-land@<branch>")
    parser.add_argument("--ttl-s", type=int, default=_DEFAULT_TTL_S, dest="ttl_s")
    parser.add_argument("test_command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.test_command and args.test_command[0] == "--":
        args.test_command = args.test_command[1:]
    try:
        return run(args)
    except LandingError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
