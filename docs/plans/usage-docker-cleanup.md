## context

smortboard has loose ends (stale branches, an open plan pr, two queued ledger units), a docker preflight that gives a false negative, and a usage card that only shows what runs happened to report. goal: close the loose ends, fix preflight, and read real account usage for claude and codex.

execution: all units run through sol 6.1 (the codex plugin, `codex:codex-rescue`). each unit goes on the repo's board as a card, one worktree per unit off `development`. git mode: free (recorded after approval with `sh ~/.claude/hooks/session-merge-mode.sh bcfcd08a-4a7b-48ab-b3cd-adff19c900b2 free`).

## findings

- origin/development at ec451dc (pr #298). only open pr: #299 (`docs/plans/execution-tiers.md`, parked, must be re-planned against development before any code).
- ledger: 90 tasks, 2 queued: `runmode-u7` (docs/readme for two run modes), `runmode-u10` (out-of-card sessions as a separate cost line). worklog stops at 2026-10-03, behind prs #289-#298.
- stale: `chore/readme-deep-legibility` (1 unpushed commit, 267 behind), `feature/board-from-online-repo` (superseded by #245/#246), old `card/*`, `feature/repo-*` lint branches, `run-main` / `run-development` detached trees, worktree `9bd5a207-...`.
- docker: `smortboard/preflight.py:75-96` `_docker_check` -> `exec/backends.py:216-234` `docker_available()` runs `docker version --format {{.Server.Version}}`, returns bool on rc==0. permission denied on `/var/run/docker.sock` (user not in docker group) exits non-zero, collapses to "daemon not answering", and the fix text (`sudo systemctl start docker`) is wrong. screenshot (`screenshots/image.png`) matches: systemd shows dockerd active, preflight red, card image row red as a knock-on. the permission-denied cause is inferred from code plus the user's description, not shown in the screenshot (unverified until stderr is captured).
- usage: windows come from per-run `rate_limit_event`s (`labs/claude_code.py` ~375-394, `telemetry.py` `usage_projection` ~190, `/api/usage` in `server/app.py` ~514). stale when idle, blind to non-smortboard use, last-writer-wins. codex parses no rate limits at all (`labs/codex.py`).
- nimbalyst not installed locally, so its method is unknown. verified local sources: claude binary has `/api/oauth/usage` + `oauth-2025-04-20`; `~/.claude/usage-watcher/state/last.json` has account-wide `five_hour` / `seven_day`; codex rollouts in `~/.codex/sessions/**/rollout-*.jsonl` carry `token_count.rate_limits` (`window_minutes` 10080 on this account); codex binary has `account/rateLimits/read` via `codex app-server`. response shapes of oauth endpoint and app-server call are unverified.

## reported case (colleague, screenshot summarised)

report: docker runs (`systemctl status docker` active), image built, no container running, preflight does not see docker. user is not in the docker group on purpose (docker runs as root there, group = sudoless root). asks if that is the cause.

screenshot (smortboard ui at 127.0.0.1:8000 beside a terminal): preflight "2 of 6 ready". green: gh cli, git. red: docker ("installed but the daemon is not answering", fix shown `sudo systemctl start docker`), card image ("cannot check for smortboard-card:latest: docker is not reachable"), card token (no file at `~/.config/smortboard/card_token`), anthropic profile "default (active)" (same missing token file). terminal: dockerd active (running) since 10:41:08 via docker.socket, `healthcheck failed` error lines, earlier `sudo systemctl start docker` attempts with failed prompts. no `docker version` or permission denied output shown.

to check: run `docker version --format '{{.Server.Version}}'` as the user. permission denied on `/var/run/docker.sock` = group cause (answer: yes, likely). "cannot connect" = daemon or DOCKER_HOST cause. confirm `ls -l /var/run/docker.sock`, `id`, `echo $DOCKER_HOST`.

to fix: u3 (distinct permission_denied state, correct fix text, raw stderr shown). also open: card token and anthropic profile rows are red for a separate reason (missing token file); check that their fix text says how to create it, add a unit if not.

## units

unit: u0 prove usage sources small
expects: this machine, logged-in claude and codex
does: one call each: oauth usage endpoint, `codex app-server` `account/rateLimits/read`; record raw shape with tokens redacted
outputs: shapes written into units u1/u2 headers
verifies: claude call returns five_hour and seven_day with utilization and resets_at; codex call returns primary window with used_percent and window_minutes; if either fails, fall back per u1/u2 and note it

unit: u1 claude real usage
expects: oauth token (keychain / ~/.claude), `last.json`, run events
does: new `smortboard/usage_sources.py`: fetch oauth usage, else last.json (reject if `ts` stale), else run events; `usage_projection` merges by freshness and treats `resets_at` in the past as rolled over
outputs: `/api/usage` windows with source + fetched_at; usage card (`board.js` usageCard/windowRow) shows source and age
verifies: pytest with fixture response -> 5h 3%, 7d 29%; expired `resets_at` -> shown as reset; token missing -> falls to last.json; no token ever logged

unit: u2 codex real usage
expects: `codex app-server` or newest rollout jsonl
does: read rate limits via app-server, fall back to last non-null `token_count.rate_limits`; also parse it in `labs/codex.py` as neutral `rate_limit` items, window by `window_minutes` (300 five_hour, 10080 seven_day)
outputs: codex windows in `/api/usage` and the usage card; "codex has reported none yet" goes away
verifies: pytest on a real-shape rollout fixture -> seven_day 13.0% with resets_at; null secondary ignored

unit: u3 docker preflight fix
expects: `docker version` rc and stderr
does: `docker_available()` keeps its bool; add `docker_status()` returning ok / not_installed / permission_denied / daemon_down / other with raw stderr; `_docker_check` and `_image_check` use it
outputs: preflight detail shows stderr; permission_denied fix = `sudo usermod -aG docker $USER`, then re-login or `newgrp docker`; daemon_down keeps the systemctl fix; docs `install.md` and `troubleshooting.md` gain a docker-group note
verifies: `tests/test_preflight.py` with faked runner: stderr "permission denied while trying to connect to the Docker daemon socket" -> permission_denied + usermod fix; "Cannot connect to the Docker daemon" -> daemon_down; image row says "fix docker permissions", not "not reachable"

unit: u4 runmode-u7 docs
expects: existing ledger entry plus 2026-10-04 decision (whole readme rewrite)
does: replace "no second mode" text in docs, security reference, docstrings, CLAUDE.md; rebase `bc13edb` readme legibility onto development and fold it in
outputs: one pr
verifies: `grep -rn 'no second mode' docs smortboard CLAUDE.md` empty

unit: u5 runmode-u10 out-of-card costs
expects: `~/.claude/projects/*.jsonl`
does: ingest as a separate cost line beside card cost, never summed in (`telemetry.py`, `board_spend`)
outputs: `/api/costs` separate field
verifies: fixture session appears under the separate line, card total unchanged

unit: u6 cleanup
does: delete superseded and merged branches/worktrees (spell out each path and name; guard hook asks on unmerged/dirty); rebase the rest; refresh WORKLOG.md and TASKS.jsonl to cover #289-#298
verifies: `git branch -vv` and `git worktree list` show only live work; each deletion checked as merged first

unit: u7 execution-tiers (pr #299)
does: not built here. re-plan against development in a deep-tier session; pr stays open
verifies: n/a

whole-system test: fresh start with user outside the docker group: preflight shows permission_denied with the usermod fix; usage card shows claude 5h/7d and codex windows with source and age, no card runs needed.

order: u0 -> u1, u2, u3 in parallel (disjoint files, except `server/app.py` for u1/u2: serialise those two or have u2 land after u1) -> u4, u5, u6.

## verify

per unit: `uv run pytest tests/test_<thing>.py -q`, ruff before each commit, full `uv run pytest -q` before each pr. u1/u2 also checked live against `/api/usage` on a running board.
