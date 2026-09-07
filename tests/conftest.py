import pytest
from fastapi.testclient import TestClient

from server import database, server
from server.accounts import AccountStore
from server.auth import AuthService
from server.message_store import MessageStore
from server.room_store import RoomStore
from server.security_store import SecurityEventStore

app, manager, rooms = server.app, server.manager, server.rooms
TEST_PASSWORD = "TestPass1!"


@pytest.fixture
def database_path(tmp_path):
    """A throwaway database per test. The developer's real
    server/accounts.sqlite3 is never opened by the suite.

    The filename matches the one test_auth_websocket.py's `auth` fixture
    builds from the same tmp_path, so accounts and messages share one file
    exactly as they do in production. Without that, messages.sender would
    reference an account stored in a different database.
    """
    path = tmp_path / "accounts.sqlite3"
    database.initialize_schema(path)
    return path


@pytest.fixture(autouse=True)
def clean_state(database_path, monkeypatch):
    """The server keeps rooms/connections in module-level state, so every
    test starts from an empty server and leaves nothing behind."""
    manager.active_connections.clear()
    for room in rooms.values():
        room.members.clear()

    # Point every store at the temporary database. These are resolved from
    # module globals at call time, so monkeypatching redirects the server.
    monkeypatch.setattr(
        server, "auth_service", AuthService(AccountStore(database_path)))
    monkeypatch.setattr(server, "room_store", RoomStore(database_path))
    monkeypatch.setattr(server, "message_store", MessageStore(database_path))
    monkeypatch.setattr(
        server, "security_store", SecurityEventStore(database_path))

    # messages.room / messages.sender are foreign keys, so the default rooms
    # must exist in this database too.
    server.room_store.ensure_defaults(server.DEFAULT_ROOMS)

    yield

    manager.active_connections.clear()
    for room in rooms.values():
        room.members.clear()


@pytest.fixture
def client():
    return TestClient(app)


def login(ws, username, password=TEST_PASSWORD):
    """Open a session as `username` and return the LOGIN_RESULT payload."""
    if server.auth_service.store.get(username.strip()) is None:
        result = server.auth_service.signup(username, password)
        assert result["success"]
    ws.send_json({
        "type": "LOGIN",
        "request_id": f"login-{username}",
        "data": {"username": username, "password": password},
    })
    return ws.receive_json()


def join(ws, username_room, request_id="join-1"):
    ws.send_json({
        "type": "JOIN_ROOM",
        "request_id": request_id,
        "data": {"room": username_room},
    })
    return ws.receive_json()


def leave(ws, room, request_id="leave-1"):
    ws.send_json({
        "type": "LEAVE_ROOM",
        "request_id": request_id,
        "data": {"room": room},
    })
    return ws.receive_json()


def chat(ws, room, content, request_id="chat-1"):
    ws.send_json({
        "type": "CHAT_MESSAGE",
        "request_id": request_id,
        "data": {"room": room, "content": content},
    })


def list_rooms(ws, request_id="rooms-1"):
    ws.send_json({
        "type": "LIST_ROOMS",
        "request_id": request_id,
        "data": {},
    })
    return ws.receive_json()
