"""GET /api/preflight: everything that has to be true before a card can run, and the exact fix.

Readiness (smortboard/server/runs.py, behind /api/runtime) answers "can I run a card right now" with
three booleans. This answers "what do I do about it" with one row per thing, so it reuses backends'
docker/token probes as the source of truth rather than recomputing them - it only widens each into a
row with a fix. /api/runtime's response is untouched.
"""

import re
import shutil
import subprocess
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
from smortboard.repo_image import check_image_freshness
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
            "install Docker Desktop (or the docker engine), then re-check.",
        )
    if not docker_available():
        return _check(
            "docker",
            "machine",
            "docker",
            "fail",
            "docker is installed but the daemon is not answering.",
            "start Docker Desktop (or `sudo systemctl start docker`), then re-check.",
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
            "claude setup-token, then (umask 077; pbpaste | tr -d '\\r\\n ' > "
            f"{path}) - see README.md 'Setup' (Linux: paste into `cat > {path}` and press Ctrl-D).",
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
            "claude setup-token, then re-paste it into that file.",
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
        fixes.append("run `claude setup-token` again and re-paste it - it looks truncated.")
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
            else "codex login, then paste the ChatGPT login JSON from ~/.codex/auth.json"
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
                    f"{setup}, then save the credential with mode 600 at {row['path']}",
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
            "install the GitHub CLI (https://cli.github.com), then `gh auth login`.",
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
                "board (key b) once the matching GitHub repo exists.",
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
                    f"create the matching repo on GitHub yourself, then: "
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
                        f"origin has no {branch} branch - has the GitHub repo been created and pushed to?",
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

    if repo.get("test_command"):
        checks.append(row("test-command", "test command", "ok", f"runs `{repo['test_command']}`."))
    else:
        checks.append(
            row(
                "test-command",
                "test command",
                "fail",
                "no test command is set.",
                "set one on the repo (key b) - the test gate cannot run a card's work without it.",
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
            if repo.get("image"):
                # visible before a card starts, not minutes in as a red suite that reads as the
                # card's fault - see smortboard/repo_image.py::check_image_freshness
                freshness = check_image_freshness(repo, run=run)
                checks.append(
                    row(
                        "image-freshness",
                        "image freshness",
                        "ok" if freshness.fresh else "fail",
                        freshness.detail,
                        ""
                        if freshness.fresh
                        else f"docker build -t {image} . (rebuild from the repo's own "
                        "Dockerfile, stamped with the current uv.lock/pyproject.toml).",
                    )
                )
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
