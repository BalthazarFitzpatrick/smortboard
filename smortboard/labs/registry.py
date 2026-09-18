"""construct an adapter per run so stream state never leaks across cards"""

from smortboard.labs.base import LabAdapter


def get_adapter(lab: str = "anthropic") -> LabAdapter:
    if lab == "anthropic":
        from smortboard.labs.claude_code import ClaudeCodeAdapter

        return ClaudeCodeAdapter()
    if lab == "openai":
        from smortboard.labs.codex import CodexAdapter

        return CodexAdapter()
    raise ValueError(f"unknown lab: {lab}")
