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
import shlex
import sys
from pathlib import Path

# git subcommands that talk to a remote - -u/--upload-pack etc on these run an arbitrary program
# on the other end of the "clone"
_GIT_REMOTE_SUBCOMMANDS = {"fetch", "pull", "clone", "ls-remote", "push", "archive"}
_GIT_REMOTE_PROGRAM_FLAGS = ("--upload-pack", "--receive-pack", "--exec")


def _refused_git_invocation(segment):
    """None if `segment` is not a git call the allowlist should refuse, else a reason string.

    `Bash(git *)` admits every git invocation, and a global option before the subcommand
    (`-c core.sshCommand=...`, `-C`, `--git-dir`, `--exec-path`, ...) turns git into a way to run
    an arbitrary program. So any dash-led token between `git` and its subcommand is refused
    outright, and the remote-talking subcommands additionally refuse a program flag anywhere.
    """
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return None  # unbalanced quotes etc - not our job, the shell will reject it itself
    i = 0
    while i < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[i]):
        i += 1  # skip leading env assignments like FOO=bar git ...
    if i >= len(tokens) or tokens[i] != "git":
        return None
    rest = tokens[i + 1 :]
    j = 0
    while j < len(rest) and rest[j].startswith("-"):
        return f"a global option ({rest[j]}) before the git subcommand is refused"
    if j >= len(rest):
        return None
    subcommand = rest[j]
    remainder = rest[j + 1 :]
    if subcommand in _GIT_REMOTE_SUBCOMMANDS:
        for flag in remainder:
            if flag == "-u" or flag.startswith(_GIT_REMOTE_PROGRAM_FLAGS):
                return f"{flag} on git {subcommand} is refused"
    return None


payload = json.load(sys.stdin)
tool_input = payload.get("tool_input", {})
# a headless run ends with its turn, so a background command is abandoned work
if tool_input.get("run_in_background"):
    print("{prefix} nothing wakes a headless run - run it in the foreground", file=sys.stderr)
    sys.exit(2)
command = tool_input.get("command")
if not command:
    sys.exit(0)

for segment in re.split(r"[;&|\\n]+", command):
    segment = segment.strip()
    if not segment:
        continue
    reason = _refused_git_invocation(segment)
    if reason is not None:
        print(f"{prefix} {reason}", file=sys.stderr)
        sys.exit(2)

lease_file = Path(__file__).with_name("lease.json")
lease = json.loads(lease_file.read_text()) if lease_file.exists() else {}
root = Path(lease.get("root") or Path(__file__).resolve().parent.parent)

# only a path that starts at a word boundary is absolute - a bare /\\S+ also matched the
# `/test_cli.py` inside `tests/test_cli.py` and refused a card its own test command
for token in re.findall(r"(?:^|(?<=[\\s'\\"=<>]))/\\S+", command):
    candidate = token.rstrip("'\\",;)")
    if candidate == "/dev/null":
        continue
    try:
        Path(candidate).resolve().relative_to(root)
    except ValueError:
        print(f"{prefix} {candidate} is outside this card's worktree", file=sys.stderr)
        sys.exit(2)

if re.search(r"(^|[;&|]\\s*)cd\\s+\\.\\.(/|\\s|$)", command):
    print("{prefix} 'cd ..' reaches above this card's worktree", file=sys.stderr)
    sys.exit(2)

sys.exit(0)
'''.replace("{prefix}", BASH_ESCAPE_PREFIX)


def write_bash_guard_hook(
    out_dir: str | Path, *, python: str = sys.executable, guard_dir: str | None = None
) -> dict:
    """writes the hook script into `out_dir`, returns its PreToolUse entry.

    `out_dir` is the board's own guard directory, never the worktree. `python` and `guard_dir` are
    as the runner sees them - see leases.write_lease_settings.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    hook_script = out_dir / "bash_guard.py"
    hook_script.write_text(_HOOK_SCRIPT)
    hook_script.chmod(0o755)

    seen_script = f"{guard_dir}/bash_guard.py" if guard_dir else hook_script
    return {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": f"{python} {seen_script}"}],
    }
