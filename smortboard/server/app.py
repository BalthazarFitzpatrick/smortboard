"""the http api: routes json endpoints to the store, and assets per smortboard/server/assets.py"""

import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.metadata import PackageNotFoundError, version

from smortboard.server.assets import AssetNotFound, content_type_for, resolve_asset
from smortboard.server.multipart import MultipartError, parse_boundary, parse_first_file
from smortboard.store import Store
from smortboard.store.errors import BlockedReasonInvalidError, NotFoundError, UnknownFieldError

_CARD_WRITABLE_FIELDS = {
    "title",
    "workstream",
    "status",
    "blocked_reason_code",
    "description",
    "position",
    "review_flag",
    "repo_id",
}

_ROUTES = [
    (re.compile(r"^/health$"), "GET"),
    (re.compile(r"^/api/boards$"), "GET"),
    (re.compile(r"^/api/boards$"), "POST"),
    (re.compile(r"^/api/boards/(?P<board_id>[^/]+)/cards$"), "GET"),
    (re.compile(r"^/api/cards$"), "POST"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)$"), "GET"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)$"), "PATCH"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)$"), "DELETE"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/comments$"), "POST"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/attachments$"), "POST"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/attachments/(?P<attachment_id>[^/]+)$"), "GET"),
    (re.compile(r"^/api/cards/(?P<card_id>[^/]+)/events$"), "GET"),
    (re.compile(r"^/ui/(?P<name>.+)$"), "GET"),
]


def _version() -> str:
    try:
        return version("smortboard")
    except PackageNotFoundError:
        return "0.0.0-dev"


def _make_handler(store: Store) -> type[BaseHTTPRequestHandler]:
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
            except MultipartError as exc:
                self._send_json(400, {"error": str(exc)})
            except (KeyError, TypeError, ValueError) as exc:
                # malformed json body or a request missing a required field
                self._send_json(400, {"error": str(exc)})

        def _handle(self, method: str, path: str, **params: str) -> None:
            if path == "/health":
                self._send_json(200, {"ok": True, "version": _version()})
            elif path == "/api/boards" and method == "GET":
                self._send_json(200, store.list_boards())
            elif path == "/api/boards" and method == "POST":
                body = self._read_json()
                board = store.create_board(name=body["name"])
                self._send_json(201, board)
            elif "board_id" in params:
                cards = store.list_cards(params["board_id"])
                self._send_json(200, cards)
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
            elif path.endswith("/events"):
                self._send_json(200, store.list_events(params["card_id"]))
            elif "card_id" in params and method == "GET":
                self._send_json(200, store.get_card(params["card_id"]))
            elif "card_id" in params and method == "PATCH":
                self._handle_patch_card(params["card_id"])
            elif "card_id" in params and method == "DELETE":
                store.delete_card(params["card_id"])
                self._send_status(204)
            elif "name" in params:
                self._handle_asset(params["name"])
            else:
                self._send_json(404, {"error": f"no route for {method} {path}"})

        def _handle_patch_card(self, card_id: str) -> None:
            body = self._read_json()
            unknown = set(body) - _CARD_WRITABLE_FIELDS
            if unknown:
                self._send_json(400, {"error": f"not writable: {sorted(unknown)}"})
                return
            card = store.update_card(card_id, **body)
            self._send_json(200, card)

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


def build_server(store: Store, port: int, host: str = "0.0.0.0") -> HTTPServer:
    # single-threaded: the store's sqlite3 connection is bound to the thread that opened it
    handler_cls = _make_handler(store)
    return HTTPServer((host, port), handler_cls)
