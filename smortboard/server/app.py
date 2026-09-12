"""the http api: routes json endpoints to the store, and assets per smortboard/server/assets.py"""

import json
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import parse_qs

from smortboard.attention import AnswerRefused, answer_card, attention_rows, with_actions
from smortboard.digest import board_digest
from smortboard.exec.runner import SYSTEM_PROMPT
from smortboard.operator import OPERATOR_NAME
from smortboard.orchestrator import (
    DEFAULT_ORCHESTRATOR_MODEL,
    ORCHESTRATOR_PROMPT,
    OrchestratorRegistry,
)
from smortboard.preflight import run_preflight
from smortboard.prompts import ROLES
from smortboard.review.decide import DecisionRefused, accept_card, reject_card
from smortboard.review.outcome import card_outcome
from smortboard.review.reviewer import REVIEW_PROMPT_HEADER
from smortboard.scheduler import SchedulerRegistry, conflicting_run
from smortboard.server.assets import AssetNotFound, content_type_for, resolve_asset
from smortboard.server.multipart import MultipartError, parse_boundary, parse_first_file
from smortboard.server.runs import (
    Readiness,
    RunNotActiveError,
    RunRegistry,
    recover_orphaned_runs,
)
from smortboard.store import Store
from smortboard.store.api import CARD_WRITABLE_FIELDS
from smortboard.store.errors import BlockedReasonInvalidError, NotFoundError, UnknownFieldError
from smortboard.store.repo_validation import validate_repo
from smortboard.telemetry import (
    board_costs,
    boards_overview,
    card_telemetry,
    roster_rows,
    usage_projection,
)
from smortboard.timeline import card_timeline

_ROUTES = [
    (re.compile(r"^/health$"), "GET"),
    (re.compile(r"^/api/boards$"), "GET"),
    (re.compile(r"^/api/boards$"), "POST"),
    (re.compile(r"^/api/boards/(?P<board_id>[^/]+)/cards$"), "GET"),
    (re.compile(r"^/api/boards/(?P<board_id>[^/]+)/repos$"), "GET"),
    (re.compile(r"^/api/boards/(?P<board_id>[^/]+)/repos$"), "POST"),
    (re.compile(r"^/api/repos/(?P<repo_id>[^/]+)$"), "PATCH"),
    (re.compile(r"^/api/cards$"), "POST"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)$"), "GET"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)$"), "PATCH"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)$"), "DELETE"),
    (re.compile(r"^/api/boards/(?P<board_id>[^/]+)$"), "DELETE"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/comments$"), "POST"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/attachments$"), "POST"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/attachments/(?P<attachment_id>[^/]+)$"), "GET"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/events$"), "GET"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/outcome$"), "GET"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/run$"), "POST"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/run$"), "GET"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/stop$"), "POST"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/accept$"), "POST"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/reject$"), "POST"),
    (re.compile(r"^/api/runs$"), "GET"),
    (re.compile(r"^/api/runtime$"), "GET"),
    (re.compile(r"^/api/preflight$"), "GET"),
    (re.compile(r"^/api/settings$"), "GET"),
    (re.compile(r"^/api/settings$"), "PATCH"),
    (re.compile(r"^/api/tasks/(?P<task_id>[^/]+)$"), "PATCH"),
    (re.compile(r"^/api/boards/(?P<board_id>[^/]+)/orchestrator$"), "GET"),
    (re.compile(r"^/api/boards/(?P<board_id>[^/]+)/orchestrator$"), "POST"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/conversation$"), "GET"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/conversation$"), "POST"),
    (re.compile(r"^/api/roster$"), "GET"),
    (re.compile(r"^/api/usage$"), "GET"),
    (re.compile(r"^/api/costs$"), "GET"),
    (re.compile(r"^/api/prompts$"), "GET"),
    (re.compile(r"^/api/prompts/(?P<role>[^/]+)$"), "PATCH"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/telemetry$"), "GET"),
    (re.compile(r"^/api/boards/(?P<board_id>[^/]+)/costs$"), "GET"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/timeline$"), "GET"),
    (re.compile(r"^/api/boards/(?P<board_id>[^/]+)/run-all$"), "POST"),
    (re.compile(r"^/api/boards/(?P<board_id>[^/]+)/run-all/stop$"), "POST"),
    (re.compile(r"^/api/boards/(?P<board_id>[^/]+)/schedule$"), "GET"),
    (re.compile(r"^/api/boards/(?P<board_id>[^/]+)/digest$"), "GET"),
    (re.compile(r"^/ui/(?P<name>.+)$"), "GET"),
    (re.compile(r"^/api/attention$"), "GET"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/answer$"), "POST"),
]

_ROLE_DEFAULTS = {
    "orchestrator": ORCHESTRATOR_PROMPT,
    "worker": SYSTEM_PROMPT,
    "reviewer": REVIEW_PROMPT_HEADER,
}


def _version() -> str:
    try:
        return version("smortboard")
    except PackageNotFoundError:
        return "0.0.0-dev"


def _make_handler(
    store: Store,
    runs: RunRegistry,
    readiness: Readiness,
    orchestrator: OrchestratorRegistry,
    scheduler: SchedulerRegistry,
    token_path: str | None = None,
) -> type[BaseHTTPRequestHandler]:
    """closes over the store instance; http.server wants a class, not an instance"""

    class Handler(BaseHTTPRequestHandler):
        server_version = "smortboard/0.1"

        def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
            pass  # keep test output quiet; nothing here is a diagnostic signal

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def do_PATCH(self) -> None:  # noqa: N802
            self._dispatch("PATCH")

        def do_DELETE(self) -> None:  # noqa: N802
            self._dispatch("DELETE")

        def _dispatch(self, method: str) -> None:
            path = self.path.split("?", 1)[0]
            try:
                for pattern, route_method in _ROUTES:
                    if route_method != method:
                        continue
                    match = pattern.match(path)
                    if match:
                        self._handle(method, path, **match.groupdict())
                        return
                self._send_json(404, {"error": f"no route for {method} {path}"})
            except NotFoundError as exc:
                self._send_json(404, {"error": str(exc)})
            except (BlockedReasonInvalidError, UnknownFieldError) as exc:
                self._send_json(400, {"error": str(exc)})
            except sqlite3.Error as exc:
                # a store error must still be an HTTP response. uncaught, it escaped _dispatch and
                # killed the connection, so the client saw no status at all - which is the hardest
                # possible failure to diagnose from the outside
                self._send_json(500, {"error": f"store error: {exc}"})
            except MultipartError as exc:
                self._send_json(400, {"error": str(exc)})
            except (KeyError, TypeError, ValueError) as exc:
                # malformed json body or a request missing a required field
                self._send_json(400, {"error": str(exc)})

        def _handle(self, method: str, path: str, **params: str) -> None:
            if path == "/health":
                self._send_json(200, {"ok": True, "version": _version(), "operator": OPERATOR_NAME})
            elif path == "/api/boards" and method == "GET":
                self._send_json(200, store.list_boards())
            elif path == "/api/boards" and method == "POST":
                body = self._read_json()
                board = store.create_board(name=body["name"])
                self._send_json(201, board)
            elif "board_id" in params and path.endswith("/cards"):
                self._send_json(200, with_actions(store, store.list_cards(params["board_id"])))
            elif "board_id" in params and path.endswith("/repos") and method == "GET":
                self._send_json(200, store.list_repos(params["board_id"]))
            elif "board_id" in params and path.endswith("/repos") and method == "POST":
                self._handle_create_repo(params["board_id"])
            elif "repo_id" in params and method == "PATCH":
                self._handle_patch_repo(params["repo_id"])
            elif path == "/api/cards" and method == "POST":
                body = self._read_json()
                card = store.create_card(**body)
                self._send_json(201, card)
            elif "attachment_id" in params:
                self._handle_get_attachment(params["card_id"], params["attachment_id"])
            elif path.endswith("/comments"):
                body = self._read_json()
                comment = store.add_comment(
                    params["card_id"], author=body["author"], body=body["body"]
                )
                self._send_json(201, comment)
            elif path.endswith("/attachments"):
                self._handle_upload(params["card_id"])
            elif path.endswith("/run") and method == "POST":
                self._handle_run(params["card_id"])
            elif "card_id" in params and path.endswith("/stop") and method == "POST":
                self._handle_stop(params["card_id"])
            elif path.endswith("/run") and method == "GET":
                state = runs.get(params["card_id"])
                self._send_json(
                    200,
                    state.as_dict()
                    if state
                    else {"card_id": params["card_id"], "phase": None, "running": False},
                )
            elif path.endswith("/accept"):
                self._handle_decision(params["card_id"], accept_card)
            elif path.endswith("/reject"):
                self._handle_decision(params["card_id"], reject_card)
            elif path.endswith("/outcome"):
                store.get_card(params["card_id"])  # a 404 for a missing card, not an empty outcome
                self._send_json(200, card_outcome(store, params["card_id"]))
            elif path == "/api/runs":
                self._send_json(200, [state.as_dict() for state in runs.active()])
            elif path == "/api/runtime":
                self._send_json(200, readiness.check())
            elif path == "/api/preflight":
                self._send_json(200, run_preflight(store, token_path=token_path))
            elif path == "/api/settings" and method == "GET":
                self._send_json(200, store.get_settings())
            elif path == "/api/settings" and method == "PATCH":
                for key, value in self._read_json().items():
                    store.set_setting(key, value)
                self._send_json(200, store.get_settings())
            elif path.endswith("/events"):
                self._send_json(200, store.list_events(params["card_id"]))
            elif "card_id" in params and path.endswith("/timeline"):
                self._handle_timeline(params["card_id"])
            elif "board_id" in params and path.endswith("/orchestrator") and method == "GET":
                self._send_json(200, self._orchestrator_view(params["board_id"]))
            elif "board_id" in params and path.endswith("/orchestrator") and method == "POST":
                self._handle_orchestrator_post(params["board_id"])
            elif "card_id" in params and path.endswith("/conversation") and method == "GET":
                self._send_json(200, self._conversation_view(params["card_id"]))
            elif "card_id" in params and path.endswith("/conversation") and method == "POST":
                self._handle_conversation_post(params["card_id"])
            elif path == "/api/roster":
                active = [{"card_id": s.card_id, "phase": s.phase} for s in runs.active()]
                self._send_json(200, roster_rows(store, active))
            elif path == "/api/usage":
                self._send_json(200, usage_projection(store))
            elif path == "/api/costs":
                self._send_json(200, boards_overview(store))
            elif path == "/api/prompts" and method == "GET":
                self._send_json(200, self._prompts_view())
            elif "role" in params and method == "PATCH":
                self._handle_patch_prompt(params["role"])
            elif "card_id" in params and path.endswith("/telemetry"):
                store.get_card(params["card_id"])  # a 404 for a missing card, not empty telemetry
                self._send_json(200, card_telemetry(store, params["card_id"]))
            elif "board_id" in params and path.endswith("/costs"):
                store.get_board(params["board_id"])  # a 404 for a missing board, not an empty table
                self._send_json(200, board_costs(store, params["board_id"]))
            elif path == "/api/attention":
                self._send_json(200, attention_rows(store))
            elif "card_id" in params and path.endswith("/answer"):
                self._handle_answer(params["card_id"])
            elif "card_id" in params and method == "GET":
                self._send_json(200, store.get_card(params["card_id"]))
            elif "card_id" in params and method == "PATCH":
                self._handle_patch_card(params["card_id"])
            elif "card_id" in params and method == "DELETE":
                store.delete_card(params["card_id"])
                self._send_status(204)
            elif "board_id" in params and method == "DELETE":
                store.delete_board(params["board_id"])
                self._send_status(204)
            elif "task_id" in params and method == "PATCH":
                self._handle_patch_task(params["task_id"])
            elif "board_id" in params and path.endswith("/run-all") and method == "POST":
                store.get_board(params["board_id"])
                self._send_json(202, scheduler.get(params["board_id"]).start_all())
            elif "board_id" in params and path.endswith("/run-all/stop") and method == "POST":
                store.get_board(params["board_id"])
                self._send_json(200, scheduler.get(params["board_id"]).stop())
            elif "board_id" in params and path.endswith("/schedule") and method == "GET":
                store.get_board(params["board_id"])
                self._send_json(200, scheduler.get(params["board_id"]).schedule_view())
            elif "board_id" in params and path.endswith("/digest") and method == "GET":
                since = float(self._query().get("since", ["0"])[0])
                self._send_json(200, board_digest(store, params["board_id"], since))
            elif "name" in params:
                self._handle_asset(params["name"])
            else:
                self._send_json(404, {"error": f"no route for {method} {path}"})

        def _handle_timeline(self, card_id: str) -> None:
            """run replay: GET /api/cards/{id}/timeline?attempt=N - see smortboard/timeline.py"""
            store.get_card(card_id)  # raises NotFoundError on a bad id
            raw_attempt = self._query().get("attempt", [None])[0]
            attempt = int(raw_attempt) if raw_attempt else None
            self._send_json(200, card_timeline(store, card_id, attempt=attempt))

        def _query(self) -> dict[str, list[str]]:
            return parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}

        def _handle_run(self, card_id: str) -> None:
            """starts a card, or reports the run already going for it.

            202, not 200: the card has been accepted and is running somewhere else. The response
            is where it is right now, not where it ended up - GET the same path for that.
            """
            card = store.get_card(card_id)  # raises NotFoundError before a thread is ever started
            own = runs.get(card_id)
            if own is None or not own.running:
                conflict = conflicting_run(store, card, [s.card_id for s in runs.active()])
                if conflict:
                    self._send_json(409, {"error": f"not started: {conflict}"})
                    return
            state = runs.start(card_id)
            self._send_json(202, state.as_dict())

        def _handle_stop(self, card_id: str) -> None:
            """stops a running card. 409 if there is nothing running to stop."""
            store.get_card(card_id)  # raises NotFoundError for an unknown card
            try:
                state = runs.stop(card_id)
            except RunNotActiveError as exc:
                self._send_json(409, {"error": str(exc)})
                return
            self._send_json(200, state.as_dict())

        def _handle_decision(self, card_id: str, decide) -> None:
            """accept or reject. 409 while a run holds the card: deciding under a live agent
            would destroy the worktree it is writing to"""
            state = runs.get(card_id)
            if state is not None and state.running:
                self._send_json(409, {"error": "this card is still running"})
                return
            try:
                card = decide(store, card_id)
            except DecisionRefused as exc:
                self._send_json(409, {"error": str(exc)})
                return
            self._send_json(200, card)

        def _handle_answer(self, card_id: str) -> None:
            """the attention inbox's reply: stores it as fabian's comment and resumes the card.

            404 for an unknown card (get_card inside answer_card raises), 409 for a card already
            running or blocked on something an answer cannot fix - see attention.answer_card.
            """
            message = (self._read_json().get("message") or "").strip()
            if not message:
                self._send_json(400, {"error": "message must not be empty"})
                return
            try:
                state = answer_card(store, runs, card_id, message)
            except AnswerRefused as exc:
                self._send_json(409, {"error": str(exc)})
                return
            self._send_json(202, state)

        def _handle_patch_card(self, card_id: str) -> None:
            body = self._read_json()
            # the store's own list - a second copy here went stale and refused `model` with a 400
            unknown = set(body) - CARD_WRITABLE_FIELDS - {"leases"}
            if unknown:
                self._send_json(400, {"error": f"not writable: {sorted(unknown)}"})
                return
            leases = body.pop("leases", None)
            if leases is not None:
                if not isinstance(leases, list):
                    self._send_json(400, {"error": "leases must be a list of globs"})
                    return
                store.set_leases(card_id, leases)
            card = store.update_card(card_id, **body) if body else store.get_card(card_id)
            self._send_json(200, card)

        def _handle_create_repo(self, board_id: str) -> None:
            """registers a repo on a board - the only way to make a card runnable.

            validated here, not in the store: path exists, is a git repo, and default_branch
            exists in it - a message a person can act on, rather than a runner failing minutes
            later inside a container.
            """
            store.get_board(board_id)  # raises NotFoundError on a bad board id
            body = self._read_json()
            name = body.get("name", "")
            default_branch = body.get("default_branch") or "main"
            try:
                expanded_path = validate_repo(name, body.get("path", ""), default_branch)
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            repo = store.create_repo(
                board_id,
                name=name,
                path=expanded_path,
                default_branch=default_branch,
                test_command=body.get("test_command"),
                image=body.get("image"),
                lint_command=body.get("lint_command"),
            )
            self._send_json(201, repo)

        def _handle_patch_repo(self, repo_id: str) -> None:
            # only these two are worth editing after registration - path/branch changes mean
            # re-registering, since they are what validate_repo checked at creation
            body = self._read_json()
            unknown = set(body) - {"test_command", "image"}
            if unknown:
                self._send_json(400, {"error": f"not writable: {sorted(unknown)}"})
                return
            repo = store.get_repo(repo_id)
            if "test_command" in body:
                repo = store.set_repo_test_command(repo_id, body["test_command"])
            if "image" in body:
                repo = store.set_repo_image(repo_id, body["image"])
            self._send_json(200, repo)

        def _handle_patch_task(self, task_id: str) -> None:
            # only "done" is exposed here - add_task/remove_task stay store-only, for a human
            body = self._read_json()
            unknown = set(body) - {"done"}
            if unknown:
                self._send_json(400, {"error": f"not writable: {sorted(unknown)}"})
                return
            task = store.set_task_done(task_id, done=bool(body.get("done", True)))
            self._send_json(200, task)

        def _orchestrator_view(self, board_id: str) -> dict:
            store.get_board(board_id)  # 404 for an unknown board rather than an empty session
            model = store.get_settings().get("orchestrator_model") or DEFAULT_ORCHESTRATOR_MODEL
            return {
                "messages": [
                    {k: m[k] for k in ("id", "author", "body", "created_at", "cards")}
                    for m in store.list_orchestrator_messages(board_id)
                ],
                "plan": store.get_plan(board_id),
                "thinking": orchestrator.thinking(board_id),
                "error": orchestrator.error(board_id),
                "model": model,
            }

        def _handle_orchestrator_post(self, board_id: str) -> None:
            store.get_board(board_id)
            message = (self._read_json().get("message") or "").strip()
            if not message:
                self._send_json(400, {"error": "message must not be empty"})
                return
            if orchestrator.thinking(board_id):
                self._send_json(409, {"error": "a turn is already in progress on this board"})
                return
            # stored here, synchronously, so the 202 body already carries it - the thread that
            # runs the turn is told not to store it again
            store.add_orchestrator_message(board_id, "fabian", message)
            orchestrator.start(board_id, message, message_already_stored=True)
            self._send_json(202, self._orchestrator_view(board_id))

        def _conversation_view(self, card_id: str) -> dict:
            card = store.get_card(card_id)
            run_state = runs.get(card_id)
            running = bool(run_state and run_state.running)
            phase = run_state.phase if run_state else None

            timeline: list[tuple[str, dict]] = []
            for event in store.list_events(card_id):
                kind, payload = event["kind"], event["payload"]
                if kind == "assistant":
                    for block in (payload.get("message", {}) or {}).get("content") or []:
                        if (
                            isinstance(block, dict)
                            and block.get("type") == "text"
                            and block.get("text")
                        ):
                            timeline.append(
                                (event["created_at"], {"author": "agent", "body": block["text"]})
                            )
                elif kind == "lifecycle_started":
                    timeline.append((event["created_at"], self._board_line("run started")))
                elif kind == "test_gate":
                    verdict = "tests passed" if payload.get("passed") else "tests failed"
                    timeline.append((event["created_at"], self._board_line(verdict)))
                elif kind == "review_gate":
                    if payload.get("approved"):
                        verdict = "reviewer approved"
                    else:
                        verdict = f"reviewer did not approve: {len(payload.get('findings') or [])} findings"
                    timeline.append((event["created_at"], self._board_line(verdict)))
                elif kind == "merge_request" and payload.get("url"):
                    timeline.append(
                        (
                            event["created_at"],
                            self._board_line(f"pull request opened: {payload['url']}"),
                        )
                    )
                elif kind == "decision":
                    timeline.append(
                        (event["created_at"], self._board_line(payload.get("decision", "")))
                    )
                elif kind == "note_delivered":
                    count = len(payload.get("comment_ids") or [])
                    plural = "s" if count != 1 else ""
                    timeline.append(
                        (
                            event["created_at"],
                            self._board_line(
                                f"delivered {count} note{plural} to the running agent"
                            ),
                        )
                    )

            for comment in store.list_comments(card_id):
                author = "fabian" if comment["author"] == "fabian" else "board"
                timeline.append(
                    (comment["created_at"], {"author": author, "body": comment["body"]})
                )

            timeline.sort(key=lambda entry: entry[0])
            messages = []
            for created_at, message in timeline:
                messages.append({**message, "created_at": created_at})

            return {
                "card_id": card["id"],
                "title": card["title"],
                "running": running,
                "phase": phase,
                # what a note sent RIGHT NOW would get: "live" while this card's run is going and
                # accepting stdin, "next_run" otherwise. matches the per-comment answer POST gives
                "delivery": "live" if running else "next_run",
                "messages": messages,
            }

        @staticmethod
        def _board_line(text: str) -> dict:
            return {"author": "board", "body": text}

        def _handle_conversation_post(self, card_id: str) -> None:
            store.get_card(card_id)
            message = (self._read_json().get("message") or "").strip()
            if not message:
                self._send_json(400, {"error": "message must not be empty"})
                return
            comment = store.add_comment(card_id, author="fabian", body=message)
            # queue AFTER the comment is durable: a live delivery that then crashed before the
            # comment was ever saved would leave nothing for the card's next run to fall back on
            delivered_live = runs.queue_note(card_id, comment)
            self._send_json(
                201,
                {
                    "author": "fabian",
                    "body": comment["body"],
                    "created_at": comment["created_at"],
                    "delivery": "live" if delivered_live else "next_run",
                },
            )

        def _prompts_view(self) -> list[dict]:
            rows = []
            for role in ROLES:
                stored = store.get_prompt(role)
                if stored is None:
                    rows.append(
                        {
                            "role": role,
                            "version": 0,
                            "body": _ROLE_DEFAULTS[role],
                            "is_default": True,
                        }
                    )
                else:
                    rows.append(
                        {
                            "role": role,
                            "version": stored["version"],
                            "body": stored["body"],
                            "is_default": False,
                        }
                    )
            return rows

        def _handle_patch_prompt(self, role: str) -> None:
            if role not in ROLES:
                self._send_json(404, {"error": f"no such role {role!r}"})
                return
            body = self._read_json().get("body")
            if not isinstance(body, str) or not body.strip():
                self._send_json(400, {"error": "body must not be empty"})
                return
            stored = store.set_prompt(role, body)
            self._send_json(
                200,
                {
                    "role": role,
                    "version": stored["version"],
                    "body": stored["body"],
                    "is_default": False,
                },
            )

        def _handle_upload(self, card_id: str) -> None:
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self._send_json(400, {"error": "expected multipart/form-data"})
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            boundary = parse_boundary(content_type)
            uploaded = parse_first_file(body, boundary)
            attachment = store.add_attachment(
                card_id,
                filename=uploaded.filename,
                media_type=uploaded.content_type,
                data=uploaded.data,
            )
            self._send_json(201, attachment)

        def _handle_get_attachment(self, card_id: str, attachment_id: str) -> None:
            meta = store.get_attachment_meta(attachment_id)
            if meta["card_id"] != card_id:
                self._send_json(404, {"error": f"no attachment {attachment_id} on card {card_id}"})
                return
            blob = store.get_attachment_blob(attachment_id)
            self.send_response(200)
            self.send_header("Content-Type", meta["media_type"])
            self.send_header("Content-Length", str(len(blob)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(blob)

        def _handle_asset(self, name: str) -> None:
            try:
                data = resolve_asset(name)
            except AssetNotFound:
                self._send_json(404, {"error": f"no asset {name!r}"})
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type_for(name))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            return json.loads(self.rfile.read(length))

        def _send_json(self, status: int, payload: object) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_status(self, status: int) -> None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

    return Handler


def build_server(
    store: Store,
    port: int,
    host: str = "127.0.0.1",
    token_path: str | None = None,
) -> HTTPServer:
    # a new board has no runs, so any card still mid-run lost the last board process under it
    recovered = recover_orphaned_runs(store)
    # single-threaded: the store's sqlite3 connection is bound to the thread that opened it. card
    # runs are the exception and get their own thread and their own connection - see runs.py
    runs = RunRegistry(store.path, token_path=token_path)
    orchestrator = OrchestratorRegistry(store.path, token_path=token_path)
    scheduler = SchedulerRegistry(store.path, runs)
    handler_cls = _make_handler(
        store, runs, Readiness(token_path), orchestrator, scheduler, token_path=token_path
    )
    server = HTTPServer((host, port), handler_cls)
    server.runs = runs  # the cli and the tests reach the registry through the server
    server.recovered = recovered
    server.orchestrator = orchestrator
    server.scheduler = scheduler
    return server
