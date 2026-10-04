"""s8 spike: run a card agent headless on the host with a scrubbed env.

usage: uv run python tools/host_run_spike.py <claude|codex> <scratch_dir> [--home real|scratch]

builds the argv with the real adapter, wraps it in the adapter's auth_shell (credential on stdin),
runs it in a throwaway worktree with an allowlisted env, and feeds stdout through the adapter's
normalize() and the runner's parse_line / attribute path. prints only event kinds and field names,
never a credential.
"""

import os
import shlex
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

from smortboard import profiles
from smortboard.exec.backends import read_card_token
from smortboard.exec.runner import parse_line
from smortboard.labs.base import RunRequest, estimate_usage
from smortboard.labs.claude_code import user_message_line
from smortboard.labs.registry import get_adapter

LEAK = "--leak" in sys.argv
CONTROL = "--control" in sys.argv
LEAK_BRIEF = (
    "write fmt.py containing exactly the single line `x   =   1` (three spaces each side of the =). "
    "then run: git add fmt.py && git commit -m 'added fmt'. report the commit result verbatim."
)
BRIEF = "create hello.txt containing hi, then git add and git commit it with message 'added hello'."
ENV_ALLOWLIST = tuple(os.environ.get("SPIKE_ENV_KEYS", "PATH,HOME,LANG,TMPDIR").split(","))
TIME_CAP_SECONDS = 180


def make_repo(scratch: Path, name: str) -> Path:
    repo = scratch / f"{name}-repo"
    work = scratch / f"{name}-worktree"
    repo.mkdir()
    git = ["git", "-c", "user.name=spike", "-c", "user.email=spike@example.invalid"]
    subprocess.run([*git, "init", "-q", "-b", "base"], cwd=repo, check=True)
    (repo / "readme.txt").write_text("spike\n")
    subprocess.run([*git, "add", "."], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], cwd=repo, check=True)
    subprocess.run(
        [*git, "worktree", "add", "-q", "-b", "main" if LEAK else "card/spike", str(work)],
        cwd=repo,
        check=True,
    )
    # identity in the worktree config so the agent can commit with a scrubbed env
    subprocess.run(["git", "config", "user.name", "spike"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "spike@example.invalid"], cwd=work, check=True)
    return work


def build_env(home: str) -> dict[str, str]:
    source = {**os.environ, "HOME": home}
    return {key: source[key] for key in ENV_ALLOWLIST if key in source}


def main() -> None:
    lab_name, scratch = sys.argv[1], Path(sys.argv[2])
    home_mode = sys.argv[sys.argv.index("--home") + 1] if "--home" in sys.argv else "real"
    home = os.path.expanduser("~") if home_mode == "real" else str(scratch / "fakehome")
    Path(home).mkdir(parents=True, exist_ok=True)
    work = make_repo(scratch, lab_name + home_mode)
    brief = LEAK_BRIEF if LEAK else BRIEF
    if LEAK:
        (work / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
        subprocess.run(["git", "add", "pyproject.toml"], cwd=work, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "pyproject"], cwd=work, check=True)
    env = build_env(home)
    adapter = get_adapter("anthropic" if lab_name == "claude" else "openai")
    if lab_name == "claude":
        model, profile = "haiku", {"kind": None}
        cmd = adapter.build_command(
            RunRequest(
                prompt=brief,
                model=model,
                budget_usd=0.50,
                stream_input=True,
                system_prompt="you are a card agent. do the task in one or two tool calls.",
            )
        )
        if CONTROL:
            # control: the same argv with user settings re-enabled, to show the hooks would fire
            cmd[cmd.index("project")] = "user,project"
        token_line = read_card_token(profiles.active_profile_path()) + "\n"
        shell = adapter.auth_shell(profile) + shlex.join(cmd)
        stdin = token_line + user_message_line(brief)
    else:
        model = os.environ.get("SPIKE_CODEX_MODEL", "gpt-5.6-luna")
        cmd = adapter.build_command(RunRequest(prompt=brief, model=model))
        codex_home = scratch / "codexhome"
        codex_home.mkdir(mode=0o700, exist_ok=True)
        env["CODEX_HOME"] = str(codex_home)
        auth = profiles.active_profile_path("openai")
        # the board's configured openai profile (auth_json kind), handed on stdin like a container run
        shell = (
            "export CODEX_HOME; umask 077; IFS= read -r T && "
            'printf %s "$T" > "$CODEX_HOME/auth.json" && unset T && ' + shlex.join(cmd)
        )
        stdin = " ".join(auth.read_text().split()) + "\n"
    started = time.time()
    process = subprocess.Popen(
        ["sh", "-c", shell],
        cwd=work,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert process.stdin is not None
    process.stdin.write(stdin)
    process.stdin.flush()
    if lab_name != "claude":
        process.stdin.close()
    suffix = ("-leak" if LEAK else "") + ("-control" if CONTROL else "")
    raw_path = scratch / f"{lab_name}-{home_mode}{suffix}.raw.jsonl"
    kinds: dict[str, int] = {}
    neutral_all = []
    result_seen = False
    with raw_path.open("w") as raw_file:
        assert process.stdout is not None
        for line in process.stdout:
            raw_file.write(line)
            raw = parse_line(line)
            if raw is None:
                continue
            for event in adapter.normalize(raw):
                event = replace(event, lab=adapter.lab, model=event.model or model)
                if event.usage is not None:
                    event = replace(
                        event, usage=estimate_usage(event.usage, adapter.lab, event.model)
                    )
                neutral_all.append(event)
                kinds[event.kind] = kinds.get(event.kind, 0) + 1
                if event.kind == "result":
                    result_seen = True
                    # a live-steering run stays open after result; end it like the runner does
                    if process.stdin and not process.stdin.closed:
                        process.stdin.close()
            if time.time() - started > TIME_CAP_SECONDS:
                os.killpg(process.pid, 15)
                break
    process.wait(timeout=30)
    stderr = process.stderr.read() if process.stderr else ""
    print("exit", process.returncode, "seconds", round(time.time() - started, 1))
    print("env keys passed:", sorted(env))
    print("neutral event kinds:", kinds, "result_seen:", result_seen)
    for event in neutral_all:
        if event.kind in ("usage", "result", "rate_limit"):
            payload = event.usage or event.result or event.rate_limit or {}
            print(event.kind, {k: v for k, v in payload.items() if k != "text"})
    print("stderr tail:", stderr[-300:].replace("\n", " | "))
    print("worktree files:", sorted(p.name for p in work.iterdir() if p.name != ".git"))
    log = subprocess.run(
        ["git", "log", "--oneline", "-3"], cwd=work, capture_output=True, text=True, check=False
    )
    if LEAK:
        fmt = work / "fmt.py"
        print("fmt.py:", repr(fmt.read_text()) if fmt.exists() else None)
    print("git log:", log.stdout.strip().replace("\n", " | "))


if __name__ == "__main__":
    main()
