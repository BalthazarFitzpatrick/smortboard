"""prompt layering: the role is the unit, not the card or the repo.

Each of the three roles - orchestrator, worker, reviewer - has a seed default living in its own
module's code (runner.SYSTEM_PROMPT, reviewer.REVIEW_PROMPT_HEADER, orchestrator.ORCHESTRATOR_PROMPT).
A stored prompt overrides the seed; every save is a new version, so history is kept rather than
overwritten. See docs/prompts.md.

Lives in its own module, not store/api.py, so runner/reviewer/orchestrator can import it without
each other - importing Store here instead would have made every one of them import the others.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from smortboard.store.api import Store

ROLES = ("orchestrator", "worker", "reviewer")


def active_prompt(store: Store | None, role: str, default: str) -> str:
    """the prompt a run should use: the latest stored version for `role`, or `default` if none."""
    if store is None:
        return default
    stored = store.get_prompt(role)
    return stored["body"] if stored is not None else default
