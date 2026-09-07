"""SQLite persistence: schema, rooms, messages, security events.

Every test runs against a temporary database provided by the `database_path`
fixture, so the developer's real server/accounts.sqlite3 is never touched.
"""
import sqlite3

import pytest

from server import database, server
from server.accounts import Account, AccountStore
from server.message_store import MessageStore
from server.room_store import RoomStore
from server.security_decision import Decision, SecurityDecision
from server.security_store import SecurityEventStore

from tests.conftest import join, leave, list_rooms, login


SECRET = "SENSITIVE_SECRET_SAUCE_never_store_me"


def columns(path, table):
    connection = sqlite3.connect(path)
    try:
        return [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
    finally:
        connection.close()


def table_names(path):
    connection = sqlite3.connect(path)
    try:
        return sorted(row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"))
    finally:
        connection.close()


# ------------------------------------------------------------------ schema

def test_every_table_is_created(database_path):
    assert table_names(database_path) == [
        "accounts", "messages", "rooms", "security_events",
    ]


def test_indexes_are_created(database_path):
    connection = sqlite3.connect(database_path)
    names = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    connection.close()
    assert "idx_messages_room_id" in names
    assert "idx_security_events_created" in names


def test_security_events_has_no_content_column(database_path):
    """Structural guarantee: blocked text cannot be stored even by mistake."""
    assert columns(database_path, "security_events") == [
        "id", "username", "event", "decision", "reason", "verdict",
        "category", "score", "created_at",
    ]
    assert "content" not in columns(database_path, "security_events")


def test_accounts_schema_is_unchanged(database_path):
    assert columns(database_path, "accounts") == [
        "username", "password_hash", "salt", "iterations",
    ]


def test_initialize_schema_is_idempotent(database_path):
    database.initialize_schema(database_path)
    database.initialize_schema(database_path)
    assert table_names(database_path) == [
        "accounts", "messages", "rooms", "security_events",
    ]


def test_foreign_keys_are_enforced(database_path):
    store = MessageStore(database_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.add("no_such_room", "nobody", "hello")


# ------------------------------------------------------------------- rooms

def test_default_rooms_are_persisted(database_path):
    assert set(RoomStore(database_path).all_names()) >= {"pizza", "football"}


def test_rooms_survive_a_new_store_instance(database_path):
    RoomStore(database_path).create("archive", created_by=None)

    # a brand-new store object, as if the process had restarted
    assert "archive" in RoomStore(database_path).all_names()


def test_rooms_reload_into_memory_like_startup(database_path):
    """server.load_rooms() rebuilds the runtime dict from the database."""
    RoomStore(database_path).create("archive")

    reloaded = server.load_rooms(RoomStore(database_path))

    assert set(reloaded) >= {"pizza", "football", "archive"}
    assert reloaded["archive"].name == "archive"
    # freshly loaded rooms start empty: membership is not persisted
    assert reloaded["archive"].get_members() == set()


def test_ensure_defaults_is_idempotent(database_path):
    store = RoomStore(database_path)
    store.ensure_defaults(["pizza", "football"])
    store.ensure_defaults(["pizza", "football"])
    assert sorted(store.all_names()) == ["football", "pizza"]


def test_membership_is_not_persisted(client, database_path):
    """Joining writes nothing durable; only the room definition is stored."""
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        join(ws, "pizza")
        assert server.rooms["pizza"].has_member("naseem")

    connection = sqlite3.connect(database_path)
    rows = connection.execute("SELECT * FROM rooms WHERE name = 'pizza'").fetchall()
    connection.close()

    # one row, and no column anywhere holds the member's name
    assert len(rows) == 1
    assert "naseem" not in str(rows)
    assert not server.rooms["pizza"].has_member("naseem")


# ---------------------------------------------------------------- messages

def test_allowed_message_is_persisted(client, database_path):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        join(ws, "pizza")
        ws.send_json({"type": "CHAT_MESSAGE", "request_id": "1",
                      "data": {"room": "pizza", "content": "hello everyone"}})
        ws.receive_json()   # NEW_MESSAGE
        ws.receive_json()   # MESSAGE_RESULT

    stored = MessageStore(database_path).get_recent("pizza")
    assert len(stored) == 1
    assert stored[0]["content"] == "hello everyone"
    assert stored[0]["room"] == "pizza"


def test_persisted_sender_is_server_resolved(client, database_path):
    """A forged `sender` in the payload must not reach the database."""
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        join(ws, "pizza")
        ws.send_json({"type": "CHAT_MESSAGE", "request_id": "1",
                      "data": {"room": "pizza", "content": "hi",
                               "sender": "qusai"}})
        ws.receive_json()
        ws.receive_json()

    stored = MessageStore(database_path).get_recent("pizza")
    assert stored[0]["sender"] == "naseem"


def test_multiple_messages_preserve_order_and_ids(client, database_path):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        join(ws, "pizza")
        for text in ("first", "second", "third"):
            ws.send_json({"type": "CHAT_MESSAGE", "request_id": "1",
                          "data": {"room": "pizza", "content": text}})
            ws.receive_json()
            ws.receive_json()

    stored = MessageStore(database_path).get_recent("pizza")
    assert [m["content"] for m in stored] == ["third", "second", "first"]
    ids = [m["id"] for m in stored]
    assert ids == sorted(ids, reverse=True)


def test_messages_are_stored_per_room(client, database_path):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        join(ws, "pizza")
        join(ws, "football")
        for room, text in (("pizza", "p"), ("football", "f")):
            ws.send_json({"type": "CHAT_MESSAGE", "request_id": "1",
                          "data": {"room": room, "content": text}})
            ws.receive_json()
            ws.receive_json()

    store = MessageStore(database_path)
    assert [m["content"] for m in store.get_recent("pizza")] == ["p"]
    assert [m["content"] for m in store.get_recent("football")] == ["f"]


def test_rejected_message_is_not_persisted(client, database_path):
    """NOT_IN_ROOM never reaches the persistence step."""
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        ws.send_json({"type": "CHAT_MESSAGE", "request_id": "1",
                      "data": {"room": "pizza", "content": SECRET}})
        assert ws.receive_json()["data"]["reason"] == "NOT_IN_ROOM"

    assert MessageStore(database_path).count() == 0


def test_invalid_message_is_not_persisted(client, database_path):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        join(ws, "pizza")
        ws.send_json({"type": "CHAT_MESSAGE", "request_id": "1",
                      "data": {"room": "pizza", "content": "   "}})
        ws.receive_json()

    assert MessageStore(database_path).count() == 0


# ----------------------------------------------- blocked messages: DLP / bot

def _force_block(monkeypatch, reason):
    """Make the security pipeline BLOCK without touching detection logic."""
    def blocked(*args, **kwargs):
        return SecurityDecision(Decision.BLOCK, reason, verdict="blocked_for_test")
    monkeypatch.setattr(server.SecurityPipeline, "evaluate", blocked)


@pytest.mark.parametrize("reason", ["DLP_SENSITIVE_CONTENT", "MALICIOUS_ADDRESS"])
def test_blocked_message_is_never_persisted(client, database_path, monkeypatch,
                                            reason):
    _force_block(monkeypatch, reason)

    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        join(ws, "pizza")
        ws.send_json({"type": "CHAT_MESSAGE", "request_id": "1",
                      "data": {"room": "pizza", "content": SECRET}})
        result = ws.receive_json()

    assert result["type"] == "MESSAGE_RESULT"
    assert result["data"]["decision"] == "BLOCK"
    assert result["data"]["reason"] == reason

    # not stored, and the raw text is nowhere in the database file
    assert MessageStore(database_path).count() == 0
    assert SECRET.encode() not in database_path.read_bytes()


def test_blocked_message_is_not_delivered_to_the_room(client, monkeypatch):
    _force_block(monkeypatch, "DLP_SENSITIVE_CONTENT")

    with client.websocket_connect("/ws") as a, \
            client.websocket_connect("/ws") as b:
        login(a, "naseem")
        login(b, "qusai")
        join(a, "pizza")
        join(b, "pizza")

        a.send_json({"type": "CHAT_MESSAGE", "request_id": "1",
                     "data": {"room": "pizza", "content": SECRET}})
        assert a.receive_json()["data"]["decision"] == "BLOCK"

        # qusai's first frame is his own later message, so he never saw the block
        b.send_json({"type": "CHAT_MESSAGE", "request_id": "2",
                     "data": {"room": "pizza", "content": "clean"}})
        # b's own send is also blocked by the patched pipeline
        assert b.receive_json()["data"]["decision"] == "BLOCK"


# --------------------------------------------------------- security events

def test_security_event_metadata_can_be_persisted(database_path):
    store = SecurityEventStore(database_path)
    store.record("dlp", "BLOCK", "naseem", reason="DLP_SENSITIVE_CONTENT",
                 verdict="sensitive_content_detected", category="recipe",
                 score=0.93)

    event = store.recent(1)[0]
    assert event["event"] == "dlp"
    assert event["decision"] == "BLOCK"
    assert event["username"] == "naseem"
    assert event["category"] == "recipe"
    assert event["score"] == pytest.approx(0.93)
    assert event["created_at"]


def test_score_and_category_are_nullable(database_path):
    store = SecurityEventStore(database_path)
    store.record("anti_bot", "ALLOW", "naseem")

    event = store.recent(1)[0]
    assert event["category"] is None
    assert event["score"] is None


def test_username_is_nullable_for_pre_auth_events(database_path):
    store = SecurityEventStore(database_path)
    store.record("anti_bot", "ALLOW")
    assert store.recent(1)[0]["username"] is None


def test_pipeline_decisions_are_recorded(client, database_path):
    """A real CHAT_MESSAGE stores its security metadata, no re-evaluation."""
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        join(ws, "pizza")
        ws.send_json({"type": "CHAT_MESSAGE", "request_id": "1",
                      "data": {"room": "pizza", "content": "hello"}})
        ws.receive_json()
        ws.receive_json()

    events = SecurityEventStore(database_path).recent(10)
    assert {e["event"] for e in events} == {"anti_bot", "dlp"}
    assert all(e["decision"] == "ALLOW" for e in events)
    assert all(e["username"] == "naseem" for e in events)


def test_blocked_event_is_recorded_without_raw_content(client, database_path,
                                                       monkeypatch):
    store = SecurityEventStore(database_path)
    store.record("dlp", "BLOCK", "naseem", reason="DLP_SENSITIVE_CONTENT",
                 category="recipe", score=0.99)

    assert store.recent(1)[0]["decision"] == "BLOCK"
    assert SECRET.encode() not in database_path.read_bytes()


def test_sensitive_text_never_reaches_the_events_table(database_path):
    """Even a caller that tries cannot smuggle content in: there is no column."""
    store = SecurityEventStore(database_path)
    with pytest.raises(TypeError):
        store.record("dlp", "BLOCK", "naseem", content=SECRET)


# ------------------------------------------------------- existing behavior

def test_account_signup_and_login_still_work(client, database_path):
    with client.websocket_connect("/ws") as ws:
        assert login(ws, "naseem")["data"]["success"] is True

    assert AccountStore(database_path).get("naseem") is not None


def test_account_store_still_writes_to_the_shared_database(database_path):
    store = AccountStore(database_path)
    assert store.create(Account("solo", b"hash", b"salt", 600_000)) is True
    assert store.create(Account("solo", b"hash", b"salt", 600_000)) is False
    assert store.get("solo").username == "solo"


def test_join_leave_and_list_still_work(client):
    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        assert join(ws, "pizza")["data"]["success"] is True

        listed = {r["name"]: r for r in list_rooms(ws)["data"]["rooms"]}
        assert listed["pizza"]["joined"] is True
        assert listed["pizza"]["members"] == 1

        assert leave(ws, "pizza")["data"]["success"] is True


def test_room_isolation_still_works(client):
    with client.websocket_connect("/ws") as a, \
            client.websocket_connect("/ws") as b:
        login(a, "naseem")
        login(b, "qusai")
        join(a, "pizza")
        join(b, "football")

        a.send_json({"type": "CHAT_MESSAGE", "request_id": "1",
                     "data": {"room": "pizza", "content": "pizza-only"}})
        assert a.receive_json()["data"]["content"] == "pizza-only"
        assert a.receive_json()["type"] == "MESSAGE_RESULT"

        b.send_json({"type": "CHAT_MESSAGE", "request_id": "2",
                     "data": {"room": "football", "content": "football-only"}})
        received = b.receive_json()
        assert received["data"]["room"] == "football"
        assert received["data"]["content"] == "football-only"
