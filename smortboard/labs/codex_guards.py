"""wire Codex patch and shell hooks to the board's lease and command policy"""

import inspect
import json
from pathlib import Path

from smortboard.exec.bash_guard import write_bash_guard_hook
from smortboard.exec.leases import lease_allows, lease_glob_regex
from smortboard.labs.base import BashPolicy, GuardFiles

_SCRIPT = (
    "import fnmatch\nimport json\nimport re\nimport shlex\nimport subprocess\nimport sys\nfrom pathlib import Path\n\n"
    + inspect.getsource(lease_glob_regex)
    + "\n"
    + inspect.getsource(lease_allows)
    + """

payload = json.load(sys.stdin)
base = Path(__file__).parent
lease = json.loads((base / "lease.json").read_text())
root = Path(lease["root"]).resolve()
tool = payload.get("tool_name", "")
args = payload.get("tool_input") or {}

def refuse(reason):
    print(reason, file=sys.stderr)
    sys.exit(2)

def check_path(path):
    candidate = Path(path)
    candidate = candidate if candidate.is_absolute() else root / candidate
    try:
        relative = str(candidate.resolve().relative_to(root))
    except ValueError:
        refuse("LEASE_CONFLICT: " + path + " is outside the worktree")
    if not lease_allows(relative, lease["path_globs"] + lease.get("remembered_globs", [])):
        refuse("LEASE_CONFLICT: " + relative + " is outside this card's lease")

if tool == "apply_patch":
    if lease.get("read_only"):
        refuse("LEASE_CONFLICT: this role is read-only")
    patch = args.get("command") or args.get("patch") or args.get("input") or ""
    paths = re.findall(r"^\\*\\*\\* (?:Add File|Update File|Delete File|Move to): (.+)$", patch, re.M)
    if not paths:
        refuse("LEASE_CONFLICT: unrecognized patch format")
    for path in paths:
        check_path(path)
elif tool in ("Edit", "Write"):
    if lease.get("read_only"):
        refuse("LEASE_CONFLICT: this role is read-only")
    path = args.get("file_path")
    if not path:
        refuse("LEASE_CONFLICT: edit has no path")
    check_path(path)
elif tool in ("Bash", "shell", "exec_command", "shell_command"):
    command = args.get("command") or args.get("cmd") or ""
    checked = subprocess.run([sys.executable, str(base / "bash_guard.py")],
        input=json.dumps({"tool_input": {**args, "command": command}}), text=True)
    if checked.returncode:
        sys.exit(checked.returncode)
    # substitutions and redirections can write outside the lease without an edit hook
    if re.search(r"[;|<>`\\n]|\\$\\(|(?<!&)&(?!&)", command):
        refuse("BASH_ESCAPE: shell control operators are not permitted")
    for part in command.split("&&"):
        part = part.strip()
        allowed = lease.get("bash_allow", [])
        words = shlex.split(part)
        read_commands = {"pwd", "ls", "cat", "head", "tail"} if lease.get("read") else set()
        search_commands = {"rg", "grep"} if lease.get("search") else set()
        reader = bool(words) and words[0] in read_commands | search_commands
        if words and words[0] == "sed" and lease.get("read"):
            # sed scripts can write or execute even without -i, so admit only line printing
            reader = len(words) >= 3 and words[1] == "-n" and bool(re.fullmatch(r"[0-9$]+(?:,[0-9$]+)?p", words[2]))
        grants = not lease.get("read_only") and any(fnmatch.fnmatchcase(part, rule) or part == rule.removesuffix(" *") for rule in allowed)
        if not reader and not grants:
            refuse("BASH_ESCAPE: command is outside the declared command policy")
"""
)


def write_guard_files(lease: list[str], bash: BashPolicy) -> GuardFiles:
    directory = Path(bash.worktree_path) / ".claude"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "lease.json").write_text(
        json.dumps(
            {
                "path_globs": lease,
                "remembered_globs": bash.remembered_globs,
                "root": bash.root or str(Path(bash.worktree_path).resolve()),
                "bash_allow": bash.bash_allow,
                "read": bash.read,
                "search": bash.search,
                "read_only": bash.read_only,
            }
        )
    )
    (directory / "codex_guard.py").write_text(_SCRIPT)
    write_bash_guard_hook(bash.worktree_path, python=bash.python, guard_dir=bash.guard_dir)
    script = (
        f"{bash.guard_dir}/codex_guard.py" if bash.guard_dir else str(directory / "codex_guard.py")
    )
    hooks = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "apply_patch|Bash|shell|exec_command|shell_command|Edit|Write",
                    "hooks": [
                        {"type": "command", "command": f"{bash.python} {script}", "timeout": 10}
                    ],
                }
            ]
        }
    }
    path = directory / "hooks.json"
    path.write_text(json.dumps(hooks))
    return GuardFiles(path, tuple(directory.iterdir()))
