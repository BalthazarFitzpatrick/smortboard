"""writes the path lease a card run is wired to obey

S3 proved the mechanism: a `PreToolUse` hook on `Edit|Write` sees `file_path` before the write
lands and can refuse it with exit 2. This module writes the two files that make that concrete for
one card's worktree — the lease itself, and a `--settings` file wiring the hook to enforce it.
"""

import inspect
import json
import re
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
# a container mounts the guards outside the repo, so there the root comes from lease.json
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
    worktree_path: str | Path,
    path_globs: list[str],
    *,
    remembered_globs: list[str] | None = None,
    python: str = sys.executable,
    guard_dir: str | None = None,
    root: str | None = None,
) -> Path:
    """writes lease.json, both hook scripts, and a settings.json wiring PreToolUse to them

    also wires bash_guard's PreToolUse:Bash guard, so every card gets both guards from one
    --settings file. returns the settings.json path, which the runner passes via `--settings`.

    `remembered_globs` is the card's repo's remembered list (see Store.remembered_leases) - a
    second, separate list the guard also allows against, so removing one from the repo later
    never has to touch a single card's own lease rows.

    `python`, `guard_dir` and `root` are the interpreter, this .claude dir and the repo as the
    RUNNER sees them. A container mounts them elsewhere and has its own interpreter, and a hook
    whose command does not resolve exits 127 - which Claude Code treats as non-blocking, so the
    guard would silently let every write through.
    """
    worktree_path = Path(worktree_path)
    claude_dir = worktree_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    lease = {"path_globs": path_globs, "remembered_globs": remembered_globs or []}
    if root:
        lease["root"] = root
    (claude_dir / "lease.json").write_text(json.dumps(lease, indent=2))

    hook_script = claude_dir / "lease_guard.py"
    hook_script.write_text(_HOOK_SCRIPT)
    hook_script.chmod(0o755)

    seen_script = f"{guard_dir}/lease_guard.py" if guard_dir else hook_script
    lease_entry = {
        "matcher": "Edit|Write",
        "hooks": [{"type": "command", "command": f"{python} {seen_script}"}],
    }
    bash_guard_entry = write_bash_guard_hook(worktree_path, python=python, guard_dir=guard_dir)

    settings = {"hooks": {"PreToolUse": [lease_entry, bash_guard_entry]}}
    settings_file = claude_dir / "settings.json"
    settings_file.write_text(json.dumps(settings, indent=2))
    return settings_file
