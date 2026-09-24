# leases

**keep leases narrow, and keep them apart.**

a lease is the paths a card may write. no note or argument with the agent moves it. small leases
that do not overlap are what let cards run side by side.

> [!IMPORTANT]
> **overlap serialises, empty refuses.**
> two cards in one repo whose leases could touch the same file never run at once, so overlap
> silently serialises your board. an empty lease allows nothing: a card without one is refused,
> not run.

## how it works

name the files the card really touches: `src/refunds/**`, `tests/test_refunds.py`. leases are
gitignore-style globs relative to the repo root: `*` stays inside one folder, `**/` is any depth, a
trailing `**` is everything below.

### strict and soft

**soft file leases** in `o` is one switch for every board: enabled or disabled, disabled by default.
while it is disabled every board is strict. while it is enabled, each board picks strict or soft in
`shift`+`o` under *file lease*. disabling it puts every board back on strict.

| mode | a card may write | use it when |
|---|---|---|
| **strict** (default) | its own lease, plus paths you approved for the whole repo | you want every file decided up front |
| **soft** | also any other repo path that is not protected and that no other active card holds | the card knows better than its lease where a fix belongs |

every path a soft card reaches beyond its lease shows on the card under needs, and the reviewer is
told about each one.

### protected paths

soft never reaches these. a lease that names one explicitly still allows it.

| group | paths |
|---|---|
| agent settings and hooks | `.claude/`, `.codex/`, `.git/`, `.vscode/`, `.husky/`, `.pre-commit-config.yaml` |
| ci | `.github/` |
| the next agent's orders | `CLAUDE.md`, `AGENTS.md` |
| what the host builds or installs from | dockerfiles, `docker/`, `pyproject.toml`, `uv.lock`, `package.json`, `package-lock.json`, `requirements*.txt`, `.gitignore`, `.gitattributes`, `.gitmodules` |
| secrets | `.env*`, `*.pem`, `*.key` |

### checked twice

a `PreToolUse` hook checks every `Edit` and `Write` before it lands. after the run, the board checks
every committed path again. the hook, the codex guard and the post-run check share one rule, so they
cannot disagree.

a refused write does not sink committed work. if the committed paths pass the post-run check, the
card goes on to the test gate and the reviewer, and the refused paths wait under needs. only a run
that committed nothing, or committed outside its lease, blocks as `LEASE_CONFLICT`.

### only you widen a lease

the guard reads the card's stored lease rows, never a note or a message. approving a lease in the
[inbox](attention.md) can also *remember* the paths for the whole repo, so later cards are not asked
again. remembered paths count in the hook and in the post-run check.

### what a lease does not cover

leases guard writes only; reads are not limited. shell commands go through a separate allowlist:
exactly the repo's declared `test_command` (and `lint_command`, if set) plus git, never a bare `Bash`
(see [repos](../reference/repos.md)).

if the declared command runs a formatter in check-only form (`ruff format --check ...`), the card
also gets its write form, so it fixes what it finds rather than only reporting it. an out-of-lease
path it reformats is still refused by the post-run check.

the real boundary is the container (see [security](../reference/security.md)).
