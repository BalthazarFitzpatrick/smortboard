# Install

## Requirements

| Tool | Why | Get it |
|---|---|---|
| Python 3.11+ and **uv** | runs the board | [uv install](https://docs.astral.sh/uv/getting-started/installation/) |
| **Docker** (Desktop or Engine), running | every card runs in its own container | [Docker Desktop](https://www.docker.com/products/docker-desktop/) |
| **GitHub CLI**, logged in | the board opens pull requests with it | [cli.github.com](https://cli.github.com/), then `gh auth login` |
| git | worktrees, branches, pushes | usually already there |

Plus at least one lab. The card image has both CLIs; locally you need only the one that mints the
credential:

| Lab | Install locally | To get a credential |
|---|---|---|
| **Claude Code** | `npm install -g @anthropic-ai/claude-code` | `claude setup-token` |
| **OpenAI Codex** | `npm install -g @openai/codex` or `brew install codex` | `codex login`, which writes `~/.codex/auth.json` |

**One lab runs the board.** A second adds a reviewer from another lab, and a fallback when the first
hits its rate limit.

**Python and uv open the board.** The rest matters once a card runs. The pre-flight checklist (`h`)
names each gap and its fix.

## Steps

### 1. Clone and build the card image

The image holds git, uv, Claude Code and Codex. No credentials.

```bash
git clone https://github.com/BalthazarFitzpatrick/smortboard && cd smortboard
uv sync
docker build -f docker/card.Dockerfile -t smortboard-card:latest .
```

### 2. Start the board

```bash
uv run smortboard
```

It prints and opens `http://127.0.0.1:8000/ui/index.html?key=...`. The first load swaps the key for
a cookie and drops it from the address bar, so a tab opened by hand has no key. Use the printed
link. Keep the terminal open: closing it stops the board and any running card.

### 3. Give cards a credential

`shift`+`p`, add a profile, paste, **activate**. A fresh board starts with an empty `default`
profile active; remove it once yours is. The board writes the credential file itself, at mode 600,
and refuses one others could read.

**Cards never use your own login.** What you paste:

| Lab | Paste |
|---|---|
| **Claude** | The token `claude setup-token` prints once. A model-only token, not your Claude Code session. |
| **OpenAI** | **ChatGPT login JSON**: all of the `~/.codex/auth.json` that `codex login` wrote. A bare access token from inside it is refused. Or an **API key**. |

At run time the credential reaches the container over stdin, into a memory-backed home that dies
with the container. Your own `~/.claude` and `~/.codex` are never mounted.

**Several profiles per lab.** In `o`, labs and models, set **usage limits** to **switch by itself**:
a limit moves the card to your next credential profile, then to the fallback model. The default,
**wait for the reset**, parks the board until the window resets. File locations:
[Credential files](../reference/credentials.md).

### 4. Keep `main` for people

**Agents merge into `development`, a person merges into `main`.** That holds for the board's agents
and for your own Claude Code or Codex sessions. One hook script,
[`tools/claude-hooks/protect-main.sh`](https://github.com/BalthazarFitzpatrick/smortboard/blob/main/tools/claude-hooks/protect-main.sh),
refuses the rest in both CLIs: a commit on `main`, a push to it by any refspec, and merging a pull
request whose base is not `development`. It needs `bash`, `jq` and a logged-in `gh`.

```bash
mkdir -p ~/.claude/hooks ~/.codex/hooks
cp tools/claude-hooks/protect-main.sh ~/.claude/hooks/
cp tools/claude-hooks/protect-main.sh ~/.codex/hooks/
chmod +x ~/.claude/hooks/protect-main.sh ~/.codex/hooks/protect-main.sh
```

Claude Code, in `~/.claude/settings.json` (merge into an existing `PreToolUse` list):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "bash ~/.claude/hooks/protect-main.sh"}]
      }
    ]
  }
}
```

Codex, in `~/.codex/hooks.json`. Codex sends shell calls as `Bash` with the same payload and honours
the same exit code, so one script serves both:

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

**Check both.** Ask the agent to run `git push origin HEAD:main`. It should be refused.

**The hook sees only what an agent runs through its shell tool.** The GitHub ruleset in
[`tools/github/protect-main.json`](https://github.com/BalthazarFitzpatrick/smortboard/blob/main/tools/github/protect-main.json)
guards `main` against everything else. Use both. [Protect main](../protect-main.md) has the
`development` branch setup, a per-repo variant of the hook and the ruleset commands.

## Without a checkout

`uvx --from git+https://github.com/BalthazarFitzpatrick/smortboard smortboard` runs the board.
Building the image still needs the clone once.
