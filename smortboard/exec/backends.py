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
import sys
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from smortboard.exec.leases import write_lease_settings
from smortboard.exec.runner import (
    HEADLESS_RULES,
    SYSTEM_PROMPT,
    ProcessHandle,
    RunResult,
    allowed_tools_for_repo,
    build_command,
    commands_preamble,
    lease_preamble,
    run_process,
)
from smortboard.exec.worktrees import (
    WorktreeError,
    current_branch,
    repo_lock,
    repo_root_of_worktree,
)
from smortboard.prompts import active_prompt
from smortboard.store.api import Store
from smortboard.store.errors import NotFoundError

# defaults, overridable per deployment - never the credential itself, which is never an env var
DEFAULT_CARD_IMAGE = "smortboard-card:latest"

CARD_IMAGE_ENV = "SMORTBOARD_CARD_IMAGE"
CARD_TOKEN_PATH_ENV = "SMORTBOARD_CARD_TOKEN_PATH"

# where the token lands inside the container - read-only, never passed as an env var so it never
# shows up in `docker inspect`
_KEYCHAIN_SERVICE = "smortboard-card-token"
_KEYCHAIN_USER = "smortboard"
_CONTAINER_WORKDIR = "/workspace"
# told up front: a real run spent two tool calls looking for its files under /home/user/repo
WORKSPACE_PREAMBLE = (
    f"Your working directory is {_CONTAINER_WORKDIR}, the repository root. "
    f"Give file tools absolute paths under {_CONTAINER_WORKDIR}.\n\n"
)
# a card's guards (settings, lease, hooks) are mounted read-only here, outside /workspace, so the
# agent can neither edit its own guard nor sweep it into a commit
CONTAINER_GUARD_DIR = "/smortboard"
# the guard hooks' interpreter inside the card image - docker/card.Dockerfile installs it
CONTAINER_PYTHON = "python3"
# who authors a card's commits: one fixed identity, set by the board in every clone
CARD_GIT_NAME = "smortboard"
CARD_GIT_EMAIL = "smortboard@localhost"


def card_image() -> str:
    return os.environ.get(CARD_IMAGE_ENV, DEFAULT_CARD_IMAGE)


def container_name(role: str, card_id: str) -> str:
    """a deterministic, unique name for one container this run starts.

    Stop needs an exact handle on the container, not just its docker client process - SIGTERM on
    the client does not reliably stop what it launched. Named per run so `docker rm -f <name>`
    always targets exactly one container, never guesses.
    """
    return f"smortboard-{role}-{card_id[:8]}-{uuid.uuid4().hex[:8]}"


def write_container_guards(worktree_path: str | Path, path_globs: list[str]) -> Path:
    """the card's lease and bash guards, written with the paths a card container sees"""
    return write_lease_settings(
        worktree_path,
        path_globs,
        python=CONTAINER_PYTHON,
        guard_dir=CONTAINER_GUARD_DIR,
        root=_CONTAINER_WORKDIR,
    )


def guard_mount(settings_path: str | Path) -> tuple[list[str], str]:
    """docker args mounting the guard dir read-only, and the `--settings` path as seen inside.

    The first real card run crashed without this: the host path went to `--settings` unchanged,
    and inside the container it did not exist.
    """
    settings_path = Path(settings_path)
    return (
        ["-v", f"{settings_path.parent}:{CONTAINER_GUARD_DIR}:ro"],
        f"{CONTAINER_GUARD_DIR}/{settings_path.name}",
    )


class CardTokenMissing(RuntimeError):
    """no card credential in the keychain or on disk"""


def card_token_path() -> Path:
    """where a card token lives when it is a file rather than a keychain entry"""
    return Path(os.environ.get(CARD_TOKEN_PATH_ENV) or _default_token_file())


def _default_token_file() -> Path:
    """where the token file lives, per platform - the first place the board looks.

    APPDATA on Windows, XDG_CONFIG_HOME or ~/.config elsewhere. The OS credential store is only
    the fallback when this file is absent.
    """
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "smortboard" / "card_token"


def _credential_store_token() -> str | None:
    """the OS credential store: Keychain on macOS, Credential Manager on Windows, Secret Service on
    Linux. One API for all three, which is why this is `keyring` rather than shelling out to
    `security` - that only ever worked on macOS.

    Returns None when there is no usable backend, which is normal on a headless Linux box. Only
    reached when there is no token file - on macOS every read here raises a keychain prompt.
    """
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError:
        return None
    try:
        return keyring.get_password(_KEYCHAIN_SERVICE, _KEYCHAIN_USER) or None
    except KeyringError:
        return None


def _store_instructions(path: Path) -> str:
    """how to save the token on the platform actually in use, rather than on mine"""
    if os.name == "nt":
        file_note = f"  write it to {path} - your user profile directory already restricts it"
        store = "or cmdkey, or Windows Credential Manager, under the name above"
    elif sys.platform == "darwin":
        file_note = f"  write it to {path} with mode 600 (owner read and write, nobody else)"
        store = (
            f"or security add-generic-password -s {_KEYCHAIN_SERVICE} -a {_KEYCHAIN_USER} -w"
            " - but macOS then prompts on every read"
        )
    else:
        file_note = f"  write it to {path} with mode 600 (owner read and write, nobody else)"
        store = f"or secret-tool store --label=smortboard service {_KEYCHAIN_SERVICE} username {_KEYCHAIN_USER}"
    return f"{file_note}\n{store}"


def read_card_token(token_path: str | Path | None = None) -> str:
    """the card credential, a mode-600 file first, then the OS credential store.

    FILE FIRST since 2026-09-10: on macOS every keyring read raised a keychain prompt, and one
    ruined a screen recording. With the file present the keychain is never touched.

    NEVER returned to anywhere it could be logged: the one caller writes it straight to the
    container's stdin. It is not stored on the backend and not put in the environment.
    """
    resolved = Path(token_path or card_token_path())
    if resolved.is_file():
        token = resolved.read_text().strip()
        if token:
            return token
    # an explicit path is the whole answer, so callers that name one never reach the keychain
    if token_path is None:
        from_keychain = _credential_store_token()
        if from_keychain:
            return from_keychain
    raise CardTokenMissing(
        "No card credential. Run `claude setup-token`, then either\n"
        + _store_instructions(resolved)
    )


def card_token_available(token_path: str | Path | None = None) -> bool:
    try:
        read_card_token(token_path)
    except CardTokenMissing:
        return False
    return True


# `docker version --format {{.Server.Version}}`, NOT `docker info`. Both prove a daemon answered -
# the Server field is empty unless one did - but measured on a healthy Docker Desktop, `info` took
# 4.9s against its own 5s timeout and reported Docker as down about half the time, which made the
# board refuse every card. The version probe answers the same question in 0.3s.
_DOCKER_PROBE = ["docker", "version", "--format", "{{.Server.Version}}"]
_DOCKER_PROBE_TIMEOUT = 10


def docker_available() -> bool:
    """true if `docker` is on PATH and a daemon actually answers - not just installed but unusable"""
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            _DOCKER_PROBE,
            capture_output=True,
            text=True,
            timeout=_DOCKER_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def card_image_available(image: str | None = None) -> bool:
    """whether the image a card would run in exists locally.

    Nothing builds it for you, and without this the failure is an opaque CRASH minutes in: docker
    says "Unable to find image", the container never starts, and the card looks like it broke.
    """
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image or card_image()],
            capture_output=True,
            timeout=_DOCKER_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _current_repo(store: Store | None, repo: dict[str, Any] | None) -> dict[str, Any] | None:
    """the repo row fresh off the store, not whatever the caller happened to be holding.

    mirrors review.gates._current_repo (duplicated rather than imported: gates.py already imports
    from this module, so importing back would be circular) - a card on the fix route calls
    run_card again for every round, and an operator's edit to test_command between rounds must
    reach the very next one. falls back to the given `repo` when there is no store or id to
    re-read by (tests hand in a bare dict, or no repo at all).
    """
    repo_id = (repo or {}).get("id")
    if store is None or not repo_id:
        return repo
    try:
        return store.get_repo(repo_id)
    except NotFoundError:
        return repo


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
        pending_notes: Callable[[], list[dict[str, Any]]] | None = None,
        on_process: Callable[[ProcessHandle], None] | None = None,
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
        pending_notes: Callable[[], list[dict[str, Any]]] | None = None,
        on_process: Callable[[ProcessHandle], None] | None = None,
    ) -> RunResult:
        # re-read, not the repo dict the caller is holding: a card's fix rounds all run through
        # this one method, and an operator's edit to test_command between rounds must scope the
        # very next round's Bash allowlist and brief, not only a card started after the edit
        repo = _current_repo(store, repo)
        token = read_card_token(token_path)
        clone_path = Path(tempfile.mkdtemp(prefix=f"smortboard-card-{uuid.uuid4().hex[:8]}-"))
        repo_root = repo_root_of_worktree(worktree_path)
        branch = current_branch(worktree_path)
        try:
            self._clone(worktree_path, clone_path, branch)
            # the card's own lease, prepended to its brief: the backend has the store and the id,
            # so no caller has to remember to pass what is already recorded
            # lease ROWS, not strings: get_card returns dicts, and handing those straight to
            # lease_preamble listed python dicts at the agent instead of paths
            lease_rows = store.get_card(card_id).get("leases") if store else None
            leases = [row["path_glob"] for row in lease_rows or []]
            brief = WORKSPACE_PREAMBLE + lease_preamble(leases) + commands_preamble(repo) + prompt
            name = container_name("worker", card_id)
            cmd = self._docker_command(clone_path, brief, settings_path, model, repo, store, name)
            # the token is a plain first line the container's shell consumes with `read -r`; the
            # brief follows as the first stream-json turn, and stdin stays open for live steering
            result = run_process(
                store,
                card_id,
                cmd,
                cwd=clone_path,
                token_line=token + "\n",
                stream_prompt=brief,
                pending_notes=pending_notes,
                container_name=name,
                on_process=on_process,
            )
            self._fetch_back(repo_root, clone_path, branch, worktree_path)
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
        # the image has no git identity, so a card's first commit failed and the agent invented
        # one - the board chooses it instead, in the clone the container mounts
        for key, value in (("user.name", CARD_GIT_NAME), ("user.email", CARD_GIT_EMAIL)):
            subprocess.run(["git", "-C", str(clone_path), "config", key, value], check=True)

    def _image_for(self, repo: dict[str, Any] | None) -> str:
        """the repo's own image if it declares one, else the default.

        SHARED TOOLS, ISOLATED CARDS: one image per repo carries the toolchain already installed, so
        a fresh container per card costs a second rather than a dependency install. the isolation is
        unchanged - the image is read-only and every card still gets its own container and clone.
        """
        if repo and repo.get("image"):
            return str(repo["image"])
        return self.image

    def _docker_command(
        self,
        clone_path: Path,
        prompt: str,
        settings_path: str | Path,
        model: str,
        repo: dict[str, Any] | None,
        store: Store | None = None,
        name: str | None = None,
    ) -> list[str]:
        mount, inner_settings = guard_mount(settings_path)
        claude_cmd = build_command(
            prompt,
            inner_settings,
            model=model,
            allowed_tools=allowed_tools_for_repo(repo),
            system_prompt=active_prompt(store, "worker", SYSTEM_PROMPT) + HEADLESS_RULES,
            stream_input=True,
        )
        # THE TOKEN ARRIVES ON STDIN AND TOUCHES NO DISK INSIDE THE CONTAINER. the host's token
        # file, if there is one, is never mounted - read from stdin the token exists only in the
        # container's memory.
        # `read` consumes exactly the first line; everything after it is still the same stdin fd,
        # so it passes straight through to `claude` - which is what live steering needs. NO
        # `< /dev/null` HERE ANY MORE: that redirect would close off the stream-json turns that
        # follow the token. still never `-e`/`--env`, so `docker inspect` shows nothing either.
        inner = (
            "IFS= read -r CLAUDE_CODE_OAUTH_TOKEN && export CLAUDE_CODE_OAUTH_TOKEN && "
            + shlex.join(claude_cmd)
        )
        return [
            "docker",
            "run",
            "--rm",
            "-i",  # stdin stays open exactly long enough to hand the token over
            *(["--name", name] if name else []),
            "-v",
            f"{clone_path}:{_CONTAINER_WORKDIR}:rw",
            *mount,
            "-w",
            _CONTAINER_WORKDIR,
            self._image_for(repo),
            "sh",
            "-c",
            inner,
        ]

    def _fetch_back(
        self, repo_root: Path, clone_path: Path, branch: str, worktree_path: str | Path
    ) -> None:
        # the clone is on the host, so pull the card's commits back into the repo that owns them;
        # the board pushes from there - nothing is ever pushed from inside the container
        # --update-head-ok: the branch is checked out in the card's own worktree
        with repo_lock(repo_root):
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
        # the fetch moves the branch but not the worktree's files, and the test gate and reviewer
        # read those files next - run 3's gate would have tested the code before the card's commit
        synced = subprocess.run(
            ["git", "-C", str(worktree_path), "reset", "-q", "--hard"],
            capture_output=True,
            text=True,
            check=False,
        )
        if synced.returncode != 0:
            raise WorktreeError(f"syncing the card's worktree failed: {synced.stderr.strip()}")


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
    # card_token_available, not is_file: the credential normally lives in the OS credential store
    # and never becomes a file at all, so checking for the file refused a board that was ready
    if not card_token_available(token_path):
        resolved = Path(token_path or card_token_path())
        problems.append(
            "No card credential. Run `claude setup-token`, then either\n"
            + _store_instructions(resolved)
        )
    if problems:
        raise CardRuntimeUnavailable("  " + "\n  ".join(problems))
    return ContainerBackend()
