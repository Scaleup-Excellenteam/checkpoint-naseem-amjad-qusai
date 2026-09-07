import pytest
from fastapi.testclient import TestClient

from server.server import app, manager, rooms


@pytest.fixture(autouse=True)
def clean_state():
    """The server keeps rooms/connections in module-level state, so every
    test starts from an empty server and leaves nothing behind."""
    manager.active_connections.clear()
    for room in rooms.values():
        room.members.clear()

    yield

    manager.active_connections.clear()
    for room in rooms.values():
        room.members.clear()


@pytest.fixture
def client():
    return TestClient(app)


def login(ws, username):
    """Open a session as `username` and return the LOGIN_RESULT payload."""
    ws.send_json({
        "type": "LOGIN",
        "request_id": f"login-{username}",
        "data": {"username": username},
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
