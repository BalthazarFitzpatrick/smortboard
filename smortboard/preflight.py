"""GET /api/preflight: everything that has to be true before a card can run, and the exact fix.

Readiness (smortboard/server/runs.py, behind /api/runtime) answers "can I run a card right now" with
three booleans. This answers "what do I do about it" with one row per thing, so it reuses backends'
docker/token probes as the source of truth rather than recomputing them - it only widens each into a
row with a fix. /api/runtime's response is untouched.
"""

import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from smortboard import profiles
from smortboard.exec.backends import (
    CardTokenMissing,
    card_image,
    card_image_available,
    card_token_available,
    card_token_path,
    docker_available,
    read_card_token,
)
from smortboard.repo_tests import detect_tests
from smortboard.store.api import Store

_TIMEOUT = 10
# a full token is 108 bytes; well short of that is a copy that dropped characters
_MIN_TOKEN_BYTES = 80
_GITHUB_URL_RE = re.compile(r"github\.com[:/]+(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")


class CommandRunner(Protocol):
    def __call__(
        self, cmd: list[str], timeout: float = ..., cwd: str | None = ...
    ) -> subprocess.CompletedProcess: ...


def default_runner(
    cmd: list[str], timeout: float = _TIMEOUT, cwd: str | None = None
) -> subprocess.CompletedProcess:
    """every docker/gh/git call in this module goes through here, so tests can fake it."""
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr=str(exc))


def _check(
    check_id: str, group: str, label: str, status: str, detail: str, fix: str = ""
) -> dict[str, Any]:
    return {
        "id": check_id,
        "group": group,
        "label": label,
        "status": status,
        "detail": detail,
        "fix": fix,
    }


def _owner_repo_from_url(url: str) -> str | None:
    match = _GITHUB_URL_RE.search(url)
    return f"{match.group('owner')}/{match.group('repo')}" if match else None


# ---- machine checks ------------------------------------------------------------------------


def _docker_check() -> dict[str, Any]:
    if shutil.which("docker") is None:
        return _check(
            "docker",
            "machine",
            "docker",
            "fail",
            "docker is not installed.",
            "install docker desktop (or the docker engine), then re-check.",
        )
    if not docker_available():
        return _check(
            "docker",
            "machine",
            "docker",
            "fail",
            "docker is installed but the daemon is not answering.",
            "start docker desktop (or `sudo systemctl start docker`), then re-check.",
        )
    return _check(
        "docker", "machine", "docker", "ok", "docker is installed and the daemon answers."
    )


def _image_check() -> dict[str, Any]:
    image = card_image()
    if not docker_available():
        return _check(
            "card-image",
            "machine",
            "card image",
            "fail",
            f"cannot check for {image}: docker is not reachable.",
            "fix docker first, then re-check.",
        )
    if card_image_available():
        return _check("card-image", "machine", "card image", "ok", f"{image} is present.")
    return _check(
        "card-image",
        "machine",
        "card image",
        "fail",
        f"no image named {image}.",
        f"docker build -f docker/card.Dockerfile -t {image} .",
    )


def _token_check(token_path: str | Path | None) -> dict[str, Any]:
    path = Path(token_path) if token_path else card_token_path()
    if not path.is_file():
        return _check(
            "card-token",
            "machine",
            "card token",
            "fail",
            f"no token file at {path}.",
            "run `claude setup-token`, then in shift+p add a profile, paste it and activate it.",
        )
    if not card_token_available(token_path):
        # surface the mode-600 refusal verbatim when that's the cause - it already names the fix
        try:
            read_card_token(token_path)
        except CardTokenMissing as exc:
            if "chmod 600" in str(exc):
                return _check("card-token", "machine", "card token", "fail", str(exc), str(exc))
        return _check(
            "card-token",
            "machine",
            "card token",
            "fail",
            f"a file exists at {path} but no usable token could be read from it.",
            "run `claude setup-token`, then in shift+p add it as a new profile, activate it, "
            "and remove this one.",
        )
    stat = path.stat()
    mode = stat.st_mode & 0o777
    problems: list[str] = []
    fixes: list[str] = []
    if mode != 0o600:
        problems.append(f"mode is {oct(mode)}, not 600.")
        fixes.append(f"chmod 600 {path}")
    if stat.st_size < _MIN_TOKEN_BYTES:
        problems.append(f"the file is only {stat.st_size} bytes; a full token is 108.")
        fixes.append(
            "it looks truncated: run `claude setup-token`, then in shift+p add it as a new "
            "profile, activate it, and remove this one."
        )
    if problems:
        return _check(
            "card-token", "machine", "card token", "warn", " ".join(problems), " && ".join(fixes)
        )
    return _check(
        "card-token", "machine", "card token", "ok", f"a token file is present at {path}."
    )


def _profile_checks() -> list[dict[str, Any]]:
    """one row per configured credential profile - present, mode 600, rate-limited right now.

    Reuses profiles.list_profiles() rather than re-probing the filesystem itself, same reason the
    rest of this module reuses backends' probes: one source of truth for what "ok" means.
    """
    rows = []
    for row in profiles.list_all_profiles():
        lab = row["lab"]
        label = f"{lab} profile: {row['name']}" + (" (active)" if row["active"] else "")
        check_id = (
            f"profile-{row['name']}" if lab == "anthropic" else f"profile-{lab}-{row['name']}"
        )
        setup = (
            "claude setup-token"
            if lab == "anthropic"
            else "codex login, which writes ~/.codex/auth.json (paste all of it)"
            if row["kind"] == "auth_json"
            else f"codex login --with-{row['kind'].replace('_', '-')}"
        )
        if not row["present"]:
            rows.append(
                _check(
                    check_id,
                    "machine",
                    label,
                    "fail",
                    f"no token file at {row['path']}.",
                    f"{setup}, then in shift+p add it as a new profile, activate it, "
                    "and remove this one.",
                )
            )
        elif not row["mode_ok"]:
            rows.append(
                _check(
                    check_id,
                    "machine",
                    label,
                    "fail",
                    f"mode is not 600 at {row['path']}.",
                    f"chmod 600 {row['path']}",
                )
            )
        elif row["limited_now"]:
            rows.append(
                _check(
                    check_id,
                    "machine",
                    label,
                    "warn",
                    f"rate-limited until {row['limited_until']}.",
                )
            )
        else:
            try:
                profiles.read_profile_token(lab, row["name"])
            except profiles.ProfileError as exc:
                rows.append(_check(check_id, "machine", label, "fail", str(exc), setup))
            else:
                rows.append(
                    _check(check_id, "machine", label, "ok", f"present at {row['path']}, mode 600.")
                )
        rows[-1].update(lab=lab, kind=row["kind"], profile=row["name"])
    return rows


def _gh_check(run: CommandRunner) -> dict[str, Any]:
    if shutil.which("gh") is None:
        return _check(
            "gh",
            "machine",
            "gh cli",
            "fail",
            "gh is not installed.",
            "install the github cli (https://cli.github.com), then `gh auth login`.",
        )
    result = run(["gh", "auth", "status"])
    if result.returncode != 0:
        return _check(
            "gh",
            "machine",
            "gh cli",
            "fail",
            "gh is installed but not authenticated.",
            "gh auth login",
        )
    return _check("gh", "machine", "gh cli", "ok", "gh is installed and authenticated.")


def _lab_checks(store: Store, run: CommandRunner) -> list[dict[str, Any]]:
    """configured additional labs need a CLI in every runnable image and usable cost data"""
    from smortboard.labs.base import RunRequest
    from smortboard.labs.catalog import load_catalog
    from smortboard.labs.registry import get_adapter

    catalog = load_catalog()
    rows = []
    images = {card_image()}
    for board in store.list_boards():
        images.update(repo["image"] for repo in store.list_repos(board["id"]) if repo.get("image"))
    for lab in profiles.configured_labs():
        adapter = get_adapter(lab)
        if lab != "anthropic":
            executable = adapter.build_command(RunRequest(prompt="", budget_usd=None))[0]
            for index, image in enumerate(sorted(images)):
                ready = docker_available()
                result = (
                    run(
                        [
                            "docker",
                            "run",
                            "--rm",
                            "--network",
                            "none",
                            "--entrypoint",
                            executable,
                            image,
                            "--version",
                        ]
                    )
                    if ready
                    else None
                )
                present = result is not None and result.returncode == 0
                rows.append(
                    _check(
                        f"lab-{lab}-image-{index}",
                        "machine",
                        f"{lab} CLI in {image}",
                        "ok" if present else "fail",
                        f"{executable} is available in {image}."
                        if present
                        else f"{image} cannot run {executable}."
                        if ready
                        else f"cannot check {image}: docker is not reachable.",
                        ""
                        if present
                        else f"rebuild {image} from docker/card.Dockerfile with {executable} installed.",
                    )
                )
                rows[-1]["lab"] = lab
        if not adapter.capabilities.reports_cost_usd:
            missing = [
                row["id"]
                for row in catalog.get(lab, {}).get("models", [])
                if not all(
                    key in (row.get("price_per_mtok") or {})
                    for key in ("input", "cached_input", "output")
                )
            ]
            if missing:
                rows.append(
                    _check(
                        f"lab-{lab}-pricing",
                        "machine",
                        f"{lab} cost estimates",
                        "warn",
                        f"cost is unknown for {', '.join(missing)}; budgets cannot be enforced for these models.",
                        "set input, cached_input and output price_per_mtok in ~/.config/smortboard/catalog.json.",
                    )
                )
                rows[-1]["lab"] = lab
    return rows


def _git_check() -> dict[str, Any]:
    if shutil.which("git") is None:
        return _check(
            "git",
            "machine",
            "git",
            "fail",
            "git is not installed.",
            "install git for your platform, then re-check.",
        )
    return _check("git", "machine", "git", "ok", "git is installed.")


# ---- per-repo checks ------------------------------------------------------------------------


def _repo_checks(repo: dict[str, Any], run: CommandRunner) -> list[dict[str, Any]]:
    name = repo["name"]
    path = Path(repo["path"])

    def row(check_id: str, label: str, status: str, detail: str, fix: str = "") -> dict[str, Any]:
        return _check(f"repo-{repo['id']}-{check_id}", name, label, status, detail, fix)

    checks: list[dict[str, Any]] = []
    branch = repo.get("default_branch") or "main"

    if not path.is_dir() or not (path / ".git").exists():
        checks.append(
            row(
                "path",
                "repo path",
                "fail",
                f"{path} does not exist or is not a git repository.",
                f"clone the repo to {path} (git clone <url> {path}), or fix its path on the "
                "board (key b) once the matching github repo exists.",
            )
        )
        checks.append(row("origin", "origin remote", "fail", "not checked: no repo at that path."))
        checks.append(row("branch", "default branch", "fail", "not checked: no repo at that path."))
    else:
        checks.append(row("path", "repo path", "ok", f"{path} is a git repository."))
        local_branch = run(["git", "branch", "--list", branch], cwd=str(path))
        if local_branch.returncode == 0 and local_branch.stdout.strip():
            checks.append(row("branch", "default branch", "ok", f"{branch} exists locally."))
        else:
            checks.append(
                row(
                    "branch",
                    "default branch",
                    "fail",
                    f"no local branch named {branch}.",
                    f"git -C {path} fetch origin {branch} && "
                    f"git -C {path} branch {branch} origin/{branch}",
                )
            )

        remote = run(["git", "remote", "get-url", "origin"], cwd=str(path))
        origin_url = remote.stdout.strip() if remote.returncode == 0 else ""
        if not origin_url:
            checks.append(
                row(
                    "origin",
                    "origin remote",
                    "fail",
                    "no `origin` remote.",
                    f"create the matching repo on github yourself, then: "
                    f"git -C {path} remote add origin git@github.com:<owner>/{name}.git",
                )
            )
        elif "github.com" not in origin_url:
            checks.append(
                row(
                    "origin",
                    "origin remote",
                    "fail",
                    f"origin is {origin_url}, not a github.com remote.",
                    f"git -C {path} remote set-url origin git@github.com:<owner>/{name}.git",
                )
            )
        else:
            checks.append(row("origin", "origin remote", "ok", f"origin is {origin_url}."))

            ls_remote = run(["git", "ls-remote", "--heads", "origin", branch], cwd=str(path))
            if ls_remote.returncode == 0 and ls_remote.stdout.strip():
                checks.append(
                    row(
                        "origin-branch",
                        "default branch on origin",
                        "ok",
                        f"{branch} exists on origin.",
                    )
                )
            else:
                checks.append(
                    row(
                        "origin-branch",
                        "default branch on origin",
                        "fail",
                        f"origin has no {branch} branch - has the github repo been created and pushed to?",
                        f"git -C {path} push -u origin {branch}",
                    )
                )

            owner_repo = _owner_repo_from_url(origin_url) or origin_url
            gh_view = run(["gh", "repo", "view", owner_repo])
            if gh_view.returncode == 0:
                checks.append(
                    row("gh-view", "gh can see the repo", "ok", "gh repo view succeeded.")
                )
            else:
                checks.append(
                    row(
                        "gh-view",
                        "gh can see the repo",
                        "fail",
                        f"gh repo view {owner_repo} failed - gh cannot see this repository.",
                        f"create it yourself: gh repo create {owner_repo} --private --source={path} "
                        "--remote=origin (or fix origin if it points at the wrong repo).",
                    )
                )

    # tests are required: a command with nothing to run fails every card on "no tests ran"
    command = repo.get("test_command")
    tests = detect_tests(str(path), repo["default_branch"])
    if command and tests.has_tests:
        checks.append(row("test-command", "test command", "ok", f"runs `{command}`."))
    elif command:
        checks.append(
            row(
                "test-command",
                "test command",
                "warn",
                f"runs `{command}`, but {repo['default_branch']} has no tests yet.",
                "ask mission control (.) for a first card that adds a test suite, or commit your "
                "own tests - every card's gate needs some to run.",
            )
        )
    else:
        suggestion = (
            f"set `{tests.command}` on the repo (key b) - it fits this repo"
            if tests.command
            else "ask mission control (.) for a first card that sets up tests and names the "
            "command, or set one on the repo (key b)"
        )
        checks.append(
            row(
                "test-command",
                "test command",
                "fail",
                "no test command is set.",
                f"{suggestion}. the test gate cannot run a card's work without one.",
            )
        )

    image = repo.get("image") or card_image()
    if not docker_available():
        checks.append(
            row(
                "image",
                "repo image",
                "fail",
                f"cannot check for {image}: docker is not reachable.",
                "fix docker first, then re-check.",
            )
        )
    else:
        inspect = run(["docker", "image", "inspect", image])
        if inspect.returncode == 0:
            checks.append(row("image", "repo image", "ok", f"{image} is present."))
            checks.append(_image_staleness_check(row, image, path, run, base_image=card_image()))
        else:
            checks.append(
                row(
                    "image",
                    "repo image",
                    "fail",
                    f"no image named {image}.",
                    f"docker build -t {image} . (or clear the repo's image to use the default "
                    "card image instead).",
                )
            )

    return checks


@dataclass
class ImageStaleness:
    stale: bool | None  # None: cannot be determined (no uv.lock, or a git/docker call failed)
    reason: str | None = None  # "uv.lock" or "base image" - which comparison found it stale
    lock_time: datetime | None = None
    built_time: datetime | None = None
    base_time: datetime | None = None


def _image_created(image: str, run: CommandRunner) -> datetime | None:
    created = run(["docker", "image", "inspect", "-f", "{{.Created}}", image])
    if created.returncode != 0:
        return None
    try:
        return datetime.fromisoformat(created.stdout.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def check_image_staleness(
    image: str, path: Path, run: CommandRunner, base_image: str | None = None
) -> ImageStaleness:
    """stale if uv.lock's last commit postdates the image's build time, OR the image's own base
    (`base_image`, e.g. smortboard-card:latest) was rebuilt more recently than the image itself.

    Reused by both the preflight row below and lifecycle.py's run-start rebuild - one source of
    truth for what "stale" means. `.stale` is None, not False, when staleness cannot be determined -
    a caller that would rebuild on False must check for None first, since None is "unknown", not
    "known fresh". Uses uv.lock's last commit time, not its mtime, because a fresh checkout/worktree
    resets file mtimes to checkout time regardless of when the lockfile changed.

    The base-image check exists because a repo image's own layers freeze the base it was built
    from - a base rebuilt later (e.g. smortboard-card:multi-lab replacing :latest's `agent` user
    without retagging it) never reaches an already-built repo image on its own; found for real
    when a repo image's rebuild started failing `chown agent:agent` for a reason with nothing to
    do with that repo's own uv.lock.
    """
    built_time = _image_created(image, run)
    if built_time is None:
        return ImageStaleness(stale=None)

    lock_path = path / "uv.lock"
    lock_time: datetime | None = None
    if lock_path.is_file():
        lock_log = run(["git", "log", "-1", "--format=%cI", "--", "uv.lock"], cwd=str(path))
        if lock_log.returncode == 0 and lock_log.stdout.strip():
            try:
                lock_time = datetime.fromisoformat(lock_log.stdout.strip())
            except ValueError:
                lock_time = None

    base_time = _image_created(base_image, run) if base_image and base_image != image else None

    if lock_time and lock_time.astimezone(UTC) > built_time.astimezone(UTC):
        return ImageStaleness(
            stale=True, reason="uv.lock", lock_time=lock_time, built_time=built_time
        )
    if base_time and base_time.astimezone(UTC) > built_time.astimezone(UTC):
        return ImageStaleness(
            stale=True, reason="base image", built_time=built_time, base_time=base_time
        )
    if lock_time is None and base_time is None:
        return ImageStaleness(stale=None)
    return ImageStaleness(stale=False, built_time=built_time, lock_time=lock_time)


def _image_staleness_check(
    row: Any, image: str, path: Path, run: CommandRunner, base_image: str | None = None
) -> dict[str, Any]:
    """warns when uv.lock changed, or the image's own base was rebuilt, after this image was built.

    A card's gate runs offline (`--no-sync`), so a dependency a card adds - or one landed on the
    base branch after the image was last built - fails the gate for a reason that has nothing to
    do with the card's own work. Same for a stale base: the failure the card sees has nothing to
    do with what the card changed.
    """
    freshness = check_image_staleness(image, path, run, base_image=base_image)
    if freshness.stale is None:
        detail = (
            "repo has no uv.lock to compare."
            if not (path / "uv.lock").is_file()
            else "could not determine the image's build time, uv.lock's last commit, or the base "
            "image's build time."
        )
        return row("image-stale", "repo image freshness", "ok", detail)

    if freshness.stale and freshness.reason == "uv.lock":
        return row(
            "image-stale",
            "repo image freshness",
            "warn",
            f"{image} was built {freshness.built_time.date()} but uv.lock last changed "
            f"{freshness.lock_time.date()} - a card's gate runs offline and will fail on any "
            "dependency added since.",
            f"docker build -t {image} . (or the board's rebuild-image action) to pick up uv.lock.",
        )
    if freshness.stale and freshness.reason == "base image":
        return row(
            "image-stale",
            "repo image freshness",
            "warn",
            f"{image} was built {freshness.built_time.date()} but its base image "
            f"({base_image}) was rebuilt {freshness.base_time.date()} - a card's gate runs "
            "offline against whatever this image already has baked in, which has nothing to do "
            "with the card's own work.",
            f"docker build -t {image} . (or the board's rebuild-image action) to pick up the "
            "newer base image.",
        )
    return row(
        "image-stale",
        "repo image freshness",
        "ok",
        f"{image} postdates uv.lock's last change and its base image's last build.",
    )


def run_preflight(
    store: Store, token_path: str | Path | None = None, runner: CommandRunner | None = None
) -> list[dict[str, Any]]:
    """every check, machine checks first, then each registered repo's, across every board."""
    run = runner or default_runner
    checks = [
        _docker_check(),
        _image_check(),
        # the credential the next run will use, which follows the active profile
        *(
            [_token_check(profiles.token_path_for_run(token_path))]
            if profiles.active_profile() is not None or token_path is not None
            else []
        ),
        *_profile_checks(),
        *_lab_checks(store, run),
        _gh_check(run),
        _git_check(),
    ]
    for board in store.list_boards():
        for repo in store.list_repos(board["id"]):
            checks.extend(_repo_checks(repo, run))
    return checks
