"""the neutral contract between a lab's command line and the board"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class Capabilities:
    live_steering: bool = False
    native_budget: bool = False
    reports_cost_usd: bool = False
    pre_tool_hooks: bool = False
    tool_allowlist: bool = False
    structured_output: bool = False
    rate_limit_windows: bool = False


@dataclass(frozen=True)
class ToolPolicy:
    read: bool = True
    edit: bool = True
    search: bool = True
    bash_allow: tuple[str, ...] = ("git *",)
    read_only: bool = False


@dataclass(frozen=True)
class RunRequest:
    prompt: str
    settings_path: str | Path | None = None
    model: str = "sonnet"
    allowed_tools: tuple[str, ...] | None = None
    budget_usd: float | None = 5.0
    system_prompt: str = ""
    stream_input: bool = False
    role: str = "worker"
    json_schema: dict[str, Any] | None = None
    schema_path: str | Path | None = None
    tool_policy: ToolPolicy | None = None
    read_only: bool = False


@dataclass(frozen=True)
class LabEvent:
    kind: str
    lab: str = "anthropic"
    model: str | None = None
    profile: str = "default"
    role: str = "worker"
    usage: dict[str, Any] | None = None
    rate_limit: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    text: str | None = None
    tool: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class GuardFiles:
    settings_path: Path | None
    files: tuple[Path, ...] = ()


@dataclass(frozen=True)
class BashPolicy:
    # where the guard files are written on the host - a board-owned dir, never the worktree
    out_dir: str | Path
    # the repo root the hooks check paths against, as the runner sees it
    root: str
    python: str = "python3"
    guard_dir: str | None = None
    remembered_globs: list[str] = field(default_factory=list)
    bash_allow: tuple[str, ...] = ("git *",)
    read: bool = True
    search: bool = True
    read_only: bool = False


class LabAdapter(Protocol):
    lab: str
    cli: str
    capabilities: Capabilities

    def build_command(self, req: RunRequest) -> list[str]: ...
    def auth_shell(self, profile: Any = None) -> str: ...
    def container_env(self) -> list[str]: ...
    def normalize(self, raw: dict[str, Any]) -> list[LabEvent]: ...
    def classify(self, final: LabEvent) -> str | None: ...
    def steering_line(self, text: str) -> str | None: ...
    def guard_files(self, lease: list[str], bash: BashPolicy) -> GuardFiles: ...
    def guard_mounts(self, settings_path: str | Path | None) -> list[str]: ...
    def preflight(self, profile: Any) -> list[dict[str, Any]]: ...
    def setup_hint(self) -> str: ...


def estimate_usage(usage: dict[str, Any], lab: str, model: str | None) -> dict[str, Any]:
    """price token counts only when an operator supplied all three rates"""
    from smortboard.labs.catalog import load_catalog

    if usage.get("cost_usd") is not None:
        return usage
    rows = load_catalog().get(lab, {}).get("models", [])
    prices = next((row.get("price_per_mtok") for row in rows if row["id"] == model), None)
    if not prices or not all(key in prices for key in ("input", "cached_input", "output")):
        return {**usage, "cost_usd": None, "cost_estimated": False}
    cached = int(usage.get("cached_tokens") or 0)
    uncached = max(0, int(usage.get("input_tokens") or 0) - cached)
    cost = (
        uncached * prices["input"]
        + cached * prices["cached_input"]
        + int(usage.get("output_tokens") or 0) * prices["output"]
    ) / 1_000_000
    return {**usage, "cost_usd": cost, "cost_estimated": True}
