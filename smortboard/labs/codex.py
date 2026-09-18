"""Codex exec protocol verified against the pinned 0.154.0 CLI"""

import json
import re
from pathlib import Path
from typing import Any

from smortboard.labs.base import BashPolicy, Capabilities, GuardFiles, LabEvent, RunRequest


class CodexAdapter:
    lab = "openai"
    cli = "codex"
    capabilities = Capabilities(structured_output=True, pre_tool_hooks=True)

    def __init__(self) -> None:
        self._session_id: str | None = None
        self._text: str | None = None
        self._structured: dict[str, Any] | None = None
        self._error: str | None = None

    def build_command(self, req: RunRequest) -> list[str]:
        readonly = req.read_only or bool(req.tool_policy and req.tool_policy.read_only)
        cmd = [
            "codex",
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--model",
            req.model,
            "--sandbox",
            "read-only" if readonly else "workspace-write",
        ]
        if req.system_prompt:
            cmd += ["-c", "developer_instructions=" + json.dumps(req.system_prompt)]
        if req.role in ("orchestrator", "fold", "reviewer"):
            cmd += ["--skip-git-repo-check"]
        if req.settings_path is not None:
            cmd += ["--dangerously-bypass-hook-trust"]
        if req.json_schema is not None or req.schema_path is not None:
            if req.schema_path is None:
                raise ValueError("Codex structured output requires a mounted schema_path")
            cmd += ["--output-schema", str(req.schema_path)]
        cmd.append(req.prompt)
        return cmd

    def auth_shell(self, profile: Any = None) -> str:
        kind = (
            profile.get("kind", "auth_json")
            if isinstance(profile, dict)
            else getattr(profile, "kind", "auth_json")
        )
        prefix = "export CODEX_HOME=/home/agent/.codex; umask 077; IFS= read -r T && "
        if kind == "auth_json":
            return prefix + 'printf %s "$T" > "$CODEX_HOME/auth.json" && unset T && '
        flag = {"api_key": "--with-api-key", "access_token": "--with-access-token"}.get(kind)
        if flag is None:
            raise ValueError(f"unsupported openai credential kind: {kind}")
        return prefix + f'printf %s "$T" | codex login {flag} >/dev/null && unset T && '

    def container_env(self) -> list[str]:
        seccomp = Path(__file__).with_name("codex-seccomp.json").resolve()
        return [
            "--tmpfs",
            "/home/agent/.codex:rw,noexec,nosuid,size=128m,uid=1000,gid=1000,mode=700",
            "--security-opt",
            f"seccomp={seccomp}",
        ]

    def normalize(self, raw: dict[str, Any]) -> list[LabEvent]:
        kind = raw.get("type")
        if kind == "thread.started":
            self._session_id = raw.get("thread_id")
            return []
        if kind == "error":
            self._error = raw.get("message")
            return []
        if kind in ("item.started", "item.completed"):
            item = raw.get("item") or {}
            item_type = item.get("type")
            if item_type == "agent_message" and kind == "item.completed":
                self._text = item.get("text")
                try:
                    parsed = json.loads(self._text or "")
                except json.JSONDecodeError:
                    parsed = None
                self._structured = parsed if isinstance(parsed, dict) else self._structured
                events = [LabEvent("assistant_text", lab=self.lab, text=self._text)]
                if self._structured is not None:
                    events.append(
                        LabEvent(
                            "tool_use",
                            lab=self.lab,
                            tool={
                                "name": "structured_output",
                                "structured_output": self._structured,
                            },
                        )
                    )
                return events
            if item_type in ("command_execution", "file_change", "mcp_tool_call"):
                tool = {
                    "name": item_type,
                    "input": item.get("command") or item.get("changes"),
                    "output": item.get("aggregated_output"),
                    "status": item.get("status"),
                    "id": item.get("id"),
                }
                output = str(item.get("aggregated_output") or "")
                denied = item.get("status") == "failed" and any(
                    word in output.lower() for word in ("denied", "refused", "lease_conflict")
                )
                return [LabEvent("tool_denied" if denied else "tool_use", lab=self.lab, tool=tool)]
            return []
        if kind in ("turn.completed", "turn.failed"):
            ok = kind == "turn.completed"
            error = raw.get("error") or {}
            text = self._text if ok else error.get("message") or self._error or self._text
            auth_failed = bool(
                re.search(
                    r"\b401\b|unauthorized|invalid.{0,30}(token|api.key)|authentication failed|not logged in",
                    text or "",
                    re.I,
                )
            )
            limited = bool(
                re.search(r"\b429\b|usage limit|rate limit|quota exceeded", text or "", re.I)
            )
            reason = None if ok else "USAGE_LIMIT" if limited else "CRASH"
            events = []
            if isinstance(raw.get("usage"), dict):
                usage = raw["usage"]
                events.append(
                    LabEvent(
                        "usage",
                        lab=self.lab,
                        usage={
                            "input_tokens": usage.get("input_tokens", 0),
                            "cached_tokens": usage.get("cached_input_tokens", 0),
                            "cache_creation_tokens": usage.get("cache_write_input_tokens", 0),
                            "output_tokens": usage.get("output_tokens", 0),
                            "cost_usd": None,
                            "cost_estimated": False,
                        },
                    )
                )
            events.append(
                LabEvent(
                    "result",
                    lab=self.lab,
                    result={
                        "ok": ok,
                        "subtype": "success" if ok else "error_during_execution",
                        "session_id": self._session_id,
                        "num_turns": 1,
                        "text": text,
                        "structured_output": self._structured,
                        "auth_failed": auth_failed,
                        "blocked_reason_code": reason,
                    },
                )
            )
            return events
        return []

    def classify(self, final: LabEvent) -> str | None:
        return (final.result or {}).get("blocked_reason_code")

    def steering_line(self, text: str) -> None:
        return None

    def guard_files(self, lease: list[str], bash: BashPolicy) -> GuardFiles:
        from smortboard.labs.codex_guards import write_guard_files

        return write_guard_files(lease, bash)

    def preflight(self, profile: Any) -> list[dict[str, Any]]:
        return []

    def guard_mounts(self, settings_path: str | Path | None) -> list[str]:
        if settings_path is None:
            return []
        return ["-v", f"{settings_path}:/home/agent/.codex/hooks.json:ro"]

    def setup_hint(self) -> str:
        return "codex login; import its auth.json, or use codex login --with-api-key"
