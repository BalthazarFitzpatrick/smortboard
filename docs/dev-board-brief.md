> **So you have found the origin story.** This is the brief smortboard started from, written on a
> plane in September 2026 and handed to Claude as the planning prompt, which is why it opens with
> instructions. Kept as written; the README is what it became.

# Dev Board — Agent Orchestration Wrapper

## How to use this document

This is a vision brief, not a spec. I wrote the raw version on a plane; this is the cleaned-up
version. Nothing here is final except the items under **Decided**.

**What I want from you, in this order:**

1. Read the whole thing.
2. Ask me questions — lots of them. Batch them by section, numbered, so I can answer inline.
   Start with the questions that would most change the architecture if answered differently.
   Don't ask me to confirm things I've already decided unless you think the decision is wrong,
   in which case say so plainly and tell me why.
3. Push back. Where my design is confused, over-engineered, or fights the grain of the tools,
   say so. Where I've assumed something (that this is easy, that agents can do this, that this
   is how git works) that isn't true, correct me.
4. Propose the things I haven't thought of. I don't know what I don't know here.
5. Only after we've been through that: write a very detailed implementation plan — phased,
   with milestones, each phase independently shippable and testable.

Do not write application code until the plan is agreed.

---

## 1. What this is

A convenience wrapper around coding agents. **Not a harness.** I am not trying to build a new
agent framework or reimplement what Claude Code / Ollama-hosted models already do. I want a board
that lets me:

- Define work as cards, in detail, up front.
- Hand cards to agents and walk away.
- Come back and see state, ask questions, approve or reject.

The design goal behind every feature below: **I want to get good at defining a project and then
leaving it alone.** Asynchronous work via the board is the point. Watching an agent type is a
failure mode, not a feature. Anything in this design that encourages hovering should be argued
against.

## 2. Non-goals

- Not a general project-management tool. Single user (me), local-first.
- Not a replacement for the agents' own reasoning loops.
- Not multi-tenant, not a SaaS, no auth system beyond credential storage.

---

## 3. Decided (challenge only if genuinely broken)

- Runs in a browser.
- ~~Distributed via Docker.~~ **REVISED 2026-09-09:** installed with `uv tool install smortboard`
  and run natively; Docker is a hard dependency and isolates each card instead - there is no fallback mode, because a board that silently ran cards unisolated would be claiming a boundary it no longer had. A containerised board that starts card
  containers needs the Docker socket, which is root on the host — the container was always there to
  contain the cards, not the board. See "The containment decision" in `PLAN.md`.
- Uses the visual design language of my **ui base repo**. Any element this project needs that
  ui base doesn't have gets **built in ui base**, not here. Existing ui base elements get
  extended or restyled in ui base if warranted. This repo contains configuration of ui base
  elements and nothing else UI-wise. This is a hard constraint.
- Keyboard-first. Mouse works, but every interaction has a key path.
- Local models via Ollama, plus one or more Claude subscriptions, extensible to other providers.
- Credentials stored securely.
- Every card's work ships with unit tests derived from its acceptance criteria, plus whatever
  else you judge necessary during development.
- All UI APIs and inter-agent communication protocols are documented well enough that future
  work on this doesn't break them.

---

## 4. Layout and navigation

### 4.1 Boards

- Multiple boards, swipeable side to side like virtual desktops.
- Controls are named buttons in a row at the top of the page.

### 4.2 Main view — two modes

1. Kanban columns: **To do / Doing / Checking / Accepted / Rejected**
2. Columns grouped by **workstream**

*[OPEN] How do I switch modes? Not specified. Suggest something.*

### 4.3 Cards

- Collapsed: a wide, flat horizontal rectangle, roughly 3:1 width:height, tastefully spaced.
- Expanded: click opens it in the centre of the screen as a large **vertical** rectangle,
  roughly 1:3 width:height. Both ratios are width:height and both are deliberate — the card
  turns from a landscape strip into a tall portrait panel.
- Click outside collapses it.
- A card being actively worked on by an agent: **green highlight**.
- A card needing my attention: **yellow-gold outline**.

### 4.4 Side panels

Two boxes, one per side, parked almost entirely off-screen with a sliver visible — the right
edge of the screen shows a centimetre or two of the *left* part of the right box, and vice versa.
Both 1:3 (portrait). Clicking the sliver slides the box into the centre of its half of the screen.

**Right box — Mission Control.** My chat with the orchestrator model (Opus or similar). This is
where I do constant chatting and planning. The orchestrator also creates, manages and maintains
cards agentically, works through dependencies, and keeps going toward the goal of a workstream
until it's concluded.

**Left box — Workforce.** A chat window into the sessions of the agents working on cards.

- Opened via its own sliver: cycles through the chat sessions of active cards. If only one is
  active, it sticks with that one.
- Clicking any card pins the left box to that card's agent session, if active.
- For a **done** card, it shows an agent-generated summary of the work performed.
- I mostly use this to *watch*. Direct communication with a workforce agent should be rare —
  that's what the attention system is for.

**Dismissal:** deselecting a card slides the left box back to parked, as does clicking its
border. Clicking the right box's border parks it too.

### 4.5 Attention system

- Cards needing input get the gold outline.
- An alert indicator top-right shows how many cards need attention.
- Clicking it expands a list; clicking an entry jumps to and opens that card in the main view,
  exactly as if I'd selected it myself.

### 4.6 Top-right controls

- **Usage & account dropdown** — opens with `u` or mouse. A tab or column per model in use.
  Multiple subscriptions of the same model appear as additional sections after a divider,
  classic ui base style. Shows rate/quota limits and how close we are to them, plus token
  counts: input, output, thinking.
- **Options menu** — next to the usage dropdown. Lightweight. Only what's needed.

---

## 5. Keyboard model

Focus starts on the top board bar.

| Context | Key | Action |
|---|---|---|
| Top bar | ←/→ | Cycle boards |
| Top bar | ↓ / Enter / Space | Move to first card of first column |
| Cards | ↓ / ↑ | Move down/up within a column |
| Cards | ←/→ | Move between columns |
| Top card in column | ↑ | Return to top bar |
| Card focused | Enter / Space | Open card |
| Card open | Esc | Close card |
| Anywhere | Cmd/Alt + → | Toggle orchestrator (right) panel |
| Anywhere | Alt + ← | Toggle workforce (left) panel, cycling active cards |
| Card focused or open | Alt/Cmd + ← | Open workforce panel pinned to *that* card's agent; same press again closes |
| Anywhere | `u` | Usage dropdown |
| Card focused or open | `i` | Toggle the card's model/token section |
| Anywhere | `s` | Keyboard shortcut overlay |

*[FLAG] Unresolved: does ←/→ still navigate columns while a card is open? Does `u`/`s` work while
a card is open or a panel is focused? What has focus after a panel slides in — the panel's input,
or the board? How do I get focus back out of a panel without closing it? Tab order generally?
Ask me.*

*[FLAG] Alt+← vs Cmd+← is inconsistent in my notes. Decide a coherent scheme and propose it —
including what happens on Linux/Windows where Cmd doesn't exist.*

---

## 6. Motion

- Very short animations throughout.
- Keyboard focus **rubberbands** to the next element, using the cream highlight from ui base.
- Panels slide. Cards expand and compact.
- Opening a card pushes the side panels a little further off-screen to make room.

---

## 7. Card anatomy

Every card has:

- Title
- Workstream
- Status
- Description
- Tasks
- Acceptance criteria
- Dependencies — both *depends on* and *depended on by*
- File attachments
- Comments
- **Telemetry section**, toggled with `i` or a button: which model did which task on this card,
  and the input / thinking / output tokens it spent. "Task" here means either *working the card*
  or *checking the work*.
- **Review flag** — per-card option, with a global override, to have a second agent review the
  work for coding practices, vulnerabilities, inefficiencies, leaked credentials, performance,
  and so on.

---

## 8. Agents and models

- Orchestrator model in Mission Control; workforce models on cards.
- Ollama for local/open-weight models; one or more Claude subscriptions; extensible to others.
- Parallel agents should talk to each other using the native mechanism where one exists
  (sendmessage or that provider's equivalent). Where an open-weight model has no such mechanism,
  **the board provides the transport layer**. That transport needs to be documented as a protocol.
- Review agent as described in §7.

**[OPEN] System prompts.** I haven't thought this through at all. Where do they live? Per board,
per workstream, per card, per model, per role (orchestrator vs worker vs reviewer)? Versioned?
Editable from the UI? Tell me what the sensible layering is.

---

## 9. Git workflow

This is the part I'm least sure about and most want your opinion on.

- Baseline: you have the repo locally, you can **commit and push, but not merge**. I merge on
  request.
- I've assumed a **branch or worktree per card** is right. Tell me the actual best practice.
- The hard problem: **how do we keep workstreams separated enough that they merge cleanly?**
  My whole model — define work, walk away, come back to finished cards — collapses if I return
  to five branches that all conflict. What does the board need to do at *card definition* time
  to prevent that? File-ownership declarations per workstream? Dependency-ordered serialisation
  of anything touching shared files? Something else?
- Design the request-to-merge flow: how a finished card surfaces to me, what I see to make the
  call, what happens on rejection.

---

## 10. Persistence

Needs to persist and be **easy to carry between systems**.

- I'm torn between a lightweight database and plain text files. Argue both. Consider: diffability,
  git-trackability, portability, concurrent agent writes, query needs (dependency graphs, usage
  aggregation).
- **[OPEN] Do we also keep an internal task ledger, or are the cards enough?** My instinct is that
  cards might be too coarse for what an orchestrator needs to track, but I don't want two sources
  of truth. Tell me.

---

## 11. Scheduled and recurring work

I think there should be a scheduled/recurring tasks section. Not thought through beyond that.
What does it schedule — card creation, card execution, maintenance sweeps? Where does it live in
the UI?

---

## 12. Packaging

- Docker is the baseline distribution.
- I'd also like it to be an application on Linux, Mac and Windows — still browser-based
  underneath.
- And especially: **a plugin for Omarchy**. I don't know how one does that. Find out and tell me
  what it involves and whether it's worth doing early or later.

---

## 13. Documentation

All UI APIs and all communication-protocol APIs documented to the standard that future
development doesn't break them. Treat this as a deliverable per phase, not a final step.

---

## 14. My open questions, collected

1. Branch vs worktree per card — what's actually best practice?
2. How do we keep workstreams merge-clean when I'm not watching?
3. Task ledger in addition to cards, or cards only?
4. System prompts — where do they live and at what granularity?
5. Database or text files for persistence?
6. How does one build an Omarchy plugin?
7. How do I switch between kanban and workstream column modes?

Add to this list anything I should be asking and am not.
