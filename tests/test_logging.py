"""Logging is configured, useful, and never leaks sensitive content."""
import logging

from server import server
from server.security_logging import log_dlp_decision
from server.security_decision import Decision, SecurityDecision

from tests.conftest import join, login


SECRET = "TOP_SECRET_RECIPE_do_not_log_me"


# ------------------------------------------------------- configuration

def test_info_level_is_enabled():
    """The bug this task fixes: INFO records used to be discarded."""
    assert server.logger.isEnabledFor(logging.INFO)
    assert logging.getLogger("tspo.security").isEnabledFor(logging.INFO)


def test_root_logger_has_a_handler():
    assert logging.getLogger().handlers


def test_configure_logging_is_idempotent():
    """Safe to call again; must not stack duplicate handlers."""
    before = len(logging.getLogger().handlers)
    server.configure_logging()
    assert len(logging.getLogger().handlers) == before


def test_server_uses_a_module_logger():
    assert isinstance(server.logger, logging.Logger)
    assert server.logger.name.endswith("server")


# --------------------------------------------------- useful INFO events

def test_lifecycle_events_are_logged_at_info(client, caplog):
    with caplog.at_level(logging.INFO):
        with client.websocket_connect("/ws") as ws:
            login(ws, "naseem")
            join(ws, "pizza")

    text = caplog.text
    assert "New WebSocket connection" in text
    assert "naseem logged in" in text
    assert "naseem joined room pizza" in text
    assert "naseem disconnected" in text


def test_security_allow_decisions_are_no_longer_discarded(caplog):
    with caplog.at_level(logging.INFO, logger="tspo.security"):
        log_dlp_decision(SecurityDecision(Decision.ALLOW, verdict="clean"),
                         "naseem")
    assert '"decision": "ALLOW"' in caplog.text


# ------------------------------------------------------ WARNING events

def test_unauthenticated_request_is_logged_as_warning(client, caplog):
    with caplog.at_level(logging.WARNING):
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "JOIN_ROOM", "request_id": "1",
                          "data": {"room": "pizza"}})
            ws.receive_json()

    assert any(r.levelno == logging.WARNING and "unauthenticated" in r.message
               for r in caplog.records)


def test_malformed_request_is_logged_as_warning(client, caplog):
    with caplog.at_level(logging.WARNING):
        with client.websocket_connect("/ws") as ws:
            ws.send_json([1, 2, 3])
            ws.receive_json()

    assert "non-object message" in caplog.text


# ------------------------------------------- sensitive data never logged

def test_chat_content_is_never_logged(client, caplog):
    with caplog.at_level(logging.DEBUG):
        with client.websocket_connect("/ws") as ws:
            login(ws, "naseem")
            join(ws, "pizza")
            ws.send_json({
                "type": "CHAT_MESSAGE", "request_id": "9",
                "data": {"room": "pizza", "content": SECRET},
            })
            ws.receive_json()
            ws.receive_json()

    assert SECRET not in caplog.text
    # the routing metadata is still there
    assert "room=pizza" in caplog.text


def test_password_is_never_logged(client, caplog):
    password = "SuperSecret9!"
    with caplog.at_level(logging.DEBUG):
        with client.websocket_connect("/ws") as ws:
            ws.send_json({
                "type": "LOGIN", "request_id": "1",
                "data": {"username": "ghost", "password": password},
            })
            ws.receive_json()

    assert password not in caplog.text
    # the failure itself is still reported
    assert "rejected reason=" in caplog.text


def test_failed_auth_does_not_echo_client_supplied_username(client, caplog):
    """A failed login must not copy arbitrary client input into the log."""
    with caplog.at_level(logging.DEBUG):
        with client.websocket_connect("/ws") as ws:
            ws.send_json({
                "type": "LOGIN", "request_id": "1",
                "data": {"username": "INJECTED_VALUE", "password": "x"},
            })
            ws.receive_json()

    assert "INJECTED_VALUE" not in caplog.text


def test_delivery_failure_logs_metadata_but_not_the_payload(caplog):
    """send_to_users must report a dead socket without logging the frame."""
    class DeadSocket:
        async def send_json(self, message):
            raise RuntimeError("socket is closed")

    dead = DeadSocket()
    manager = server.ConnectionManager()
    manager.active_connections[dead] = "naseem"

    import asyncio
    with caplog.at_level(logging.DEBUG):
        delivered = asyncio.run(manager.send_to_users(
            ["naseem"], {"type": "NEW_MESSAGE",
                         "data": {"content": SECRET, "room": "pizza"}},
        ))

    assert delivered == 0
    assert dead not in manager.active_connections      # still cleaned up
    assert "Delivery failed" in caplog.text
    assert "user=naseem" in caplog.text
    assert "socket is closed" in caplog.text           # exception reported
    assert SECRET not in caplog.text                   # payload is not
