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
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol

from smortboard.exec.leases import changed_paths_outside_lease, write_lease_settings
from smortboard.exec.runner import (
    DEFAULT_CARD_BUDGET_USD,
    HEADLESS_RULES,
    SYSTEM_PROMPT,
    ProcessHandle,
    RunResult,
    allowed_tools_for_repo,
    commands_preamble,
    lease_preamble,
    new_note_marker,
    note_marker_paragraph,
    run_process,
)
from smortboard.exec.worktrees import (
    WorktreeError,
    current_branch,
    default_branch,
    repo_lock,
    repo_root_of_worktree,
    rev_parse,
)
from smortboard.labs.base import BashPolicy, RunRequest
from smortboard.labs.catalog import parse_ref
from smortboard.labs.registry import get_adapter
from smortboard.prompts import active_prompt
from smortboard.store.api import Store
from smortboard.store.errors import NotFoundError

# defaults, overridable per deployment - never the credential itself, which is never an env var
DEFAULT_CARD_IMAGE = "smortboard-card:latest"

CARD_IMAGE_ENV = "SMORTBOARD_CARD_IMAGE"
CARD_TOKEN_PATH_ENV = "SMORTBOARD_CARD_TOKEN_PATH"

# where the token lands inside the container - read-only, never passed as an env var so it never
# shows up in `docker inspect`
_CONTAINER_WORKDIR = "/workspace"
# told up front: a real run spent two tool calls looking for its files under /home/user/repo
WORKSPACE_PREAMBLE = (
    f"Your working directory is {_CONTAINER_WORKDIR}, the repository root. "
    f"Give file tools absolute paths under {_CONTAINER_WORKDIR}.\n\n"
)
# container hardening applied to every card/gate/reviewer/orchestrator `docker run` - cheap
# defense-in-depth on top of the `--rm`, no-socket, no-host-mount containment already in place
CONTAINER_HARDENING_FLAGS = [
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
    "--pids-limit=512",
    "--memory=4g",
]

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


def write_container_guards(
    out_dir: str | Path, path_globs: list[str], remembered_globs: list[str] | None = None
) -> Path:
    """the card's lease and bash guards, written into `out_dir` with the paths a card container
    sees. `out_dir` is the board's own directory for this attempt, never the worktree: guard_mount
    puts it at /smortboard read-only, and the test gate, which mounts the worktree, never sees it.

    `remembered_globs` is the repo's remembered lease list - the post-run committed-path check
    already allowed it, so the hook refusing it asked the operator twice for the same path.
    """
    settings = write_lease_settings(
        out_dir,
        path_globs,
        root=_CONTAINER_WORKDIR,
        remembered_globs=remembered_globs,
        python=CONTAINER_PYTHON,
        guard_dir=CONTAINER_GUARD_DIR,
    )
    # the card image runs as a non-root uid that rarely owns a host temp dir; read, never write
    Path(out_dir).chmod(0o755)
    return settings


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
    """no usable card credential file"""


def card_token_path() -> Path:
    """the configured card credential file"""
    return Path(os.environ.get(CARD_TOKEN_PATH_ENV) or _default_token_file())


def config_base() -> Path:
    """the platform's config root - APPDATA on Windows, XDG_CONFIG_HOME or ~/.config elsewhere.

    shared with smortboard/profiles.py so a profile's token file and the legacy card_token file
    live under the same tree; kept here since this is where that tree was first decided.
    """
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def _default_token_file() -> Path:
    """the default credential file for this platform"""
    return config_base() / "smortboard" / "card_token"


def _store_instructions(path: Path) -> str:
    """how to save the token on the platform actually in use, rather than on mine"""
    if os.name == "nt":
        return f"  write it to {path} - your user profile directory already restricts it"
    return f"  write it to {path} with mode 600 (owner read and write, nobody else)"


def read_card_token(token_path: str | Path | None = None) -> str:
    """the card credential from a mode-600 file.

    NEVER returned to anywhere it could be logged: the one caller writes it straight to the
    container's stdin. It is not stored on the backend and not put in the environment.
    """
    resolved = Path(token_path or card_token_path())
    if resolved.is_file():
        # group/world readable token files are refused everywhere, not just for named profiles
        if os.name != "nt" and (resolved.stat().st_mode & 0o077) != 0:
            raise CardTokenMissing(
                f"{resolved} is not mode 600 - refusing to read it. chmod 600 {resolved}"
            )
        token = resolved.read_text().strip()
        if token:
            return token
    raise CardTokenMissing(
        "No card credential. Run `claude setup-token`, then\n" + _store_instructions(resolved)
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
        from smortboard import profiles

        lab, model_id = parse_ref(model)
        adapter = get_adapter(lab)
        profile = profiles.active_profile(lab=lab)
        kind = profiles.profile_kind(profile, lab=lab)
        token = (
            read_card_token(profiles.token_path_for_profile(profile, token_path))
            if lab == "anthropic"
            else profiles.read_profile_token(lab, profile)
        )
        clone_path = Path(tempfile.mkdtemp(prefix=f"smortboard-card-{uuid.uuid4().hex[:8]}-"))
        repo_root = repo_root_of_worktree(worktree_path)
        branch = current_branch(worktree_path)
        try:
            self._clone(worktree_path, clone_path, branch)
            self._expose_upstream_base(clone_path, default_branch(repo or {}))
            if store is not None and repo is not None:
                # prior work and base merges are already in the clone
                start_commit = subprocess.run(
                    ["git", "-C", str(clone_path), "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
                previous = next(
                    (
                        event
                        for event in reversed(store.list_events(card_id))
                        if event["kind"] in {"lease_check_started", "lease_check_finished"}
                    ),
                    None,
                )
                # retrying must not forgive unchecked or previously rejected changes
                if previous and not previous["payload"].get("passed"):
                    start_commit = previous["payload"]["base_commit"]
                store.append_event(card_id, "lease_check_started", {"base_commit": start_commit})
            # the card image now runs as a non-root uid (docker/card.Dockerfile), which rarely
            # matches the host uid that owns this tempdir - open it up so the container can still
            # write its commits into a mount it does not otherwise share ownership with
            subprocess.run(["chmod", "-R", "go+rwX", str(clone_path)], check=True)
            # the card's own lease, prepended to its brief: the backend has the store and the id,
            # so no caller has to remember to pass what is already recorded
            # lease ROWS, not strings: get_card returns dicts, and handing those straight to
            # lease_preamble listed python dicts at the agent instead of paths
            lease_rows = store.get_card(card_id).get("leases") if store else None
            leases = [row["path_glob"] for row in lease_rows or []]
            if not adapter.capabilities.tool_allowlist:
                bash_allow = tuple(
                    tool[5:-1] for tool in allowed_tools_for_repo(repo) if tool.startswith("Bash(")
                )
                # beside the attempt's own guards, which guard_mount puts at /smortboard
                guards = adapter.guard_files(
                    leases,
                    BashPolicy(
                        out_dir=Path(settings_path).parent,
                        python=CONTAINER_PYTHON,
                        guard_dir=CONTAINER_GUARD_DIR,
                        root=_CONTAINER_WORKDIR,
                        bash_allow=bash_allow,
                        remembered_globs=[
                            row["path_glob"] for row in (repo or {}).get("remembered_leases", [])
                        ],
                    ),
                )
                settings_path = guards.settings_path
            brief = WORKSPACE_PREAMBLE + lease_preamble(leases) + commands_preamble(repo) + prompt
            name = container_name("worker", card_id)
            # minted once per run: the same nonce goes into the system prompt (so the agent knows
            # what a genuine note looks like) and into the feeder (so that's what a real note
            # carries) - see F4, a fixed marker is guessable from anything the worker reads
            note_marker = new_note_marker()
            cmd = self._docker_command(
                clone_path, brief, settings_path, model, repo, store, note_marker, name, kind
            )
            # the token is a plain first line the container's shell consumes with `read -r`; the
            # brief follows as the first stream-json turn, and stdin stays open for live steering
            result = run_process(
                store,
                card_id,
                cmd,
                cwd=clone_path,
                token_line=token + "\n" if adapter.capabilities.live_steering else None,
                stream_prompt=brief if adapter.capabilities.live_steering else None,
                stdin_text=None if adapter.capabilities.live_steering else token + "\n",
                pending_notes=pending_notes,
                note_marker=note_marker,
                container_name=name,
                on_process=on_process,
                adapter=adapter,
                lab=lab,
                model=model_id,
                profile=profile,
                budget_usd=store.spend_cap("worker_budget_usd", DEFAULT_CARD_BUDGET_USD)
                if store
                else DEFAULT_CARD_BUDGET_USD,
            )
            self._fetch_back(repo_root, clone_path, branch, worktree_path)
            if store is not None and repo is not None:
                remembered = [row["path_glob"] for row in repo.get("remembered_leases", [])]
                base_name = default_branch(repo)
                base_ref = next(
                    (
                        ref
                        for ref in (f"origin/{base_name}", base_name)
                        if rev_parse(repo_root, ref)
                    ),
                    None,
                )
                outside = changed_paths_outside_lease(
                    repo_root, start_commit, branch, leases + remembered, base_ref=base_ref
                )
                store.append_event(
                    card_id,
                    "lease_check_finished",
                    {
                        "base_commit": start_commit,
                        "passed": not outside,
                        "outside": outside,
                    },
                )
                if outside:
                    result = replace(
                        result,
                        subtype="error_lease_conflict",
                        is_error=True,
                        blocked_reason_code="LEASE_CONFLICT",
                        result_text="Committed paths outside the lease:\n" + "\n".join(outside),
                    )
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

    def _expose_upstream_base(self, clone_path: Path, base: str) -> None:
        """makes `origin/<base>` inside the clone the repo's fetched upstream, not its local branch.

        The clone's origin is the worktree, so a plain clone maps origin/<base> to the repo's LOCAL
        <base> - which is behind whenever nobody pulled. A worker told to "merge origin/<base>"
        then got "Already up to date", resolved nothing, and the card blocked MERGE_CONFLICT again
        at handover, forever. Best effort: no upstream ref (a local-only repo) leaves it as is.
        """
        subprocess.run(
            [
                "git",
                "-C",
                str(clone_path),
                "fetch",
                "-q",
                "origin",
                f"+refs/remotes/origin/{base}:refs/remotes/origin/{base}",
            ],
            capture_output=True,
            check=False,
        )

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
        note_marker: str | None = None,
        name: str | None = None,
        kind: str | None = None,
    ) -> list[str]:
        lab, model_id = parse_ref(model)
        adapter = get_adapter(lab)
        mount, inner_settings = guard_mount(settings_path)
        agent_cmd = adapter.build_command(
            RunRequest(
                prompt=prompt,
                settings_path=inner_settings,
                model=model_id,
                allowed_tools=allowed_tools_for_repo(repo),
                budget_usd=(
                    store.spend_cap("worker_budget_usd", DEFAULT_CARD_BUDGET_USD)
                    if store is not None
                    else DEFAULT_CARD_BUDGET_USD
                ),
                system_prompt=active_prompt(store, "worker", SYSTEM_PROMPT)
                + HEADLESS_RULES
                + note_marker_paragraph(note_marker or new_note_marker()),
                stream_input=adapter.capabilities.live_steering,
            )
        )
        # THE TOKEN ARRIVES ON STDIN AND TOUCHES NO DISK INSIDE THE CONTAINER. the host's token
        # file, if there is one, is never mounted - read from stdin the token exists only in the
        # container's memory.
        # `read` consumes exactly the first line; everything after it is still the same stdin fd,
        # so it passes straight through to `claude` - which is what live steering needs. NO
        # `< /dev/null` HERE ANY MORE: that redirect would close off the stream-json turns that
        # follow the token. still never `-e`/`--env`, so `docker inspect` shows nothing either.
        inner = adapter.auth_shell({"kind": kind}) + shlex.join(agent_cmd)
        return [
            "docker",
            "run",
            "--rm",
            "-i",  # stdin stays open exactly long enough to hand the token over
            *(["--name", name] if name else []),
            *CONTAINER_HARDENING_FLAGS,
            *adapter.container_env(),
            "-v",
            f"{clone_path}:{_CONTAINER_WORKDIR}:rw",
            *mount,
            *adapter.guard_mounts(settings_path),
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


def require_card_runtime(
    token_path: str | Path | None = None, lab: str = "anthropic"
) -> ContainerBackend:
    """the one way a card runs. raises CardRuntimeUnavailable with instructions if it cannot."""
    from smortboard import profiles

    problems = []
    if not docker_available():
        problems.append(
            "Docker is not running or not installed. smortboard runs every card in its own "
            "container; install Docker Desktop and start it."
        )
    try:
        if lab == "anthropic":
            read_card_token(profiles.token_path_for_run(token_path, lab=lab))
        else:
            profiles.read_profile_token(lab, profiles.active_profile(lab=lab))
    except (CardTokenMissing, profiles.ProfileError) as exc:
        problems.append(str(exc))
    if problems:
        raise CardRuntimeUnavailable("  " + "\n  ".join(problems))
    return ContainerBackend()
