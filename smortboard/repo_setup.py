"""gets any folder ready for a board: git, a `development` branch, a private GitHub origin.

One path for every starting point (the operator, 2026-09-24: "it should be seamless"). A folder is
inspected and only what it is missing is done:

    new or empty folder        -> git init, a starter commit on main, branch development
    folder with files, no git  -> the same, after the operator confirms the file list
    repo without development   -> branch development (fetched from origin when it is there)
    repo without an origin     -> a private GitHub repo as origin, via gh, never pushed with --push
    development not on origin  -> push development

The board never pushes main. When it created the origin, main still has to reach GitHub and become
its default branch - that one command is returned for the operator to run (push_main).
"""

from __future__ import annotations

import re
import shlex
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

DEVELOPMENT = "development"
# written before a first commit when the folder has no .gitignore, so confirming cannot push a key
DEFAULT_GITIGNORE = ".env\n.env.*\n*.pem\n*.key\n.venv/\nnode_modules/\n__pycache__/\n.DS_Store\n"
# what GitHub accepts as a repository name
_REPO_NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")

Runner = Callable[..., subprocess.CompletedProcess]


class SetupRefused(ValueError):
    """the folder cannot be made ready as it is - the message says what to change"""


class NeedsConfirm(Exception):
    """a folder with files and no git: these would be its first commit. not an error - the operator
    is asked, and the same call with confirm=True goes on"""

    def __init__(self, files: list[str]) -> None:
        super().__init__(f"{len(files)} files would be committed")
        self.files = files


@dataclass
class SetupResult:
    base: str = DEVELOPMENT
    steps: list[str] = field(default_factory=list)
    push_main: str | None = None
    created_origin: bool = False


def default_runner(
    cmd: list[str], cwd: str | None = None, timeout: float = 60
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr=str(exc))


class _Git:
    def __init__(self, root: Path, runner: Runner) -> None:
        self.root, self.runner = root, runner

    def run(self, *args: str, timeout: float = 60) -> subprocess.CompletedProcess:
        return self.runner(["git", "-C", str(self.root), *args], timeout=timeout)

    def ok(self, *args: str) -> bool:
        return self.run(*args).returncode == 0

    def must(self, *args: str, timeout: float = 60) -> str:
        result = self.run(*args, timeout=timeout)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise SetupRefused(f"git {' '.join(args[:2])} failed: {detail}")
        return result.stdout.strip()


def preview_files(root: Path, runner: Runner = default_runner) -> list[str]:
    """what a first commit would hold, worked out without writing anything in the folder: a
    throwaway git dir elsewhere, the folder's own .gitignore plus the default one"""
    with tempfile.TemporaryDirectory() as tmp:
        excludes = Path(tmp) / "exclude"
        excludes.write_text(DEFAULT_GITIGNORE)
        git_dir = Path(tmp) / "probe.git"
        runner(["git", "init", "-q", "--bare", str(git_dir)])
        result = runner(
            [
                "git",
                f"--git-dir={git_dir}",
                f"--work-tree={root}",
                "-c",
                f"core.excludesFile={excludes}",
                "add",
                "--all",
                "--dry-run",
            ]
        )
    return [line[5:-1] for line in result.stdout.splitlines() if line.startswith("add '")]


def _first_commit(root: Path, git: _Git, steps: list[str], starter: bool) -> None:
    if starter:
        (root / "README.md").write_text(f"# {root.name}\n")
    if not (root / ".gitignore").exists():
        (root / ".gitignore").write_text(DEFAULT_GITIGNORE)
    if not (root / ".git").exists():
        git.must("init", "-q", "-b", "main")
        steps.append("git init")
    git.must("add", "--all")
    git.must("commit", "-q", "-m", "initial project scaffold")
    steps.append("first commit on main")


def _default_branch(git: _Git, has_origin: bool) -> str:
    if has_origin:
        head = git.run("symbolic-ref", "--short", "refs/remotes/origin/HEAD").stdout.strip()
        if head.startswith("origin/"):
            return head.removeprefix("origin/")
    return git.must("branch", "--show-current") or "main"


def _push_development(git: _Git, steps: list[str]) -> None:
    # the only push this module makes: an explicit refspec naming development, so main can never
    # be the destination whatever the remote's config says
    refspec = f"refs/heads/{DEVELOPMENT}:refs/heads/{DEVELOPMENT}"
    git.must("push", "--set-upstream", "origin", refspec, timeout=120)
    steps.append(f"pushed {DEVELOPMENT}")


def _create_origin(root: Path, git: _Git, runner: Runner, steps: list[str]) -> str:
    """a private GitHub repo named after the folder, added as origin, nothing pushed"""
    if not _REPO_NAME.match(root.name):
        raise SetupRefused(
            f"{root.name!r} is not a GitHub repo name - rename the folder to letters, digits, "
            ". _ or - and try again"
        )
    owner = runner(["gh", "api", "user", "--jq", ".login"]).stdout.strip()
    if not owner:
        raise SetupRefused("gh cannot say who you are - run `gh auth login`, then try again")
    full = f"{owner}/{root.name}"
    created = runner(
        ["gh", "repo", "create", full, "--private", "--source", str(root), "--remote", "origin"],
        timeout=120,
    )
    if created.returncode != 0:
        raise SetupRefused(
            f"gh could not create {full}: {(created.stderr or created.stdout).strip()} - if the "
            "repo already exists, add it as origin yourself and try again"
        )
    steps.append(f"created private GitHub repo {full}")
    return full


def prepare(
    path: str | Path, *, confirm: bool = False, runner: Runner = default_runner
) -> SetupResult:
    """makes the folder at `path` ready for a board and says what it did. raises NeedsConfirm for
    a folder with files and no git (call again with confirm=True), SetupRefused when it cannot"""
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise SetupRefused(f"{root} is not a folder")
    git = _Git(root, runner)
    result = SetupResult()

    # a folder of its own: a subfolder of another repo's work tree is not a repo for a board
    is_repo = (root / ".git").exists()
    has_commit = is_repo and git.ok("rev-parse", "--verify", "--quiet", "HEAD")
    if not has_commit:
        entries = [p for p in root.iterdir() if p.name not in (".git", ".DS_Store")]
        if entries and not confirm:
            raise NeedsConfirm(preview_files(root, runner))
        _first_commit(root, git, result.steps, starter=not entries)

    has_origin = git.ok("remote", "get-url", "origin")
    default = _default_branch(git, has_origin)
    dev_on_origin = has_origin and git.ok(
        "ls-remote", "--exit-code", "--heads", "origin", DEVELOPMENT
    )
    if not git.ok("show-ref", "--verify", "--quiet", f"refs/heads/{DEVELOPMENT}"):
        if dev_on_origin:
            git.must("fetch", "origin", f"{DEVELOPMENT}:{DEVELOPMENT}", timeout=120)
            result.steps.append(f"fetched {DEVELOPMENT} from origin")
        else:
            git.must("branch", DEVELOPMENT, default)
            result.steps.append(f"branched {DEVELOPMENT} from {default}")

    full = None
    if not has_origin:
        full = _create_origin(root, git, runner, result.steps)
        result.created_origin = True
        if default != DEVELOPMENT:
            result.push_main = (
                f"git -C {shlex.quote(str(root))} push -u origin {default} && "
                f"gh repo edit {full} --default-branch {default}"
            )
    if not dev_on_origin:
        try:
            _push_development(git, result.steps)
        except SetupRefused as exc:
            # the GitHub repo exists now either way - say so, or a retry only hears "name taken"
            if full is None:
                raise
            raise SetupRefused(
                f"created {full} on GitHub, then {exc}. the repo stays there: push "
                f"{DEVELOPMENT} yourself, or `gh repo delete {full}` and try again"
            ) from exc
    return result
