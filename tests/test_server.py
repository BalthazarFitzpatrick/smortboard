"""server tests: run a real http server in a thread against a tmp_path database"""

import json
import subprocess
import threading
import urllib.error
import urllib.request

import pytest

from smortboard.actions import next_action
from smortboard.server.app import build_server
from smortboard.store import Store
from tests.test_repo_setup import FakeGh, _git, _remote_heads


@pytest.fixture(autouse=True)
def fake_gh(tmp_path_factory, monkeypatch):
    """a board's folder setup runs real git against a gh that makes local bare repos, so no test
    here reaches GitHub - and the operator's own git config cannot decide whether a commit works"""
    config = tmp_path_factory.mktemp("git") / "gitconfig"
    config.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "t")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "t@t")
    gh = FakeGh(tmp_path_factory.mktemp("remotes"))
    monkeypatch.setattr("smortboard.repo_setup.default_runner", gh)
    return gh


@pytest.fixture
def running_server(tmp_path):
    # the store's sqlite3 connection is bound to its creating thread, so build both
    # the store and the server inside the same thread that will run serve_forever
    ready = threading.Event()
    holder = {}

    def _run():
        store = Store(tmp_path / "board.db")
        server = build_server(store, port=0, host="127.0.0.1")
        holder["server"] = server
        ready.set()
        server.serve_forever()
        store.close()  # closed on the same thread that created the connection

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    ready.wait()
    port = holder["server"].server_address[1]
    yield f"http://127.0.0.1:{port}"
    holder["server"].shutdown()
    thread.join()
    holder["server"].server_close()


def _request(url, method="GET", body=None, headers=None):
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers = {**(headers or {}), "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            payload = resp.read()
            parsed = json.loads(payload) if payload else None
            return resp.status, parsed
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        parsed = json.loads(payload) if payload else None
        return exc.code, parsed


def test_health(running_server):
    status, body = _request(f"{running_server}/health")
    assert status == 200
    assert body["ok"] is True
    assert "version" in body


def test_board_and_card_round_trip(running_server):
    status, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    assert status == 201
    assert board["name"] == "dev"

    status, boards = _request(f"{running_server}/api/boards")
    assert status == 200
    assert any(b["id"] == board["id"] for b in boards)

    status, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {
            "board_id": board["id"],
            "repo_id": None,
            "title": "wire the server",
            "tasks": ["a", "b"],
            "criteria": ["works"],
        },
    )
    assert status == 201
    assert card["title"] == "wire the server"
    assert [t["text"] for t in card["tasks"]] == ["a", "b"]
    assert card["depends_on"] == []
    assert card["comments"] == []

    status, fetched = _request(f"{running_server}/api/cards/{card['id']}")
    assert status == 200
    assert fetched["id"] == card["id"]

    status, cards = _request(f"{running_server}/api/boards/{board['id']}/cards")
    assert status == 200
    assert len(cards) == 1


def test_patch_card_writable_fields(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x"},
    )
    status, updated = _request(
        f"{running_server}/api/cards/{card['id']}", "PATCH", {"status": "doing"}
    )
    assert status == 200
    assert updated["status"] == "doing"


def test_patch_card_model_over_http(running_server):
    # the server kept its own copy of the writable fields, which went stale and answered 400 here
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x"},
    )
    status, updated = _request(
        f"{running_server}/api/cards/{card['id']}", "PATCH", {"model": "haiku"}
    )
    assert status == 200
    assert updated["model"] == "haiku"


def test_a_long_card_title_is_warned_about_never_refused_or_cut(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    long_title = "one two three four five six seven eight nine ten eleven twelve"
    status, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": long_title},
    )
    assert status == 201
    assert card["title"] == long_title
    assert any("12 words" in note and "max 8" in note for note in card["warnings"])

    status, short = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "one two three four five six"},
    )
    assert status == 201
    assert short["warnings"] == []


def test_patching_card_text_answers_with_warnings(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x"},
    )
    url = f"{running_server}/api/cards/{card['id']}"
    description = " ".join(["word"] * 30)
    status, updated = _request(url, "PATCH", {"description": description})
    assert status == 200
    assert updated["description"] == description
    assert updated["warnings"] == ['"x" runs long: 30 words in the description (max 20)']
    # a move is not a text edit, so an old long card stays quiet
    status, moved = _request(url, "PATCH", {"status": "doing"})
    assert status == 200
    assert moved["warnings"] == []


def test_patch_card_unknown_field_is_400(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x"},
    )
    status, body = _request(
        f"{running_server}/api/cards/{card['id']}", "PATCH", {"tasks": ["nope"]}
    )
    assert status == 400
    assert "error" in body


def test_patch_card_blocked_without_reason_is_400(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x"},
    )
    status, body = _request(
        f"{running_server}/api/cards/{card['id']}", "PATCH", {"status": "blocked"}
    )
    assert status == 400
    assert "error" in body


def test_patch_task_done_round_trips(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x", "tasks": ["ship it"]},
    )
    task_id = card["tasks"][0]["id"]

    status, task = _request(f"{running_server}/api/tasks/{task_id}", "PATCH", {"done": True})
    assert status == 200
    assert task["done"] == 1

    status, fetched = _request(f"{running_server}/api/cards/{card['id']}")
    assert fetched["tasks"][0]["done"] == 1


def test_patch_task_unknown_field_is_400(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x", "tasks": ["ship it"]},
    )
    task_id = card["tasks"][0]["id"]

    status, body = _request(f"{running_server}/api/tasks/{task_id}", "PATCH", {"text": "rewritten"})
    assert status == 400
    assert "error" in body


def test_get_missing_card_is_404(running_server):
    status, body = _request(f"{running_server}/api/cards/does-not-exist")
    assert status == 404
    assert "error" in body


def test_get_card_carries_its_next_action(running_server):
    # the open card reads what to do next off this route, same as the board list
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x"},
    )
    _request(
        f"{running_server}/api/cards/{card['id']}",
        "PATCH",
        {"status": "doing", "blocked_reason_code": "TESTS_FAILED"},
    )
    status, fetched = _request(f"{running_server}/api/cards/{card['id']}")
    assert status == 200
    assert fetched["next_action"] == next_action("TESTS_FAILED")
    assert fetched["next_action_short"] == "answer it"
    assert fetched["handled_by_board"] is False


def test_delete_a_card_that_has_been_used(running_server):
    """the endpoint dropped the connection without a status for any card with children, because the
    store raised and nothing caught it. a 000 from curl is the least diagnosable failure there is."""
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "used"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "has been used"},
    )
    _request(
        f"{running_server}/api/cards/{card['id']}/comments",
        "POST",
        {"author": "me", "body": "hello"},
    )

    status, _ = _request(f"{running_server}/api/cards/{card['id']}", "DELETE")
    assert status == 204

    status, _ = _request(f"{running_server}/api/cards/{card['id']}", "GET")
    assert status == 404


def test_delete_card(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x"},
    )
    status, body = _request(f"{running_server}/api/cards/{card['id']}", "DELETE")
    assert status == 204
    assert body is None

    status, _ = _request(f"{running_server}/api/cards/{card['id']}")
    assert status == 404


def test_comments(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x"},
    )
    status, comment = _request(
        f"{running_server}/api/cards/{card['id']}/comments",
        "POST",
        {"author": "operator", "body": "looks good"},
    )
    assert status == 201
    assert comment["body"] == "looks good"

    status, fetched = _request(f"{running_server}/api/cards/{card['id']}")
    assert [c["body"] for c in fetched["comments"]] == ["looks good"]


def test_events(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x"},
    )
    status, events = _request(f"{running_server}/api/cards/{card['id']}/events")
    assert status == 200
    assert events == []


def test_attachment_upload_and_download(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x"},
    )

    boundary = "smortboundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="note.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        "hello world\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    req = urllib.request.Request(
        f"{running_server}/api/cards/{card['id']}/attachments",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req) as resp:
        status = resp.status
        attachment = json.loads(resp.read())
    assert status == 201
    assert attachment["filename"] == "note.txt"
    assert "blob" not in attachment

    with urllib.request.urlopen(
        f"{running_server}/api/cards/{card['id']}/attachments/{attachment['id']}"
    ) as resp:
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "text/plain"
        assert resp.read() == b"hello world"
        # an attachment always downloads, so stored html never renders beside the api
        assert resp.headers["Content-Disposition"] == "attachment; filename*=UTF-8''note.txt"
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["Content-Security-Policy"] == "sandbox"


def test_attachment_with_odd_media_type_is_served_as_bytes(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x"},
    )
    body = (
        b"--b\r\n"
        b'Content-Disposition: form-data; name="file"; filename="x.html"\r\n'
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b"<b>x</b>\r\n"
        b"--b--\r\n"
    )
    req = urllib.request.Request(
        f"{running_server}/api/cards/{card['id']}/attachments",
        data=body,
        method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=b"},
    )
    with urllib.request.urlopen(req) as resp:
        attachment = json.loads(resp.read())
    url = f"{running_server}/api/cards/{card['id']}/attachments/{attachment['id']}"
    with urllib.request.urlopen(url) as resp:
        assert resp.headers["Content-Type"] == "application/octet-stream"


def test_board_page_carries_a_strict_csp(running_server):
    with urllib.request.urlopen(f"{running_server}/ui/index.html") as resp:
        csp = resp.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp


def test_asset_traversal_is_404(running_server):
    status, _ = _request(f"{running_server}/ui/../pyproject.toml")
    assert status == 404


def test_asset_served_from_local_ui_dir_over_ui_base(running_server, tmp_path, monkeypatch):
    import smortboard.server.assets as assets_module

    local_dir = tmp_path / "ui"
    local_dir.mkdir()
    (local_dir / "base.css").write_text("/* local override */")
    monkeypatch.setattr(assets_module, "LOCAL_UI_DIR", local_dir)

    status, _ = (None, None)
    import urllib.request as ur

    with ur.urlopen(f"{running_server}/ui/base.css") as resp:
        status = resp.status
        body = resp.read()
    assert status == 200
    assert body == b"/* local override */"


def test_asset_falls_back_to_ui_base(running_server):
    with urllib.request.urlopen(f"{running_server}/ui/base.css") as resp:
        assert resp.status == 200
        assert len(resp.read()) > 0


def test_unknown_asset_is_404(running_server):
    status, _ = _request(f"{running_server}/ui/does-not-exist.css")
    assert status == 404


def _init_repo(path, branch="main"):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.email=t@t.com",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "init",
        ],
        check=True,
    )


def _commit_files(path, files):
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.email=t@t.com", "-c", "user.name=t"]
        + ["commit", "-q", "-m", "files"],
        check=True,
    )


def test_register_repo_round_trips(running_server, tmp_path):
    repo_path = tmp_path / "repo"
    _init_repo(repo_path)
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    status, repo = _request(
        f"{running_server}/api/boards/{board['id']}/repos",
        "POST",
        {"name": "smortboard", "path": str(repo_path), "default_branch": "main"},
    )
    assert status == 201
    assert repo["name"] == "smortboard"
    assert repo["path"] == str(repo_path)

    status, repos = _request(f"{running_server}/api/boards/{board['id']}/repos")
    assert status == 200
    assert len(repos) == 1


def test_register_repo_fills_a_blank_test_command_from_the_repo(running_server, tmp_path):
    repo_path = tmp_path / "repo"
    _init_repo(repo_path)
    _commit_files(repo_path, {"pyproject.toml": "[project]\nname = 'x'\n"})
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    status, repo = _request(
        f"{running_server}/api/boards/{board['id']}/repos",
        "POST",
        {"name": "x", "path": str(repo_path), "default_branch": "main", "test_command": ""},
    )
    assert status == 201
    assert repo["test_command"] == "uv run --no-sync pytest -q"
    assert repo["tests"] == {"command": "uv run --no-sync pytest -q", "has_tests": False}


def test_register_repo_keeps_an_explicit_test_command(running_server, tmp_path):
    repo_path = tmp_path / "repo"
    _init_repo(repo_path)
    _commit_files(repo_path, {"pyproject.toml": "", "tests/test_a.py": "def test_a(): pass\n"})
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    status, repo = _request(
        f"{running_server}/api/boards/{board['id']}/repos",
        "POST",
        {
            "name": "x",
            "path": str(repo_path),
            "default_branch": "main",
            "test_command": "make check",
        },
    )
    assert status == 201
    assert repo["test_command"] == "make check"
    assert repo["tests"] == {"command": "make check", "has_tests": True}


def test_register_repo_missing_path_is_400_with_actionable_message(running_server, tmp_path):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    status, body = _request(
        f"{running_server}/api/boards/{board['id']}/repos",
        "POST",
        {"name": "smortboard", "path": str(tmp_path / "nope"), "default_branch": "main"},
    )
    assert status == 400
    assert "does not exist" in body["error"]


def test_register_repo_not_a_git_repo_is_400(running_server, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    status, body = _request(
        f"{running_server}/api/boards/{board['id']}/repos",
        "POST",
        {"name": "smortboard", "path": str(plain), "default_branch": "main"},
    )
    assert status == 400
    assert "not a git repository" in body["error"]


def test_register_repo_missing_branch_is_400(running_server, tmp_path):
    repo_path = tmp_path / "repo"
    _init_repo(repo_path, branch="main")
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    status, body = _request(
        f"{running_server}/api/boards/{board['id']}/repos",
        "POST",
        {"name": "smortboard", "path": str(repo_path), "default_branch": "release"},
    )
    assert status == 400
    assert "release" in body["error"]


def test_register_repo_empty_name_is_400(running_server, tmp_path):
    repo_path = tmp_path / "repo"
    _init_repo(repo_path)
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    status, body = _request(
        f"{running_server}/api/boards/{board['id']}/repos",
        "POST",
        {"name": "", "path": str(repo_path), "default_branch": "main"},
    )
    assert status == 400
    assert "name" in body["error"]


def test_patch_repo_test_command_and_image(running_server, tmp_path):
    repo_path = tmp_path / "repo"
    _init_repo(repo_path)
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, repo = _request(
        f"{running_server}/api/boards/{board['id']}/repos",
        "POST",
        {"name": "smortboard", "path": str(repo_path), "default_branch": "main"},
    )
    status, updated = _request(
        f"{running_server}/api/repos/{repo['id']}",
        "PATCH",
        {"test_command": "uv run pytest", "image": "card-python:latest"},
    )
    assert status == 200
    assert updated["test_command"] == "uv run pytest"
    assert updated["image"] == "card-python:latest"


def test_patch_repo_default_branch_moves_the_base(running_server, tmp_path):
    """a base branch that merged into main must be movable without a db write"""
    repo_path = tmp_path / "repo"
    _init_repo(repo_path, branch="feature/review-tool")
    subprocess.run(["git", "-C", str(repo_path), "branch", "main"], check=True)
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, repo = _request(
        f"{running_server}/api/boards/{board['id']}/repos",
        "POST",
        {"name": "smolsmort", "path": str(repo_path), "default_branch": "feature/review-tool"},
    )
    status, updated = _request(
        f"{running_server}/api/repos/{repo['id']}", "PATCH", {"default_branch": "main"}
    )
    assert status == 200
    assert updated["default_branch"] == "main"


def test_patch_repo_to_a_missing_branch_is_400_and_changes_nothing(running_server, tmp_path):
    repo_path = tmp_path / "repo"
    _init_repo(repo_path)
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, repo = _request(
        f"{running_server}/api/boards/{board['id']}/repos",
        "POST",
        {"name": "smortboard", "path": str(repo_path), "default_branch": "main"},
    )
    status, body = _request(
        f"{running_server}/api/repos/{repo['id']}",
        "PATCH",
        {"default_branch": "no-such-branch", "test_command": "uv run pytest"},
    )
    assert status == 400
    assert "no-such-branch" in body["error"]
    _, unchanged = _request(f"{running_server}/api/boards/{board['id']}/repos", "GET")
    assert unchanged[0]["default_branch"] == "main"
    assert unchanged[0]["test_command"] is None


def test_folders_lists_subfolders_and_marks_repos(running_server, tmp_path):
    _init_repo(tmp_path / "a-repo")
    (tmp_path / "plain").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "a-file.txt").write_text("x")
    status, body = _request(f"{running_server}/api/folders?under={tmp_path}")
    assert status == 200
    assert body["here"] == str(tmp_path.resolve())
    assert body["parent"] == str(tmp_path.resolve().parent)
    assert [(f["name"], f["repo"]) for f in body["folders"]] == [("a-repo", True), ("plain", False)]


def test_folders_on_a_missing_path_is_400(running_server, tmp_path):
    status, body = _request(f"{running_server}/api/folders?under={tmp_path / 'nope'}")
    assert status == 400
    assert "not a folder" in body["error"]


def test_board_from_a_local_repo_registers_it(running_server, tmp_path):
    _init_repo(tmp_path / "myrepo")
    status, body = _request(
        f"{running_server}/api/boards/from-repo", "POST", {"path": str(tmp_path / "myrepo")}
    )
    assert status == 201
    assert body["board"]["name"] == "myrepo"
    # from-repo is from-folder under its old name, so the repo gets development as its base
    assert body["repo"]["default_branch"] == "development"
    # an unknown stack gets no command, and says there is nothing to run yet
    assert body["tests"] == {"command": None, "has_tests": False}
    _, repos = _request(f"{running_server}/api/boards/{body['board']['id']}/repos")
    assert [r["name"] for r in repos] == ["myrepo"]


@pytest.mark.parametrize(
    ("files", "has_tests"),
    [
        ({"pyproject.toml": ""}, False),
        ({"pyproject.toml": "", "tests/test_a.py": "def test_a(): pass\n"}, True),
    ],
)
def test_board_from_a_python_repo_stores_its_test_command(
    running_server, tmp_path, files, has_tests
):
    _init_repo(tmp_path / "myrepo")
    _commit_files(tmp_path / "myrepo", files)
    status, body = _request(
        f"{running_server}/api/boards/from-repo", "POST", {"path": str(tmp_path / "myrepo")}
    )
    assert status == 201
    assert body["repo"]["test_command"] == "uv run --no-sync pytest -q"
    assert body["tests"] == {"command": "uv run --no-sync pytest -q", "has_tests": has_tests}


def test_board_from_a_missing_folder_is_400_and_creates_no_board(running_server, tmp_path):
    status, body = _request(
        f"{running_server}/api/boards/from-repo", "POST", {"path": str(tmp_path / "not-there")}
    )
    assert status == 400
    assert "not a folder" in body["error"]
    _, boards = _request(f"{running_server}/api/boards")
    assert boards == []


def test_board_from_an_empty_folder_sets_it_up_on_development(running_server, tmp_path, fake_gh):
    folder = tmp_path / "fresh"
    folder.mkdir()
    status, body = _request(
        f"{running_server}/api/boards/from-folder", "POST", {"path": str(folder)}
    )
    assert status == 201
    assert body["board"]["name"] == "fresh"
    assert body["repo"]["default_branch"] == "development"
    assert "created private GitHub repo tester/fresh" in body["setup"]["steps"]
    # main is the operator's to push: the reply carries the command, the origin only development
    assert "push -u origin main" in body["setup"]["push_main"]
    assert "gh repo edit tester/fresh --default-branch main" in body["setup"]["push_main"]
    assert _remote_heads(fake_gh.remotes / "fresh.git") == ["development"]


def test_board_in_a_new_folder_creates_the_folder_first(running_server, tmp_path):
    status, body = _request(
        f"{running_server}/api/boards/from-folder",
        "POST",
        {"path": str(tmp_path), "new_folder": "made"},
    )
    assert status == 201
    assert (tmp_path / "made" / ".git").is_dir()
    assert body["board"]["name"] == "made"
    assert body["repo"]["path"] == str(tmp_path / "made")


@pytest.mark.parametrize("name", ["a/b", "..", ".", " ", "taken"])
def test_a_new_folder_is_one_plain_name_not_there_yet(running_server, tmp_path, name):
    (tmp_path / "taken").mkdir()
    status, body = _request(
        f"{running_server}/api/boards/from-folder",
        "POST",
        {"path": str(tmp_path), "new_folder": name},
    )
    assert status == 400
    assert "one plain name" in body["error"] or "already exists" in body["error"]
    _, boards = _request(f"{running_server}/api/boards")
    assert boards == []


def test_a_new_folder_github_would_refuse_is_never_made(running_server, tmp_path):
    status, body = _request(
        f"{running_server}/api/boards/from-folder",
        "POST",
        {"path": str(tmp_path), "new_folder": "my project!"},
    )
    assert status == 400
    assert "GitHub repo name" in body["error"]
    assert not (tmp_path / "my project!").exists()


def test_a_folder_with_files_is_asked_about_before_its_first_commit(running_server, tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n")
    (folder / ".env").write_text("TOKEN=secret\n")
    url = f"{running_server}/api/boards/from-folder"

    status, body = _request(url, "POST", {"path": str(folder)})
    assert status == 409
    assert body == {"needs_confirm": True, "files": ["app.py"], "path": str(folder)}
    assert not (folder / ".git").exists()
    _, boards = _request(f"{running_server}/api/boards")
    assert boards == []

    status, body = _request(url, "POST", {"path": str(folder), "confirm": True})
    assert status == 201
    assert body["repo"]["default_branch"] == "development"
    committed = _git(folder, "ls-tree", "-r", "--name-only", "main").split()
    assert sorted(committed) == [".gitignore", "app.py"]


def test_a_ready_repo_is_registered_with_nothing_set_up(running_server, tmp_path, fake_gh):
    bare = fake_gh.remotes / "ready.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    repo = tmp_path / "ready"
    _init_repo(repo)
    _git(repo, "branch", "development")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "origin", "main", "development")
    status, body = _request(f"{running_server}/api/boards/from-folder", "POST", {"path": str(repo)})
    assert status == 201
    assert body["setup"] == {"steps": [], "push_main": None}
    assert body["repo"]["default_branch"] == "development"
    assert fake_gh.pushes() == []


def test_patch_repo_unknown_field_is_400(running_server, tmp_path):
    repo_path = tmp_path / "repo"
    _init_repo(repo_path)
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, repo = _request(
        f"{running_server}/api/boards/{board['id']}/repos",
        "POST",
        {"name": "smortboard", "path": str(repo_path), "default_branch": "main"},
    )
    status, body = _request(
        f"{running_server}/api/repos/{repo['id']}", "PATCH", {"path": "/somewhere/else"}
    )
    assert status == 400
    assert "error" in body


def test_register_repo_on_missing_board_is_404(running_server, tmp_path):
    repo_path = tmp_path / "repo"
    _init_repo(repo_path)
    status, body = _request(
        f"{running_server}/api/boards/does-not-exist/repos",
        "POST",
        {"name": "smortboard", "path": str(repo_path), "default_branch": "main"},
    )
    assert status == 404


def test_patch_card_sets_its_lease(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x"},
    )
    status, updated = _request(
        f"{running_server}/api/cards/{card['id']}",
        "PATCH",
        {"leases": ["src/**", "tests/**"], "model": "haiku"},
    )
    assert status == 200
    assert [row["path_glob"] for row in updated["leases"]] == ["src/**", "tests/**"]
    assert updated["model"] == "haiku"


@pytest.mark.parametrize("leases", ["src/**", ["/etc/passwd"]])
def test_patch_card_refuses_a_malformed_lease(running_server, leases):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board["id"], "repo_id": None, "title": "x"},
    )
    status, body = _request(f"{running_server}/api/cards/{card['id']}", "PATCH", {"leases": leases})
    assert status == 400
    assert "error" in body


def _make_card(running_server, board_id, title, **fields):
    status, card = _request(
        f"{running_server}/api/cards",
        "POST",
        {"board_id": board_id, "repo_id": None, "title": title, **fields},
    )
    return status, card


def test_patch_card_sets_depends_on(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, upstream = _make_card(running_server, board["id"], "upstream")
    _, downstream = _make_card(running_server, board["id"], "downstream")

    status, updated = _request(
        f"{running_server}/api/cards/{downstream['id']}",
        "PATCH",
        {"depends_on": [upstream["id"]]},
    )
    assert status == 200
    assert updated["depends_on"] == [upstream["id"]]

    status, fetched = _request(f"{running_server}/api/cards/{downstream['id']}")
    assert fetched["depends_on"] == [upstream["id"]]

    status, cleared = _request(
        f"{running_server}/api/cards/{downstream['id']}", "PATCH", {"depends_on": []}
    )
    assert status == 200
    assert cleared["depends_on"] == []


def test_post_card_with_depends_on_creates_the_links(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, upstream = _make_card(running_server, board["id"], "upstream")

    status, downstream = _make_card(
        running_server, board["id"], "downstream", depends_on=[upstream["id"]]
    )
    assert status == 201
    assert downstream["depends_on"] == [upstream["id"]]


def test_patch_card_depends_on_unknown_id_is_400_and_changes_nothing(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _make_card(running_server, board["id"], "x")

    status, body = _request(
        f"{running_server}/api/cards/{card['id']}", "PATCH", {"depends_on": ["nope"]}
    )
    assert status == 400
    assert "error" in body
    status, fetched = _request(f"{running_server}/api/cards/{card['id']}")
    assert fetched["depends_on"] == []


def test_patch_card_depends_on_self_is_400_and_changes_nothing(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, card = _make_card(running_server, board["id"], "x")

    status, body = _request(
        f"{running_server}/api/cards/{card['id']}", "PATCH", {"depends_on": [card["id"]]}
    )
    assert status == 400
    assert "error" in body
    status, fetched = _request(f"{running_server}/api/cards/{card['id']}")
    assert fetched["depends_on"] == []


def test_patch_card_depends_on_cycle_is_400_and_changes_nothing(running_server):
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, a = _make_card(running_server, board["id"], "a")
    _, b = _make_card(running_server, board["id"], "b")
    status, _ = _request(
        f"{running_server}/api/cards/{b['id']}", "PATCH", {"depends_on": [a["id"]]}
    )
    assert status == 200

    status, body = _request(
        f"{running_server}/api/cards/{a['id']}", "PATCH", {"depends_on": [b["id"]]}
    )
    assert status == 400
    assert "error" in body
    status, fetched = _request(f"{running_server}/api/cards/{a['id']}")
    assert fetched["depends_on"] == []


def test_an_oversized_json_body_is_refused_before_it_is_read(running_server):
    req = urllib.request.Request(
        f"{running_server}/api/boards",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json", "Content-Length": "5000000"},
    )
    try:
        urllib.request.urlopen(req)
    except urllib.error.HTTPError as exc:
        assert exc.code == 413
    else:
        pytest.fail("an oversized body was accepted")


def test_a_bad_repo_image_is_a_400(running_server, tmp_path):
    repo_path = tmp_path / "repo"
    _init_repo(repo_path)
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    spec = {"name": "r", "path": str(repo_path), "default_branch": "main", "image": "--privileged"}
    status, body = _request(f"{running_server}/api/boards/{board['id']}/repos", "POST", spec)
    assert status == 400
    assert "image" in body["error"]


def test_an_image_build_runs_off_the_request_thread(running_server, tmp_path, monkeypatch):
    from smortboard.repo_image import BuildResult

    release = threading.Event()

    def slow_build(repo):
        release.wait(5)
        return BuildResult(ok=True, tag=f"{repo['name']}-repo:latest", log="built")

    monkeypatch.setattr("smortboard.server.app.build_repo_image", slow_build)
    repo_path = tmp_path / "repo"
    _init_repo(repo_path)
    _, board = _request(f"{running_server}/api/boards", "POST", {"name": "dev"})
    _, repo = _request(
        f"{running_server}/api/boards/{board['id']}/repos",
        "POST",
        {"name": "widgets", "path": str(repo_path), "default_branch": "main"},
    )
    build_url = f"{running_server}/api/repos/{repo['id']}/image/build"
    assert _request(build_url, "POST") == (202, {"state": "building"})
    # the board still answers while the build is running, and refuses a second build
    assert _request(f"{running_server}/api/boards")[0] == 200
    assert _request(build_url, "POST")[0] == 409
    assert _request(build_url)[1] == {"state": "building"}
    release.set()
    for _ in range(50):
        status, build = _request(build_url)
        if build["state"] != "building":
            break
        threading.Event().wait(0.1)
    assert build == {"state": "built", "image": "widgets-repo:latest", "log": "built"}
    _, repos = _request(f"{running_server}/api/boards/{board['id']}/repos")
    assert repos[0]["image"] == "widgets-repo:latest"
