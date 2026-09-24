# Keep main for people, give development to the agents

**Agents merge into `development`. A person merges `development` into `main`.** That covers the
board's cards and any Claude Code or Codex session you run next to it. Three pieces make it hold:

1. a `development` branch that exists on GitHub
2. a hook, shared by Claude Code and Codex, that lets agents merge into `development` and refuses
   anything that writes `main`
3. a GitHub ruleset on `main`, so the rule holds even for a tool the hook never sees

The hook catches agent mistakes. The ruleset is the real protection. Use both.

## 1. The development branch

```bash
git switch -c development origin/main
git push -u origin development
```

Register the repo on the board with `development` as its default branch (`b`, or
`PATCH /api/repos/<id>` with `{"default_branch": "development"}`). Cards then land there, and the
board keeps one pull request from `development` into `main` open for you.

## 2. The hook

[`tools/claude-hooks/protect-main.sh`](https://github.com/BalthazarFitzpatrick/smortboard/blob/main/tools/claude-hooks/protect-main.sh)
is a `PreToolUse` hook for Claude Code's Bash tool. It needs `bash`, `jq` and a logged-in `gh`.

| The agent runs | The hook |
|---|---|
| commits, pushes and merges on any branch but main | allows |
| `gh pr merge` on a pull request whose base is `development` | allows |
| `gh pr merge` on a pull request into `main`/`master` | refuses |
| `gh pr merge` on a pull request into any other branch | refuses (agents merge into `development` only) |
| `gh pr merge` when GitHub can't say what the base is | refuses |
| `git commit` (or merge, rebase) while on `main`/`master` | refuses |
| `git push` whose target is `main`/`master`, from any branch (`HEAD:main`, `+main`) | refuses |

A refusal exits with code 2 and a one-line reason, which Claude Code shows to the agent.
`PROTECT_MAIN_DEV_BRANCH` changes the development branch's name.

**For this repo only**, add it to the project's `.claude/settings.json`:

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

**For every repo you work in**, copy it once and reference it from `~/.claude/settings.json`,
merging the entry into any `PreToolUse` list already there:

```bash
mkdir -p ~/.claude/hooks
cp tools/claude-hooks/protect-main.sh ~/.claude/hooks/
chmod +x ~/.claude/hooks/protect-main.sh
```

```json
{"type": "command", "command": "bash ~/.claude/hooks/protect-main.sh"}
```

**For Codex**, the same script works unchanged. Codex sends a shell call as `tool_name` `Bash` with
`tool_input.command`, and a hook exiting 2 with a reason on stderr refuses it
([S6](spikes/S6-codex-hooks.md)). Copy it and register it in `~/.codex/hooks.json`:

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

Codex runs only a hook it has recorded as trusted, under `[hooks.state]` in `~/.codex/config.toml`.

**Check it.** `/hooks` in Claude Code lists it. Then ask either agent to run
`git push origin HEAD:main`. It should be refused. `tests/test_protect_main_hook.py` covers every
row of the table above against a stubbed `gh`, plus a Codex-shaped payload.

**The hook sees only commands an agent runs through its Bash tool.** A command in your own
terminal, a GUI client or a script outside the agent goes straight past it. That is what the
ruleset is for.

## 3. The ruleset on main

[`tools/github/protect-main.json`](https://github.com/BalthazarFitzpatrick/smortboard/blob/main/tools/github/protect-main.json)
is a repository ruleset for the default branch: no deletion, no force-push, changes only through a
pull request.

```bash
gh api -X POST repos/<owner>/<repo>/rulesets --input tools/github/protect-main.json
gh api repos/<owner>/<repo>/rulesets --jq '.[] | "\(.id) \(.name) \(.enforcement)"'
```

**With CI in place**, add its job as a required status check on the same ruleset, so a pull request
into `main` can't merge while tests fail. smortboard's own `main` requires `test (3.11)`.

**GitHub offers rulesets on private repositories only with a paid plan.** On a free private
repository the hook is the only guard.
