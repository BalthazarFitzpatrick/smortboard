"""writes the path lease a card run is wired to obey

S3 proved the mechanism: a `PreToolUse` hook on `Edit|Write` sees `file_path` before the write
lands and can refuse it with exit 2. This module writes the two files that make that concrete for
one card's worktree — the lease itself, and a `--settings` file wiring the hook to enforce it.
"""

import contextlib
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from smortboard.exec.bash_guard import write_bash_guard_hook

# our own prefix, so the board can tell a lease refusal apart from any other hook denial
LEASE_CONFLICT_PREFIX = "LEASE_CONFLICT:"


def lease_glob_regex(glob: str) -> str:
    """gitignore-style: * and ? stay inside one folder, **/ is any number of folders (none too),
    and a trailing ** is everything below - what people and mission control write when they mean
    "anywhere under here". fnmatch let * cross folders yet needed a real one for **/"""
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        elif glob[i] == "[" and "]" in glob[i + 2 :]:
            end = glob.index("]", i + 2)
            body = glob[i + 1 : end].replace("\\", "\\\\")
            out.append("[^" + body[1:] + "]" if body.startswith("!") else "[" + body + "]")
            i = end + 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return "(?s:" + "".join(out) + r")\Z"


def lease_allows(rel: str, globs: list[str]) -> bool:
    """whether a repo-relative path is inside any of the lease's globs"""
    return any(re.match(lease_glob_regex(glob), rel) for glob in globs)


LEASE_MODES = ("strict", "soft")

# a soft lease never reaches these on its own: agent settings and hooks, ci, the next agent's
# orders, what the host builds, installs or runs from, and secrets. a lease that names one allows it
PROTECTED_GLOBS = (
    "**/.git/**",
    "**/.claude/**",
    "**/.codex/**",
    "**/.github/**",
    "**/CLAUDE.md",
    "**/AGENTS.md",
    "**/Dockerfile*",
    "**/*.Dockerfile",
    "docker/**",
    "**/pyproject.toml",
    "**/uv.lock",
    "**/package.json",
    "**/package-lock.json",
    "**/requirements*.txt",
    "**/.gitignore",
    "**/.gitattributes",
    "**/.gitmodules",
    "**/.pre-commit-config.yaml",
    "**/.vscode/**",
    "**/.husky/**",
    "**/.env*",
    "**/*.pem",
    "**/*.key",
)


def lease_permits(rel: str, lease: dict) -> bool:
    """may this card write `rel` - one answer for the hook, the codex guard and the post-run check.

    strict: its own globs and the repo's remembered ones, nothing else. soft adds any other repo
    path that is neither protected nor held by another active card on the same repo.
    """
    own = list(lease.get("path_globs") or []) + list(lease.get("remembered_globs") or [])
    if lease_allows(rel, own):
        return True
    if lease.get("mode") != "soft" or rel.startswith("/") or rel.split("/")[0] == "..":
        return False
    fenced = list(lease.get("protected_globs") or []) + list(lease.get("held_globs") or [])
    return not lease_allows(rel, fenced)


def lease_policy(
    store: Any, card: dict[str, Any], remembered_globs: list[str] | None = None
) -> dict[str, Any]:
    """the lease every guard writes and the post-run check reads: the card's globs, the repo's
    remembered ones, the board's mode, and in soft mode the protected list plus every glob another
    doing or checking card on the same repo holds, so a soft card never writes into theirs
    """
    board = store.get_board(card["board_id"]) if store is not None else {}
    mode = board.get("lease_mode") or "strict"
    policy: dict[str, Any] = {
        "path_globs": [row["path_glob"] for row in card.get("leases") or []],
        "remembered_globs": list(remembered_globs or []),
        "mode": mode,
    }
    if mode == "soft":
        held = []
        for other in store.list_cards(card["board_id"]):
            if other["id"] == card["id"] or other.get("repo_id") != card.get("repo_id"):
                continue
            if other.get("status") not in ("doing", "checking"):
                continue
            leases = other.get("leases")
            if leases is None:
                leases = store.get_card(other["id"]).get("leases") or []
            held += [row["path_glob"] for row in leases]
        policy["protected_globs"] = list(PROTECTED_GLOBS)
        policy["held_globs"] = sorted(set(held))
    return policy


def split_outside_lease(paths: list[str], policy: dict[str, Any]) -> tuple[list[str], list[str]]:
    """committed paths outside the card's own lease, as (expanded, refused) under its mode - strict
    refuses all of them, soft keeps the ones lease_permits allows as expansions"""
    expanded = [path for path in paths if lease_permits(path, policy)]
    return expanded, [path for path in paths if path not in expanded]


def _diff_paths(repo_path: str | Path, spec: str) -> list[str] | None:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "diff", "--name-only", "--no-renames", "-z", spec, "--"],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        return None
    paths = result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    return [path for path in paths if path]


def changed_paths_outside_lease(
    repo_path: str | Path,
    base: str,
    branch: str,
    globs: list[str],
    base_ref: str | None = None,
) -> list[str]:
    """check committed paths independently of the agent's tool hooks.

    `base_ref` is the branch the card started from (e.g. origin/development). Once the card merges
    it in, everything that branch brought comes along in `base..branch`, and none of it is the
    card's own edit - measured 2026-09-19, two cards blocked LEASE_CONFLICT over CLAUDE.md, the
    docs images and CI config after merging development. Only paths that ALSO differ from the merge
    base with `base_ref` count, so this can drop a false positive but never adds a new flag.
    """
    changed = _diff_paths(repo_path, f"{base}..{branch}")
    if changed is None:
        from smortboard.exec.worktrees import WorktreeError

        raise WorktreeError("could not verify the card's committed path lease")
    if base_ref:
        own = _diff_paths(repo_path, f"{base_ref}...{branch}")
        if own is not None:
            changed = [path for path in changed if path in set(own)]
    return sorted(path for path in changed if not lease_allows(path, globs))


# the hook runs inside the card's container with no smortboard installed, so it carries the two
# functions above as source - one definition for the board and the guard, they cannot drift
_HOOK_SCRIPT = (
    '"""PreToolUse hook: refuses an Edit/Write outside the card\'s declared path lease"""\n'
    "import json\nimport re\nimport sys\nfrom pathlib import Path\n\n\n"
    + inspect.getsource(lease_glob_regex)
    + "\n\n"
    + inspect.getsource(lease_allows)
    + "\n\n"
    + inspect.getsource(lease_permits)
    + """
payload = json.load(sys.stdin)
file_path = payload.get("tool_input", {}).get("file_path")
if not file_path:
    sys.exit(0)

lease = json.loads(Path(__file__).with_name("lease.json").read_text())
# the guards never sit inside the repo, so the root comes from lease.json
repo_root = Path(lease.get("root") or Path(__file__).resolve().parents[1])

try:
    rel = Path(file_path).resolve().relative_to(repo_root)
except ValueError:
    # outside the repo is never leased, whatever the mode
    rel = Path(file_path).resolve()

# own globs, the repo's remembered ones, and in soft mode any unprotected path nobody else holds
if lease_permits(str(rel), lease):
    sys.exit(0)

print(f"{prefix} {rel} is outside this card's lease", file=sys.stderr)
sys.exit(2)
""".replace("{prefix}", LEASE_CONFLICT_PREFIX)
)


def write_lease_settings(
    out_dir: str | Path,
    path_globs: list[str],
    *,
    root: str | Path,
    remembered_globs: list[str] | None = None,
    policy: dict[str, Any] | None = None,
    python: str = sys.executable,
    guard_dir: str | None = None,
) -> Path:
    """writes lease.json, both hook scripts, and a settings.json wiring PreToolUse to them

    also wires bash_guard's PreToolUse:Bash guard, so every card gets both guards from one
    --settings file. returns the settings.json path, which the runner passes via `--settings`.

    `out_dir` is a directory the board owns, NEVER the worktree: the test gate mounts the worktree,
    and a repo's own `ruff check .` failed a card on the board's generated hook scripts. So the
    hooks cannot find the repo from their own location - `root` is always recorded in lease.json.

    `remembered_globs` is the card's repo's remembered list (see Store.remembered_leases) - a
    second, separate list the guard also allows against, so removing one from the repo later
    never has to touch a single card's own lease rows.

    `python`, `guard_dir` and `root` are the interpreter, `out_dir` and the repo as the RUNNER
    sees them. A container mounts them elsewhere and has its own interpreter, and a hook whose
    command does not resolve exits 127 - which Claude Code treats as non-blocking, so the guard
    would silently let every write through.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # `policy` is lease_policy's mode, protected and held globs; without one the lease is strict
    lease = {
        **(policy or {}),
        "path_globs": path_globs,
        "remembered_globs": remembered_globs or [],
        "root": str(root),
    }
    (out_dir / "lease.json").write_text(json.dumps(lease, indent=2))

    hook_script = out_dir / "lease_guard.py"
    hook_script.write_text(_HOOK_SCRIPT)
    hook_script.chmod(0o755)

    seen_script = f"{guard_dir}/lease_guard.py" if guard_dir else hook_script
    lease_entry = {
        "matcher": "Edit|Write",
        "hooks": [{"type": "command", "command": f"{python} {seen_script}"}],
    }
    bash_guard_entry = write_bash_guard_hook(out_dir, python=python, guard_dir=guard_dir)

    settings = {"hooks": {"PreToolUse": [lease_entry, bash_guard_entry]}}
    settings_file = out_dir / "settings.json"
    settings_file.write_text(json.dumps(settings, indent=2))
    return settings_file


# what the guard writers (and the reviewer's schema) once put into <worktree>/.claude
_LEGACY_GUARD_FILES = (
    "settings.json",
    "lease.json",
    "lease_guard.py",
    "bash_guard.py",
    "codex_guard.py",
    "hooks.json",
    "review-schema.json",
)


def drop_legacy_guards(worktree_path: str | Path) -> list[str]:
    """removes guard files an older board wrote into the worktree's .claude dir, returns their names.

    A reused worktree still holds them, and the test gate mounts the worktree - measured, card
    05de2d52 blocked TESTS_FAILED on the repo's ruff linting `.claude/bash_guard.py`. Only
    untracked files go: a repo's own committed `.claude/settings.json` stays where it is.
    """
    claude_dir = Path(worktree_path) / ".claude"
    present = [name for name in _LEGACY_GUARD_FILES if (claude_dir / name).is_file()]
    if not present:
        return []
    listed = subprocess.run(
        ["git", "-C", str(worktree_path), "ls-files", "-z", "--", ".claude"],
        capture_output=True,
        text=True,
        check=False,
    )
    # no answer from git means no way to tell the repo's own files apart, so nothing is removed
    if listed.returncode:
        return []
    tracked = set(listed.stdout.split("\0"))
    removed = [name for name in present if f".claude/{name}" not in tracked]
    for name in removed:
        (claude_dir / name).unlink()
    with contextlib.suppress(OSError):
        claude_dir.rmdir()
    return removed
