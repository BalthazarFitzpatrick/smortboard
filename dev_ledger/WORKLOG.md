# Worklog

<!-- append-only history of what was actually done, one entry per real handoff point (not per
     tool call, not per keep-going nudge). never rewrite or delete a past entry — TASKS.jsonl is
     the task ledger (mutated in place, current state only); this is the untrimmed "what happened"
     record. newest entry at the bottom.

     keep this file small — it gets read into context on every resume. if it grows past
     roughly 500KB, move older entries to WORKLOG.archive.md and leave only recent history
     here. -->

<!-- entry format:

## YYYY-MM-DD HH:MM — <branch>
<2-5 sentences: what was accomplished this session/stop, any decisions made and why,
anything that turned out to be wrong or got reverted. not a diff — a human should be able
to read only this file, top to bottom, and understand the project's history.>

-->

## 2026-09-08 22:05 — feature/setup (smortboard), chore/smortboard-ledger (dev_ledgers)

Scaffolded `smortboard` with `/init-project`: base CLAUDE.md, .gitignore, the stock
`.claude/settings.local.json`, and the python toolchain (uv sync, ruff, pytest, `tests/`). Ruff
passes clean. Ledgers were placed here in dev_ledgers and symlinked back in relative, matching the
ui_base and smolsmort convention. Mode recorded as outcome-defined — the operator has a vision to be
checked and then implemented, not an open investigation.

Two things left deliberately open: the frontend toolchain (ui_base has no build step and no npm, so
js tooling may be unneeded) and docker for the backend. Both are planning decisions, not scaffold
decisions.

Noted mid-run: `_claude_setup` changed under this session — another session is on
`chore/prune-and-resync-playbook` and moved `templates/modules/python/pyproject.toml` into
`templates/base/`. The scaffold used the new location.

## 2026-09-08 22:40 — planning session, plan approved

Read `dev-board-brief.md` and ran the full review round the operator asked for: 28 numbered questions,
all answered inline, plus four rounds of structured forks. Plan approved and copied to
`smortboard/docs/PLAN.md`; the ledger above now carries the three spikes and seven phases.

Locked: headless `claude -p` per card on the interactive subscription seat, worktree + branch per
card with repo as a card attribute, SQLite gitignored with an on-demand export, cards as ground
truth with an append-only event log, Blocked as one state with reason codes, gate to Checking is
tests passing AND reviewer approving, board pushes and links a PR but never merges.

Four places where the plan pushes back rather than implements. The workforce panel inverts §4.4 —
it defaults to the result view, because §4.4's "I mostly use this to watch" contradicts §1's
"watching an agent type is a failure mode"; the operator accepted the inversion. Omarchy was dropped
entirely once it turned out a plugin is a QML/Quickshell bar widget that cannot be a standalone
app. The §8 transport protocol is deliberately not written in v1, since specifying it against
Claude alone would fossilise Claude's semantics before Ollama exists to abstract over. And the
panel keybindings went bracket → comma/period after the operator pointed out brackets need AltGr on
European layouts — which exposed the real bug, that I was binding on `event.key` (layout-dependent)
rather than `event.code`. All bindings now resolve on physical key position.

Research findings worth keeping: ui_base is already tagged to v0.1.9 with a proper PR workflow, so
the gap is distribution not rigour — and the operator is right that git-pin now, PyPI later is a one-line
change per consumer. a consumer project consumes it correctly via a pinned git dep and already runs three
`.claude/worktrees/`, so the worktree-per-card model automates something already proven by hand.
fish_gate vendors a 245-line-stale fork, but it is out of scope — it gets redone anyway.

Next: spike S1. Nothing in phase 2 gets built until it has run.

## 2026-09-08 23:10 — spikes S1 and S2

Ran both against an isolated scratch repo. **S1 confirmed:** a headless `claude -p` card ran to
completion in 15s and met its acceptance criteria. The Agent SDK fallback is not needed and no phase
is invalidated. Findings written to `smortboard/docs/spikes/S1-S2-findings.md`.

Two things came back better than planned. The `result` event already carries everything §7's
telemetry section needs (`usage`, `modelUsage` per model, cost, plus a clean completion-vs-crash
signal), and two event types the plan did not know about turned up: `system/vcs_state_changed` gives
commit and branch detection without polling git, and `rate_limit_event` carries
`unifiedWindows` — five-hour and seven-day utilisation with reset times.

**That last one corrects the plan.** It states, citing the global playbook, that the status line is
the only surface exposing subscription usage and that §4.6 might reduce to token counts. Wrong:
every card's own stream carries the quota. The usage panel and quota meter are fully buildable with
no scraping. Phases 2 and 6 both shrink — session lifecycle, worktree creation and commit detection
are all existing CLI features rather than code to write.

**S2 concurrency clean:** two headless sessions ran simultaneously on the one interactive seat, both
succeeded, no interference. Parallelism is a scheduling problem, not a credentials one. Honest
caveat recorded in the findings: utilisation stayed at 0.11, so this proves concurrency and proves
the limit signal is readable, not behaviour at the limit.

One new requirement fell out of S1. The agent committed to a branch it named itself, because a
headless run inherits `~/.claude/CLAUDE.md` and the playbook forbids committing to main. Good
compliance, unpredictable name — so the board must cut the worktree and branch itself and hand over
a checkout already on the right branch, or card-to-branch mapping is guesswork.

S3's mechanism is identified but unproven: a `PreToolUse` hook refusing out-of-lease writes, the
same shape as the existing block-main-commit hook. That is the next action.

## 2026-09-08 23:45 — spike S3, all spikes now confirmed

**S3 confirmed: leases are enforceable at write time, not advisory.** A `PreToolUse` hook matched on
`Edit|Write` reads `file_path` from the payload and exits 2 to block. Verified against a card asked
to make two edits — the in-lease one was written, the out-of-lease one refused with the file
unchanged on disk. The refusal is distinguishable three ways: the hook response's exit code and our
own `LEASE_CONFLICT:` message, `permission_denials` carrying the exact path, and the agent's own
report. The risk logged against this in `PLAN.md` is closed.

One trap recorded: the run still ends `subtype: success`, `is_error: false`. A lease block is not a
card failure, and the runner must not infer one from exit status.

The first attempt at the spike failed in a more useful way than it succeeded. The agent's opening
move was `git checkout -b`, because a headless run inherits `~/.claude/CLAUDE.md` and the playbook
forbids working on main. That call was outside `--allowedTools`, so the card stopped and asked for
approval rather than doing any work. That is §4.5's `AGENT_QUESTION` state, proven detectable — the
question lands in `result.result`, the blocked call in `permission_denials`. It also sharpens the S1
requirement: a card handed a checkout not already on its branch does not merely pick an
unpredictable name, it can deadlock asking for something nobody is there to grant.

It also showed `--settings` **adds to** the operator's global hooks, CLAUDE.md and permissions
rather than replacing them. Phase 2 now carries an explicit decision: scope what a card inherits
via `--setting-sources` and `--system-prompt`, because the personal playbook is written for an
interactive operator who can answer a prompt and a card agent has nobody to ask.

Plan is no longer provisional. Next is phase 0 — the ui_base components.

## 2026-09-09 00:20 — phase 0 built

The five ui_base primitives are done on `feature/board-primitives` (e29f59f): `buckets.js`,
`expand.js`, `indicate.js`, plus hazard stripes and the badge and focus-marker styles in `base.css`.
34 tests pass, ruff clean, dom-stub coverage for the two components with real logic.

Verified independently rather than taking the build report at face value: no domain vocabulary in
any new file, no `import`/`export` in the assets, no colour literals outside `rgba()`, and
`.rubber-band` untouched apart from a comment explaining why the focus marker is a separate class.

Two gaps the spec left that got decided during the build. `exit-top` is delivered as a plain
callback rather than a DOM `CustomEvent`, because the container holds many buckets and there is no
natural element to dispatch from. And the expanded panel sizes off `90vh` clamped by `90vw`, since
the spec fixed the 1:3 ratio but never said how large "centred" should be.

Also settled this session: the PR body now goes into a single squashed commit so GitHub prefills
the pull request, since `gh` is not installed. And a token-scoping conclusion worth keeping — token
permissions are per-repo but not per-action, so "create PRs but never merge" cannot be granted by
scope; only branch protection requiring an approving review enforces that.

Next is phase 1 — the board with no agents.

## 2026-09-09 — phase 1 shipped, then a long design pass

Phase 1 is merged and meets every ship criterion: the store, the JSON API, the interface, docker,
export round-trip, and state surviving a container restart. The Docker image never built until it
was actually run — `python:3.13-slim` ships without git, which uv needs to fetch ui_base, and which
surfaced as an opaque "Git operation failed" rather than a missing binary.

Then a long design pass with the operator, live, against a running board. Cards became plates rather than
outlines; the board bar became buttons; the side drawers became real ui_base components parked with
a sliver showing; the card grow starts from the centre; every overlay key closes what it opened;
focus is the gliding cream marker rather than per-card rings.

The palette grew three derived hues and lost one. Vanilla is attention. The kingfisher and its milk
are new, and the milk is now the working plate — **cold on purpose**, because green against red is
the single pairing that collapses under red-green colour blindness, and a board saying "running" in
lichen and "rejected" in stone is one some people cannot read. Burnt orange and its milk are in and
deliberately unused, held for something unmissable. The milky lichen was retired after an afternoon:
it existed only to be the working plate and had no other user. The three plates now sit 47.6, 49.5
and 63.2 apart; the green set they replaced had a closest pair of 19.6.

the operator chose the plates knowing two of the earlier candidates were only dE 19.6 apart, and said so
plainly — his tool, his call. The measurement stays in the CSS as a fact for whoever repalettes
next, not as an instruction.

**The recurring lesson of the day, three separate instances: a green test suite said nothing about
whether the thing worked.** `makeDrawer` built an element with perfect geometry and never put it in
the document. `indicateFocus` could have been undefined in the browser because neither JS suite
loaded `indicate.js`. The binding suite had been failing since the drawers went in, and both suites
were reported green on the strength of running one. Each was caught by opening the page and looking.
Both suites now load the real ui_base scripts and the drawer stub asserts DOM containment.

Also fixed a hole in ui_base's own guard: the two-hue test matched only tokens named lichen or
stone, so the vanilla — the actual third hue — walked straight past it. It now accounts for every
literal colour in `:root` by name.

The main-branch hook grew twice: it now refuses a push whose refspec *targets* main from any branch,
which is the hole a Contents-write token leaves open since writing to main needs no pull-request
permission, and it stopped tripping on its own documentation once heredoc bodies were stripped
before matching. It still has a known false positive, logged below.

Next: phase 2, one card actually executing. All three spikes are confirmed, so the mechanics are
proven — headless runs per card in an isolated worktree, a PreToolUse hook enforcing the path lease
at write time, and the event stream carrying tokens, quota and commit detection.

**Open, for whoever picks this up:** the main-branch hook scans every word of a command for a main
destination, so a single command that both pulls from main and pushes a feature branch is refused.
The refspec scan needs scoping to the push invocation itself rather than the whole command string.

**Card design settled the same evening.** Strips became cards at 1:1.2, with a two-row title block
reserved whether the title needs it or not — a title that grows with its text puts every rule and
footer at a different height, and a wall of cards stops reading as a grid. Both rules stop short of
the border. Colour is back on the edge rather than filling the card.

The fan is built and liked: overlapping cards where the focused one stays put and everything below
slides down, done in pure CSS off `:focus` so the arrow keys already drive it and nothing tracks a
selection in script. **One open problem, deliberately unsolved:** collapsed, the stack does not read
as separate cards — with 96px of a 233px card showing, all you see is consecutive title bands, which
look like one ruled list. Recorded as its own task with the options nobody has tried yet.

## 2026-09-09 (late) — the guard rewritten, phase 2 built, five overlap options

**The git rule is now the operator's framing, not mine:** main is his, every other branch is the agent's.
Cutting branches, committing, pushing and merging one branch into another are free and need no
permission — for me and for smortboard's own card agents. What is refused is writing main by any
route, including merging into it and merging a pull request. Enforced locally on purpose: a hook
binds agentic work and leaves his own hands free, which branch protection would not, and GitHub
rulesets need Pro for private repos anyway (verified against the API).

`gh` is installed and authenticated via the browser flow, so it has its own token in its own
keyring and the git credential in the macOS keychain is untouched. The PAT could not do it: it
carries Contents write, which is why pushing works, but not the read the API needs — and GitHub
answers 404 rather than 403 there, so it cannot be diagnosed from outside. **PRs are now created
with a real title and body and no squashing**, which removes the out-of-order regression that bit
twice: a squashed branch is a full replay against whatever main was at squash time.

**Phase 2 is built** (PR #10). The spine of card execution: a worktree per card handed over already
on its branch, the path lease enforced at write time inside that worktree, and the headless runner
streaming events into the store. The ordering detail worth remembering is that a lease conflict is
checked before `is_error`, because a blocked write still ends the run as a success.

**Five overlap treatments** (PR #11, preview). Building them corrected a number recorded wrongly
last night: the 96px was the hidden part, not the visible one, so the cards barely stacked and the
problem being solved barely appeared. Raised to 176px of a 264px card for the comparison.

Two CSS specificity bugs today, both the same shape: a rule that looked right and lost silently to
one with more classes. The swatch colours lost to `.card-strip.card-working`, and so did the border
neutralisation. Both fixed by raising specificity rather than by relying on source order.

## 2026-09-09 (midday) — phases 1 and 2 closed, proven against the real binary

Phase 2 is no longer taken on trust. Two cards ran end to end against the actual claude binary,
about $0.12 each. The first was the plan's own whole-system test: a worktree cut and handed over
already on `card/<id>`, eleven turns, thirty-nine events recorded into the store, a commit on the
branch, and the acceptance criterion met. The second was the harder one — a card whose lease covered
only `cli.py` but whose brief asked it to edit `README.md` as well. The README came back untouched,
`cli.py` was changed and committed, `permission_denials` named the refused path, and the run still
ended `subtype: success`, `is_error: false`. That last part is the detail worth keeping: **a lease
block is not a card failure**, and a runner inferring failure from exit status would have parked a
card that worked correctly.

It also settled the one thing Phase 2 rested on documentation rather than evidence. The agent's own
summary read "Blocked — this card's lease only covers toolkit/cli.py … I noted this and didn't
retry, per instructions", which confirms `--setting-sources project` plus the explicit system prompt
behave as intended with the personal playbook stripped out.

Two gaps the runs exposed, both now decided and being built: the allowlist is too narrow for a card
to run its own tests (the run logged two Bash denials), and card commits do not follow the house
style, because the playbook that carries it is deliberately not inherited.

Process, learned the hard way twice today: **a pull request closes the moment it is merged, so
anything pushed to that branch afterwards is orphaned.** Two commits sat on a closed PR and never
landed — one of them the fix that stopped every arrow key working after visiting a comment box. The
playbook now says to check a PR's state before listing it, and to cut a fresh branch when a push
lands after a merge. Listing already-merged links also cost a real conversation about whether the
merges were mine; they were not, and the hook refuses a merge outright, but the audit trail cannot
prove it because `gh` authenticates as the operator's own account.

## 2026-09-09 (afternoon) — containment decided, and the credential moved off disk

The architecture question that had been half-open since S3 is settled. **Cards are individually
contained**: a throwaway container each, with a clone bind-mounted rather than a worktree — a
worktree's `.git` is a pointer into the parent repo, so mounting it alone leaves git dead and
committing is the card's whole job.

**The board runs natively, and that is the safer choice rather than a compromise.** A containerised
board that starts card containers needs the Docker socket, which is root on the host; a native board
has the user's own privileges. The container was always there to contain the cards. It ships as
`uv tool install smortboard`.

**Docker is a hard dependency with no fallback.** A subprocess mode would put an agent that may have
read untrusted input onto the operator's own filesystem, with their credential and their GitHub
access. A board that quietly degraded would be claiming an isolation it no longer had.

**No GitHub credential goes into a card, and that is what actually protects main** — structurally,
not by rule. The guard refusing a push to main lives in user settings a card never sees, because it
runs with `--setting-sources project`. A card that cannot reach GitHub cannot write main whatever it
decides. The card commits locally; the board fetches and pushes.

The card credential is a `claude setup-token`: a one-year OAuth token that **can only make model
requests** — it cannot open Remote Control sessions or fetch connectors, so it is inherently
narrower than the operator's own login. It now lives in the OS credential store rather than a
plaintext file, and reaches the container **on stdin** rather than as a mount or an env var, so it
touches no disk and `docker inspect` shows nothing.

One correction from earlier: I had asserted the card token was "revocable independently". The docs
do not say that. What is documented is the one-year lifetime and the reduced scope.

Windows is off priority but the code is not macOS-only; see the task added for exactly what is
unverified.

**Ruff caught a bug 71 passing tests had walked past** — an undefined constant on the default
token-path branch, which no test reached because every one passed an explicit path. Third time today
the suite was green while something was broken, and the pattern holds: it is always the path nobody
thought to exercise.

## 2026-09-10 — ledger caught up, phase 3 finish planned

The ledger had stopped at the afternoon of 2026-09-09 while PRs #14-#24 landed. Task 13 is closed
(#15); phase 3's gates, merge request and chained lifecycle are all on main. What is left, and the
plan for it:

1. store: migration 4 - `cards.findings_route` (fix / attention / null) and a `settings` table for
   the global value, which forces every card when set. `/api/settings` GET and PATCH.
2. lifecycle: status moves to checking only after BOTH gates pass (it moved before them - a bug).
   On a rejected review with route fix, the worker gets the findings and the gates re-run, up to 2
   rounds, then REVIEW_REJECTED and attention.
3. outcome: a pure projection of the event log - worker summary, test gate, reviewer verdict, PR
   url, fix rounds. `GET /api/cards/{id}/outcome`.
4. decide: accept (checking -> accepted, worktree removed, branch kept for the PR) and reject
   (attempt diff attached, PR closed with gh, worktree and local branch destroyed, dependents get
   DEPENDENCY_REJECTED). `POST /api/cards/{id}/accept|reject`, 409 from the wrong status or mid-run.
5. ui (separate agent, own worktree): outcome section in the card panel, `y` accept, `x` reject.
6. tests/test_whole_system.py: real http, real git, fakes only where the model, docker and gh sit.

## 2026-09-10 (later) — phase 3 closed on its own verify

PR #25 landed the decision half: checking gated on both verdicts, the outcome projection, accept and
reject, the fix route with two rounds, and the whole-system test. On main 05daa2d that test passes (6),
the full suite is 179 passed and 1 failed, and the one failure is the real-docker smoke test with the
daemon not running. Ruff clean.

The whole-system test fakes the model, docker and gh, so phase 3 is proven at its seams, not against
the real binary the way phase 2 was. One real card through both gates to a real PR is still owed.

Next is phase 4, mission control. It is non-trivial, so it opens with a planning session.

## 2026-09-10 (evening) — the first real card run, and what it found

Phase 3 was closed this afternoon on a verify that fakes Docker, and the first real run showed
why that was not enough. Docker was up, the card token was stored, and the whole-system card from
the plan went in. It **crashed in 27 seconds before the model started**: the container was handed
the host path of its lease settings, and only the clone is mounted inside.

Digging in turned up worse. The guard hooks were wired as the host's Python at host paths, and the
card image had **no python3 at all**. A PreToolUse hook that exits 127 does not block, per the hooks
docs, so inside a container **the lease and the bash guard could never have refused anything**. The
container backend had never run a real card. Phase 2's real runs predate containerisation.

Fixed on smortboard `fix/container-card-settings`: guards mounted read-only at `/smortboard`, outside
the workspace, python3 in the image, and the reviewer on the same path. Proven in a real container:
the lease refuses `README.md` with exit 2 and the bash guard refuses `/etc/passwd`. Phase 3 is back
in progress until a real run goes through.

Two more things the attempt surfaced. The stock image cannot run smortboard's tests with no network,
so smortboard needs its own repo image (task 16, proven, not committed). And **the board's keychain
reads raised macOS popups that ruined a consumer project's recording**. The fix is the operator's call (task 15).
Anything that reads the keychain waits for a warning first.

## 2026-09-10 (late) — the card token moves back to a file, on purpose

the operator chose the 600 file over the keychain: a single-operator board, and the popups cost a real
recording. This reverses yesterday's move off disk knowingly. The board now reads
`~/.config/smortboard/card_token` first and only asks the keychain when there is no file.
Packaging-grade answers (a signed app, or reading through `/usr/bin/security`) are noted on task 15
for the day smortboard ships to anyone else.

## 2026-09-10 (night) — run 3: the first card that really worked, and a false pass underneath it

With the full token (the first paste was cut off at 80 bytes), the card authenticated and did its
job inside the container: `--version` plus a test, committed, $0.88 over 39 turns. It blocked
`AGENT_QUESTION` before the gates, and that turned out to be the least interesting part.

**The gates would have tested stale files.** The board fetches the card's commits back into the
branch its worktree has checked out, with `--update-head-ok`: the ref moves and the files do not.
The test gate and the reviewer both read that worktree, so a card could have passed its tests without
the tests ever seeing its work. Nothing faked could catch it, because the whole-system test's fake
worker commits straight into the worktree. That is task 18, and it goes first.

The rest, tasks 19-22: the card's own test command was refused though the allowlist and the docs
say it matches; the repo's CLAUDE.md orders ruff while the allowlist refuses it, which ate most of
the 16 denials; the container has no git identity, so the agent invented one; and the word
"approval" in a summary was read as a question. Two of those need the operator's decision.

## 2026-09-10 (night, later) — run 4: a card goes all the way to a pull request

Every fix from run 3 landed on smortboard `fix/real-run-3-findings`, and run 4 went end to end:
the agent did the work in 15 turns for $0.24, the board's own test gate ran **6 passed on the card's
commit**, the reviewer approved, and **the board opened PR #28 itself**. Total about $0.43.

Task 19's mystery was not the allowlist at all. The refusal text in run 3's events named the bash
guard: its regex read the `/test_cli.py` inside `tests/test_cli.py` as an absolute path. The guard
only started working with today's container fix, and working, it exposed its own old bug. A container
probe confirmed the allowlist rule matched the exact command all along.

What is left for phase 3 is the decision step for real: accept or reject PR #28 from the board.
the operator also asked for a model per card, planned by the orchestrator and overridable - task 23.

## 2026-09-10 (night, last) — accepted for real, and the open card got room

the operator accepted run 4's card from the board and merged its PR, so the accept half of phase 3's
decision step has now run for real; the reject half has not. The fixes from run 3 (tasks 18-22)
are merged as #29.

On the board itself: an open card is three times as wide (900 wide on a 1440x900 screen, was 300),
space closes what it opened, and deciding a card from inside its panel closes the panel first, then
slides the card into its new column over the expander's 220ms. Verified in headless Chromium, not
only in the node tests: the panel measured, the slide sampled mid-flight, no page errors.

## 2026-09-10 (night) — reversible decisions, green for done, and fifteen layouts to choose from

x on an accepted card used to be refused, and the badge wrote "refused" beside "accepted", which read
as the labels swapped. Decisions now flip both ways: rejecting an accepted card notes that a merged
pull request stays merged, and accepting a rejected one says to reopen its pull request on GitHub,
since the board's gh allowlist has no reopen. Accepting again also releases dependents it had blocked.

Colour, after the operator's call: done is a lichen border, rejected the red one, blue stays with doing -
edges, not fills. Measured under simulated colour blindness, lichen against the red stays delta-e 50
or more in all three types and 2.6x brighter. The weak pair is lichen against vanilla (attention):
delta-e 14.6 under deuteranopia at nearly equal brightness. Position and the foot label separate
them today; a second cue for attention is an open offer.

The open card got fifteen layout variants on a preview board, three per column, all carrying run 4's
real content (task 24, awaiting the pick). The first screenshot pass showed every long line clipped:
ui_base's `.field-value` is nowrap, and the new wrapper had borrowed the name. The motion work went to
a separate agent in ui_base: a transform-based expander that collapses on close, timed by
`--motion-duration` and `--motion-ease`; smortboard is pinned to it.

## 2026-09-10 (late night) — the pick, the archive, and what comes next

the operator picked two-up with the state edge and the run timeline; the three combine into one default
for every open card. The other fifteen are kept as a design archive, on their own board in a durable
copy at `~/Library/Application Support/smortboard/design-archive.db`, so the decision can be revisited.

Next, in order:
1. merge ui_base `feature/smooth-expand-motion` (smortboard pins it), then smortboard
   `feature/card-panel-layouts-and-flip`, then this ledger; switch the local ui_base checkout back to main
2. phase 3's last acceptance item: reject a real card from the board (task 8), and task 25 - r on an accepted card fails on its kept branch and hides the real outcome
3. commit smortboard's repo image for the offline test gate (task 16; the draft is in its notes)
4. then phase 4, mission control - opens with a planning session. task 23 (a model per card)
   is small enough to land before it
Open offers: a second cue for attention next to lichen, and `gh pr reopen` for re-accepting.

## 2026-09-11 (overnight) — phase 4 plan: four panels, conversational

the operator, before bed: wire `.` mission control, `,` workforce, `a` roster and `u` usage. "For now the
panels will be just like terminals, but with our font and our look and style. They just relay
messages between the agent and me back and forth. Conversational, I don't need to see code
changes." Plan mode needs his approval and he is asleep, so the plan lives here instead.

Prompt layering (the brief's open question): the ROLE is the unit - orchestrator, worker, reviewer -
versioned in the store with the code's constant as the seed default, editable over the API.
Repo conventions keep coming from the repo's own CLAUDE.md; the card is its own content.

Units, built by two agents in parallel worktrees against one written contract:
- backend (feature/p4-backend): migration 6 (prompts, orchestrator messages, orchestrator plan);
  orchestrator turn = one container run, no tools, JSON schema {reply, plan, cards} - the BOARD
  creates the cards; roster and usage projections of stored events; the card conversation
  (agent text blocks + the operator's notes + board comments; notes reach the agent on its next run);
  routes; docs/PROMPTS.md
- frontend (feature/p4-frontend): terminal-style drawers for `.` and `,`, Menu popups for `a`
  and `u`, node tests against the contract
Whole-system: a message in mission control creates a card on the board, the roster lists a running
card with a derived activity line, usage shows the latest window and per-model tokens.
Found while planning: today's rate_limit_event carries status and resetsAt but no utilisation, so
the usage panel shows what is there rather than an invented percentage.

## 2026-09-11 (overnight, done) — phase 4 wired, and proven with a real card

The four panels are built and joined on `feature/phase4-mission-control`: `.` mission control and
`,` workforce as terminal-style chats, `a` roster and `u` usage as menus. Two agents built backend
and frontend in parallel against one written API contract; I read both diffs, joined them, and
tested the result in a real browser.

The real check went through first time for the orchestrator: one sentence in, one small card out, in
ten seconds. That card then went through the whole board to PR #32 with zero denials, which is phase
4's ships-when. The narration instruction added to the worker prompt worked: the workforce
transcript reads as plain sentences, with tool calls kept in the event log.

The first real render found two bugs the unit tests could not: the key that opens a drawer typed
itself into the input it focused, and workforce asked which card was focused after that input had
already taken focus. Both fixed with a regression test.

Also pushed: `feature/repo-image` (task 16). Next session: merge, then the prompt editor in the UI,
task 25, the reject path for real, and task 23 (a model per card).

## 2026-09-11 (morning) — round two plan

the operator: keep developing. In order, stacked on feature/phase4-mission-control until it merges:
1. prompt editor, key `p` (agent, frontend only): the three role prompts - orchestrator, worker,
   reviewer - read, edited and saved as a new version in the board's terminal look. the API exists
2. task 25 (agent, backend only): refuse running an accepted card up front (its branch is kept for
   the pull request); rejected cards stay re-runnable. the outcome skips attempts that never reached
   the worker, so a refused rerun cannot hide a real result
3. task 23 after both land: a model per card, orchestrator-planned, overridable
4. last: phase 3's reject path for real

## 2026-09-11 (morning) — phase 3 closed for real, task 25 and task 23's backend built

The reject path ran on a real card: run, reject, run, reject. The second attempt was cut fresh from
base, opened #35, and rejecting it kept its diff as an attachment, closed the pull request with its
branch kept, and left nothing local. With last night's accept, phase 3 is done for real.

The first attempt of that card found its clone's copy of the base commit unreadable, stopped, and
asked a clear question rather than editing `.git` - which the guard refused anyway. The repository
itself checked clean; the clone raced a pull, probably. Recorded as a watch item.

Task 25 (an agent's work, reviewed) and task 23's backend are pushed on their own branches and merge
cleanly with main and with each other. Rejecting a real card also showed the card panel's
attachments section had never listed anything: `get_card` did not return them. Fixed on the task 23
branch. The prompt editor is still with its agent.

## 2026-09-11 (late morning) — round two landed on branches; round three planned

Round two: the prompt editor (`p`), task 25, and task 23 whole - a card's model from the
orchestrator and from `m` - plus the operator's asks from the hour: a two-column shortcut overlay and a
usage panel in a card's language with a fill bar ported from a consumer project into ui_base. The real
browser caught three things tests could not: a stale second copy of the writable card fields in the
server, a flex rule of mine that stretched a 2px divider to 268px, and the focus marker drawing
through every menu.

Round three, the operator's pick of all five product ideas, as tasks 26-30: the attention inbox with
answers that resume work, overnight parallelism with a morning digest, live steering (spike first -
the mechanic is unproven), cost and learning, and replay. Agents build 26, 27, 29 and 30 in parallel
from `base/round-three` with their own modules; 28 waits for the spike.

## 2026-09-11 (afternoon) — round three integrated, reviewed, documented

All five ideas are on smortboard `feature/round-three`, PR #39, which needs ui_base #20 merged first
(the pin points at its commit). Four were built by agents from `base/round-three`, merged here with
union conflict resolution (routes, script tags, the key contract list), and checked in a real
browser.

Live steering is proven in the real container: a note queued nine seconds into a haiku run landed
between two tool calls and was obeyed, with no injection flag, for $0.055. As merged it would only
have arrived after the agent's whole task, so a feeder thread now writes notes as soon as they are
queued.

The review pass, all measured or tested: the board listened on every interface with no auth (now
loopback, `--host` to opt in); a scheduler stop pressed mid-tick was overwritten by the tick;
telemetry credited Claude Code's haiku helper with sonnet's work; `list_cards` ran eight queries per
card (batched plus migration 8's indexes: 28.1 to 3.4 ms at 200 cards). the operator's asks from the hour:
every key panel centred over an 80% dim (menus had opened under an open card), usage laid out wide,
attention cards glow, and `n` stays the inbox. The Qwen check was dropped at his word.

The README is new, and the old one's `uvx smortboard` was false (not on PyPI). The field guide is
published as a private artifact. Known gaps: no way to stop a running card from the board, accepted
is not merged for dependents, the agent spends a turn finding `/workspace`.

## 2026-09-11 (evening) — round four: stop, merged dependencies, resume briefing, cost overview

the operator asked for everything on the open list plus a cost overview on `c`. Four agents built stop,
merged dependencies, the resume briefing and the cost overview in parallel; I did the ones that
need the real container and token, then merged all five into `feature/round-four`.

Proven for real, all on haiku and scratch repos: three cards across two repos ran at once and each
committed only its leased file ($0.14); `k` stopped a running card and its container was gone in
0.2 s after reordering the kill; a bogus token comes back as a 401 result, now a refusal with how to
renew; one session's two results cost $0.0087 then $0.0129, so cost is per turn and the runner now
sums it. The first cost overview clipped every line and was rebuilt in the usage card's language.
The card design change (sections headed like columns) is separate, PR #40.

## 2026-09-11 (night) — the alpha, and the first card on a real repo

Polish (#47): the operator's name from one setting, chat as name over text, timeline dots coloured by
meaning, the unpinned workforce called mall cam, and a cost overview with one row per board so nine
boards fit. The README took the field guide's art (#48).

The alpha, for colleagues on their own machines (#50): `h` is a pre-flight checklist with the exact
fix on every row, `b` creates boards and registers repos, and there is a bug report template. #48
had merged into #47's branch twelve seconds after #47 landed and never reached main; #50 carried it
over unchanged. Walking the first run from an empty database found that creating the very first
board threw: the empty state had removed the columns it renders into.

Main is protected on all three repos by a ruleset: no deletion, no force-push, pull requests only,
admin bypass. The first version also restricted updates, which blocked every merge; removed.

From using it: `,` and `.` open without taking focus, `/` to type, escape back (#52); a dot per chat
name, a two-space indent, no log box, textareas centred in smortui (#53); the overlay's board keys
as one row (#54); the focus frame's lag, which was stale timers from earlier targets, measured at
11/11/14 samples off to none (#55); and the board follows every running card, not only ones started
with `r` (#56).

**The first card on a real repo.** Smolsmort's review-tool port was planned with the orchestrator
into ten cards on an integration branch. The design card reached a PR for $5.11 over 53 turns in two
attempts and found three board bugs: a re-run on a card with an open PR passed both gates and was
never pushed; the PR's cost line showed only the reviewer's last run (both fixed in #57); and the
gate did not run the format check CI runs (now in smolsmort's gate command). The orchestrator's
first reply described three cards but returned none; asked again, it created them. The PR's task
boxes are ticked by nothing - task 40 is the operator's decision.

Docker Desktop's disk was reset during a memory resize and every image and volume went with it. The
board's own state is on the host and survived; both images were rebuilt and the gate re-verified.
Background board processes were killed repeatedly for low memory, so the board now runs in the operator's
own terminal.

## 2026-09-12 — two smolsmort cards parked on LEASE_CONFLICT, and why

After the board's disconnect (board process gone, Docker daemon down; both runs had finished
before it, at 06:27 and 06:46) two cards waited on the operator:

- **Port train.py** ($4.59, 68 turns): done and committed on its card branch (1fe46a9, 129 passed).
  It parked only because it tried to edit `snapshot/README.md`'s table, outside its lease. Nothing
  needs widening: the last port card deletes `snapshot/` whole. Its `ruff format` calls were also
  denied - the Bash grants only allow the gate's exact `ruff format --check` form, so a card can
  check formatting but never apply it.
- **Port classdefs.py and verify.py** ($1.91, 61 turns): created with an EMPTY lease, so the guard
  refused every write. Seven of the ten smolsmort cards have none. It also found `verify.py` is not
  in `snapshot/`; in the parent project it is the VLM triage tab, which `snapshot/README.md` lists
  as staying behind.

Fixed on `fix/unleased-cards`: the lifecycle refuses an unleased card before its agent runs,
`Store.set_leases` plus `PATCH /api/cards/<id> {"leases": [...]}` make a lease settable (nothing
could before - the "widen it in the note" docstring was wrong, the guard reads lease rows), the
orchestrator is told to lease every card and flags one without, and the card panel shows the lease.
Card B's lease was set by hand in the live db (backup in the session scratchpad):
`smolsmort/review/classscheme/**`, `tests/test_review_classscheme*.py`. Both cards still wait on
an answer from the operator to resume.

Also on `fix/orphaned-runs`: nothing recovered a card whose board process died mid-run - the run
registry is in memory, so such a card sat in doing, unflagged, out of the inbox. Every run now
writes `run_ended`, and a starting board blocks any card still mid-run without one as CRASH with a
note. A dry run on a copy of the live db recovered nothing, so today's disconnect orphaned no card.

## 2026-09-12 — a third stuck card: two worktree cuts raced on .git/config

After #60 and #61 merged, the smolsmort housekeeping card turned out to be a third card waiting:
refused at 06:20:41, 17 ms after the classdefs card started, with `could not lock config file
.git/config`. Both were cut from `origin/feature/review-tool`; `worktree add -b` from a remote ref
writes upstream tracking into the shared config, and git fails a held lock rather than waiting.
Reproduced in a test (6 of 12 concurrent cuts failed with the same error) and fixed on
`fix/serialize-git-per-repo`: a per-repo lock around every git write, and `--no-track` on the cut.
Its branch `card/313033b8...` exists with no worktree, so pressing r takes the add_worktree path.
Docker Desktop and the board are still down; no card can run until both are started.

## 2026-09-13 - wrap-up before moving dev work onto one board per repo

Merged since the last entry, all from a disconnect triage that turned into a day of fixes found by
real smolsmort runs: #63 flagged cards glow, #64 the lease-overlap rule applies to manual runs and
inbox answers, #65 headless cards never wait (no Monitor/ScheduleWakeup, no background Bash, the
rule appended to every worker prompt) and a commitless run stops before its gates, #66 cmd/ctrl/alt
pass through to the browser, #67 a next action on every surface a waiting card shows up on, #68
ui_base pinned to smortui 3ea805c. Every consumer is on that same full sha now (smolsmort #8,
a consumer project's PR #110, which also moved to smolsmort 2269799).

The board runs smortboard main bec20d3 (restarted after the three smolsmort runs ended; #68 is
README-only in ui_base, so no restart since). Its inbox holds three smolsmort cards, and two of
them are waiting on board fixes, not answers: train.py hit the reviewer's $0.50 budget and its
findings were thrown away (task 52), housekeeping hit the 600 s gate with no output kept (task 53).
classdefs reached smolsmort #9 and waits in checking. The live board also has stored worker and
orchestrator prompts from the smolsmort session; a stored prompt replaces the code default whole.

The operator's plan (task 55): one session creates a board per repo, and dev work goes through the board
from then on; small side projects stay in sessions. Peers wrapped up: smolsmort-92 (dev_ledgers
#28 open, no branches kept), private-project-7b (dev_ledgers #29, a private project's #7 open),
consumer-project-5e (asked; a consumer project's PR #60 is open and conflicting since 09-10). Leftover unmerged branches
from before this session, all superseded or merged by their PRs, are listed in the handover for
the operator to delete or keep.

## 2026-09-13T11:30Z - consumer-project-5e [c4c82c] - handover from dev-ledgers-40, base-branch api, readme quick start
- dev-ledgers-40 handed this ledger, the board process and the smolsmort cards to consumer-project-5e and went quiet.
- smortboard #70: PATCH /api/repos/<id> can move default_branch (task 41, half). no more direct db writes for it.
- smortboard #71 (docs/quick-start): a one-minute start at the top of the readme.
- the board on :8000 was not answering at handover; the operator restarts it from ../smortboard on main.
- waiting on the restart: PATCH smolsmort to default_branch main, close card e6b11adb (superseded: main has --version from c54d79b), then the parked cards 2cd984d6 (REVIEW_REJECTED, budget) and 313033b8 (TESTS_FAILED, gate timeout), both fixed by #69.

## 2026-09-13T13:50Z - consumer-project-5e [c4c82c] - board database purged, board from a repo
- the operator purged the board database (moved to smortboard.db.bak-20260913-before-purge); boards and repos get set up again from the repos and ledgers.
- the create board dialogue gets three buttons in its first row: new board, from local repo (built on feature/board-from-local-repo), from online repo (queued as its own task).

## 2026-09-13T16:48Z - consumer-project-77 [cb4b0d] - empty-board fix, ledger into the repo, lease globs found wrong
- #73: an empty board hid its five columns with [hidden], but .bucket's display: flex outranked it, so the columns squeezed beside the hazard box. added .bucket[hidden] { display: none }.
- the ledger moved into dev_ledger/ with root symlinks and was scrubbed for a public repo: the operator's name, home paths and consumer-project names.
- queued card-leases-from-real-files: two cards were leased to src/**/*.tsx globs that match nothing here and stopped on LEASE_CONFLICT.

## 2026-09-13T17:55Z - consumer-project-77 [cb4b0d] - board triage and the mission-control plan
- every card on this board was stuck: 11 of 16 carried src/** leases (mission control has never seen a file), and the db purge left every repo without test_command, lint_command or image, so the gate refused cards with correct leases and the bash guard refused their test runs.
- plan approved: mission control spans every board by default and reads read-only clones of the repos; it imports ledger tasks once, linked; dev_ledger/CARDS.jsonl and REPO.json keep cards and repo settings through a board-opened sync PR; leases are checked against real files, a conflict parks for one-key approve and is remembered per repo.
- queued as 19 mc-* tasks, in build order; card-leases-from-real-files and lease-editor-in-ui are folded into mc-lease-check, mc-lease-approve and mc-ui-leases.

## 2026-09-14T18:32Z - wowtomate-fixes-and-board-cleanup [76b838] - plan: a development branch the board lands cards on
- operator approved (2026-09-14): each repo gets a development branch; cards branch from it, sync with it at hand-over, and the board merges them into it; the operator merges development into main now and then. main stays unreachable for the board.
- units: review/integrate.py (merge commit built with commit-tree, pushed as a fast-forward, refuses protected bases, a moved base is a retry), lifecycle (after the PR opens on a non-protected base: sync, retest when anything came in, land, accept, keep one standing development-into-main PR), tests for both.
- deploy: create development on each repo's remote from main, set each repo's default_branch to development, restart the board.
