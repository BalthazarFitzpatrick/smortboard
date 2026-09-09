"""how a card runs: one throwaway container, and nothing else.

Per docs/PLAN.md "The containment decision": a card will eventually read input nobody wrote for it -
an issue body, a fetched page, a dependency's readme - so only a boundary it cannot argue with is
worth having.

THERE IS NO SECOND MODE. `require_card_runtime` returns the container runtime or refuses with
instructions. A subprocess fallback would put an agent that may have read untrusted input on the
operator's own filesystem, with their own credential and their own GitHub access - which is the
whole thing the container exists to prevent. A board that quietly degraded would be claiming an
isolation it no longer had.
"""

import os
import shlex
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Protocol

from smortboard.exec.runner import RunResult, allowed_tools_for_repo, build_command, run_process
from smortboard.exec.worktrees import WorktreeError, current_branch, repo_root_of_worktree
from smortboard.store.api import Store

# defaults, overridable per deployment - never the credential itself, which is never an env var
DEFAULT_CARD_IMAGE = "smortboard-card:latest"
DEFAULT_TOKEN_PATH = Path.home() / ".config" / "smortboard" / "card_token"

CARD_IMAGE_ENV = "SMORTBOARD_CARD_IMAGE"
CARD_TOKEN_PATH_ENV = "SMORTBOARD_CARD_TOKEN_PATH"

# where the token lands inside the container - read-only, never passed as an env var so it never
# shows up in `docker inspect`
_KEYCHAIN_SERVICE = "smortboard-card-token"
_CONTAINER_WORKDIR = "/workspace"


def card_image() -> str:
    return os.environ.get(CARD_IMAGE_ENV, DEFAULT_CARD_IMAGE)


class CardTokenMissing(RuntimeError):
    """no card credential in the keychain or on disk"""


def card_token_path() -> Path:
    """where a card token lives when it is a file rather than a keychain entry"""
    return Path(
        os.environ.get(CARD_TOKEN_PATH_ENV, Path.home() / ".config" / "smortboard" / "card_token")
    )


def _keychain_token() -> str | None:
    """the macOS keychain, which is where Claude Code keeps its own credentials.

    preferred over a file because a file means a plaintext credential sitting under the operator's
    home directory for as long as the board exists. absent or unreadable, the caller falls back.
    """
    if not shutil.which("security"):
        return None
    result = subprocess.run(
        ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE, "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    token = result.stdout.strip()
    return token if result.returncode == 0 and token else None


def read_card_token(token_path: str | Path | None = None) -> str:
    """the card credential, keychain first, then a file.

    NEVER returned to anywhere it could be logged: the one caller writes it straight to the
    container's stdin. It is not stored on the backend and not put in the environment.
    """
    explicit = token_path is not None
    if not explicit:
        from_keychain = _keychain_token()
        if from_keychain:
            return from_keychain
    resolved = Path(token_path or card_token_path())
    if resolved.is_file():
        token = resolved.read_text().strip()
        if token:
            return token
    raise CardTokenMissing(
        f"No card credential. Run `claude setup-token`, then either\n"
        f"  security add-generic-password -s {_KEYCHAIN_SERVICE} -a smortboard -w <token>\n"
        f"or write it to {resolved} with mode 600."
    )


def card_token_available(token_path: str | Path | None = None) -> bool:
    try:
        read_card_token(token_path)
    except CardTokenMissing:
        return False
    return True


def docker_available() -> bool:
    """true if `docker` is on PATH and a daemon actually answers - not just installed but unusable"""
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


class RunnerBackend(Protocol):
    """a card runner: a working directory and a token in, a RunResult out.

    everything else - the prompt, the settings file, the model, the repo's allowlist - is shared;
    only how (and where) the `claude` process actually runs differs between implementations.
    """

    name: str

    def run_card(
        self,
        store: Store,
        card_id: str,
        worktree_path: str | Path,
        prompt: str,
        settings_path: str | Path,
        model: str = "sonnet",
        repo: dict[str, Any] | None = None,
        token_path: str | Path | None = None,
    ) -> RunResult: ...


class ContainerBackend:
    """one throwaway container per card: a clone bind-mounted, the token handed over on stdin, and
    no GitHub credential inside it anywhere.

    What this contains: the card's filesystem and process, and the shell escapes bash_guard.py
    admits it cannot stop. What it does not contain: the network (the run has to reach the API, so
    egress is open) and the credential itself (a container is a filesystem boundary, not a
    credential one - what protects the token is that it is a separate, narrower one: a
    setup-token can only make model requests).
    """

    name = "container"

    def __init__(self, image: str | None = None) -> None:
        self.image = image or card_image()

    def run_card(
        self,
        store: Store,
        card_id: str,
        worktree_path: str | Path,
        prompt: str,
        settings_path: str | Path,
        model: str = "sonnet",
        repo: dict[str, Any] | None = None,
        token_path: str | Path | None = None,
    ) -> RunResult:
        token = read_card_token(token_path)
        clone_path = Path(tempfile.mkdtemp(prefix=f"smortboard-card-{uuid.uuid4().hex[:8]}-"))
        repo_root = repo_root_of_worktree(worktree_path)
        branch = current_branch(worktree_path)
        try:
            self._clone(worktree_path, clone_path, branch)
            cmd = self._docker_command(clone_path, prompt, settings_path, model, repo)
            # the token goes straight down the container's stdin and is not kept anywhere
            result = run_process(store, card_id, cmd, cwd=clone_path, stdin_text=token + "\n")
            self._fetch_back(repo_root, clone_path, branch)
            return result
        finally:
            shutil.rmtree(clone_path, ignore_errors=True)

    def _clone(self, worktree_path: str | Path, clone_path: Path, branch: str) -> None:
        # a clone, not a mount of the worktree itself: the worktree's .git is a pointer file into
        # the parent repo, so mounting it alone leaves git dead and the card unable to commit
        result = subprocess.run(
            ["git", "clone", "--branch", branch, str(worktree_path), str(clone_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise WorktreeError(f"clone for card container failed: {result.stderr.strip()}")

    def _docker_command(
        self,
        clone_path: Path,
        prompt: str,
        settings_path: str | Path,
        model: str,
        repo: dict[str, Any] | None,
    ) -> list[str]:
        claude_cmd = build_command(
            prompt,
            settings_path,
            model=model,
            allowed_tools=allowed_tools_for_repo(repo),
        )
        # THE TOKEN ARRIVES ON STDIN AND TOUCHES NO DISK. it used to be a bind-mounted file, which
        # meant a plaintext credential had to exist under the operator's home directory for as long
        # as the board did. read from stdin it exists only in the container's memory.
        # `read` consumes exactly the first line, then stdin is redirected from /dev/null for the
        # run itself - without that redirect `claude -p` waits 3s for input it will never get (S1).
        # still never `-e`/`--env`, so `docker inspect` shows nothing either.
        inner = (
            "IFS= read -r CLAUDE_CODE_OAUTH_TOKEN && export CLAUDE_CODE_OAUTH_TOKEN && "
            + shlex.join(claude_cmd)
            + " < /dev/null"
        )
        return [
            "docker",
            "run",
            "--rm",
            "-i",  # stdin stays open exactly long enough to hand the token over
            "-v",
            f"{clone_path}:{_CONTAINER_WORKDIR}:rw",
            "-w",
            _CONTAINER_WORKDIR,
            self.image,
            "sh",
            "-c",
            inner,
        ]

    def _fetch_back(self, repo_root: Path, clone_path: Path, branch: str) -> None:
        # the clone is on the host, so pull the card's commits back into the repo that owns them;
        # the board pushes from there - nothing is ever pushed from inside the container
        # --update-head-ok: the branch is checked out in the card's own worktree, which git
        # otherwise refuses to fetch into directly - safe here since that worktree is destroyed
        # once the card's run is reviewed, never used mid-run
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "fetch",
                "--update-head-ok",
                str(clone_path),
                f"{branch}:{branch}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise WorktreeError(f"fetching card commits back failed: {result.stderr.strip()}")


class CardRuntimeUnavailable(RuntimeError):
    """docker or the card credential is missing, so no card can run.

    THERE IS NO FALLBACK ON PURPOSE. running the card as a plain subprocess would put an agent that
    may have read untrusted input on the operator's own filesystem, with their own credential and
    their own GitHub access - the exact thing the container exists to prevent. A board that quietly
    degraded would be claiming an isolation it no longer had, so it refuses instead and says how to
    fix it.
    """


def require_card_runtime(token_path: str | Path | None = None) -> ContainerBackend:
    """the one way a card runs. raises CardRuntimeUnavailable with instructions if it cannot."""
    problems = []
    if not docker_available():
        problems.append(
            "Docker is not running or not installed. smortboard runs every card in its own "
            "container; install Docker Desktop and start it."
        )
    resolved = Path(token_path or card_token_path())
    if not resolved.is_file():
        problems.append(
            f"No card credential at {resolved}. Run `claude setup-token` and write the token "
            f"there - a card-scoped credential, so it can be revoked without touching your own "
            f"session."
        )
    if problems:
        raise CardRuntimeUnavailable("  " + "\n  ".join(problems))
    return ContainerBackend()
