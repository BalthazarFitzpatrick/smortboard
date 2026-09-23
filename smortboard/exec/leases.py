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
    + """
payload = json.load(sys.stdin)
file_path = payload.get("tool_input", {}).get("file_path")
if not file_path:
    sys.exit(0)

lease = json.loads(Path(__file__).with_name("lease.json").read_text())
globs = lease["path_globs"]
# a repo's remembered globs (see Store.remember_lease_paths) - approved once from the inbox,
# permitted on every card on this repo since, alongside its own lease rather than instead of it
remembered = lease.get("remembered_globs") or []
# the guards never sit inside the repo, so the root comes from lease.json
repo_root = Path(lease.get("root") or Path(__file__).resolve().parents[1])

try:
    rel = Path(file_path).resolve().relative_to(repo_root)
except ValueError:
    rel = Path(file_path)

if lease_allows(str(rel), globs) or lease_allows(str(rel), remembered):
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

    lease = {
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
