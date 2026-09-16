"""who the board works for: the name agents are told to trust, and the one the board shows on their
own lines. stored rows keep the internal author key "operator" - only what people and agents read uses
this name."""

import os
import subprocess


def _git_user_name() -> str | None:
    try:
        found = subprocess.run(
            ["git", "config", "--global", "user.name"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return found.stdout.strip() or None


# the env var first, then the git identity, then a neutral word - read once, at import
OPERATOR_NAME = os.environ.get("SMORTBOARD_OPERATOR_NAME") or _git_user_name() or "the operator"

# the stored author key, stable regardless of OPERATOR_NAME - see schema.py migration 16
AUTHOR_KEY = "operator"
