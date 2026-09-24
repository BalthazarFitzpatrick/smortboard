# Leases

**Keep leases narrow, and keep them apart.**

A lease is the paths a card may write. No note or argument with the agent moves it. Small leases
that do not overlap are what let cards run side by side.

> [!IMPORTANT]
> **Overlap serialises, empty refuses.**
> Two cards in one repo whose leases could touch the same file never run at once, so overlap
> silently serialises your board. An empty lease allows nothing: a card without one is refused,
> not run.

## How it works

Name the files the card really touches: `src/refunds/**`, `tests/test_refunds.py`. Leases are
gitignore-style globs relative to the repo root: `*` stays inside one folder, `**/` is any depth, a
trailing `**` is everything below.

### Strict and soft

**Soft file leases** in `o` is one switch for every board: enabled or disabled, disabled by default.
While it is disabled every board is strict. While it is enabled, each board picks strict or soft in
`shift`+`o` under *file lease*. Disabling it puts every board back on strict.

| Mode | A card may write | Use it when |
|---|---|---|
| **strict** (default) | its own lease, plus paths you approved for the whole repo | you want every file decided up front |
| **soft** | also any other repo path that is not protected and that no other active card holds | the card knows better than its lease where a fix belongs |

Every path a soft card reaches beyond its lease shows on the card under needs, and the reviewer is
told about each one.

### Protected paths

Soft never reaches these. A lease that names one explicitly still allows it.

| Group | Paths |
|---|---|
| agent settings and hooks | `.claude/`, `.codex/`, `.git/`, `.vscode/`, `.husky/`, `.pre-commit-config.yaml` |
| CI | `.github/` |
| the next agent's orders | `CLAUDE.md`, `AGENTS.md` |
| what the host builds or installs from | Dockerfiles, `docker/`, `pyproject.toml`, `uv.lock`, `package.json`, `package-lock.json`, `requirements*.txt`, `.gitignore`, `.gitattributes`, `.gitmodules` |
| secrets | `.env*`, `*.pem`, `*.key` |

### Checked twice

A `PreToolUse` hook checks every Edit and Write before it lands. After the run, the board checks
every committed path again. The hook, the Codex guard and the post-run check share one rule, so they
cannot disagree.

A refused write does not sink committed work. If the committed paths pass the post-run check, the
card goes on to the test gate and the reviewer, and the refused paths wait under needs. Only a run
that committed nothing, or committed outside its lease, blocks as `LEASE_CONFLICT`.

### Only you widen a lease

The guard reads the card's stored lease rows, never a note or a message. Approving a lease in the
[inbox](attention.md) can also *remember* the paths for the whole repo, so later cards are not asked
again. Remembered paths count in the hook and in the post-run check.

### What a lease does not cover

Leases guard writes only; reads are not limited. Shell commands go through a separate allowlist:
exactly the repo's declared `test_command` (and `lint_command`, if set) plus git, never a bare `Bash`
(see [repos](../reference/repos.md)).

If the declared command runs a formatter in check-only form (`ruff format --check ...`), the card
also gets its write form, so it fixes what it finds rather than only reporting it. An out-of-lease
path it reformats is still refused by the post-run check.

The real boundary is the container (see [security](../reference/security.md)).
