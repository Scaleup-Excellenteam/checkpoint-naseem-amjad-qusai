"""Two guarantees that must not regress:

1. security events persist regardless of logging level
2. a message that cannot be stored is never delivered
"""
import logging

import pytest

from server import server
from server.anti_bot import AntiBotService
from server.dlp import DLPService
from server.message_store import MessageStore
from server.security_decision import Decision
from server.security_pipeline import SecurityPipeline
from server.security_store import SecurityEventStore

from tests.conftest import join, login


SECRET = "PERSISTENCE_SECRET_never_log_or_leak_me"


# ------------- 1. persistence is independent of the logging level -------------

@pytest.fixture
def logging_silenced():
    """Turn console/root logging fully off, the way production might."""
    root = logging.getLogger()
    security = logging.getLogger("tspo.security")
    previous = (root.level, security.level, security.disabled)
    root.setLevel(logging.CRITICAL)
    security.setLevel(logging.CRITICAL)
    security.disabled = True
    yield
    root.setLevel(previous[0])
    security.setLevel(previous[1])
    security.disabled = previous[2]


def send_chat(client, content, username="naseem"):
    with client.websocket_connect("/ws") as ws:
        login(ws, username)
        join(ws, "pizza")
        ws.send_json({"type": "CHAT_MESSAGE", "request_id": "1",
                      "data": {"room": "pizza", "content": content}})
        return [ws.receive_json(), ws.receive_json()]


def test_allow_events_persist_with_logging_disabled(client, database_path,
                                                    logging_silenced):
    """The regression this change is about: INFO logging off, rows still written."""
    assert not logging.getLogger("tspo.security").isEnabledFor(logging.INFO)

    send_chat(client, "hello")

    events = SecurityEventStore(database_path).recent(10)
    assert {e["event"] for e in events} == {"anti_bot", "dlp"}
    assert all(e["decision"] == "ALLOW" for e in events)
    assert all(e["username"] == "naseem" for e in events)


def test_block_events_persist_with_logging_disabled(client, database_path,
                                                    monkeypatch,
                                                    logging_silenced):
    def blocked(self, content, address, username):
        from server.security_decision import SecurityDecision
        result = SecurityDecision(Decision.BLOCK, "DLP_SENSITIVE_CONTENT",
                                  verdict="sensitive_content_detected",
                                  category="recipe", score=0.97)
        self._observe("dlp", result, username)
        return result
    monkeypatch.setattr(SecurityPipeline, "evaluate", blocked)

    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        join(ws, "pizza")
        ws.send_json({"type": "CHAT_MESSAGE", "request_id": "1",
                      "data": {"room": "pizza", "content": SECRET}})
        assert ws.receive_json()["data"]["decision"] == "BLOCK"

    event = SecurityEventStore(database_path).recent(1)[0]
    assert event["decision"] == "BLOCK"
    assert event["reason"] == "DLP_SENSITIVE_CONTENT"
    assert event["category"] == "recipe"
    assert event["score"] == pytest.approx(0.97)
    # blocked text is nowhere in the database
    assert SECRET.encode() not in database_path.read_bytes()


def test_per_stage_metadata_is_preserved(client, database_path):
    """Both stages are still recorded separately, not collapsed into one."""
    send_chat(client, "hello")

    events = {e["event"]: e for e in SecurityEventStore(database_path).recent(10)}
    assert set(events) == {"anti_bot", "dlp"}
    assert events["dlp"]["verdict"] is not None
    assert events["anti_bot"]["verdict"] is not None


def test_observer_runs_without_any_logging_handler(database_path):
    """The pipeline itself notifies the observer; no logger involved."""
    seen = []
    pipeline = SecurityPipeline(
        AntiBotService(), DLPService(),
        on_decision=lambda event, result, username, address=None:
            seen.append((event, result.decision, username)),
    )
    logging.getLogger("tspo.security").disabled = True
    try:
        pipeline.evaluate("hello", None, "naseem")
    finally:
        logging.getLogger("tspo.security").disabled = False

    assert [s[0] for s in seen] == ["anti_bot", "dlp"]
    assert all(s[1] is Decision.ALLOW for s in seen)


def test_pipeline_without_observer_still_works():
    """Backwards compatible: on_decision is optional."""
    result = SecurityPipeline(AntiBotService(), DLPService()).evaluate(
        "hello", None, "naseem")
    assert result.decision is Decision.ALLOW


def test_observer_failure_does_not_break_delivery(client, monkeypatch, caplog):
    """A broken security store must not take the chat down."""
    def boom(*args, **kwargs):
        raise RuntimeError("security store offline")
    monkeypatch.setattr(server.security_store, "record", boom)

    with caplog.at_level(logging.DEBUG):
        frames = send_chat(client, "still delivered")

    assert frames[0]["type"] == "NEW_MESSAGE"
    assert frames[0]["data"]["content"] == "still delivered"
    assert "Could not persist security event" in caplog.text


# -------------- 2. a message that cannot be stored is not delivered -----------

def fail_store(monkeypatch, exc=RuntimeError("database unavailable")):
    def boom(*args, **kwargs):
        raise exc
    monkeypatch.setattr(server.message_store, "add", boom)


def test_failed_persistence_returns_an_error_to_the_sender(client, monkeypatch):
    fail_store(monkeypatch)

    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        join(ws, "pizza")
        ws.send_json({"type": "CHAT_MESSAGE", "request_id": "77",
                      "data": {"room": "pizza", "content": SECRET}})
        response = ws.receive_json()

    assert response["type"] == "ERROR"
    assert response["request_id"] == "77"
    assert response["data"]["reason"] == "INTERNAL_SERVER_ERROR"


def test_failed_persistence_does_not_deliver_to_the_room(client, monkeypatch):
    """The other member must never see a message the archive rejected."""
    original_add = server.message_store.add
    fail_store(monkeypatch)

    with client.websocket_connect("/ws") as a, \
            client.websocket_connect("/ws") as b:
        login(a, "naseem")
        login(b, "qusai")
        join(a, "pizza")
        join(b, "pizza")

        a.send_json({"type": "CHAT_MESSAGE", "request_id": "1",
                     "data": {"room": "pizza", "content": SECRET}})
        assert a.receive_json()["data"]["reason"] == "INTERNAL_SERVER_ERROR"

        # Restore storage explicitly (never monkeypatch.undo() here - it would
        # also revert the clean_state fixture's temporary-database patches),
        # then send a message that really is stored.
        monkeypatch.setattr(server.message_store, "add", original_add)
        b.send_json({"type": "CHAT_MESSAGE", "request_id": "2",
                     "data": {"room": "pizza", "content": "recovered"}})

        # qusai's first frame is the recovered message, so the one that
        # failed to persist never reached him
        received = b.receive_json()
        assert received["type"] == "NEW_MESSAGE"
        assert received["data"]["content"] == "recovered"
        b.receive_json()   # qusai's MESSAGE_RESULT
        a.receive_json()   # drain naseem's copy


def test_failed_persistence_does_not_leak_details_to_the_client(client,
                                                                monkeypatch):
    """No SQLite text, no exception text, no paths, no content on the wire."""
    fail_store(monkeypatch, RuntimeError(
        "no such table: messages /secret/path/accounts.sqlite3"))

    with client.websocket_connect("/ws") as ws:
        login(ws, "naseem")
        join(ws, "pizza")
        ws.send_json({"type": "CHAT_MESSAGE", "request_id": "1",
                      "data": {"room": "pizza", "content": SECRET}})
        response = ws.receive_json()

    payload = repr(response)
    assert "no such table" not in payload
    assert "sqlite" not in payload.lower()
    assert "/secret/path" not in payload
    assert SECRET not in payload


def test_failed_persistence_logs_metadata_but_not_content(client, monkeypatch,
                                                          caplog):
    fail_store(monkeypatch)

    with caplog.at_level(logging.DEBUG):
        with client.websocket_connect("/ws") as ws:
            login(ws, "naseem")
            join(ws, "pizza")
            ws.send_json({"type": "CHAT_MESSAGE", "request_id": "1",
                          "data": {"room": "pizza", "content": SECRET}})
            ws.receive_json()

    assert "Could not persist message" in caplog.text
    assert "user=naseem" in caplog.text
    assert "room=pizza" in caplog.text
    assert "database unavailable" in caplog.text      # traceback is kept
    assert SECRET not in caplog.text                  # content is not


def test_successful_persistence_still_delivers_normally(client, database_path):
    with client.websocket_connect("/ws") as a, \
            client.websocket_connect("/ws") as b:
        login(a, "naseem")
        login(b, "qusai")
        join(a, "pizza")
        join(b, "pizza")

        a.send_json({"type": "CHAT_MESSAGE", "request_id": "1",
                     "data": {"room": "pizza", "content": "all good"}})
        assert a.receive_json()["type"] == "NEW_MESSAGE"
        assert b.receive_json()["data"]["content"] == "all good"
        assert a.receive_json()["data"]["success"] is True

    assert [m["content"] for m in
            MessageStore(database_path).get_recent("pizza")] == ["all good"]
