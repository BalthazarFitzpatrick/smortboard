"""the http api: routes json endpoints to the store, and assets per smortboard/server/assets.py"""

import json
import re
import sqlite3
import threading
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote

from smortboard import profiles
from smortboard.attention import (
    AnswerRefused,
    answer_card,
    approve_lease,
    attention_rows,
    fallback_run,
    with_actions,
)
from smortboard.budgets import spend_refusal
from smortboard.consolidate import FoldRegistry
from smortboard.digest import board_digest
from smortboard.exec.runner import SYSTEM_PROMPT
from smortboard.labs.catalog import ROLES as MODEL_ROLES
from smortboard.labs.catalog import load_catalog
from smortboard.local_repos import detect_default_branch, list_folders
from smortboard.operator import AUTHOR_KEY, OPERATOR_NAME
from smortboard.orchestrator import (
    DEFAULT_ORCHESTRATOR_MODEL,
    ORCHESTRATOR_PROMPT,
    OrchestratorRegistry,
    card_text_warnings,
)
from smortboard.preflight import run_preflight
from smortboard.prompts import ROLES
from smortboard.pulls import open_pull_requests
from smortboard.repo_image import build_repo_image
from smortboard.review.decide import DecisionRefused, accept_card, reject_card
from smortboard.review.landing import DEFAULT_TTL_S as DEFAULT_LANDING_TTL_S
from smortboard.review.landing import resolve_repo_key
from smortboard.review.outcome import card_outcome
from smortboard.review.reviewer import REVIEW_PROMPT_HEADER
from smortboard.scheduler import (
    SchedulerRegistry,
    SchedulerTicker,
    conflicting_run,
    relabel_stale_crashes,
)
from smortboard.server import access
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
    cost_optimisation,
    roster_rows,
    usage_projection,
)
from smortboard.timeline import card_timeline

_ROUTES = [
    (re.compile(r"^/health$"), "GET"),
    (re.compile(r"^/api/boards$"), "GET"),
    (re.compile(r"^/api/boards$"), "POST"),
    (re.compile(r"^/api/boards/from-repo$"), "POST"),
    (re.compile(r"^/api/folders$"), "GET"),
    (re.compile(r"^/api/boards/(?P<board_id>[^/]+)/cards$"), "GET"),
    (re.compile(r"^/api/boards/(?P<board_id>[^/]+)/repos$"), "GET"),
    (re.compile(r"^/api/boards/(?P<board_id>[^/]+)/repos$"), "POST"),
    (re.compile(r"^/api/repos/(?P<repo_id>[^/]+)$"), "PATCH"),
    (re.compile(r"^/api/repos/(?P<repo_id>[^/]+)/image/build$"), "POST"),
    (re.compile(r"^/api/repos/(?P<repo_id>[^/]+)/image/build$"), "GET"),
    (re.compile(r"^/api/cards$"), "POST"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)$"), "GET"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)$"), "PATCH"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)$"), "DELETE"),
    (re.compile(r"^/api/boards/(?P<board_id>[^/]+)$"), "DELETE"),
    (re.compile(r"^/api/boards/(?P<board_id>[^/]+)$"), "PATCH"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/comments$"), "POST"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/attachments$"), "POST"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/attachments/(?P<attachment_id>[^/]+)$"), "GET"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/events$"), "GET"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/outcome$"), "GET"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/run$"), "POST"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/fallback-run$"), "POST"),
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
    (re.compile(r"^/api/boards/(?P<board_id>[^/]+)/fold$"), "GET"),
    (re.compile(r"^/api/boards/(?P<board_id>[^/]+)/fold$"), "POST"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/conversation$"), "GET"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/conversation$"), "POST"),
    (re.compile(r"^/api/roster$"), "GET"),
    (re.compile(r"^/api/usage$"), "GET"),
    (re.compile(r"^/api/costs$"), "GET"),
    (re.compile(r"^/api/costs/optimisation$"), "GET"),
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
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/lease/approve$"), "POST"),
    (re.compile(r"^/api/repos/(?P<repo_id>[^/]+)/lease/(?P<lease_id>[^/]+)$"), "DELETE"),
    (re.compile(r"^/api/profiles$"), "GET"),
    (re.compile(r"^/api/catalog$"), "GET"),
    (re.compile(r"^/api/profiles$"), "POST"),
    (re.compile(r"^/api/profiles/(?P<lab>[^/]+)/(?P<profile_name>[^/]+)/activate$"), "POST"),
    (re.compile(r"^/api/profiles/(?P<lab>[^/]+)/(?P<profile_name>[^/]+)$"), "DELETE"),
    (re.compile(r"^/api/profiles/(?P<profile_name>[^/]+)/activate$"), "POST"),
    (re.compile(r"^/api/profiles/(?P<profile_name>[^/]+)$"), "DELETE"),
    (re.compile(r"^/api/pulls$"), "GET"),
    (re.compile(r"^/api/repos/(?P<repo_id>[^/]+)/landing$"), "POST"),
    (re.compile(r"^/api/landing/(?P<lease_id>[^/]+)$"), "DELETE"),
    (re.compile(r"^/api/landing$"), "GET"),
]

# a full claude setup-token is 108 bytes (see README Setup); this is a shape check, not a network
# call - short enough to catch an empty paste, generous enough to never reject a real token
_MIN_TOKEN_LENGTH = 80
_MAX_TOKEN_LENGTH = 4096


def _token_shape_problem(token: str) -> str | None:
    if not token or len(token) < _MIN_TOKEN_LENGTH or len(token) > _MAX_TOKEN_LENGTH:
        return "that doesn't look like a claude setup-token - check the length and try again."
    if any(ch.isspace() for ch in token.strip()):
        return "a token is a single line - remove any internal spaces or line breaks."
    return None


_ROLE_DEFAULTS = {
    "orchestrator": ORCHESTRATOR_PROMPT,
    "worker": SYSTEM_PROMPT,
    "reviewer": REVIEW_PROMPT_HEADER,
}


# scripts and styles only from the board's own files: a markup sink an agent reaches stays inert
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
_MEDIA_TYPE = re.compile(r"^[\w.+-]+/[\w.+-]+$")


# one thread serves every request, so a slow client or a huge body must not hold it
_REQUEST_TIMEOUT_S = 30
_MAX_JSON_BYTES = 1_000_000
_MAX_UPLOAD_BYTES = 25_000_000


class NotJsonError(ValueError):
    """a json route was sent a body that does not declare itself json"""


class BodyTooLargeError(ValueError):
    """a request body past its route's cap, refused before it is read"""


def _version() -> str:
    try:
        return version("smortboard")
    except PackageNotFoundError:
        return "0.0.0-dev"


def _text_warnings(card: dict[str, Any]) -> list[str]:
    """card text past the card text rules, told to the caller - the card is kept exactly as sent"""
    criteria = [criterion["text"] for criterion in card["criteria"]]
    return card_text_warnings({**card, "criteria": criteria})


def _make_handler(
    store: Store,
    runs: RunRegistry,
    readiness: Readiness,
    orchestrator: OrchestratorRegistry,
    scheduler: SchedulerRegistry,
    token_path: str | None = None,
    api_key: str | None = None,
    bound_host: str = "127.0.0.1",
) -> type[BaseHTTPRequestHandler]:
    """closes over the store instance; http.server wants a class, not an instance"""

    allowed_hosts = access.allowed_hostnames(bound_host)
    # image builds run off the request thread; repo_id -> {"state", "log", ...}, newest per repo
    image_builds: dict[str, dict] = {}
    image_builds_lock = threading.Lock()

    # the message ids each board's mission control already accepted, newest last - a retried or
    # second-tab send of the same message is answered as done instead of starting another turn
    accepted_messages: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=500))
    folds = FoldRegistry(store.path, token_path=token_path)
    from smortboard.server.landings import LandingRegistry, needs_landing

    landings = LandingRegistry(store.path)

    class Handler(BaseHTTPRequestHandler):
        server_version = "smortboard/0.1"
        timeout = _REQUEST_TIMEOUT_S

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
            refusal = self._refusal(method, path)
            if refusal:
                self._send_json(*refusal)
                return
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
            except NotJsonError as exc:
                self._send_json(415, {"error": str(exc)})
            except BodyTooLargeError as exc:
                self._send_json(413, {"error": str(exc)})
            except (KeyError, TypeError, ValueError) as exc:
                # malformed json body or a request missing a required field
                self._send_json(400, {"error": str(exc)})

        def _refusal(self, method: str, path: str) -> tuple[int, dict] | None:
            """the status and body to refuse this request with, or None to let it through"""
            headers = self.headers
            if not access.host_ok(headers.get("Host"), allowed_hosts):
                return 403, {"error": "unexpected Host header"}
            origin, fetch_site = headers.get("Origin"), headers.get("Sec-Fetch-Site")
            if not access.origin_ok(method, origin, fetch_site, allowed_hosts):
                return 403, {"error": "cross-origin request refused"}
            if api_key is None or not path.startswith("/api/"):
                return None
            presented = headers.get(access.KEY_HEADER) or access.cookie_value(
                headers.get("Cookie"), self._cookie_name()
            )
            if not access.key_ok(presented, api_key):
                return 401, {"error": "missing api key - open the link smortboard printed on start"}
            return None

        def _cookie_name(self) -> str:
            return access.cookie_name(self.server.server_address[1])

        def _handle_key_exchange(self) -> bool:
            """swaps ?key= on the board page for a cookie and a clean url, so the key leaves the
            address bar and history; True when a redirect was sent"""
            presented = self._query().get(access.KEY_QUERY, [None])[0]
            if api_key is None or not access.key_ok(presented, api_key):
                return False
            self.send_response(303)
            self.send_header(
                "Set-Cookie", f"{self._cookie_name()}={api_key}; Path=/; HttpOnly; SameSite=Strict"
            )
            self.send_header("Location", "/ui/index.html")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True

        def _handle(self, method: str, path: str, **params: str) -> None:
            card_id = params.get("card_id")
            if card_id and (
                method in ("PATCH", "DELETE")
                or path.endswith(("/run", "/fallback-run", "/accept", "/reject", "/answer"))
            ):
                landing = landings.get(card_id)
                if landing is not None and landing.running:
                    self._send_json(409, {"error": "this card is landing"})
                    return
            if method == "DELETE" and "board_id" in params:
                for card in store.list_cards(params["board_id"]):
                    landing = landings.get(card["id"])
                    if landing is not None and landing.running:
                        self._send_json(409, {"error": "a card on this board is landing"})
                        return
            if path == "/health":
                self._send_json(200, {"ok": True, "version": _version(), "operator": OPERATOR_NAME})
            elif path == "/api/boards" and method == "GET":
                self._send_json(200, store.list_boards())
            elif path == "/api/boards" and method == "POST":
                body = self._read_json()
                board = store.create_board(name=body["name"])
                self._send_json(201, board)
            elif path == "/api/boards/from-repo" and method == "POST":
                self._handle_board_from_repo()
            elif path == "/api/folders":
                query = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                self._send_json(200, list_folders(query.get("under", [None])[0]))
            elif "board_id" in params and path.endswith("/cards"):
                cards = store.list_cards(params["board_id"])
                self._send_json(200, with_actions(store, cards, scheduler))
            elif "board_id" in params and path.endswith("/repos") and method == "GET":
                self._send_json(200, store.list_repos(params["board_id"]))
            elif "board_id" in params and path.endswith("/repos") and method == "POST":
                self._handle_create_repo(params["board_id"])
            elif "repo_id" in params and method == "PATCH":
                self._handle_patch_repo(params["repo_id"])
            elif "repo_id" in params and path.endswith("/image/build") and method == "POST":
                self._handle_build_repo_image(params["repo_id"])
            elif "repo_id" in params and path.endswith("/image/build") and method == "GET":
                store.get_repo(params["repo_id"])  # raises NotFoundError on a bad id
                with image_builds_lock:
                    build = image_builds.get(params["repo_id"], {"state": "none"})
                self._send_json(200, build)
            elif "lease_id" in params and path.startswith("/api/landing/") and method == "DELETE":
                self._send_json(200, store.release_landing(params["lease_id"]))
            elif "lease_id" in params and method == "DELETE":
                self._handle_forget_lease(params["repo_id"], params["lease_id"])
            elif "repo_id" in params and path.endswith("/landing") and method == "POST":
                self._handle_landing_request(params["repo_id"])
            elif path == "/api/landing" and method == "GET":
                self._send_json(200, store.list_landing())
            elif path == "/api/cards" and method == "POST":
                body = self._read_json()
                card = store.create_card(**body)
                self._send_json(201, {**card, "warnings": _text_warnings(card)})
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
            elif path.endswith("/fallback-run") and method == "POST":
                self._handle_fallback_run(params["card_id"])
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
                store.set_settings(self._read_json())
                self._send_json(200, store.get_settings())
            elif path.endswith("/events"):
                self._send_json(200, store.list_events(params["card_id"]))
            elif "card_id" in params and path.endswith("/timeline"):
                self._handle_timeline(params["card_id"])
            elif "board_id" in params and path.endswith("/orchestrator") and method == "GET":
                self._send_json(200, self._orchestrator_view(params["board_id"]))
            elif "board_id" in params and path.endswith("/orchestrator") and method == "POST":
                self._handle_orchestrator_post(params["board_id"])
            elif "board_id" in params and path.endswith("/fold") and method == "GET":
                store.get_board(params["board_id"])
                self._send_json(200, self._fold_view(params["board_id"]))
            elif "board_id" in params and path.endswith("/fold") and method == "POST":
                self._handle_fold_post(params["board_id"])
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
            elif path == "/api/costs/optimisation":
                board_id = self._query().get("board", [None])[0]
                if board_id:
                    store.get_board(board_id)  # a 404 for an unknown board
                self._send_json(200, cost_optimisation(store, board_id))
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
                self._send_json(200, attention_rows(store, scheduler))
            elif "card_id" in params and path.endswith("/lease/approve"):
                self._handle_lease_approve(params["card_id"])
            elif path == "/api/pulls":
                self._send_json(200, open_pull_requests(store))
            elif "card_id" in params and path.endswith("/answer"):
                self._handle_answer(params["card_id"])
            elif path == "/api/profiles" and method == "GET":
                self._send_json(200, self._profiles_view())
            elif path == "/api/catalog" and method == "GET":
                self._send_json(200, self._catalog_view())
            elif path == "/api/profiles" and method == "POST":
                self._handle_add_profile()
            elif "profile_name" in params and path.endswith("/activate") and method == "POST":
                self._handle_activate_profile(
                    params["profile_name"], params.get("lab", "anthropic")
                )
            elif "profile_name" in params and method == "DELETE":
                self._handle_remove_profile(params["profile_name"], params.get("lab"))
            elif "card_id" in params and method == "GET":
                # the board list's enrichment too, so the open card says what to do next
                card = store.get_card(params["card_id"])
                self._send_json(200, with_actions(store, [card], scheduler)[0])
            elif "card_id" in params and method == "PATCH":
                self._handle_patch_card(params["card_id"])
            elif "card_id" in params and method == "DELETE":
                store.delete_card(params["card_id"])
                self._send_status(204)
            elif "board_id" in params and method == "DELETE":
                store.delete_board(params["board_id"])
                self._send_status(204)
            elif "board_id" in params and method == "PATCH":
                body = self._read_json()
                # only this board's own parallel cap and daily budget are writable here; "unset"
                # arrives as an explicit null, same convention PATCH /api/settings uses for
                # clearing a value
                if "merge_mode" in body:
                    store.set_board_merge_mode(params["board_id"], body["merge_mode"])
                if "lease_mode" in body:
                    store.set_board_lease_mode(params["board_id"], body["lease_mode"])
                if "max_parallel" in body:
                    store.set_board_max_parallel(params["board_id"], body["max_parallel"])
                if "daily_budget_usd" in body:
                    store.set_board_daily_budget(params["board_id"], body["daily_budget_usd"])
                self._send_json(200, store.get_board(params["board_id"]))
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
                if params["name"] == "index.html" and self._handle_key_exchange():
                    return
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
                refusal = spend_refusal(store, card)
                if refusal:
                    self._send_json(409, {"error": refusal})
                    return
            state = runs.start(card_id)
            self._send_json(202, state.as_dict())

        def _handle_fallback_run(self, card_id: str) -> None:
            """the inbox's retry on a usage-limited card: its limited role runs once on the next
            usable fallback model. 202 once started, 404 for an unknown card, 409 when it runs,
            is not usage limited, has no usable fallback, or a lease or spend rule refuses it"""
            try:
                state = fallback_run(store, runs, card_id, scheduler)
            except AnswerRefused as exc:
                self._send_json(409, {"error": str(exc)})
                return
            self._send_json(202, state)

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
            landing = landings.get(card_id)
            if landing is not None and landing.running:
                self._send_json(409, {"error": "this card is already landing"})
                return
            try:
                candidate = store.get_card(card_id)
                if decide is accept_card and needs_landing(store, candidate):
                    if candidate["blocked_reason_code"]:
                        raise DecisionRefused("this card is blocked")
                    landings.start(card_id)
                    self._send_json(202, {"state": "landing"})
                    return
                card = decide(store, card_id)
            except DecisionRefused as exc:
                self._send_json(409, {"error": str(exc)})
                return
            self._send_json(200, card)

        def _handle_answer(self, card_id: str) -> None:
            """the attention inbox's reply: stores it as the operator's comment and resumes the card.

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

        def _handle_lease_approve(self, card_id: str) -> None:
            """the inbox's one-click reply to a LEASE_CONFLICT: widen the lease by exactly the
            refused paths and resume. 400 for an empty/invalid path list or a bad glob, 404 for an
            unknown card, 409 when the card is not blocked on LEASE_CONFLICT or the resume itself
            is refused (the lease stays widened either way - see attention.approve_lease).

            `remember: true` also adds the same paths to the repo's remembered globs, so a later
            card on this repo is never asked again - always the operator's explicit tick, never
            implied by a plain approve.
            """
            body = self._read_json()
            paths = body.get("paths")
            remember = bool(body.get("remember"))
            try:
                state = approve_lease(store, runs, card_id, paths)
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            except AnswerRefused as exc:
                self._send_json(409, {"error": str(exc)})
                return
            if remember:
                repo_id = store.get_card(card_id)["repo_id"]
                if repo_id:
                    store.remember_lease_paths(repo_id, paths)
            self._send_json(202, state)

        def _handle_forget_lease(self, repo_id: str, lease_id: str) -> None:
            """the boards panel's remove on one remembered glob - see Store.forget_lease_path"""
            self._send_json(200, store.forget_lease_path(repo_id, lease_id))

        def _handle_landing_request(self, repo_id: str) -> None:
            """acquires (or queues for) the push lock on this repo's target branch. calling again
            with the lease_id it returned is the heartbeat/poll - see Store.request_landing.
            repo_id is looked up as a board repo id first, then as a path - see
            smortboard.review.landing.resolve_repo_key, so an outside tool needs no board repo
            registered to lock the same key the board itself would"""
            body = self._read_json()
            holder = (body.get("holder") or "").strip()
            branch = (body.get("branch") or "").strip()
            if not holder or not branch:
                self._send_json(400, {"error": "holder and branch must not be empty"})
                return
            repo_key = resolve_repo_key(unquote(repo_id), store)
            result = store.request_landing(
                repo_key,
                holder,
                branch,
                target=body.get("target") or "development",
                ttl_s=int(body.get("ttl_s") or DEFAULT_LANDING_TTL_S),
                lease_id=body.get("lease_id"),
            )
            self._send_json(200 if result["granted"] else 202, result)

        def _profiles_view(self) -> list[dict]:
            """credential metadata across labs, never secret values or filesystem paths"""
            return [
                {
                    "name": row["name"],
                    "lab": row["lab"],
                    "kind": row["kind"],
                    "active": row["active"],
                    "present": row["present"],
                    "limited_until": row["limited_until"],
                }
                for row in profiles.list_all_profiles()
            ]

        def _catalog_view(self) -> dict:
            catalog = load_catalog()
            rows = profiles.list_all_profiles()
            for lab, entry in catalog.items():
                usable = False
                for row in rows:
                    if row["lab"] != lab or not row["present"] or not row["mode_ok"]:
                        continue
                    try:
                        profiles.read_profile_token(lab, row["name"])
                    except profiles.ProfileError:
                        continue
                    usable = True
                    break
                entry["available"] = usable
                entry["unavailable_reason"] = None if usable else f"no usable {lab} profile"
            return catalog

        def _handle_add_profile(self) -> None:
            """pastes a token straight into its mode-600 file - checked for shape, never echoed"""
            body = self._read_json()
            name = (body.get("name") or "").strip()
            lab = body.get("lab") or "anthropic"
            kind = body.get("kind")
            token = body.get("token") or ""
            problem = (
                _token_shape_problem(token)
                if lab == "anthropic"
                else (
                    "token must be non-empty and contain no whitespace"
                    if not isinstance(token, str)
                    or not token.strip()
                    or (kind != "auth_json" and any(char.isspace() for char in token.strip()))
                    else None
                )
            )
            if problem:
                self._send_json(400, {"error": problem})
                return
            try:
                profiles.add_profile(name, token, lab=lab, kind=kind)
            except (profiles.ProfileError, OSError) as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(201, self._one_profile_view(name, lab))

        def _one_profile_view(self, name: str, lab: str = "anthropic") -> dict:
            for row in self._profiles_view():
                if row["name"] == name and row["lab"] == lab:
                    return row
            raise profiles.ProfileError(f"no such profile '{name}'")  # pragma: no cover - defensive

        def _handle_activate_profile(self, name: str, lab: str = "anthropic") -> None:
            try:
                profiles.set_active(name, lab=lab)
            except profiles.ProfileError as exc:
                self._send_json(404, {"error": str(exc)})
                return
            self._send_json(200, self._one_profile_view(name, lab))

        def _handle_remove_profile(self, name: str, lab: str | None = None) -> None:
            scoped = lab is not None
            lab = lab or "anthropic"
            settings = store.get_settings()
            references = []
            role_labs = {
                settings.get(f"{role}_lab") or "anthropic"
                for role in MODEL_ROLES
                if role != "worker"
            }
            for board in store.list_boards():
                for card in store.list_cards(board["id"]):
                    worker_lab = (
                        (card.get("lab") or "anthropic")
                        if card.get("model")
                        else settings.get("worker_lab") or "anthropic"
                    )
                    if worker_lab == lab or lab in role_labs:
                        references.append(card)
            try:
                profiles.remove_profile(
                    name, lab=lab, referenced_cards=references if scoped or references else None
                )
            except profiles.ProfileError as exc:
                message = str(exc)
                status = 404 if "no such" in message else 409
                self._send_json(status, {"error": message})
                return
            self._send_status(204)

        def _handle_patch_card(self, card_id: str) -> None:
            body = self._read_json()
            # the store's own list - a second copy here went stale and refused `model` with a 400
            unknown = set(body) - CARD_WRITABLE_FIELDS - {"leases", "depends_on"}
            if unknown:
                self._send_json(400, {"error": f"not writable: {sorted(unknown)}"})
                return
            # shapes are checked before anything is written, so a bad depends_on
            # can't leave a lease change (or vice versa) committed behind a 400
            leases = body.pop("leases", None)
            if leases is not None and not isinstance(leases, list):
                self._send_json(400, {"error": "leases must be a list of globs"})
                return
            depends_on = body.pop("depends_on", None)
            if depends_on is not None and not isinstance(depends_on, list):
                self._send_json(400, {"error": "depends_on must be a list of card ids"})
                return
            if leases is not None:
                store.set_leases(card_id, leases)
            if depends_on is not None:
                store.set_dependencies(card_id, depends_on)
            card = store.update_card(card_id, **body) if body else store.get_card(card_id)
            # only a text edit is checked, so moving an old long card stays quiet
            warnings = _text_warnings(card) if {"title", "description"} & set(body) else []
            self._send_json(200, {**card, "warnings": warnings})

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

        def _handle_board_from_repo(self) -> None:
            """a board named after a local repo, with that repo registered on it - the boards
            panel's "from local repo" button. every check runs before anything is written, so a
            folder that is not a usable repo leaves no empty board behind
            """
            body = self._read_json()
            path = Path(body.get("path", "")).expanduser()
            if not path.is_dir():
                self._send_json(400, {"error": f"not a folder: {path}"})
                return
            try:
                branch = detect_default_branch(str(path))
                expanded_path = validate_repo(path.name, str(path), branch)
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            board = store.create_board(name=path.name)
            repo = store.create_repo(
                board["id"], name=path.name, path=expanded_path, default_branch=branch
            )
            self._send_json(201, {"board": board, "repo": repo})

        def _handle_patch_repo(self, repo_id: str) -> None:
            # path still means re-registering. default_branch is editable because a base branch
            # can merge into main, and it goes through the same validate_repo check as creation
            body = self._read_json()
            unknown = set(body) - {"test_command", "image", "default_branch"}
            if unknown:
                self._send_json(400, {"error": f"not writable: {sorted(unknown)}"})
                return
            repo = store.get_repo(repo_id)
            if "default_branch" in body:
                # checked before any write, so a bad branch leaves the whole row as it was
                try:
                    validate_repo(repo["name"], repo["path"], body["default_branch"] or "")
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                repo = store.set_repo_default_branch(repo_id, body["default_branch"])
            if "test_command" in body:
                repo = store.set_repo_test_command(repo_id, body["test_command"])
            if "image" in body:
                repo = store.set_repo_image(repo_id, body["image"])
            self._send_json(200, repo)

        def _handle_build_repo_image(self, repo_id: str) -> None:
            """starts building this repo's own test image in the background; GET on the same
            route reports it. 404 for an unknown repo, 409 while a build for it is running, 202
            once started. a finished build sets the repo's image, a failed one leaves its log tail.
            """
            repo = store.get_repo(repo_id)
            with image_builds_lock:
                if image_builds.get(repo_id, {}).get("state") == "building":
                    self._send_json(409, {"error": "an image build for this repo is running"})
                    return
                image_builds[repo_id] = {"state": "building"}
            threading.Thread(
                target=_build_image_in_background,
                args=(store.path, repo, image_builds, image_builds_lock),
                daemon=True,
            ).start()
            self._send_json(202, {"state": "building"})

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
            body = self._read_json()
            message = (body.get("message") or "").strip()
            client_id = body.get("client_id")
            mode = body.get("mode") if body.get("mode") in ("planning", "manage") else "planning"
            if not message:
                self._send_json(400, {"error": "message must not be empty"})
                return
            # checked before the turn-in-progress refusal, so a duplicate is told it is done
            # rather than being kept in the sender's queue to try again
            if client_id and client_id in accepted_messages[board_id]:
                self._send_json(200, self._orchestrator_view(board_id))
                return
            if orchestrator.thinking(board_id):
                self._send_json(409, {"error": "a turn is already in progress on this board"})
                return
            # stored here, synchronously, so the 202 body already carries it - the thread that
            # runs the turn is told not to store it again
            store.add_orchestrator_message(board_id, AUTHOR_KEY, message)
            if client_id:
                accepted_messages[board_id].append(client_id)
            orchestrator.start(board_id, message, message_already_stored=True, mode=mode)
            self._send_json(202, self._orchestrator_view(board_id))

        def _fold_view(self, board_id: str) -> dict:
            return {"running": folds.running(board_id), "error": folds.error(board_id)}

        def _handle_fold_post(self, board_id: str) -> None:
            store.get_board(board_id)  # 404 for an unknown board before anything starts
            if folds.running(board_id):
                self._send_json(409, {"error": "a fold is already running on this board"})
                return
            # stored before the thread starts, so mission control shows it the moment it opens
            store.add_orchestrator_message(
                board_id, "board", "fold: reading every card and the ledger - this takes minutes"
            )
            folds.start(board_id)
            self._send_json(202, self._fold_view(board_id))

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
                author = AUTHOR_KEY if comment["author"] == AUTHOR_KEY else "board"
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
            comment = store.add_comment(card_id, author=AUTHOR_KEY, body=message)
            # queue AFTER the comment is durable: a live delivery that then crashed before the
            # comment was ever saved would leave nothing for the card's next run to fall back on
            delivered_live = runs.queue_note(card_id, comment)
            self._send_json(
                201,
                {
                    "author": AUTHOR_KEY,
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
            length = self._body_length(_MAX_UPLOAD_BYTES)
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
            media_type = meta["media_type"]
            if not _MEDIA_TYPE.match(media_type or ""):
                media_type = "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(blob)))
            self.send_header("Cache-Control", "no-store")
            # an uploaded html file must download, never render same-origin with the api behind it
            filename = quote(meta["filename"] or "attachment", safe="")
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{filename}")
            self.send_header("Content-Security-Policy", "sandbox")
            self._send_hardening_headers()
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
            self.send_header("Content-Security-Policy", _CSP)
            self._send_hardening_headers()
            self.end_headers()
            self.wfile.write(data)

        def _send_hardening_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")

        def _body_length(self, cap: int) -> int:
            length = int(self.headers.get("Content-Length", 0))
            if length < 0:
                raise ValueError("Content-Length must not be negative")
            if length > cap:
                raise BodyTooLargeError(f"request body over {cap} bytes")
            return length

        def _read_json(self) -> dict:
            length = self._body_length(_MAX_JSON_BYTES)
            if length == 0:
                return {}
            # a cross-site form can only send text/plain or form bodies, never application/json
            if self.headers.get_content_type() != "application/json":
                raise NotJsonError("expected Content-Type: application/json")
            return json.loads(self.rfile.read(length))

        def _send_json(self, status: int, payload: object) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._send_hardening_headers()
            self.end_headers()
            self.wfile.write(body)

        def _send_status(self, status: int) -> None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

    return Handler


def _build_image_in_background(
    db_path: Path, repo: dict, builds: dict[str, dict], lock: threading.Lock
) -> None:
    """runs one image build on its own thread with its own store connection"""
    try:
        result = build_repo_image(repo)
    except (OSError, ValueError) as exc:
        outcome = {"state": "failed", "log": str(exc)}
    else:
        if result.ok:
            with Store(db_path) as build_store:
                build_store.set_repo_image(repo["id"], result.tag)
            outcome = {"state": "built", "image": result.tag, "log": result.log}
        else:
            outcome = {"state": "failed", "log": result.log, "stack": result.stack}
    with lock:
        builds[repo["id"]] = outcome


class BoardServer(HTTPServer):
    """the board's http server, which also owns the scheduler ticker - closing the server stops
    it, so neither the cli nor a test leaves that thread behind"""

    ticker: SchedulerTicker | None = None

    def server_close(self) -> None:
        if self.ticker is not None:
            self.ticker.stop()
        super().server_close()


def start_background(server: BoardServer) -> list[str]:
    """requeues the retries the last process owed and never fired, then starts the ticker that
    fires them from here on. the cli calls this once serving; build_server alone never starts a
    run or a thread, so a test opts in. returns the requeued card ids"""
    requeued = server.scheduler.requeue_lapsed()
    server.ticker.start()
    return requeued


def build_server(
    store: Store,
    port: int,
    host: str = "127.0.0.1",
    token_path: str | None = None,
    api_key: str | None = None,
) -> BoardServer:
    """api_key None skips the key check (tests); the cli always passes one"""
    # a new board has no runs, so any card still mid-run lost the last board process under it
    recovered = recover_orphaned_runs(store)
    # one-time: a CRASH card blocked before the classifier learned session-limit/api-unreachable
    # wording becomes USAGE_LIMIT/API_UNREACHABLE instead, so it is picked up by the auto-retry
    # paths rather than sitting mislabeled
    relabel_stale_crashes(store)
    # single-threaded: the store's sqlite3 connection is bound to the thread that opened it. card
    # runs are the exception and get their own thread and their own connection - see runs.py
    runs = RunRegistry(store.path, token_path=token_path)
    orchestrator = OrchestratorRegistry(store.path, token_path=token_path)
    scheduler = SchedulerRegistry(store.path, runs)
    runs.set_finish_hook(scheduler.finish_hook)
    handler_cls = _make_handler(
        store,
        runs,
        Readiness(token_path),
        orchestrator,
        scheduler,
        token_path=token_path,
        api_key=api_key,
        bound_host=host,
    )
    server = BoardServer((host, port), handler_cls)
    server.runs = runs  # the cli and the tests reach the registry through the server
    server.recovered = recovered
    server.orchestrator = orchestrator
    server.scheduler = scheduler
    server.ticker = SchedulerTicker(scheduler)
    return server
