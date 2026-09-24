# keep main for people, give development to the agents

**agents merge into `development`. a person merges `development` into `main`.** that covers the
board's cards and any claude code or codex session you run next to it. three pieces make it hold:

1. a `development` branch that exists on github
2. a hook, shared by claude code and codex, that lets agents merge into `development` and refuses
   anything that writes `main`
3. a github ruleset on `main`, so the rule holds even for a tool the hook never sees

the hook catches agent mistakes. the ruleset is the real protection. use both.

## 1. the development branch

```bash
git switch -c development origin/main
git push -u origin development
```

register the repo on the board with `development` as its default branch (`b`, or
`PATCH /api/repos/<id>` with `{"default_branch": "development"}`). cards then land there, and the
board keeps one pull request from `development` into `main` open for you.

## 2. the hook

[`tools/claude-hooks/protect-main.sh`](https://github.com/BalthazarFitzpatrick/smortboard/blob/main/tools/claude-hooks/protect-main.sh)
is a `PreToolUse` hook for claude code's `Bash` tool. it needs `bash`, `jq` and a logged-in `gh`.

| the agent runs | the hook |
|---|---|
| commits, pushes and merges on any branch but main | allows |
| `gh pr merge` on a pull request whose base is `development` | allows |
| `gh pr merge` on a pull request into `main`/`master` | refuses |
| `gh pr merge` on a pull request into any other branch | refuses (agents merge into `development` only) |
| `gh pr merge` when github can't say what the base is | refuses |
| `git commit` (or merge, rebase) while on `main`/`master` | refuses |
| `git push` whose target is `main`/`master`, from any branch (`HEAD:main`, `+main`) | refuses |

a refusal exits with code 2 and a one-line reason, which claude code shows to the agent.
`PROTECT_MAIN_DEV_BRANCH` changes the development branch's name.

**for this repo only**, add it to the project's `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {"type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR\"/tools/claude-hooks/protect-main.sh"}
        ]
      }
    ]
  }
}
```

**for every repo you work in**, copy it once and reference it from `~/.claude/settings.json`,
merging the entry into any `PreToolUse` list already there:

```bash
mkdir -p ~/.claude/hooks
cp tools/claude-hooks/protect-main.sh ~/.claude/hooks/
chmod +x ~/.claude/hooks/protect-main.sh
```

```json
{"type": "command", "command": "bash ~/.claude/hooks/protect-main.sh"}
```

**for codex**, the same script works unchanged. codex sends a shell call as `tool_name` `Bash` with
`tool_input.command`, and a hook exiting 2 with a reason on stderr refuses it
([s6](spikes/s6-codex-hooks.md)). copy it and register it in `~/.codex/hooks.json`:

```bash
mkdir -p ~/.codex/hooks
cp tools/claude-hooks/protect-main.sh ~/.codex/hooks/
chmod +x ~/.codex/hooks/protect-main.sh
```

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "^Bash$",
        "hooks": [{"type": "command", "command": "bash ~/.codex/hooks/protect-main.sh", "timeout": 15}]
      }
    ]
  }
}
```

codex runs only a hook it has recorded as trusted, under `[hooks.state]` in `~/.codex/config.toml`.

**check it.** `/hooks` in claude code lists it. then ask either agent to run
`git push origin HEAD:main`. it should be refused. `tests/test_protect_main_hook.py` covers every
row of the table above against a stubbed `gh`, plus a codex-shaped payload.

**the hook sees only commands an agent runs through its `Bash` tool.** a command in your own
terminal, a gui client or a script outside the agent goes straight past it. that is what the
ruleset is for.

## 3. the ruleset on main

[`tools/github/protect-main.json`](https://github.com/BalthazarFitzpatrick/smortboard/blob/main/tools/github/protect-main.json)
is a repository ruleset for the default branch: no deletion, no force-push, changes only through a
pull request.

```bash
gh api -X POST repos/<owner>/<repo>/rulesets --input tools/github/protect-main.json
gh api repos/<owner>/<repo>/rulesets --jq '.[] | "\(.id) \(.name) \(.enforcement)"'
```

**with ci in place**, add its job as a required status check on the same ruleset, so a pull request
into `main` can't merge while tests fail. smortboard's own `main` requires `test (3.11)`.

**github offers rulesets on private repositories only with a paid plan.** on a free private
repository the hook is the only guard.
