"""writes the path lease a card run is wired to obey

S3 proved the mechanism: a `PreToolUse` hook on `Edit|Write` sees `file_path` before the write
lands and can refuse it with exit 2. This module writes the two files that make that concrete for
one card's worktree — the lease itself, and a `--settings` file wiring the hook to enforce it.
"""

import json
import sys
from pathlib import Path

from smortboard.exec.bash_guard import write_bash_guard_hook

# our own prefix, so the board can tell a lease refusal apart from any other hook denial
LEASE_CONFLICT_PREFIX = "LEASE_CONFLICT:"

_HOOK_SCRIPT = '''\
"""PreToolUse hook: refuses an Edit/Write outside the card's declared path lease"""
import fnmatch
import json
import sys
from pathlib import Path

payload = json.load(sys.stdin)
file_path = payload.get("tool_input", {}).get("file_path")
if not file_path:
    sys.exit(0)

lease = json.loads(Path(__file__).with_name("lease.json").read_text())
globs = lease["path_globs"]
# a container mounts the guards outside the repo, so there the root comes from lease.json
repo_root = Path(lease.get("root") or Path(__file__).resolve().parents[1])

try:
    rel = Path(file_path).resolve().relative_to(repo_root)
except ValueError:
    rel = Path(file_path)

if any(fnmatch.fnmatch(str(rel), glob) for glob in globs):
    sys.exit(0)

print(f"{prefix} {rel} is outside this card's lease", file=sys.stderr)
sys.exit(2)
'''.replace("{prefix}", LEASE_CONFLICT_PREFIX)


def write_lease_settings(
    worktree_path: str | Path,
    path_globs: list[str],
    *,
    python: str = sys.executable,
    guard_dir: str | None = None,
    root: str | None = None,
) -> Path:
    """writes lease.json, both hook scripts, and a settings.json wiring PreToolUse to them

    also wires bash_guard's PreToolUse:Bash guard, so every card gets both guards from one
    --settings file. returns the settings.json path, which the runner passes via `--settings`.

    `python`, `guard_dir` and `root` are the interpreter, this .claude dir and the repo as the
    RUNNER sees them. A container mounts them elsewhere and has its own interpreter, and a hook
    whose command does not resolve exits 127 - which Claude Code treats as non-blocking, so the
    guard would silently let every write through.
    """
    worktree_path = Path(worktree_path)
    claude_dir = worktree_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    lease = {"path_globs": path_globs}
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
