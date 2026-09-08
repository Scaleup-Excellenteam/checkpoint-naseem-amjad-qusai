"""CREATE_ROOM: protocol, authorization, naming policy and persistence.

Every test runs against the temporary database from the shared `database_path`
fixture, and `clean_state` restores the module-level room registry afterwards,
so a room created here never leaks into another test.
"""
import sqlite3

import pytest

from server import server
from server.room_store import RoomStore
from server.validation import MAX_ROOM_NAME_LENGTH

from tests.conftest import chat, create_room, join, leave, list_rooms, login


def room_rows(path, name):
    connection = sqlite3.connect(path)
    try:
        return connection.execute(
            "SELECT name, created_by, created_at FROM rooms WHERE name = ?",
            (name,),
        ).fetchall()
    finally:
        connection.close()


def by_name(response):
    return {r["name"]: r for r in response["data"]["rooms"]}


# ----------------------------------------------------------------- success

def test_authenticated_user_can_create_a_room(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")

        response = create_room(ws, "gaming")

        assert response["type"] == "CREATE_ROOM_RESULT"
        assert response["data"] == {"success": True, "room": "gaming"}


def test_successful_response_echoes_the_request_id(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")

        response = create_room(ws, "gaming", request_id="70")

        assert response["request_id"] == "70"
        assert response["data"]["success"] is True


def test_failure_response_echoes_the_request_id(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")

        response = create_room(ws, "my room", request_id="71")

        assert response["type"] == "CREATE_ROOM_RESULT"
        assert response["request_id"] == "71"
        assert response["data"]["success"] is False


def test_surrounding_whitespace_is_stripped(client):
    """Stripping is the only transformation allowed on the name."""
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")

        assert create_room(ws, "  gaming  ")["data"]["room"] == "gaming"
        assert "gaming" in server.rooms


@pytest.mark.parametrize("name", [
    "pizza2", "football_league", "gaming-room", "room_1",
    "a", "A-1_b", "x" * MAX_ROOM_NAME_LENGTH,
])
def test_valid_names_are_accepted(client, name):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")

        assert create_room(ws, name)["data"] == {"success": True, "room": name}


# --------------------------------------------------------------- rejection

def test_unauthenticated_user_cannot_create_a_room(client):
    with client.websocket_connect("/ws") as ws:
        response = create_room(ws, "gaming", request_id="72")

        assert response["type"] == "ERROR"
        assert response["request_id"] == "72"
        assert response["data"]["reason"] == "NOT_AUTHENTICATED"

    assert "gaming" not in server.rooms


def test_unauthenticated_attempt_persists_nothing(client, database_path):
    with client.websocket_connect("/ws") as ws:
        create_room(ws, "gaming")

    assert room_rows(database_path, "gaming") == []


def test_create_room_is_in_the_centralized_auth_gate():
    """The gate, not a per-handler check, is what protects CREATE_ROOM."""
    assert "CREATE_ROOM" in server.AUTH_REQUIRED_TYPES


@pytest.mark.parametrize("name,label", [
    ("", "empty"),
    ("   ", "whitespace only"),
    ("my room", "contains a space"),
    ("room!!!", "invalid punctuation"),
    ("room.name", "dot"),
    ("room/name", "path separator"),
    ("<script>", "markup"),
    ("x" * (MAX_ROOM_NAME_LENGTH + 1), "too long"),
    ("  " + "x" * (MAX_ROOM_NAME_LENGTH + 1) + "  ", "too long after strip"),
])
def test_invalid_names_are_rejected(client, name, label):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")

        response = create_room(ws, name)

        assert response["type"] == "CREATE_ROOM_RESULT", label
        assert response["data"] == {
            "success": False,
            "reason": "INVALID_ROOM_NAME",
        }, label


@pytest.mark.parametrize("name", [None, 12, ["gaming"], {"name": "gaming"}])
def test_missing_or_non_string_name_is_rejected(client, name):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")

        ws.send_json({"type": "CREATE_ROOM", "request_id": "73",
                      "data": {} if name is None else {"name": name}})
        response = ws.receive_json()

        assert response["type"] == "CREATE_ROOM_RESULT"
        assert response["data"]["success"] is False
        assert response["data"]["reason"] == "INVALID_ROOM_NAME"


def test_an_invalid_name_is_never_persisted(client, database_path):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        create_room(ws, "my room")

    assert room_rows(database_path, "my room") == []
    assert "my room" not in server.rooms


# --------------------------------------------------------------- duplicates

def test_duplicate_room_is_rejected(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        assert create_room(ws, "gaming")["data"]["success"] is True

        response = create_room(ws, "gaming", request_id="74")

        assert response["type"] == "CREATE_ROOM_RESULT"
        assert response["request_id"] == "74"
        assert response["data"] == {
            "success": False,
            "reason": "ROOM_ALREADY_EXISTS",
        }


def test_a_default_room_cannot_be_recreated(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")

        assert create_room(ws, "pizza")["data"]["reason"] == "ROOM_ALREADY_EXISTS"


def test_duplicate_does_not_overwrite_the_existing_room(client, database_path):
    """The original creator, creation time and members all survive."""
    with client.websocket_connect("/ws") as a:
        login(a, "naseem")
        create_room(a, "gaming")
        join(a, "gaming")
        before = room_rows(database_path, "gaming")

        with client.websocket_connect("/ws") as b:
            login(b, "qusai")
            assert create_room(b, "gaming")["data"]["success"] is False

        assert room_rows(database_path, "gaming") == before
        assert before[0][1] == "naseem"
        assert server.rooms["gaming"].get_members() == {"naseem"}


def test_a_row_the_registry_never_saw_is_still_not_overwritten(
        client, database_path):
    """The store's PRIMARY KEY is the second line of defence behind the
    in-memory existence check."""
    RoomStore(database_path).create("gaming", created_by=None)
    assert "gaming" not in server.rooms

    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")

        assert create_room(ws, "gaming")["data"] == {
            "success": False,
            "reason": "ROOM_ALREADY_EXISTS",
        }

    assert room_rows(database_path, "gaming")[0][1] is None
    assert "gaming" not in server.rooms


# -------------------------------------------------------------- persistence

def test_room_is_persisted_with_its_creator(client, database_path):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        create_room(ws, "gaming")

    rows = room_rows(database_path, "gaming")
    assert len(rows) == 1
    name, created_by, created_at = rows[0]
    assert name == "gaming"
    assert created_by == "naseem"
    # created_at comes from the schema default; the handler never sets it.
    assert created_at


def test_room_survives_a_reload_from_the_database(client, database_path):
    """Exactly what startup does: rebuild the registry from storage."""
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        create_room(ws, "gaming")
        join(ws, "gaming")

    reloaded = server.load_rooms(RoomStore(database_path))

    assert set(reloaded) >= {"pizza", "football", "gaming"}
    assert reloaded["gaming"].name == "gaming"
    # membership is not persisted, so a reloaded room starts empty
    assert reloaded["gaming"].get_members() == set()


def test_membership_of_a_created_room_is_not_persisted(client, database_path):
    """created_by records who made the room; joining it writes nothing."""
    with client.websocket_connect("/ws") as a, \
            client.websocket_connect("/ws") as b:
        login(a, "naseem")
        login(b, "qusai")
        create_room(a, "gaming")
        after_creation = room_rows(database_path, "gaming")

        join(a, "gaming")
        join(b, "gaming")

        assert room_rows(database_path, "gaming") == after_creation
        # qusai only ever joined, so his name is nowhere in the row
        assert "qusai" not in str(after_creation)

    # and the membership disappears with the connections
    assert server.rooms["gaming"].get_members() == set()


# ------------------------------------------------------- database failures

def explode(*args, **kwargs):
    raise sqlite3.OperationalError("disk I/O error: /var/lib/tspo/secret.sqlite3")


def test_database_failure_does_not_create_the_room_in_memory(client, monkeypatch):
    monkeypatch.setattr(server.room_store, "create", explode)

    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        create_room(ws, "gaming")

        assert "gaming" not in server.rooms
        # the connection is still usable, and the room is not listed
        assert "gaming" not in by_name(list_rooms(ws))


def test_database_failure_returns_a_safe_error(client, monkeypatch):
    monkeypatch.setattr(server.room_store, "create", explode)

    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")

        response = create_room(ws, "gaming", request_id="75")

    assert response["type"] == "CREATE_ROOM_RESULT"
    assert response["request_id"] == "75"
    assert response["data"] == {
        "success": False,
        "reason": "INTERNAL_SERVER_ERROR",
    }
    # no SQLite detail and no filesystem path reach the client
    assert "sqlite" not in repr(response).lower()
    assert "disk I/O" not in repr(response)
    assert "/var/lib" not in repr(response)


def test_database_failure_is_logged_as_safe_metadata(client, monkeypatch, caplog):
    monkeypatch.setattr(server.room_store, "create", explode)

    with caplog.at_level("ERROR"), client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        create_room(ws, "gaming")

    record = next(r for r in caplog.records
                  if "Could not persist room" in r.getMessage())
    # safe metadata only: the user and the room, plus a server-side traceback
    assert record.args == ("naseem", "gaming")
    assert record.exc_info is not None


def test_a_room_created_after_a_failure_still_works(client, monkeypatch):
    """A failed attempt leaves no state that blocks a later retry."""
    working_create = server.room_store.create
    monkeypatch.setattr(server.room_store, "create", explode)

    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        assert create_room(ws, "gaming")["data"]["success"] is False

        # only the store is repaired; the rest of the test wiring stays put
        monkeypatch.setattr(server.room_store, "create", working_create)
        assert create_room(ws, "gaming")["data"]["success"] is True


# ------------------------------------------------------------- LIST_ROOMS

def test_new_room_appears_immediately_in_list_rooms(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        create_room(ws, "gaming")

        listed = by_name(list_rooms(ws))

        assert "gaming" in listed
        assert listed["gaming"] == {"name": "gaming", "members": 0,
                                    "joined": False}


def test_new_room_is_visible_to_other_connections(client):
    with client.websocket_connect("/ws") as a, \
            client.websocket_connect("/ws") as b:
        login(a, "naseem")
        login(b, "qusai")
        create_room(a, "gaming")

        assert "gaming" in by_name(list_rooms(b))


def test_list_rooms_stays_sorted_after_a_creation(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        create_room(ws, "arcade")

        names = [r["name"] for r in list_rooms(ws)["data"]["rooms"]]

        assert names == sorted(names)
        assert names[0] == "arcade"


def test_list_rooms_remains_the_only_catalog(client):
    """The listing is still built from the server's live room state."""
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        create_room(ws, "gaming")

        assert set(by_name(list_rooms(ws))) == set(server.rooms)


# ------------------------------------------------------------- membership

def test_creating_a_room_does_not_join_the_creator(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        create_room(ws, "gaming")

        assert server.rooms["gaming"].get_members() == set()
        assert by_name(list_rooms(ws))["gaming"]["joined"] is False


def test_the_creator_cannot_chat_before_joining(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        create_room(ws, "gaming")

        chat(ws, "gaming", "hello")

        assert ws.receive_json()["data"]["reason"] == "NOT_IN_ROOM"


def test_the_creator_can_join_the_new_room_separately(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        create_room(ws, "gaming")

        assert join(ws, "gaming")["data"] == {"success": True, "room": "gaming"}
        assert server.rooms["gaming"].has_member("naseem")


def test_a_created_room_routes_chat_like_any_other(client):
    with client.websocket_connect("/ws") as a, \
            client.websocket_connect("/ws") as b:
        login(a, "naseem")
        login(b, "qusai")
        create_room(a, "gaming")
        join(a, "gaming")
        join(b, "gaming")

        chat(a, "gaming", "hello gamers")

        assert a.receive_json()["data"]["content"] == "hello gamers"
        assert a.receive_json()["type"] == "MESSAGE_RESULT"
        received = b.receive_json()
        assert received["type"] == "NEW_MESSAGE"
        assert received["data"]["room"] == "gaming"


# -------------------------------------------------- existing behavior kept

def test_pizza_and_football_are_untouched_by_a_creation(client, database_path):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        create_room(ws, "gaming")

        assert join(ws, "pizza")["data"]["success"] is True
        listed = by_name(list_rooms(ws))
        assert listed["pizza"]["joined"] is True
        assert listed["pizza"]["members"] == 1
        assert listed["football"]["members"] == 0
        assert leave(ws, "pizza")["data"]["success"] is True

    assert set(RoomStore(database_path).all_names()) == {
        "pizza", "football", "gaming",
    }


def test_room_isolation_holds_between_a_default_and_a_created_room(client):
    with client.websocket_connect("/ws") as a, \
            client.websocket_connect("/ws") as b:
        login(a, "naseem")
        login(b, "qusai")
        create_room(a, "gaming")
        join(a, "gaming")
        join(b, "pizza")

        chat(a, "gaming", "gaming-only")
        assert a.receive_json()["data"]["content"] == "gaming-only"
        assert a.receive_json()["type"] == "MESSAGE_RESULT"

        # qusai's first frame is his own pizza message: nothing leaked
        chat(b, "pizza", "pizza-only")
        received = b.receive_json()
        assert received["type"] == "NEW_MESSAGE"
        assert received["data"]["room"] == "pizza"
        assert received["data"]["content"] == "pizza-only"


def test_an_unknown_room_type_is_still_unknown(client):
    """CREATE_ROOM is dispatched by name only; nothing else was opened up."""
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        ws.send_json({"type": "CREATE_ROOMS", "request_id": "76", "data": {}})

        assert ws.receive_json()["data"]["reason"] == "UNKNOWN_MESSAGE_TYPE"
