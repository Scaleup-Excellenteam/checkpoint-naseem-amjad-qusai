"""CHAT_MESSAGE routing, room isolation, disconnect cleanup, /health."""
from server.server import manager, rooms

from tests.conftest import chat, join, leave, login


def test_member_sends_to_room_successfully(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")
        join(ws, "pizza")

        chat(ws, "pizza", "hello", request_id="30")

        # the sender is a member, so they get the server-authored copy back
        new_message = ws.receive_json()
        assert new_message["type"] == "NEW_MESSAGE"
        assert new_message["request_id"] is None
        assert new_message["data"] == {
            "sender": "qusai",
            "room": "pizza",
            "content": "hello",
        }

        ack = ws.receive_json()
        assert ack["type"] == "MESSAGE_RESULT"
        assert ack["request_id"] == "30"
        assert ack["data"]["success"] is True


def test_room_members_receive_the_message(client):
    with client.websocket_connect("/ws") as a, \
            client.websocket_connect("/ws") as b:
        login(a, "qusai")
        login(b, "amjad")
        join(a, "pizza")
        join(b, "pizza")

        chat(a, "pizza", "hello everyone")

        for ws in (a, b):
            message = ws.receive_json()
            assert message["type"] == "NEW_MESSAGE"
            assert message["data"]["sender"] == "qusai"
            assert message["data"]["room"] == "pizza"
            assert message["data"]["content"] == "hello everyone"

        assert a.receive_json()["type"] == "MESSAGE_RESULT"


def test_other_room_does_not_receive_the_message(client):
    """pizza = {qusai, amjad}, football = {naseem}.

    Naseem must not see the pizza message. Proven without a timeout: a
    second message is sent to football, and it is the FIRST thing naseem
    ever receives.
    """
    with client.websocket_connect("/ws") as a, \
            client.websocket_connect("/ws") as b, \
            client.websocket_connect("/ws") as c:
        login(a, "qusai")
        login(b, "amjad")
        login(c, "naseem")

        join(a, "pizza")
        join(b, "pizza")
        join(a, "football")
        join(c, "football")

        chat(a, "pizza", "pizza-only")
        assert a.receive_json()["data"]["content"] == "pizza-only"
        assert b.receive_json()["data"]["content"] == "pizza-only"
        assert a.receive_json()["type"] == "MESSAGE_RESULT"

        chat(a, "football", "football-only")

        # Drain the sender's own frames first. qusai is a member of football
        # too, so leaving his copies unread stalls delivery to the others.
        a.receive_json()   # qusai's own NEW_MESSAGE
        a.receive_json()   # qusai's MESSAGE_RESULT

        # naseem's very first message is the football one -> he never got
        # the pizza message
        received = c.receive_json()
        assert received["type"] == "NEW_MESSAGE"
        assert received["data"]["room"] == "football"
        assert received["data"]["content"] == "football-only"


def test_recipient_count_is_room_scoped(client):
    with client.websocket_connect("/ws") as a, \
            client.websocket_connect("/ws") as b, \
            client.websocket_connect("/ws") as c:
        login(a, "qusai")
        login(b, "amjad")
        login(c, "naseem")
        join(a, "pizza")
        join(b, "pizza")
        join(c, "football")

        chat(a, "pizza", "hi")

        a.receive_json()                       # own NEW_MESSAGE
        b.receive_json()
        ack = a.receive_json()

        assert ack["data"]["recipients"] == 2   # not 3


def test_message_stops_after_a_member_leaves(client):
    with client.websocket_connect("/ws") as a, \
            client.websocket_connect("/ws") as b:
        login(a, "qusai")
        login(b, "amjad")
        join(a, "pizza")
        join(b, "pizza")

        leave(b, "pizza")

        chat(a, "pizza", "second message")

        assert a.receive_json()["data"]["content"] == "second message"
        assert a.receive_json()["data"]["recipients"] == 1


def test_sender_not_in_target_room(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")

        chat(ws, "pizza", "hello", request_id="31")
        response = ws.receive_json()

        assert response["type"] == "MESSAGE_RESULT"
        assert response["request_id"] == "31"
        assert response["data"]["success"] is False
        assert response["data"]["reason"] == "NOT_IN_ROOM"


def test_chat_to_unknown_room(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")

        chat(ws, "sushi", "hello", request_id="32")
        response = ws.receive_json()

        assert response["type"] == "MESSAGE_RESULT"
        assert response["data"]["success"] is False
        assert response["data"]["reason"] == "ROOM_NOT_FOUND"


def test_empty_content_is_rejected(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")
        join(ws, "pizza")

        for bad in ("", "   ", "\n\t"):
            chat(ws, "pizza", bad, request_id="33")
            response = ws.receive_json()

            assert response["type"] == "ERROR"
            assert response["request_id"] == "33"
            assert response["data"]["reason"] == "EMPTY_MESSAGE"


def test_non_string_content_does_not_crash_the_server(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")
        join(ws, "pizza")

        for bad in (None, 123, {"a": 1}, ["hi"], True):
            ws.send_json({
                "type": "CHAT_MESSAGE",
                "request_id": "34",
                "data": {"room": "pizza", "content": bad},
            })
            response = ws.receive_json()

            assert response["type"] == "ERROR"
            assert response["data"]["reason"] == "INVALID_MESSAGE"

        # session survived every one of them
        chat(ws, "pizza", "still alive")
        assert ws.receive_json()["data"]["content"] == "still alive"


def test_unauthenticated_chat_is_rejected(client):
    with client.websocket_connect("/ws") as ws:
        chat(ws, "pizza", "hello", request_id="35")
        response = ws.receive_json()

        assert response["type"] == "ERROR"
        assert response["data"]["reason"] == "NOT_AUTHENTICATED"


def test_client_supplied_sender_is_ignored(client):
    """The client must not be able to forge who sent a message."""
    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")
        join(ws, "pizza")

        ws.send_json({
            "type": "CHAT_MESSAGE",
            "request_id": "36",
            "data": {
                "room": "pizza",
                "content": "spoofed",
                "sender": "amjad",
            },
        })

        assert ws.receive_json()["data"]["sender"] == "qusai"


def test_chat_missing_room_field(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")
        join(ws, "pizza")

        ws.send_json({
            "type": "CHAT_MESSAGE",
            "request_id": "37",
            "data": {"content": "hello"},
        })
        response = ws.receive_json()

        assert response["type"] == "ERROR"
        assert response["data"]["reason"] == "MISSING_FIELD"


# --------------------------------------------------------------- DISCONNECT

def test_disconnect_removes_user_from_all_rooms(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")
        join(ws, "pizza")
        join(ws, "football")

        assert rooms["pizza"].has_member("qusai")
        assert rooms["football"].has_member("qusai")

    assert not rooms["pizza"].has_member("qusai")
    assert not rooms["football"].has_member("qusai")
    assert not manager.active_connections


def test_remaining_clients_keep_working_after_a_disconnect(client):
    with client.websocket_connect("/ws") as survivor:
        login(survivor, "amjad")
        join(survivor, "pizza")

        with client.websocket_connect("/ws") as leaver:
            login(leaver, "qusai")
            join(leaver, "pizza")

        # qusai is gone; amjad can still chat
        chat(survivor, "pizza", "still here")

        assert survivor.receive_json()["data"]["content"] == "still here"
        assert survivor.receive_json()["data"]["recipients"] == 1
        assert rooms["pizza"].get_members() == {"amjad"}


def test_reconnect_has_no_stale_membership(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")
        join(ws, "pizza")

    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")

        assert not rooms["pizza"].has_member("qusai")

        # a fresh join must succeed, not report ALREADY_IN_ROOM
        assert join(ws, "pizza")["data"]["success"] is True


# ------------------------------------------------- HEALTH / CONNECTION / IO

def test_health_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_reports_live_state(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")
        join(ws, "pizza")

        body = client.get("/health").json()

        assert body["connected_clients"] == 1
        assert body["rooms"]["pizza"] == 1
        assert body["rooms"]["football"] == 0


def test_multiple_clients_can_connect(client):
    with client.websocket_connect("/ws") as a, \
            client.websocket_connect("/ws") as b, \
            client.websocket_connect("/ws") as c:
        login(a, "qusai")
        login(b, "amjad")
        login(c, "naseem")

        assert len(manager.active_connections) == 3
        assert sorted(manager.active_connections.values()) == [
            "amjad", "naseem", "qusai",
        ]


def test_malformed_envelopes_do_not_kill_the_session(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")

        ws.send_text("this is not json")
        assert ws.receive_json()["data"]["reason"] == "INVALID_MESSAGE"

        ws.send_text('"a bare string"')
        assert ws.receive_json()["data"]["reason"] == "INVALID_MESSAGE"

        ws.send_json([1, 2, 3])
        assert ws.receive_json()["data"]["reason"] == "INVALID_MESSAGE"

        ws.send_json({"request_id": "40"})                    # no type
        response = ws.receive_json()
        assert response["data"]["reason"] == "INVALID_MESSAGE"
        assert response["request_id"] == "40"

        ws.send_json({"type": 5, "request_id": "41"})         # type not a str
        assert ws.receive_json()["data"]["reason"] == "INVALID_MESSAGE"

        ws.send_json({"type": "JOIN_ROOM", "data": "pizza"})  # data not a dict
        assert ws.receive_json()["data"]["reason"] == "INVALID_MESSAGE"

        ws.send_json({"type": "DANCE", "request_id": "42"})
        response = ws.receive_json()
        assert response["data"]["reason"] == "UNKNOWN_MESSAGE_TYPE"
        assert response["request_id"] == "42"

        # after all that abuse the session is still fully usable
        assert join(ws, "pizza")["data"]["success"] is True


def test_message_without_data_field_is_accepted(client):
    """'data' is optional; a JOIN_ROOM without it is a MISSING_FIELD, not
    a crash."""
    with client.websocket_connect("/ws") as ws:
        login(ws, "qusai")

        ws.send_json({"type": "JOIN_ROOM", "request_id": "43"})

        assert ws.receive_json()["data"]["reason"] == "MISSING_FIELD"


def test_non_string_username_does_not_crash_login(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "type": "LOGIN",
            "request_id": "44",
            "data": {"username": 12345},
        })
        response = ws.receive_json()

        assert response["type"] == "LOGIN_RESULT"
        assert response["data"]["reason"] == "INVALID_CREDENTIALS"

        # session still usable
        assert login(ws, "qusai")["data"]["success"] is True
