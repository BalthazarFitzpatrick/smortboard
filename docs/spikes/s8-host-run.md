# s8: running a card agent on the host (open mode)

measured 2026-10-03 with claude code 2.1.288 and codex-cli 0.160.0 on macos. the card image runs
claude 2.1.273 (comment in `labs/claude_code.py`), so the cli version differs from the container
runs the existing fixtures came from.

## question

can a card's agent, claude code and codex, run headless on the host in a worktree cwd with a
scrubbed environment, and still emit the stream-json usage and result events that cost capture
reads, with the same shape as a container run?

decided in advance, falsified if:

- a usage or result event with cost or tokens is missing
- `--setting-sources project` still loads user-level hooks or settings from the real `~/.claude`
- the scrubbed env breaks auth or the cli

## procedure

- throwaway repo and worktree in the scratchpad, never inside smortboard. brief: create
  `hello.txt`, `git add`, `git commit`.
- argv from the real adapter: `ClaudeCodeAdapter.build_command(RunRequest(...))` with
  `stream_input=True` and `budget_usd=0.50`, model haiku. wrapped in the adapter's `auth_shell`
  (`IFS= read -r CLAUDE_CODE_OAUTH_TOKEN && export ... &&`) so the token arrives on stdin and is
  never in the spawn env. token from `read_card_token(profiles.active_profile_path())`, the board's
  configured profile file. never printed.
- `subprocess.Popen(["sh", "-c", ...], cwd=worktree, env=allowlist, start_new_session=True)`. no
  docker.
- stdout lines through `runner.parse_line`, then `adapter.normalize`, then `estimate_usage` (the
  `attribute` path in `run_process`).
- script: `tools/host_run_spike.py`. kill check: `tools/pgroup_kill_check.py`.

## minimal env allowlist

for claude, `PATH` alone is enough. measured, `env={PATH}`: exit 0, usage and result events, commit
made (worktree-local `user.name`/`user.email` set, so no global git config needed). `HOME` unset.

also measured working: `PATH,HOME,LANG,TMPDIR` with `HOME` real, and the same with `HOME` pointing
at an empty scratch dir. so a throwaway `HOME` does not break auth: the oauth token env var is the
whole credential, and claude created nothing it needed under `~`.

`HOME` only, without `PATH`: `sh: claude: command not found`, exit 127.

recommended for `HostBackend`: `PATH` (explicit, with the dir holding `claude`/`codex`/`git`/`uv`),
`LANG`, `TMPDIR`, and a per-card throwaway `HOME` (see leakage below for why). left out on purpose:
`GH_TOKEN`, `GITHUB_TOKEN`, `SSH_AUTH_SOCK`, `AWS_*`, anything credential-shaped. codex adds
`CODEX_HOME` (its own dir, below).

caveat: a repo whose test command needs tools under `~` (uv cache, nvm, cargo) will want those dirs
in `PATH` or `HOME` passed through. that is a per-board choice for u3, not a spike finding.

## claude results

run `c2` (real `HOME`, haiku, 3 turns, exit 0, 11 s). neutral events after `normalize`:

| kind | count |
|---|---|
| usage | 1 |
| result | 1 |
| rate_limit | 4 (two per `rate_limit_event`, windows `five_hour` and `seven_day`) |
| tool_use | 2 |
| assistant_text | 1 |

- usage fields: `input_tokens` 26, `output_tokens` 804, `cached_tokens` 15061,
  `cache_creation_tokens` 8215, `cost_usd` 0.0219821, `cost_estimated` false
- result fields: `ok` true, `subtype` success, `session_id`, `num_turns` 3, `auth_failed` false,
  `blocked_reason_code` none, `cost_usd` equal to the usage `cost_usd`
- raw `result` event keys include `total_cost_usd`, `modelUsage` (per model: `inputTokens`,
  `outputTokens`, `cacheReadInputTokens`, `cacheCreationInputTokens`, `costUSD`), `usage`,
  `permission_denials`, `session_id`, `num_turns`, `subtype`, `is_error`, `api_error_status`
- raw `rate_limit_event` carries `rate_limit_info.unifiedWindows.{five_hour,seven_day}` with
  `utilization` and `resetsAt`, the shape `normalize` already reads

comparison with a container run: no docker was started and no recorded real container stream is in
the repo. the reference is the field table in `s1-s2-findings.md` (`subtype`, `is_error`, `usage`,
`modelUsage`, `total_cost_usd`, `session_id`, `permission_denials`, `num_turns`) and the fixtures in
`tests/test_cost_telemetry.py` and `tests/test_exec.py` (`modelUsage` with `inputTokens`,
`outputTokens`, `costUSD`; `rate_limit_event` with `unifiedWindows`). every field those name is
present in the host stream and `cost_estimated` is false, as for a container run. transport is not
part of the event shape: stdout comes from the same cli either way. not compared: a live container
run on 2.1.273.

repeat runs (`PATH` only, scratch `HOME`, leak variants): every run produced exactly one usage and
one result with `cost_usd` set and `cost_estimated` false.

## user-level leakage

`~/.claude/settings.json` (read-only) declares hooks on `PreToolUse` (bash: `block-main-commit.sh`),
`PostToolUse` (`usage-watcher/guard.sh`, and `format-python.sh` on edit|write), `UserPromptSubmit`,
`Stop`, `SessionStart`, `SessionEnd`, plus a `statusLine`.

the stream shows no hook events at all in the project run (event types seen: assistant, user,
system/init, system/thinking_tokens, system/vcs_state_changed, rate_limit_event, result). absence in
the stream is weak evidence on its own, so two behavioural probes were run in a throwaway repo with
a `pyproject.toml` and the worktree on a branch named `main`. the brief: write `fmt.py` containing
`x   =   1`, then `git add` and `git commit` it.

| run | argv | result |
|---|---|---|
| project (the adapter's) | `--setting-sources project` | `fmt.py` stays `'x   =   1'` (format-python did not run). commit on branch `main` succeeded: `8811722 added fmt` (block-main-commit did not run) |
| control | same argv with `user,project` | `fmt.py` became `'x = 1\n'` (format-python ran). two bash calls got `PreToolUse:Bash hook error: ... BLOCKED: refusing to write to main` and `BLOCKED: run branch switches separately`. the agent switched to `feature/fmt-file` and committed. 2 permission denials |

both probes ran with the real `HOME` and the same env, so the only difference is the setting
source. `--setting-sources project` does not load the real `~/.claude` hooks, for the two hook
types probed (pretooluse and posttooluse). not probed: `SessionStart`, `Stop`, `UserPromptSubmit`,
`SessionEnd`, `statusLine`. unverified hypothesis: they are skipped the same way, since the whole
user settings source is not loaded.

other things the init event showed with the real `HOME`: `memory_paths.auto` points at
`~/.claude/projects/<mangled cwd>/memory/`, so the auto-memory directory of the host `HOME` is still
in play, and claude writes session transcripts under `~/.claude/projects/`. a per-card scratch
`HOME` removes both reaches. it also hides the real `~/.claude/CLAUDE.md` if that is read as
memory outside `--setting-sources` (unverified: not measured whether the user claude.md is loaded
under `project`; the control did not test it).

## codex results

falsification not reached for codex, auth blocked. `codex exec --json --ephemeral
--ignore-user-config --sandbox workspace-write ...` built by `CodexAdapter.build_command` ran on
the host with `CODEX_HOME` set to a scratch dir and env `PATH,HOME,LANG,TMPDIR,CODEX_HOME`. the
board's openai profile (`bf_openai`, kind `auth_json`, last written 2026-09-18) was written to
`$CODEX_HOME/auth.json` through stdin the way `auth_shell` does it.

result: the cli started and streamed events (`thread.started`, `item.completed`, `turn.started`,
`error` x12 "reconnecting... workspace routing discovery unauthorized (401)", `turn.failed`).
stderr: "your refresh token was revoked. please log out and sign in again". `normalize` gave one
`result` event: `ok` false, `auth_failed` true, `blocked_reason_code` crash. no `usage` event,
because `turn.failed` has no `usage` object. spend 0.

i did not use `~/.codex/auth.json` or any other credential. a usable codex login is needed to
finish this half: refresh the board's `bf_openai` profile (`codex login`, then import), and rerun
`uv run python tools/host_run_spike.py codex <scratch> --home real`. until then these are not
measured: that a usage event with tokens appears on a host run (cost is `None` for codex either
way, the adapter sets `cost_usd` none and relies on catalog prices), whether the macos seatbelt
`workspace-write` sandbox lets the agent commit in a worktree (container run needed
`writable_roots=["/workspace/.git"]`; a worktree's git dir is `<repo>/.git/worktrees/<id>`, outside
the cwd), and the time cap behaviour. what did work: cli on host, own `CODEX_HOME`, stdin credential
handoff, event normalization. note `--ignore-user-config` left the real `~/.codex` untouched.

## codex rerun (2026-10-04)

the first codex attempt failed for a reason that was not auth and not the host: the spike script
defaulted to model `gpt-5.4-mini`, which the api refuses for a chatgpt account (http 400 "model is not
supported when using Codex with a ChatGPT account"). the first rerun with a fresh login reproduced the
`error_during_execution` the result event reported; the raw stream carried the 400. the script now
defaults to `gpt-5.6-luna`, a catalog model.

rerun on the host with `gpt-5.6-luna`, scratch `CODEX_HOME`, env `PATH,HOME,LANG,TMPDIR,CODEX_HOME`:

- exit 0 in 20 s. usage event present: `input_tokens` 38549, `cached_tokens` 34048,
  `output_tokens` 729. `cost_usd` is `None`, as in a container (the adapter prices from the catalog).
  result event: `ok` true, `auth_failed` false.
- the agent created `hello.txt` but could not `git add` it: the macos `workspace-write` sandbox denied
  `index.lock`, because a worktree's git dir is `<repo>/.git/worktrees/<id>`, outside the cwd. the
  container run solved this with `writable_roots=["/workspace/.git"]`.

so the one thing a codex host worker needs is the repo's real git dir in `writable_roots`. the host
backend passes it (`host_git_dirs`) and gives codex its own `CODEX_HOME` holding the login and the
lease hooks. verdict for codex: confirmed apart from that, now built.

## process group kill

replacement for `docker rm -f` (`ProcessHandle.terminate`, `runner.py` ~244). `tools/pgroup_kill_check.py`
starts `sh -c "sleep 301 & sleep 302 & wait"` and stops it two ways:

- `proc.terminate()` on a plain child: `children [70192, 70193] alive after: [70192, 70193]`
- start with `start_new_session=True`, stop with `os.killpg(proc.pid, SIGTERM)`: `children
  [70290, 70291] alive after: []`

so the host handle needs `start_new_session=True` at `Popen`, and `terminate` must send sigterm to
the group, wait, then sigkill the group on timeout. a plain `terminate()` leaks every child the
agent spawned (test runs, dev servers). the same holds for the time-cap and stall watchdogs, which
call `handle.terminate()`.

## spend

claude only. per-run `total_cost_usd`: 0.0220, 0.0085, 0.0782 (control), 0.0085, 0.0109. total about
0.13 usd, against the 2 usd limit. codex 0.

## verdict

| lab | verdict |
|---|---|
| claude | confirmed. usage and result events present with cost and tokens, `cost_estimated` false, scrubbed env (even `PATH` only) does not break auth or the cli, `--setting-sources project` did not load user hooks (two hook types shown by control) |
| codex | partly. cli, scratch `CODEX_HOME`, credential handoff and normalization work on the host; usage/cost events and the sandbox with a worktree git dir unmeasured, blocked by a revoked stored refresh token |

nothing falsified. the re-plan trigger did not fire for claude. codex is not falsified, only
unfinished.

## consequences for the runmode cards

- u3 (`HostBackend`, env scrub): build env from an allowlist (`PATH`, `LANG`, `TMPDIR`, and
  `CODEX_HOME` for codex) plus a per-card throwaway `HOME`; reuse `adapter.auth_shell` and the stdin
  token line unchanged for claude. codex `auth_shell` hardcodes `/home/agent/.codex` and
  `build_command` hardcodes `/workspace/.git` in `writable_roots`, both need parametrising
  (already in the plan). `run_process` must pass `env=` (it defaults to `None`, the full-env leak)
  and `start_new_session=True`. set worktree git identity in the worktree config: with no global git
  config the agent still committed. `ProcessHandle.terminate` needs a process-group branch.
- u3 also: `guard_mounts` and `write_container_guards` have no host equivalent; the host run here
  used no `--settings` file, so lease hooks on the host are untested. `--settings <path>` still works
  alongside `--setting-sources project`, but that combination was not run.
- u4 (gate, reviewer): reviewer runs the same argv path with read-only tools; no new finding. the
  gate is a plain subprocess in the worktree; the env allowlist applies there too.
- u5 (mission control): same argv and env as the worker; no finding.
- u6 (preflight): host mode needs the cli on `PATH` and a token file; docker not needed. add a
  readiness check for codex login freshness, since a revoked refresh token only shows at run time as
  `auth_failed` after a dozen 401 retries (30 s here).
- before u3 relies on codex: rerun the codex half with a fresh login.
