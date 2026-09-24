# install

## requirements

| tool | why | get it |
|---|---|---|
| python 3.11+ and **uv** | runs the board | [uv install](https://docs.astral.sh/uv/getting-started/installation/) |
| **docker** (desktop or engine), running | every card runs in its own container | [docker desktop](https://www.docker.com/products/docker-desktop/) |
| **github cli**, logged in | the board opens pull requests with it | [cli.github.com](https://cli.github.com/), then `gh auth login` |
| git | worktrees, branches, pushes | usually already there |

plus at least one lab. the card image has both clis; locally you need only the one that mints the
credential:

| lab | install locally | to get a credential |
|---|---|---|
| **claude code** | `npm install -g @anthropic-ai/claude-code` | `claude setup-token` |
| **openai codex** | `npm install -g @openai/codex` or `brew install codex` | `codex login`, which writes `~/.codex/auth.json` |

**one lab runs the board.** a second adds a reviewer from another lab, and a fallback when the first
hits its rate limit.

**python and uv open the board.** the rest matters once a card runs. the pre-flight checklist (`h`)
names each gap and its fix.

## steps

### 1. clone and build the card image

the image holds git, uv, claude code and codex. no credentials.

```bash
git clone https://github.com/BalthazarFitzpatrick/smortboard && cd smortboard
uv sync
docker build -f docker/card.Dockerfile -t smortboard-card:latest .
```

### 2. start the board

```bash
uv run smortboard
```

it prints and opens `http://127.0.0.1:8000/ui/index.html?key=...`. the first load swaps the key for
a cookie and drops it from the address bar, so a tab opened by hand has no key. use the printed
link. keep the terminal open: closing it stops the board and any running card.

### 3. give cards a credential

`shift`+`p`, add a profile, paste, **activate**. a fresh board starts with an empty `default`
profile active; remove it once yours is. the board writes the credential file itself, at mode 600,
and refuses one others could read.

**cards never use your own login.** what you paste:

| lab | paste |
|---|---|
| **claude** | the token `claude setup-token` prints once. a model-only token, not your claude code session. |
| **openai** | **chatgpt login json**: all of the `~/.codex/auth.json` that `codex login` wrote. a bare access token from inside it is refused. or an **api key**. |

at run time the credential reaches the container over stdin, into a memory-backed home that dies
with the container. your own `~/.claude` and `~/.codex` are never mounted.

**several profiles per lab.** in `o`, labs and models, set **usage limits** to **switch by itself**:
a limit moves the card to your next credential profile, then to the fallback model. the default,
**wait for the reset**, parks the board until the window resets. file locations:
[credential files](../reference/credentials.md).

### 4. keep `main` for people

**agents merge into `development`, a person merges into `main`.** that holds for the board's agents
and for your own claude code or codex sessions. one hook script,
[`tools/claude-hooks/protect-main.sh`](https://github.com/BalthazarFitzpatrick/smortboard/blob/main/tools/claude-hooks/protect-main.sh),
refuses the rest in both clis: a commit on `main`, a push to it by any refspec, and merging a pull
request whose base is not `development`. it needs `bash`, `jq` and a logged-in `gh`.

```bash
mkdir -p ~/.claude/hooks ~/.codex/hooks
cp tools/claude-hooks/protect-main.sh ~/.claude/hooks/
cp tools/claude-hooks/protect-main.sh ~/.codex/hooks/
chmod +x ~/.claude/hooks/protect-main.sh ~/.codex/hooks/protect-main.sh
```

claude code, in `~/.claude/settings.json` (merge into an existing `PreToolUse` list):

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

codex, in `~/.codex/hooks.json`. codex sends shell calls as `Bash` with the same payload and honours
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

codex runs only a hook it has recorded as trusted, under `[hooks.state]` in `~/.codex/config.toml`.

**check both.** ask the agent to run `git push origin HEAD:main`. it should be refused.

**the hook sees only what an agent runs through its shell tool.** the github ruleset in
[`tools/github/protect-main.json`](https://github.com/BalthazarFitzpatrick/smortboard/blob/main/tools/github/protect-main.json)
guards `main` against everything else. use both. [protect main](../protect-main.md) has the
`development` branch setup, a per-repo variant of the hook and the ruleset commands.

## without a checkout

`uvx --from git+https://github.com/BalthazarFitzpatrick/smortboard smortboard` runs the board.
building the image still needs the clone once.
