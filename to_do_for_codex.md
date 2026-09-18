# To do for codex: merge modes, review-required and free-merge

## Implementation notes, 2026-09-18

- Network-based landing detection runs in the background and returns 202. A recorded landing
  still accepts synchronously. Checking cards created before a switch to free mode also land
  before acceptance; changing mode cannot bypass that step.
- Operator-triggered landing tests the current branch under the integration lock, including
  branches already synced by a sweep. Sweep and retarget operations share that lock.
- A doing child is retargeted at handoff, so landing its parent never changes its live worktree.
- Rejected stacks cannot safely be rebuilt under the same published branch names without a
  separate attempt-naming design. The implementation keeps the branches and refuses unsafe
  retries. Decide the dependent attempts and define fresh work instead of reusing rejected code.
- Accepted protected-base parents still count as unmerged until GitHub confirms the merge.
- Part B lives in `_agentic_work_setup/claude`; its PR targets main because that repo has no
  development branch. Source and deployed hook tests cover scoped review approvals.

The plan below is retained as the original handoff. Browser walkthroughs remain unverified;
automated coverage uses DOM fixtures and real temporary Git repos with fake model/GitHub edges.

Handed over 2026-09-18, approved in principle by the operator. Work in a worktree off
`origin/development`, never in the main checkout. smortboard runs in **review** git mode: open a
pull request against `development` and wait for his okay before merging. Start with A11, which
stands alone.

## Context

Two related problems, one shape.

**In smortboard**, a card that passes tests and the reviewer lands by itself on any base that is
not `main`/`master`/`trunk` (`lifecycle.py:730-731` -> `_integrate` at `lifecycle.py:399-451`,
which merges and then calls `accept_card` at `:441`). On a repo based on `development` that means
work reaches `development` with nobody looking, and the operator loses the thread of what happened
and cannot keep out what he does not like.

**In Claude Code sessions**, `~/.claude/CLAUDE.md:79-82` makes merging into `development` the
agent's job, and `~/.claude/hooks/block-main-commit.sh` guards only `main`/`master`
(`:92-94` refuses a PR based on main; anything else exits 0 at `:96`). Same outcome.

The fix in both places is a mode, not a rule change: one mode that merges freely (today's
behaviour), one that stops at a reviewable pull request and merges only on the operator's word.

Settled during planning:

- a card B depending on an unaccepted card A is **cut from A's branch**, and its PR is opened
  against A's branch, so the board keeps working while the operator is away
- the pulsating border means **free-merge mode is on**, nothing else
- the toggle is **per board**, on shift+A
- existing boards **default to review-required** when the migration lands
- Claude Code sessions are asked **at the first work that could end in a merge**; until a session
  has an answer on record, the hook **refuses** a merge into `development` and says why

## What this contradicts

`smortboard/CLAUDE.md:38-40` says "The board pushes and links a PR; it never merges." That is
already untrue (`lifecycle.py:730-731`), and both the old text and the new modes need writing down.

`scheduler._dependency_wait` (`scheduler.py:297-327`) rests its whole docstring on "THE BOARD NEVER
MERGES, so `accepted` only means the operator signed off". Under stacking that reasoning inverts.

## Part A - smortboard

### A1. Storage: the per-board mode

Follow the existing per-board pattern exactly - a column on `boards`, a `set_board_*` store method,
`PATCH /api/boards/<id>`: `max_parallel` (migration 14, `schema.py:312-315`,
`api.py:305-310`, `app.py:453-454`) and `daily_budget_usd` (migration 17, `schema.py:381-384`).

- migration, next free number (an uncommitted branch in this checkout is already adding 20):
  `ALTER TABLE boards ADD COLUMN merge_mode TEXT;`
- `NULL` means review-required. Only `'free'` opts out. That gives the "existing boards default to
  review" answer with no data migration and no backfill.
- `Store.set_board_merge_mode(board_id, value)` validating `{None, 'review', 'free'}`; board rows
  carry `merge_mode` through `list_boards`/`get_board`; `app.py:451-457` gains the branch.
- one helper both call sites use, e.g. `store.board_merges_freely(board_id) -> bool`.

**Verify**: `uv run pytest tests/test_store.py tests/test_boards_http.py -q`, plus migrating a copy
of a real db and checking existing boards read as review-required.

### A2. The lifecycle stops at the pull request

`lifecycle.py:730-731` becomes: integrate only when the base is unprotected **and** the board
merges freely. Otherwise take the branch that already exists at `:733-743` for protected bases -
note, `review_flag=True`, status stays `checking`, phase `opened` - with wording that says the
board is waiting for `y` rather than "merging is yours".

Nothing else in the free-merge path changes; `_integrate` keeps auto-accepting as it does today.

**Verify**: `uv run pytest tests/test_lifecycle.py -q`; a card on a `development`-based repo in
review mode ends `checking` with an open PR and no merge commit on the base.

### A3. One landing core, and proof it already landed

Pure refactor plus detection, before anything new triggers a merge - this is what stops the two
modes growing two merge implementations.

- lift the body of `lifecycle._integrate` (`lifecycle.py:399-451`) into a `land_card(...)` in
  `review/`, keeping the `INTEGRATE_ATTEMPTS` loop, both locks and the `_sync_and_retest` retry
  exactly as they are. `_integrate` becomes a thin caller. Free mode must not change by a byte -
  `tests/test_lifecycle_integrate.py:26-34` monkeypatches `lifecycle.integrate` and
  `lifecycle.open_release_request` by name, so either keep those names patchable at `lifecycle` or
  update the fakes deliberately.
- add `already_landed(...)`, checked in this order: an `integrated` event already on the card; then
  `pr_view` (`merge_request.py:389-401`) reporting `merged` - this is the "you merged it on GitHub"
  case; then `git merge-base --is-ancestor <branch> origin/<base>` after a fetch. `pr_view`'s
  `error` is never proof either way (`merge_request.py:378-382`) - fall through to the git check,
  which is local and authoritative. A hit records an `integrated` event with how it landed and
  accepts cleanly, which also makes a retried accept idempotent.

**Verify**: `uv run pytest tests/test_lifecycle_integrate.py tests/test_integrate.py
tests/test_landing_lock.py -q`, plus `already_landed` unit tests against a stubbed `pr_view` (that
fake pattern exists at `tests/test_scheduler.py:163-238`).

### A4. Accept triggers the landing, off the request thread

**The server is single-threaded** (`app.py:1152`: the store's sqlite connection is bound to the
thread that opened it; card runs are the exception and get their own thread). `landing_lock` polls
with no timeout against a 600s TTL (`landing.py:79-87`), and a moved base reruns the repo's whole
test command (`lifecycle.py:379-392`). Merging inside the accept request would freeze the entire
board UI for minutes. So:

- a `LandingRegistry` modelled on `RunRegistry` (`server/runs.py:98-175`): one thread per job, its
  own `Store` opened on that thread, a `{card_id: state}` map. The job resolves repo, base and mode,
  runs `already_landed`, lands, accepts, opens the release request, writes the note, then runs the
  re-target pass (A6).
- `decide.accept_card` grows an injected `land` callable - the same shape `reject_card` already uses
  for `close_pr` (`decide.py:94-100`), so a merge is never an invisible consequence of calling the
  function. Order inside: land, then release the worktree (`decide.py:78`), then set `accepted`;
  `integrate` needs the checkout that release destroys.
- a failed landing raises `DecisionRefused`: the card stays in `checking` with its PR open, and a
  conflict additionally blocks it the way `scheduler._flag_merge_conflict` does
  (`scheduler.py:189-200`). **A rejected accept must never leave a half-merged base.**
- the route (`app.py:519-531`) returns **202** `{state: "landing"}` after starting the job, and
  keeps today's synchronous 200 for every other case: protected base, free mode, already landed,
  reversing a rejection. Keep the existing 409-while-running guard and add one for a landing
  already in flight. `board.js:496-522` handles 202 by leaving the card put and letting the poll
  move it.
- **two accepts at once** are already safe: `integration_lock` serialises in-process,
  `landing_lock` serialises against card runs and outside `smortboard-land` clients, and
  `INTEGRATE_ATTEMPTS` re-syncs the loser. Add a board comment after the first wait so a job queued
  behind an outside holder is visible rather than silent.

**Verify**: `uv run pytest tests/test_settings_and_decisions.py tests/test_server.py -q`, plus new
tests: accept lands then accepts; accept refused and card still `checking` when the merge
conflicts; **accept succeeds without calling `integrate` when `pr_view` says merged**; free-mode
card already integrated is not merged twice. JS: the 202 path.

### A5. Dependency stacking

Today `_dependency_wait` (`scheduler.py:297-327`) holds B until A is accepted **and** A's PR is
merged on GitHub. In review mode that is exactly the idling to be rid of.

Two names, kept apart deliberately: **base** stays the repo's default branch (protection checks,
`branch_has_commits`, the reviewer's diff, `open_release_request`); the **integration base** is what
a card is cut from, synced against and opens its PR against, which for a stacked card is the
parent's branch. Conflating them is how a card would try to land on its parent.

- review mode: B starts once every dependency has reached `checking` unblocked, or is accepted;
  `todo`, `doing`, blocked and rejected still wait. Free mode keeps today's rule verbatim,
  GitHub-unreachable branch included.
- **one parent only, and at most three deep**. Two unmerged dependencies means B waits - it keeps
  the cut trivially correct. A tower nobody has reviewed is worse than a queued card.
- `_base_for_fresh_cut` (`lifecycle.py:249-267`) returns the parent's branch in that case, and
  `open_merge_request(..., base=<parent branch>)` opens B's PR against it. Record it as a
  `stacked_on` event - an event, not a column, read back the way `_latest_merge_request_url` reads
  `merge_request` events (`scheduler.py:128-133`), so no second migration.
- **`scheduler._sweep_checking_prs` must not dissolve the stack** - it merges `origin/<base>` into
  every checking card's branch on a timer. A11 replaces the timer with a trigger that only fires
  when the base actually moved, which leaves a stacked child alone for free, since its integration
  base is its parent's branch.

**Verify**: `uv run pytest tests/test_scheduler.py -q` with the existing eight dependency tests
pinned to a free-mode board and review-mode twins beside them, plus a new `tests/test_stacked_cards.py`
on a real temp git repo (the `tests/test_integrate.py` pattern) asserting B's branch contains A's
commit and that `open_merge_request` got `base=<A's branch>`.

### A6. Re-targeting when the parent lands

- add `("pr", "edit")` to `ALLOWED_GH_COMMANDS` (`merge_request.py:35`) - a deliberate widening,
  with a comment saying why it is safe: `pr edit --base` retargets a review and lands nothing, and
  `pr merge` is still absent and still unreachable through `_gh` (`merge_request.py:91-104`). Reach
  it only through a wrapper that builds its own argv and refuses any base but the repo's default,
  the same discipline `_push` uses (`merge_request.py:106-122`).
- after A lands: for each undecided dependent carrying a `stacked_on` event naming A, merge
  `origin/<base>` into its branch (`mergeable.merge_branch`), push, then retarget its PR. A conflict
  there blocks B with `MERGE_CONFLICT` and leaves its PR on A's branch - **A stays accepted**;
  B's problem must never un-land A.
- only direct children. A grandchild stays stacked on its own parent and is retargeted when that one
  lands; each card knows exactly one parent and the chain resolves itself one landing at a time.
- skip when the PR's base already is the repo base, which covers the operator having retargeted it
  himself. That needs `baseRefName` added to `pr_view`'s `--json` list (`merge_request.py:394`).

### A7. A rejected or conflicted parent

- `reject_card` deletes the local branch (`decide.py:122-123`). **Guard it**: skip the local delete
  while any undecided dependent is stacked on this card, and say so in the note. The remote branch
  is already kept on purpose (`decide.py:135-137`), so B's PR survives either way.
- the existing `DEPENDENCY_REJECTED` cascade (`decide.py:140-149`) stays as it is; its comment gains
  one fact - B sits on top of rejected work, so its next run cuts fresh from base rather than
  rebasing.
- **A accepted but its merge conflicts**: nothing happens to the stack, and that is the point. A4
  makes accept atomic with landing, so A stays `checking` with `MERGE_CONFLICT`, B stays stacked,
  and no re-target fires. Worth a test precisely because doing nothing is the correct behaviour.

**Verify**: `uv run pytest tests/test_settings_and_decisions.py tests/test_merge_request.py -q`,
plus: rejecting a stacked parent keeps its local branch; a conflicted landing leaves the stack
alone; the allowlist still refuses `pr merge` and the wrapper refuses a non-default base.

### A8. The shift+A toggle

House pattern, from the single existing shift binding (`shortcuts.js:37-39, 345-346`):

- do **not** add a BINDINGS row - `tests/js/keyboard_bindings.mjs:73-75` freezes the count. Extend
  the existing `{code: 'KeyA'}` row (`shortcuts.js:34`): `label: 'a / shift+a'`, and say both
  actions in `action`.
- insert the shift branch **above** the plain `KeyA` branch at `shortcuts.js:336`, which today
  opens the agent roster for shift+A too.
- confirm with `openActionConfirm` (`card_panel.js:22-54`) defaulting to `cancel`, per the policy
  at `card_panel.js:12-14`. The confirm text should name the consequence in words, not the mode
  name: "merge cards into development without asking?" / "stop at a pull request for review?"
- it applies to the focused board, PATCHing `/api/boards/<id>`, then re-renders the bar and the
  indicator - not `loadBoards()`, which re-runs `initShell` (`board.js:110-116`).
- **`toggleBoardMergeMode` must live in `board.js`, not `boards.js`**: the js test bundle
  (`tests/js/keyboard_bindings.mjs:60-62`) concatenates `columns.js, card_panel.js, chat.js,
  shortcuts.js, board.js, telemetry.js, costs.js` and does not include `boards.js`, so a shortcut
  reaching into it would throw there. `boards` and `currentBoardId` are `board.js` globals anyway.

**Verify**: `node --test tests/js/` - extend `keyboard_bindings.mjs` for the new branch and add a
case in the style of `tests/js/profiles.mjs:58-67` (`press('KeyA', ..., {shiftKey: true})`) asserting
no PATCH fires before `menu.pick('confirm')`.

### A9. The pulse, in ui_base

Hard constraint, `smortboard/CLAUDE.md:34-36`: the primitive lives in ui_base with domain-free
naming; smortboard only wires it on.

**Chosen 2026-09-18** from the tuning bench (artifact `23ZC8mQ3KyW4GAVyavUXa4`): variant A, two
waves. The values are settled, so this unit writes them straight in:

```css
--ambient-pulse-color: #e06f2d;   /* burnt orange */
--ambient-pulse-ms: 4800ms;
--ambient-pulse-drift-ms: 5500ms;
--ambient-pulse-min: 0.5;
--ambient-pulse-max: 0.84;
--ambient-pulse-width: 2px;
--ambient-pulse-blur: 16px;
--ambient-pulse-spread: 3px;
--ambient-pulse-reach: 4px;
```

The two periods sit close together (4800 against 5500), so the waves realign only about every 44
seconds - slow enough that it never reads as mechanical. A floor of 0.5 means the border is always
clearly lit and the mode is never invisible, which is also what makes the reduced-motion still frame
work at the midpoint.
- new rule in `smortui/ui_base/assets/base.css`, next to `.focus-glow` (`:296-332`), named without
  domain words - `.edge-pulse` or similar. Each part on its own custom property, the way
  `.focus-glow` does at `:152-161`, so a host retunes by restating a token.
- non-uniform by construction: two superimposed `box-shadow` layers on keyframe tracks with
  different, non-commensurate periods - one a centred ring breathing between a floor and a ceiling,
  one an offset shadow whose offset walks the four sides. Their beat is what makes parts of the
  border wax and wane out of step rather than the whole edge fading together.
- **animate `box-shadow` and `opacity` only, never `transform` and never `filter`** - the focus
  treatment already uses both (`base.css:296-332`), and `base.css:312-314` leaves transform to the
  host. The pulse rides elements that wear the focus glow, so it must stay out of them.
- `@media (prefers-reduced-motion: reduce)` drops the animation but **keeps a static ring at the
  mid value** - the mode still has to be visible without motion. Precedent: `.row-enter`
  (`base.css:548-555`).
- **Branch trap, verified**: smortui's local `main` (`da5f2a2`) does not contain `.focus-glow-soft`,
  which `board.js:103` and `card_panel.js:424` already use. The pinned sha (`pyproject.toml:14`,
  `2eb5e93`) is the tip of a local `feature/focus-glow-soft` branch. Because
  `tests/js/dom_stub.mjs:27-33` reads the sibling checkout **before** the installed package, a
  developer sitting on `main` is already testing against a stylesheet the app does not ship. Branch
  from the pinned sha, check the sibling out onto it while working, then bump the pin.

Wiring, on the smortboard side: restate the tokens in `layout.css` the way the focus tokens are
already restated (`layout.css:43,196,249`), and put the class on **`#bucket-row`**, the board's own
frame. Not the nav tab, which is small and already carries focus and active states; and not a card
strip, because `tests/js/dense_columns.mjs:867-870` asserts the exact class set a strip wears. Add a
label naming the mode so the glow is not the only channel.

**Verify**: smortui's own tests; in smortboard a new `tests/js/merge_mode.mjs` asserting the class
appears for a free board, is absent for a review board, and flips on toggle without a reload. Do
**not** assert base.css content from smortboard - the sibling-first resolution above makes such a
test a false-green generator.

### A11. The sweep fires when the base moves, not on a clock

Independent of the modes - it stands on its own, is strictly cheaper than what is there now, and
can land first.

`_sweep_checking_prs` (`scheduler.py:143-187`) keeps a waiting PR honest by merging `origin/<base>`
into every checking card's branch and pushing. It earns its place: the docstring records card
`59727ba3` / PR #112 drifting **34 commits** behind main while its PR waited, conflicting in four
files other PRs had touched. It is a timer because the board has no push signal for "someone merged
something in this repo" (`scheduler.py:137-139`).

Two faults found while reading it:

1. **It starves all but one card per repo.** `_last_sweep` is keyed by `repo_path`
   (`scheduler.py:163-166`) but consumed inside the per-card loop: the first eligible card sets the
   timestamp, every other checking card on that repo is skipped, and five minutes later the loop
   starts at the same first card again. Three checking cards on one repo means two are effectively
   never swept.
2. **A failed fetch burns the window.** `_last_sweep[repo_path] = now` is set at `:167`, before
   `has_remote`, `fetch_base` and the worktree check, so a failed fetch marks the repo swept with
   nothing synced.

Replace the clock with the question it was approximating:

- keep the last-seen `origin/<base>` sha per repo. Each tick: fetch, compare. Unchanged means
  nothing to do and no card is touched - which is the common case and costs one fetch.
- changed means sweep **every** checking card of that repo in that pass, not one.
- the board knows its own landings, so `land_card` (A3) triggers the sweep directly instead of the
  next tick waiting up to five minutes.
- keep a slow timer as a backstop only, because merges also happen outside the board - the operator
  merging on GitHub, another agent's `smortboard-land`.
- a stacked card is skipped by construction: its integration base is its parent's branch, so the
  repo base moving is not its business.

**Verify**: `uv run pytest tests/test_scheduler.py -q`, plus new tests: three checking cards on one
repo all sweep in one pass; an unchanged base sweeps nothing; a failed fetch does not record the
sha; a stacked card is left alone.

### A10. Docs

"The board never merges" is asserted in eight places and has been untrue since `_integrate` shipped.
The code is not the debt here, the text is. Fix: `CLAUDE.md:38-42`, `docs/PLAN.md:36`,
`README.md:119` and `:254-260`, and the module docstrings in `review/decide.py:1-7`,
`review/merge_request.py:1,9-12,260`, `scheduler.py:303-307`, `lifecycle.py:14`, `pulls.py:28`.
State the two modes, the stacking rule, and what stays true: main is never merged, and `gh pr merge`
is not on the allowlist.

**Verify**: `grep -rn "never merge" README.md docs/*.md CLAUDE.md smortboard/` returns only the
claims that are still true.

## Part B - Claude Code sessions

### B1. The session question

In `_claude_setup/global/CLAUDE.md` (then copied to `~/.claude/CLAUDE.md`), a new rule under
"Git workflow": before the first action that could end in a merge into `development` - not at
session start - ask via `AskUserQuestion` which mode this session runs in, then record it.

The existing precedent for a session-start instruction is `CLAUDE.md:372-377`; the difference here
is that the answer is written down, so it survives compaction and is readable by a hook.

State file: `~/.claude/state/session/<session_id>.json`, holding the mode and a timestamp. Every
hook payload already carries `.session_id`, and both `guard.sh:18-20` and `block-main-commit.sh:12-13`
already parse hook JSON from stdin the same way. The usage-watcher's `state/` directory is the
existing precedent for hook-written state, but it is keyed per project (`keyfn.sh:21-33`), so this
is a new, session-keyed file rather than a reuse.

### B2. The hook enforces it

Extend `~/.claude/hooks/block-main-commit.sh` (source: `_claude_setup/global/hooks/`):

- read `session_id` alongside the existing `tool_name` parse
- keep every current refusal exactly as it is
- add: when the command merges into `development` (the `gh pr merge` base lookup at `:60-97`, and
  the local `git merge`/push-refspec paths at `:99-128`), read the session's state file. Mode
  `free` -> allow, as today. Mode `review` or **no file at all** -> `exit 2` with a message that
  names the reason and tells the agent to ask which mode this session runs in.
- extend `_claude_setup/global/hooks/test-block-main-commit.sh` with cases for: no state file,
  review mode, free mode, and main still refused in all three.

Note the drift found during exploration: `~/.claude/settings.json` differs from the repo's
`global/settings.json`, and `~/.claude/skills/write-like-fabs/` is not in the repo at all. Copy the
live versions back before editing, or that work is lost.

## What not to build

- no automatic merging into `main`, in either mode, from either side, and no auto-merge of the
  standing development-into-main release PR (`integrate.py:104-144`)
- no `gh pr merge` in smortboard. The allowlist grows by exactly one verb, `pr edit`, reached only
  through a wrapper that builds its own argv
- no background "merge the accepted backlog later" daemon. Accept is the trigger; if it cannot
  land, it refuses and says why
- no third mode, no per-card override, no per-repo mode inside a board, no cross-board toggle
- no rebasing of stacked branches - merge only, and `_push` never forces
  (`merge_request.py:108-111`)
- no new status or column for "waiting to merge": a landing is a transient, not a state
- no second confirm-menu primitive; `openActionConfirm` covers it

## The design test

`smortboard/CLAUDE.md:14-16` asks of every feature: does it help the operator define work and then
walk away? Review-required as the default pulls toward hovering, and only stacking keeps it honest -
B never idles behind an unreviewed A, and free-merge stays for boards where being gone is the whole
point. If stacking were cut, review-by-default would fail that test and should be argued against
rather than shipped.

## Where this work should happen

This is feature work in smortboard, so by the playbook it belongs on the board as cards rather than
being built in a session. It also cannot start in this checkout while the multi-lab work is
uncommitted here - either wait for that to land or cut a separate worktree.

**Sequencing, as of this writing**: the multi-lab branch is still uncommitted in this checkout and
carries migration 20, so this feature's migration is 21 - the executing agent re-checks
`_MIGRATIONS` (`schema.py:29`) rather than trusting that number. Nothing here starts until that work
is merged into `development`.

Order: **A11 first** - it is independent of the modes, fixes two live faults, and makes the stacking
in A5 safe before stacking exists. Then A1+A2, A3, A4, A5+A6, A7, A8, A9, A10.

Settled since planning: smortboard runs in **review** git mode, so every branch here opens a PR
against `development` and waits for the operator's okay - that section needs writing into
`smortboard/CLAUDE.md` as part of A10. The work happens in a worktree off `origin/development`
(`.claude/worktrees/merge-modes`, branch `feature/merge-modes`), not in the main checkout.

## Verification, end to end

On a throwaway repo based on `development`, with two cards where B depends on A:

1. board in review mode (the default): A runs, ends in `checking` with an open PR, nothing on
   `development`
2. B starts anyway, cut from A's branch, its PR based on A's branch
3. press `y` on A: the board merges A into `development`, accepts it, and re-targets B's PR onto
   `development`
4. merge B's PR on GitHub by hand, then press `y` on B: the board detects it is already merged,
   adds no second merge commit, and accepts
5. shift+A, confirm: the board shows the pulse; a third card now merges and accepts itself
6. shift+A back; `uv run pytest -q` and `node --test tests/js/` green in both repos
