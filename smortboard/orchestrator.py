"""mission control: one message from fabian, one headless turn, cards created by the board.

The orchestrator proposes; it never writes the store directly. It runs in a throwaway container -
same shape as the reviewer (docker_available, read_card_token, token on stdin, no volume mounts,
no tools at all) - and returns JSON matching ORCHESTRATOR_JSON_SCHEMA. THE BOARD CREATES THE
CARDS, not the model: repo names are resolved against this board's own repos, dependencies against
titles in the same reply or existing cards, and an unresolvable name becomes a board message
instead of a silent card. See docs/PLAN.md Phase 4 and the API contract in the phase 4 brief.
"""

from __future__ import annotations

import json
import re
import shlex
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from smortboard.exec.backends import card_image, docker_available, read_card_token
from smortboard.exec.runner import build_command, run_process
from smortboard.prompts import active_prompt
from smortboard.store.api import Store

ORCHESTRATOR_PROMPT = (
    "You are Fabian's mission control partner for one smortboard board. You talk with him and plan "
    "work; you never touch code and never run a tool - none are available to you.\n\n"
    "You will be shown this board's repos, its cards, its current plan, and recent conversation. "
    "Reply conversationally to Fabian's message, then propose cards for the work you agree belongs "
    "on the board. YOU DO NOT CREATE CARDS - the board does, from the `cards` you return: it "
    "resolves each `repo` by name against this board's own repos (an unknown name gets no repo and "
    "a note, rather than being guessed at) and resolves `depends_on` against titles you proposed in "
    "this same reply or against cards already on the board. Keep cards feature-sized, not edit-sized "
    "- see 'a card is a feature, not an edit' in the plan. Return an updated `plan`: your own ledger "
    "of what the board is working toward, reconciled against the cards that exist, not a copy of "
    "them.\n\n"
    "Give each card a `model` for its worker: `sonnet` for ordinary work, `opus` only where the card "
    "needs real design judgement, `haiku` for mechanical edits, or null to use the board's default. "
    "Fabian can change it on the card.\n\n"
    "Return JSON matching the given schema. `cards` may be empty - most turns are just "
    "conversation."
)

ORCHESTRATOR_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "plan": {"type": "string"},
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "repo": {"type": ["string", "null"]},
                    "criteria": {"type": "array", "items": {"type": "string"}},
                    "tasks": {"type": "array", "items": {"type": "string"}},
                    "leases": {"type": "array", "items": {"type": "string"}},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "model": {"type": ["string", "null"]},
                },
                "required": [
                    "title",
                    "description",
                    "repo",
                    "criteria",
                    "tasks",
                    "leases",
                    "depends_on",
                    "model",
                ],
            },
        },
    },
    "required": ["reply", "plan", "cards"],
}

# a turn is a conversation, not a build - it should cost far less than a card run
DEFAULT_TURN_BUDGET_USD = 1.00
DEFAULT_ORCHESTRATOR_MODEL = "opus"

# a model name reaches the card's `claude --model`, so anything but a plain alias or id is refused
_MODEL_NAME = re.compile(r"^[a-z0-9][a-z0-9.\-]{0,63}$")


def _clean_model(raw: Any) -> tuple[str | None, str | None]:
    """the card's model as the orchestrator proposed it, or None with a note when it is not a name"""
    if raw is None or raw == "":
        return None, None
    name = str(raw).strip().lower()
    if _MODEL_NAME.match(name):
        return name, None
    return None, f'model "{raw}" is not a model name, so that card uses the board default'


_MESSAGE_HISTORY = 20
# fabian's actual comment length is unbounded, but the snapshot's cards list stays short - see
# _snapshot_card
_BOARD_AUTHOR = "board"


class OrchestratorRunner(Protocol):
    """a turn: a prompt and a model in, the raw response text out. tests inject a fake."""

    def __call__(self, prompt: str, model: str, budget_usd: float) -> str: ...


def _real_runner(token_path: str | Path | None, system_prompt: str) -> OrchestratorRunner:
    """the production runner: a throwaway container, same handoff as the reviewer's."""

    def run(prompt: str, model: str, budget_usd: float) -> str:
        if not docker_available():
            raise RuntimeError("Docker is not running, and the orchestrator runs in a container.")
        token = read_card_token(token_path)
        claude_cmd = build_command(
            prompt,
            None,
            model=model,
            allowed_tools=(),
            budget_usd=budget_usd,
            system_prompt=system_prompt,
        )
        claude_cmd += ["--json-schema", json.dumps(ORCHESTRATOR_JSON_SCHEMA)]
        inner = (
            "IFS= read -r CLAUDE_CODE_OAUTH_TOKEN && export CLAUDE_CODE_OAUTH_TOKEN && "
            + shlex.join(claude_cmd)
            + " < /dev/null"
        )
        # no -v at all: the orchestrator never sees the filesystem, not even read-only
        cmd = ["docker", "run", "--rm", "-i", card_image(), "sh", "-c", inner]
        result = run_process(None, "orchestrator", cmd, stdin_text=token + "\n")
        if result.blocked_reason_code is not None or not result.result_text:
            raise RuntimeError(
                f"orchestrator run did not complete cleanly: "
                f"{result.blocked_reason_code or 'no output'}"
            )
        return result.result_text

    return run


def _short_id(card_id: str) -> str:
    return card_id[:8]


def _snapshot_repos(store: Store, board_id: str) -> list[dict[str, Any]]:
    return [
        {
            "name": r["name"],
            "default_branch": r["default_branch"],
            "test_command": r["test_command"],
        }
        for r in store.list_repos(board_id)
    ]


def _snapshot_cards(store: Store, board_id: str) -> list[dict[str, Any]]:
    cards = []
    for card in store.list_cards(board_id):
        cards.append(
            {
                "id": _short_id(card["id"]),
                "title": card["title"],
                "status": card["status"],
                "blocked_reason_code": card["blocked_reason_code"],
                "depends_on": [_short_id(d) for d in card["depends_on"]],
            }
        )
    return cards


def build_board_snapshot(store: Store, board_id: str) -> dict[str, Any]:
    """everything the orchestrator sees of this board - no host paths, ever"""
    return {
        "repos": _snapshot_repos(store, board_id),
        "cards": _snapshot_cards(store, board_id),
        "plan": store.get_plan(board_id),
        "messages": [
            {"author": m["author"], "body": m["body"]}
            for m in store.list_orchestrator_messages(board_id, limit=_MESSAGE_HISTORY)
        ],
    }


def build_turn_prompt(snapshot: dict[str, Any], message: str) -> str:
    return (
        "Board snapshot:\n"
        + json.dumps(snapshot, indent=2)
        + "\n\nFabian's new message:\n"
        + message
    )


@dataclass
class OrchestratorTurnResult:
    reply_message: dict[str, Any] | None
    error: str | None = None


def _resolve_repo(store: Store, board_id: str, name: str | None) -> tuple[str | None, str | None]:
    """returns (repo_id, warning) - warning is set when a name was given but matched nothing"""
    if not name:
        return None, None
    for repo in store.list_repos(board_id):
        if repo["name"] == name:
            return repo["id"], None
    return None, f'Repo "{name}" was not found on this board, so the card has no repo.'


def run_orchestrator_turn(
    store: Store,
    board_id: str,
    message: str,
    token_path: str | Path | None = None,
    runner: OrchestratorRunner | None = None,
    store_message: bool = True,
) -> OrchestratorTurnResult:
    """one full turn: store fabian's message, run the orchestrator, create the cards it proposed,
    store its reply and the new plan. a failed or unparseable run stores a board error message
    instead and leaves the plan untouched.

    `store_message=False` for the http path, which stores fabian's message itself before handing
    the turn to its own thread - so a 202 response can already show it, without a race against the
    thread doing it a moment later"""
    if store_message:
        store.add_orchestrator_message(board_id, "fabian", message)

    model = store.get_settings().get("orchestrator_model") or DEFAULT_ORCHESTRATOR_MODEL
    system_prompt = active_prompt(store, "orchestrator", ORCHESTRATOR_PROMPT)
    snapshot = build_board_snapshot(store, board_id)
    prompt = build_turn_prompt(snapshot, message)
    run = runner or _real_runner(token_path, system_prompt)

    try:
        raw = run(prompt, model, DEFAULT_TURN_BUDGET_USD)
    except Exception as exc:  # noqa: BLE001 - any runner failure becomes a board message, not a crash
        error = f"the orchestrator run failed: {exc}"
        store.add_orchestrator_message(board_id, _BOARD_AUTHOR, error)
        return OrchestratorTurnResult(reply_message=None, error=error)

    try:
        data = json.loads(raw)
        reply, plan, proposed = data["reply"], data["plan"], data.get("cards") or []
        if (
            not isinstance(reply, str)
            or not isinstance(plan, str)
            or not isinstance(proposed, list)
        ):
            raise ValueError("malformed orchestrator response shape")
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        error = f"the orchestrator's response could not be parsed: {exc}"
        store.add_orchestrator_message(board_id, _BOARD_AUTHOR, error)
        return OrchestratorTurnResult(reply_message=None, error=error)

    created_by_title: dict[str, str] = {}
    created_summaries: list[dict[str, str]] = []
    warnings: list[str] = []

    for spec in proposed:
        title = str(spec.get("title") or "").strip()
        if not title:
            continue
        repo_id, warning = _resolve_repo(store, board_id, spec.get("repo"))
        if warning:
            warnings.append(warning)
        model, warning = _clean_model(spec.get("model"))
        if warning:
            warnings.append(warning)
        card = store.create_card(
            board_id,
            repo_id,
            title,
            status="todo",
            description=spec.get("description") or None,
            criteria=list(spec.get("criteria") or []),
            tasks=list(spec.get("tasks") or []),
            leases=list(spec.get("leases") or []),
            model=model,
        )
        created_by_title[title] = card["id"]
        created_summaries.append({"id": card["id"], "title": title})

    # depends_on resolves against titles proposed in this same reply first, then against any
    # existing card on the board by id or title - a name matching neither is dropped silently,
    # since the schema gives no way to say why one dependency in a list failed to resolve
    existing_cards = store.list_cards(board_id)
    existing_by_title = {c["title"]: c["id"] for c in existing_cards}
    existing_ids = {c["id"] for c in existing_cards}
    for spec in proposed:
        title = str(spec.get("title") or "").strip()
        card_id = created_by_title.get(title)
        if card_id is None:
            continue
        for dep_name in spec.get("depends_on") or []:
            dep_id = created_by_title.get(dep_name) or existing_by_title.get(dep_name)
            if dep_id is None and dep_name in existing_ids:
                dep_id = dep_name
            if dep_id is not None and dep_id != card_id:
                store.add_dependency(card_id, dep_id)

    for warning in warnings:
        store.add_orchestrator_message(board_id, _BOARD_AUTHOR, warning)

    store.set_plan(board_id, plan)
    reply_message = store.add_orchestrator_message(
        board_id, "orchestrator", reply, cards=created_summaries or None
    )
    return OrchestratorTurnResult(reply_message=reply_message)


class OrchestratorRegistry:
    """mirrors server.runs.RunRegistry's shape, one board at a time: is it thinking, and what did
    the last turn's error say. its own thread and its own store connection per turn, same reason -
    the server is single-threaded and a turn takes real time."""

    def __init__(self, db_path: str | Path, token_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path)
        self._token_path = token_path
        self._thinking: dict[str, bool] = {}
        self._error: dict[str, str | None] = {}
        self._lock = threading.Lock()

    def thinking(self, board_id: str) -> bool:
        with self._lock:
            return self._thinking.get(board_id, False)

    def error(self, board_id: str) -> str | None:
        with self._lock:
            return self._error.get(board_id)

    def start(
        self,
        board_id: str,
        message: str,
        runner: OrchestratorRunner | None = None,
        message_already_stored: bool = False,
    ) -> bool:
        """starts a turn, or refuses if one is already thinking on this board. returns whether it
        started. `message_already_stored` lets the http handler store fabian's message itself,
        synchronously, before this returns."""
        with self._lock:
            if self._thinking.get(board_id, False):
                return False
            self._thinking[board_id] = True
            self._error[board_id] = None

        thread = threading.Thread(
            target=self._run,
            args=(board_id, message, runner, message_already_stored),
            daemon=True,
        )
        thread.start()
        return True

    def _run(
        self,
        board_id: str,
        message: str,
        runner: OrchestratorRunner | None,
        message_already_stored: bool,
    ) -> None:
        store = Store(self._db_path)  # this thread's own connection, never the server's
        try:
            result = run_orchestrator_turn(
                store,
                board_id,
                message,
                token_path=self._token_path,
                runner=runner,
                store_message=not message_already_stored,
            )
            with self._lock:
                self._error[board_id] = result.error
        except Exception as exc:  # noqa: BLE001 - a dead thread must still clear thinking
            with self._lock:
                self._error[board_id] = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._thinking[board_id] = False
            store.close()
