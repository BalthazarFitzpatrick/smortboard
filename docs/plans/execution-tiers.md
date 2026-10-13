# execution tiers: sealed + mounts, hybrid, open

<!-- parked plan, written 2026-10-05 against main @0ab6dc2. file:line refs below point at main -->

## read first: overlap with development

since this plan was mapped, development gained a host run mode (#288-#298). before executing,
re-plan the overlapping units against development in a deep-tier session.

already on development:
- board `run_mode` sealed|open + global `allow_open_mode` switch (`store/api.py:175,492-509`,
  migration 31): covers the board half of unit 1, but not hybrid or the per-card override
- `HostBackend` (`exec/backends.py:843`): scrubbed env, host guards, push neutralised, process-group
  stop, real worktree (no clone, no fetch-back). covers most of units 4 and 5
- gate, base-red, reviewer and mission control on host in open mode (#295, #296): covers unit 6
  and goes further (the reviewer runs on host too, where this plan kept it sealed)
- readiness and preflight follow the run mode (#297): part of unit 9
- `docs/spikes/s8-host-run.md`: env allowlist and event shape. it does not cover read or write
  confinement, which unit 0 still has to prove

semantic gap: development's open mode runs a claude worker **without** an os sandbox (codex keeps
its own). that matches this plan's "unconfined", not its "open". deciding whether open becomes
sandboxed by default, with unconfined as the escape, is the first call of the re-plan.

still new: hybrid mode + per-card tier override, the claude sandbox on host, unconfined as a
badged escape, read-only mounts (units 2, 3), capabilities and port blocks (unit 7), ui (unit 8),
docs (unit 10), the menu layout study.


## context

every card runs in docker today, with no fallback on purpose (`smortboard/exec/backends.py:657-689`,
`docs/dev-board-brief.md:57`). two real needs break that:
- big untracked assets (data, fixtures, model weights) that would bloat the repo image or never reach
  a card, since a clone carries tracked files only
- host-only runtimes (coreml/metal, darwin rust targets, xcode, tableau desktop) that a linux
  container on a mac cannot run

outcome: one board setting with three modes, plus per-card overrides, keeping a stated boundary for
every card:

| board mode | default | per-card override |
|---|---|---|
| sealed | container + declared read-only mounts | none |
| hybrid | container + mounts | single cards run on host, in the lab's sandbox |
| open | host, in the lab's sandbox | single cards back into a container |

`unconfined` is a separate per-card escape (host card, no sandbox), badged, reason required, behind a
global gate. the brief's promise holds: no silent fallback, every unsealed card is visible.

decided in this session:
- test gate follows the card's tier (host card -> host gate, sandboxed, network off); the reviewer
  stays sealed read-only in docker
- mounts are read-only in v1
- host concurrency in v1 is declared only: own host cap, named capability mutex, per-card port block.
  measured cpu/ram admission is later (no runs table exists, `server/runs.py:94-135` is in memory)

out of scope: rw mounts, measured admission, a macos vm tier (tart; 2 guests per host by license),
mission control on host (stays sealed).

## findings that shape the units

- one backend seam: `RunnerBackend.run_card` (`backends.py:293-315`), called only at
  `lifecycle.py:748` via `require_card_runtime` (`lifecycle.py:630`). gate (`review/gates.py:137-158`),
  reviewer (`review/reviewer.py:218-271`) and mission control (`orchestrator.py:281-298`) build their
  own `docker run`
- guards are written with container paths *before* the backend is chosen (`lifecycle.py:681` ->
  `write_container_guards` `backends.py:110-135` -> `leases.write_lease_settings` `leases.py:215-269`).
  the hooks already read `root` from `lease.json`, so they are path-agnostic; `BashPolicy`
  (`labs/base.py:69-83`) already carries root/python/guard_dir
- hardcoded container paths to route through that seam: `backends.py:71,73,88,90`,
  `labs/codex.py:39,62,74,200`, `attention.py:39` (`_WORKSPACE_PREFIX`), `runner.py:147` prompt text
- codex is sandboxed already (`--sandbox workspace-write`, `codex.py:33-39`); claude has no `sandbox`
  block anywhere — the container is its only boundary today
- lease rows are path globs only (`card_leases.path_glob`, `store/schema.py:117`); the hook and the
  post-run diff compile them as paths, so capabilities need their own table, not a `kind` column
- scheduler gates per card in `_tick_locked` (`scheduler.py:634`): dependency -> `_lease_wait` (:460)
  -> spend. a capability wait is a sibling of `_lease_wait`; `conflicting_run` (:468) covers manual
  starts
- patterns to copy: board enum + gate (`set_board_lease_mode` `store/api.py:449`, `_BOARD_MODE_GATES`
  :133, `gatedBoardToggle` `ui/board_settings.js:51`), card field (`effort`, migration #28,
  `CARD_WRITABLE_FIELDS`, `openComplexityMenu` `ui/card_panel.js:927`), repo list
  (`repo_remembered_leases`, `ui/boards.js:587`), path list setting (`mission_control_read_paths`),
  preflight row (`_check` `preflight.py:54`)

## units

execution: each unit becomes a card on smortboard's own board. standard tier unless marked deep.
order: 0 first (it can rewrite 4-6); 1, 2 and 3 in parallel; then 4; then 5, 6, 7; then 8, 9, 10.

```
unit:       0 spike: host sandbox mechanics                                   [deep]
expects:    macos host, current claude and codex clis, a throwaway clone at <tmp>/clone, a canary
            file at <tmp>/outside/canary.txt and one under ~/ (e.g. ~/.smortboard-canary)
does:       proves whether each lab's sandbox, run headless on the host, confines a worker the way
            a card needs
outputs:    docs/spikes/s8-host-sandbox.md with pass/fail per check and the exact config used
verifies:   decided up front, per lab:
            - write inside clone and clone/.git (commit) -> succeeds
            - write to <tmp>/outside -> refused
            - read ~/.smortboard-canary -> refused (if not achievable: record it; host tier ships
              with a "reads not confined" badge text instead of claiming it)
            - lease PreToolUse hook runs with sys.executable and blocks an out-of-lease Edit
            - claude: `sandbox` block honoured under `-p --permission-mode acceptEdits
              --setting-sources <ours>` with CLAUDE_CONFIG_DIR pointed at a temp dir (no read or
              write of the operator's ~/.claude)
            - gate: a plain command (`sh -c "curl example.com; touch ../x"`) under sandbox-exec or
              `codex sandbox` -> network refused, write outside refused
```

```
unit:       1 store: tiers and gates
expects:    current schema (last migration #29)
does:       adds board mode, card override, unconfined flag and their global gates
outputs:    migrations: boards.tier ('sealed'|'hybrid'|'open', default 'sealed'), cards.tier
            ('sealed'|'host'|null), cards.unconfined_reason (text|null); settings gates
            allow_host_cards, allow_unconfined; store.resolve_card_tier(card, board) -> 'sealed'|'host';
            PATCH board/card fields wired in server/app.py
verifies:   tests/test_tiers.py: sealed board + card tier 'host' -> refused; hybrid board, card null
            -> 'sealed', card 'host' -> 'host'; open board, card null -> 'host', card 'sealed' ->
            'sealed'; unconfined_reason on a sealed-resolving card -> refused; turning
            allow_host_cards off resets hybrid/open boards to sealed (same as set_settings :971)
```

```
unit:       2 store: repo mounts
expects:    nothing from other units
does:       lets a repo declare named read-only host paths and a card opt into some of them
outputs:    tables repo_mounts(id, repo_id, name, host_path, UNIQUE(repo_id,name)) and
            card_mounts(card_id, mount_id); store api + PATCH routes; validation: absolute path,
            existing dir or file, name [a-z0-9_-]+, refuses paths under ~/.ssh, ~/.claude, ~/.codex,
            the board data dir; helper card_mounts(card) -> [(name, host_path)]
verifies:   tests/test_repo_mounts.py: declare "fixtures" -> /tmp/x, card opts in, card_mounts returns
            [("fixtures", "/tmp/x")]; relative path, ~/.ssh path, duplicate name -> each refused;
            deleting the repo mount drops card_mounts rows
```

```
unit:       3 sealed mounts
expects:    unit 2's card_mounts(card)
does:       mounts a card's chosen sets read-only into worker, gate and reviewer containers
outputs:    `-v <host_path>:/mounts/<name>:ro` and `-e SMORTBOARD_MOUNT_<NAME>=/mounts/<name>` in
            backends._docker_command, gates.py and reviewer.py; one shared helper, not three copies
verifies:   tests/test_backends.py: card with "fixtures" -> docker argv contains the -v and -e pair
            in all three commands; card without mounts -> argv unchanged from today
```

```
unit:       4 runtime paths seam                                              [deep, cross-cutting]
expects:    current lifecycle and backend
does:       makes the backend, not constants, own workspace root, guard dir and python path, with
            container behaviour byte-identical
outputs:    RunnerBackend gains bash_policy(clone_path, guard_dir) -> BashPolicy; lifecycle asks the
            chosen backend before write_container_guards; codex writable_roots, WORKSPACE_PREAMBLE,
            attention._WORKSPACE_PREFIX and the runner prompt line read from it;
            require_card_runtime(store, card) selects by resolve_card_tier (host branch raises
            until unit 5 lands)
verifies:   full suite green with no test expectation edited for container runs; new test: a fake
            backend with root /x/y gets lease.json root "/x/y" and refusal targets stripped of "/x/y/"
```

```
unit:       5 host backend (worker)                                           [deep]
expects:    units 0, 1, 2, 4
does:       runs a card's worker on the host in a fresh temp clone under the lab's sandbox
outputs:    HostBackend.run_card mirroring ContainerBackend: _clone, _expose_upstream_base, run
            adapter command with cwd=clone, token passed to the subprocess only, CLAUDE_CONFIG_DIR /
            CODEX_HOME as per-run temp dirs, claude settings gain the `sandbox` block from unit 0
            (writes: clone, declared cache dirs; reads: mounts allowed), codex keeps workspace-write;
            unconfined_reason set -> no sandbox block / codex danger-full-access; env
            SMORTBOARD_MOUNT_<NAME>=<host_path>, PORT_BASE/PORT_COUNT from unit 7 when present;
            _fetch_back and post-run lease check reused, not copied
verifies:   tests/test_host_backend.py with a fake lab cli: argv has cwd=clone, settings contain the
            sandbox block, env has no token var leaking into a child `env` dump beyond the agent,
            commits fetched back to card/<id>; unconfined -> no sandbox block; one manual run on
            the demo board with a real lab: card commits a file, canary read refused (per unit 0)
```

```
unit:       6 host test gate
expects:    units 0, 1, 4
does:       runs a host card's test command on the host, sandboxed, network off, worktree
            read-only
outputs:    gates.run_test_gate branches on resolve_card_tier; host path wraps test_command in the
            sandbox mechanism unit 0 chose; same RUFF_CACHE_DIR / PYTEST_ADDOPTS env as today;
            sealed path unchanged
verifies:   tests/test_gates.py: host card -> argv starts with the sandbox wrapper and contains no
            `docker`; sealed card -> argv identical to today; real run: a test doing `uname -s`
            passes with "Darwin" on host, a test opening a socket fails
```

```
unit:       7 host admission: cap, capabilities, ports
expects:    unit 1
does:       admits host cards by a host cap, exclusive named capabilities and port blocks
outputs:    setting host_max_parallel (default 2, counted across boards); table
            card_capabilities(card_id, name); scheduler._capability_wait beside _lease_wait (exact
            name mutex across all boards, host or sealed); conflicting_run checks capabilities too;
            port allocator: each running host card gets PORT_BASE = 20000 + 100*slot, PORT_COUNT=100,
            released at run end; waiting reason strings shown in the existing queue view
verifies:   tests/test_scheduler.py: two cards both needing "tableau-desktop" never run together;
            different capabilities run together; host_max_parallel=1 -> second host card waits
            while a sealed card still starts; two concurrent host cards get disjoint port ranges;
            manual start of a capability-holding card -> conflicting_run names the holder
```

```
unit:       8 ui: tiers, mounts, capabilities
expects:    units 1, 2, 7 api fields
does:       exposes every new field with existing ui_base widgets only
outputs:    settings `o`: allow host cards, allow unconfined toggles; board settings shift+o: mode
            choice (sealed/hybrid/open) via gatedBoardToggle-style control, host cap number field;
            card overflow menu: tier override, mounts picker, capabilities editor, unconfined with
            reason; card head badge "host" / "unconfined" via cardHeadHtml meta.push; repo row:
            mounts list editor like remembered leases
verifies:   tests/js: board settings renders mode choice greyed when gate off; card head shows
            "host" for a host-resolving card and "unconfined" when reason set; repo mounts editor
            round-trips a PATCH; headless chromium screenshot of board settings and one card head
```

```
unit:       9 preflight for tiers
expects:    units 1, 2, 5
does:       names whatever a host or mounted card is missing before it fails minutes in
outputs:    preflight._tier_checks: for boards resolving any host card -> lab cli on host PATH,
            sandbox mechanism present (unit 0's), version noted; per repo -> each mount path
            exists; warn row listing unconfined cards
verifies:   tests/test_preflight.py: open board + missing host `codex` -> fail row naming it; mount
            path deleted -> fail row; sealed-only boards -> no new rows
```

```
unit:       10 docs: the new boundary
expects:    units 0-9 merged
does:       replaces "no fallback" with the tier contract
outputs:    backends.py module docstring and CardRuntimeUnavailable text, docs/plan.md:66 and
            :479-489, docs/dev-board-brief.md:57, new docs/practices/tiers.md (modes, unconfined,
            mounts, capabilities, what host reads are and are not confined per unit 0), CLAUDE.md
            mission line
verifies:   grep for "no fallback" / "THERE IS NO SECOND MODE" returns only historical spike docs
```

## whole-system test

hybrid board, review mode, one repo declaring mount `fixtures` -> a host dir with `data.csv`:
- card a (sealed, mounts fixtures): adds a test reading `$SMORTBOARD_MOUNT_FIXTURES/data.csv`; gate
  passes in docker; pr opens
- card b (host, mounts fixtures, capability `probe`): adds a test asserting `uname -s` == Darwin and
  reading the same file; gate passes on host; its worker's attempt to `cat ~/.smortboard-canary`
  is refused (or recorded as allowed if unit 0 found reads unconfinable)
- card c (host, capability `probe`) queued alongside b -> waits with reason naming b, starts after
- both b and c run with disjoint PORT_BASE; board head shows "host" badges; preflight `h` green
pass = three prs open, c's wait visible in the queue, canary outcome matches unit 0.

## status: parked

approved in reading, not executed. waiting for the machine to go quiet after the other session ends.
nothing below runs until fabian says go.

## parked: settings menus layout study (`o` and `shift+o`)

problem: the settings (`ui/settings.js`) and board settings (`ui/board_settings.js`) menus have
uneven spacing, line breaks, misalignments, and dense regions next to very thin ones. they are hard
to read and hard to adjust.

goal: a set of rendered layout suggestions to choose from. no code change to the live menus in
this step.
- a) grouped by topic
- b) easy to read and adjust
- c) no visual breaks, wrapped lines, misalignment, dense or thin regions

executors: both, in parallel, same inputs, compared side by side.
- codex (openai) via `codex:rescue`: renders its options as html mockups plus pngs, saved under
  `docs/design/menus/codex/`. it can't publish artifacts.
- claude: one published artifact with many options to flip between. each option is a full menu
  mockup built from m1's inventory, with a short note on its grouping logic.

```
unit:       m1 capture every menu state
expects:    the board running on localhost, the demo board loaded
does:       screenshots every page and section of `o` and `shift+o`, every option visible, using
            the repo's own playwright, headless chromium
outputs:    ui_shots/menus/<menu>-<section>-<theme>.png for light and dark, desktop and phone
            width, plus an inventory (menu, section, control, widget type, current group)
verifies:   inventory row count equals the number of controls in SETTINGS_SECTIONS
            (settings.js:216) plus renderBoardSettings (board_settings.js:167); every row has a
            screenshot showing it
```

```
unit:       m2 layout suggestions
expects:    m1's screenshots and inventory, the ui_base widget list, the ui/ wiring-only rule
            (any new primitive goes to ui_base under domain-free names)
does:       proposes many distinct arrangements grouped by topic (3-5 from codex, as many as
            differ meaningfully from claude), each rendered as a mockup built from existing
            ui_base classes
outputs:    codex: docs/design/menus/codex/<option>.html + .png; claude: one artifact holding all
            its options. both name, per option, the groups, what moved, and which ui_base
            primitives it needs (new ones flagged)
verifies:   each mockup shows every inventory control exactly once (check against m1's
            inventory); screenshots at desktop and phone width have no wrapped labels or
            horizontal scroll
```

after m2, fabian picks one. the implementation is a separate card set, planned then.

## verification

- per unit: its `verifies:` test file, `uv run pytest tests/test_<thing>.py -q`
- before each pr: `uv run pytest -q` and `uv run ruff check . && uv run ruff format --check .`
- end: the whole-system test above on the demo board, then headless chromium screenshots of the
  board settings and the badges
