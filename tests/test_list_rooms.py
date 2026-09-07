"""LIST_ROOMS / ROOMS_LIST, and the centralized authentication gate."""
import pytest

from server.server import AUTH_REQUIRED_TYPES, rooms

from tests.conftest import chat, join, list_rooms, login


def by_name(response):
    return {r["name"]: r for r in response["data"]["rooms"]}


# ------------------------------------------------------------- LIST_ROOMS

def test_authenticated_user_can_list_rooms(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")

        response = list_rooms(ws)

        assert response["type"] == "ROOMS_LIST"
        assert isinstance(response["data"]["rooms"], list)


def test_unauthenticated_list_rooms_is_rejected(client):
    with client.websocket_connect("/ws") as ws:
        response = list_rooms(ws, request_id="50")

        assert response["type"] == "ERROR"
        assert response["request_id"] == "50"
        assert response["data"]["reason"] == "NOT_AUTHENTICATED"


def test_response_contains_every_server_room(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")

        listed = by_name(list_rooms(ws))

        assert set(listed) == set(rooms)


def test_rooms_are_sorted_alphabetically(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")

        names = [r["name"] for r in list_rooms(ws)["data"]["rooms"]]

        assert names == sorted(names)


def test_member_counts_and_joined_flag_are_correct(client):
    """naseem + amjad in pizza, qusai in football; naseem asks."""
    with client.websocket_connect("/ws") as a, \
            client.websocket_connect("/ws") as b, \
            client.websocket_connect("/ws") as c:
        login(a, "naseem")
        login(b, "amjad")
        login(c, "qusai")
        join(a, "pizza")
        join(b, "pizza")
        join(c, "football")

        listed = by_name(list_rooms(a))

        assert listed["pizza"]["members"] == 2
        assert listed["pizza"]["joined"] is True
        assert listed["football"]["members"] == 1
        assert listed["football"]["joined"] is False


def test_joined_flag_is_per_requesting_user(client):
    """Same server state, two requesters, different `joined` answers."""
    with client.websocket_connect("/ws") as a, \
            client.websocket_connect("/ws") as b:
        login(a, "naseem")
        login(b, "qusai")
        join(a, "pizza")
        join(b, "football")

        for ws, mine, theirs in ((a, "pizza", "football"),
                                 (b, "football", "pizza")):
            listed = by_name(list_rooms(ws))
            assert listed[mine]["joined"] is True
            assert listed[theirs]["joined"] is False


def test_request_id_is_echoed(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")

        assert list_rooms(ws, request_id="51")["request_id"] == "51"


def test_member_usernames_are_never_exposed(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        join(ws, "pizza")

        response = list_rooms(ws)

        assert "naseem" not in repr(response)
        for room in response["data"]["rooms"]:
            assert set(room) == {"name", "members", "joined"}


def test_server_never_sends_an_active_flag(client):
    """The active room is a client-side concept only."""
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        join(ws, "pizza")

        for room in list_rooms(ws)["data"]["rooms"]:
            assert "active" not in room


def test_listing_does_not_change_membership(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        join(ws, "pizza")

        before = {n: r.get_members() for n, r in rooms.items()}
        list_rooms(ws)
        list_rooms(ws)

        assert {n: r.get_members() for n, r in rooms.items()} == before


def test_listing_does_not_change_message_routing(client):
    with client.websocket_connect("/ws") as a, \
            client.websocket_connect("/ws") as b:
        login(a, "naseem")
        login(b, "qusai")
        join(a, "pizza")
        join(b, "football")

        list_rooms(a)

        # still isolated: qusai's first frame is his own football message
        chat(a, "pizza", "pizza-only")
        assert a.receive_json()["data"]["content"] == "pizza-only"
        assert a.receive_json()["type"] == "MESSAGE_RESULT"

        chat(b, "football", "football-only")
        received = b.receive_json()
        assert received["type"] == "NEW_MESSAGE"
        assert received["data"]["room"] == "football"
        assert received["data"]["content"] == "football-only"


# ------------------------------------------- centralized auth gate itself

@pytest.mark.parametrize("message_type,data", [
    ("JOIN_ROOM", {"room": "pizza"}),
    ("LEAVE_ROOM", {"room": "pizza"}),
    ("CHAT_MESSAGE", {"room": "pizza", "content": "hi"}),
    ("LIST_ROOMS", {}),
])
def test_every_protected_type_is_gated(client, message_type, data):
    """One table-driven check that the gate covers all protected types."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "type": message_type,
            "request_id": "60",
            "data": data,
        })
        response = ws.receive_json()

        assert response["type"] == "ERROR"
        assert response["request_id"] == "60"
        assert response["data"]["reason"] == "NOT_AUTHENTICATED"


def test_login_is_public(client):
    """The gate must never make LOGIN unreachable."""
    assert "LOGIN" not in AUTH_REQUIRED_TYPES

    with client.websocket_connect("/ws") as ws:
        assert login(ws, "naseem")["data"]["success"] is True


def test_gate_runs_after_envelope_validation(client):
    """A malformed envelope is still INVALID_MESSAGE, not NOT_AUTHENTICATED,
    and an unknown type is still UNKNOWN_MESSAGE_TYPE."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"request_id": "61"})
        assert ws.receive_json()["data"]["reason"] == "INVALID_MESSAGE"

        ws.send_json({"type": "DANCE", "request_id": "62"})
        assert ws.receive_json()["data"]["reason"] == "UNKNOWN_MESSAGE_TYPE"


def test_authenticated_user_passes_the_gate_for_every_protected_type(client):
    """The gate must not block legitimate traffic."""
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")

        assert join(ws, "pizza")["data"]["success"] is True
        assert list_rooms(ws)["type"] == "ROOMS_LIST"

        chat(ws, "pizza", "hello")
        assert ws.receive_json()["type"] == "NEW_MESSAGE"
        assert ws.receive_json()["data"]["success"] is True
