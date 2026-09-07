"""JOIN_ROOM / LEAVE_ROOM over a real WebSocket session."""
from server.server import rooms

from tests.conftest import join, leave, login


# ---------------------------------------------------------------- JOIN_ROOM

def test_authenticated_join_succeeds(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")

        response = join(ws, "pizza", request_id="10")

        assert response["type"] == "JOIN_ROOM_RESULT"
        assert response["request_id"] == "10"
        assert response["data"]["success"] is True
        assert response["data"]["room"] == "pizza"
        assert rooms["pizza"].has_member("qusai")


def test_duplicate_join_returns_already_in_room(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")
        join(ws, "pizza")

        response = join(ws, "pizza", request_id="11")

        assert response["type"] == "JOIN_ROOM_RESULT"
        assert response["data"]["success"] is False
        assert response["data"]["reason"] == "ALREADY_IN_ROOM"


def test_join_unknown_room_returns_room_not_found(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")

        response = join(ws, "sushi", request_id="12")

        assert response["type"] == "JOIN_ROOM_RESULT"
        assert response["data"]["success"] is False
        assert response["data"]["reason"] == "ROOM_NOT_FOUND"


def test_unauthenticated_join_is_rejected(client):
    with client.websocket_connect("/ws") as ws:
        response = join(ws, "pizza", request_id="13")

        assert response["type"] == "ERROR"
        assert response["request_id"] == "13"
        assert response["data"]["reason"] == "NOT_AUTHENTICATED"
        assert not rooms["pizza"].members


def test_invalid_room_input_does_not_crash_the_server(client):
    """A missing room, a non-string room and a blank room are all
    MISSING_FIELD, and the session stays usable afterwards."""
    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")

        for bad_data in ({}, {"room": 123}, {"room": "   "}, {"room": None},
                         {"room": ["pizza"]}):
            ws.send_json({
                "type": "JOIN_ROOM",
                "request_id": "14",
                "data": bad_data,
            })
            response = ws.receive_json()

            assert response["type"] == "ERROR"
            assert response["data"]["reason"] == "MISSING_FIELD"

        # the connection still works
        assert join(ws, "pizza")["data"]["success"] is True


# --------------------------------------------------------------- LEAVE_ROOM

def test_valid_leave(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")
        join(ws, "pizza")

        response = leave(ws, "pizza", request_id="20")

        assert response["type"] == "LEAVE_ROOM_RESULT"
        assert response["request_id"] == "20"
        assert response["data"]["success"] is True
        assert response["data"]["room"] == "pizza"
        assert not rooms["pizza"].has_member("qusai")


def test_second_leave_returns_not_in_room(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")
        join(ws, "pizza")
        leave(ws, "pizza")

        response = leave(ws, "pizza", request_id="21")

        assert response["type"] == "LEAVE_ROOM_RESULT"
        assert response["data"]["success"] is False
        assert response["data"]["reason"] == "NOT_IN_ROOM"


def test_leave_unknown_room(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")

        response = leave(ws, "sushi", request_id="22")

        assert response["type"] == "LEAVE_ROOM_RESULT"
        assert response["data"]["success"] is False
        assert response["data"]["reason"] == "ROOM_NOT_FOUND"


def test_unauthenticated_leave_is_rejected(client):
    with client.websocket_connect("/ws") as ws:
        response = leave(ws, "pizza", request_id="23")

        assert response["type"] == "ERROR"
        assert response["data"]["reason"] == "NOT_AUTHENTICATED"


def test_leave_invalid_room_input(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")

        ws.send_json({
            "type": "LEAVE_ROOM",
            "request_id": "24",
            "data": {"room": 99},
        })
        response = ws.receive_json()

        assert response["type"] == "ERROR"
        assert response["data"]["reason"] == "MISSING_FIELD"


def test_leaving_one_room_keeps_other_memberships(client):
    """Multi-room membership is intentional: leaving pizza must not touch
    football."""
    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")
        join(ws, "pizza")
        join(ws, "football")

        leave(ws, "pizza")

        assert not rooms["pizza"].has_member("qusai")
        assert rooms["football"].has_member("qusai")
