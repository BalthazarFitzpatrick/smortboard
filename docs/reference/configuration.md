# Configuration

## Flags and environment

| Flag | Env | Default |
|---|---|---|
| `--port` | `SMORTBOARD_PORT` | `8000` (`8001` with `--demo`) |
| `--host` | `SMORTBOARD_HOST` | `127.0.0.1` |
| `--db` | `SMORTBOARD_DB` | user data dir |
| `--no-browser` | | opens a tab |
| `--demo` | | off |
| `--version` | | |
| | `SMORTBOARD_CARD_IMAGE` | `smortboard-card:latest` |
| | `SMORTBOARD_CARD_TOKEN_PATH` | `~/.config/smortboard/card_token` |
| | `SMORTBOARD_OPERATOR_NAME` | your `git config user.name` |

A flag beats the matching `SMORTBOARD_*` env var, which beats the default.

## The data directory

The platform's user data dir: `~/Library/Application Support/smortboard` on macOS, the equivalent
elsewhere. Created mode 700, it holds `smortboard.db`. It is never the working directory, so running
the board from anywhere scatters no database files.

Configuration and tokens live apart, under `~/.config/smortboard` (`%APPDATA%\smortboard` on
Windows). See [Credential files](credentials.md).

## The settings panel

`o` opens three groups: how the board behaves, which lab and model each role runs on, and what it may
spend.

<table><tr>
<td width="33%"><img src="../images/settings-general.jpg" alt="General board settings: the mouse toggle, how many cards run at once globally and per board, strict or soft file leases per board, the resume briefing, the gate timeout and the mall cam interval" width="100%"><br><sub>general — how it behaves</sub></td>
<td width="33%"><img src="../images/settings-labs.jpg" alt="Labs and models: automatic credential profile rotation, whether to ask before switching to a fallback model on a usage limit, and a primary model, a fallback order and an effort for the worker, reviewer, orchestrator and fold" width="100%"><br><sub>labs and models — who runs what</sub></td>
<td width="33%"><img src="../images/settings-cost.jpg" alt="Spend caps per run: a card run, a review, a mission control turn, a fold, and a per-card total across every run" width="100%"><br><sub>cost control — what it may spend</sub></td>
</tr></table>

Each is a stored setting:

| Kind | Keys |
|---|---|
| lab and model per role | the `*_lab` and `*_model` pairs, and their `*_cross_lab_fallback` lists |
| effort per role (unset passes no effort flag) | `worker_effort`, `reviewer_effort`, `orchestrator_effort`, `fold_effort` |
| spend cap per run | `worker_budget_usd`, `reviewer_budget_usd`, `orchestrator_budget_usd`, `fold_budget_usd` |
| spend cap per card | `card_total_budget_usd` |
| absolute paths mission control may also read | `mission_control_read_paths` |
| the rest | `findings_route`, `max_parallel`, `resume_briefing`, `gate_timeout_seconds`, `auto_switch_profiles`, `usage_limit_route` |

Per board: its own parallel cap, its `daily_budget_usd`, its merge mode and its lease mode.
