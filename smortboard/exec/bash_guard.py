"""writes the PreToolUse hook that keeps a card's Bash calls inside its worktree

sibling to leases.py's lease guard, same S3 mechanism: PreToolUse, exit 2 blocks. Be honest about
what this is — it is NOT a security boundary. A shell is arbitrary and a determined command escapes
any string-level check. This only catches accidents: an absolute path outside the worktree, or an
obvious `cd ..` above it. Real containment means running the card in a container.
"""

import sys
from pathlib import Path

# our own prefix, distinguishable from LEASE_CONFLICT_PREFIX in permission_denials
BASH_ESCAPE_PREFIX = "BASH_ESCAPE:"

_HOOK_SCRIPT = '''\
"""PreToolUse hook: refuses a Bash command that reaches outside the card's worktree

NOT a security boundary - shell is arbitrary and a determined command gets out. This only catches
accidents: an absolute path outside the worktree, or an obvious `cd ..` above it. Real containment
means running the card in a container.
"""
import json
import re
import sys
from pathlib import Path

payload = json.load(sys.stdin)
command = payload.get("tool_input", {}).get("command")
if not command:
    sys.exit(0)

lease_file = Path(__file__).with_name("lease.json")
lease = json.loads(lease_file.read_text()) if lease_file.exists() else {}
root = Path(lease.get("root") or Path(__file__).resolve().parent.parent)

for token in re.findall(r"/\\S+", command):
    candidate = token.rstrip("'\\",;)")
    try:
        Path(candidate).resolve().relative_to(root)
    except ValueError:
        print(f"{prefix} {candidate} is outside this card's worktree", file=sys.stderr)
        sys.exit(2)

if re.search(r"(^|[;&|]\\s*)cd\\s+\\.\\.(/|\\s|$)", command):
    print(f"{prefix} 'cd ..' reaches above this card's worktree", file=sys.stderr)
    sys.exit(2)

sys.exit(0)
'''.replace("{prefix}", BASH_ESCAPE_PREFIX)


def write_bash_guard_hook(
    worktree_path: str | Path, *, python: str = sys.executable, guard_dir: str | None = None
) -> dict:
    """writes the hook script under the worktree's .claude dir, returns its PreToolUse entry.

    `python` and `guard_dir` are as the runner sees them - see leases.write_lease_settings.
    """
    worktree_path = Path(worktree_path)
    claude_dir = worktree_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    hook_script = claude_dir / "bash_guard.py"
    hook_script.write_text(_HOOK_SCRIPT)
    hook_script.chmod(0o755)

    seen_script = f"{guard_dir}/bash_guard.py" if guard_dir else hook_script
    return {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": f"{python} {seen_script}"}],
    }
