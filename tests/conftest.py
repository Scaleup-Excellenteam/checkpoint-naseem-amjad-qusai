import pytest
from fastapi.testclient import TestClient

from server import server
from server.accounts import AccountStore
from server.auth import AuthService

app, manager, rooms = server.app, server.manager, server.rooms
TEST_PASSWORD = "TestPass1!"


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """The server keeps rooms/connections in module-level state, so every
    test starts from an empty server and leaves nothing behind."""
    manager.active_connections.clear()
    for room in rooms.values():
        room.members.clear()
    monkeypatch.setattr(
        server,
        "auth_service",
        AuthService(AccountStore(tmp_path / "accounts.sqlite3")),
    )

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
