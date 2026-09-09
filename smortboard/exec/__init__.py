"""phase 2 execution layer — worktree per card, path leases, and the headless runner"""

from smortboard.exec.bash_guard import BASH_ESCAPE_PREFIX
from smortboard.exec.leases import write_lease_settings
from smortboard.exec.runner import RunResult, allowed_tools_for_repo, run_card
from smortboard.exec.worktrees import (
    WorktreeInfo,
    create_worktree,
    destroy_worktree,
    list_worktrees,
)

__all__ = [
    "WorktreeInfo",
    "create_worktree",
    "destroy_worktree",
    "list_worktrees",
    "write_lease_settings",
    "BASH_ESCAPE_PREFIX",
    "RunResult",
    "run_card",
    "allowed_tools_for_repo",
]
