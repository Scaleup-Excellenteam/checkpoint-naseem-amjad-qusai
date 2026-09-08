import asyncio
import logging
from types import SimpleNamespace

from fastapi import WebSocketDisconnect
import pytest

from server import server
from server.accounts import AccountStore
from server.auth import AuthService
from server.dlp import DLPService
from server.anti_bot import AntiBotService, ReputationResult


PASSWORD = "CorrectHorse1!"


class FakeSocket:
    """Exercise the real endpoint while capturing identity at delivery time."""

    def __init__(self, messages):
        self.messages = iter(messages)
        self.sent = []
        self.identities = []

    async def accept(self):
        pass

    async def receive_json(self):
        try:
            message = next(self.messages)
        except StopIteration:
            raise WebSocketDisconnect()
        if isinstance(message, Exception):
            raise message
        return message

    async def send_json(self, message):
        self.sent.append(message)
        self.identities.append(server.manager.get_username(self))


@pytest.fixture
def auth(tmp_path, monkeypatch):
    service = AuthService(AccountStore(tmp_path / "accounts.sqlite3"))
    monkeypatch.setattr(server, "auth_service", service)
    monkeypatch.setattr(server, "manager", server.ConnectionManager())
    return service


def credentials(kind="LOGIN", username="alice", password=PASSWORD, request_id="req-1"):
    return {"type": kind, "request_id": request_id,
            "data": {"username": username, "password": password}}


def run_socket(*messages, address=None):
    normalized = []
    usernames = set()
    for message in messages:
        if isinstance(message, dict):
            message = dict(message)
            data = message.get("data")
            if message.get("type") == "LOGIN" and isinstance(data, dict):
                username = data.get("username")
                if isinstance(username, str):
                    usernames.add(username.strip())
            if message.get("type") == "CHAT_MESSAGE" and isinstance(data, dict):
                message["data"] = {"room": "pizza", **data}
        normalized.append(message)
    for username in usernames:
        server.rooms["pizza"].add_member(username)
    socket = FakeSocket(normalized)
    socket.client = SimpleNamespace(host=address) if address is not None else None
    asyncio.run(server.websocket_endpoint(socket))
    assert socket not in server.manager.active_connections
    return socket


def test_signup_does_not_authenticate_and_echoes_request_id(auth):
    socket = run_socket(credentials("SIGNUP"))
    assert socket.sent == [{"type": "SIGNUP_RESULT", "request_id": "req-1",
                            "data": {"success": True, "username": "alice"}}]
    assert socket.identities == [None]


def test_failed_login_cannot_chat(auth):
    auth.signup("alice", PASSWORD)
    socket = run_socket(credentials(password="wrong password"),
                        {"type": "CHAT_MESSAGE", "data": {"content": "hello"}})
    assert socket.sent[0] == {"type": "LOGIN_RESULT", "request_id": "req-1",
                              "data": {"success": False, "reason": "INVALID_CREDENTIALS"}}
    assert socket.sent[1]["data"]["reason"] == "NOT_AUTHENTICATED"
    assert socket.identities == [None, None]


def test_successful_login_maps_identity_and_ignores_client_sender(auth):
    auth.signup("alice", PASSWORD)
    socket = run_socket(credentials(), {"type": "CHAT_MESSAGE",
                        "data": {"content": "hello", "sender": "mallory"}})
    assert socket.identities == ["alice", "alice"]
    assert socket.sent[0] == {"type": "LOGIN_RESULT", "request_id": "req-1",
                              "data": {"success": True, "username": "alice"}}
    assert socket.sent[1] == {"type": "NEW_MESSAGE",
                              "request_id": None,
                              "data": {"sender": "alice", "room": "pizza",
                                       "content": "hello"}}


def test_duplicate_signup_is_account_based_after_disconnect(auth):
    run_socket(credentials("SIGNUP"))
    assert not server.manager.active_connections
    socket = run_socket(credentials("SIGNUP", request_id="duplicate"))
    assert socket.sent[0] == {"type": "SIGNUP_RESULT", "request_id": "duplicate",
                              "data": {"success": False, "reason": "USERNAME_ALREADY_EXISTS"}}


def test_existing_active_login_does_not_replace_credential_verification(auth):
    auth.signup("alice", PASSWORD)
    existing = FakeSocket([])
    server.manager.set_username(existing, "alice")
    socket = run_socket(credentials())
    assert socket.sent[0]["data"] == {
        "success": False, "reason": "USERNAME_ALREADY_CONNECTED"
    }
    assert server.manager.get_username(existing) == "alice"


@pytest.mark.parametrize("kind", ["SIGNUP", "LOGIN"])
def test_repeat_authentication_keeps_original_identity(auth, kind):
    auth.signup("alice", PASSWORD)
    socket = run_socket(credentials(), credentials(kind, "bob", request_id="repeat"))
    assert socket.sent[1] == {"type": kind + "_RESULT", "request_id": "repeat",
                              "data": {"success": False, "reason": "ALREADY_AUTHENTICATED"}}
    assert socket.identities == ["alice", "alice"]
    assert auth.store.get("bob") is None


@pytest.mark.parametrize("request_id", [None, "", "   ", 123])
def test_invalid_request_id_does_not_create_account(auth, request_id):
    socket = run_socket(credentials("SIGNUP", request_id=request_id))
    assert socket.sent[0]["data"]["reason"] == "INVALID_REQUEST_ID"
    assert socket.identities == [None]
    assert auth.store.get("alice") is None


@pytest.mark.parametrize("data", [None, [], {"username": 123, "password": []}])
def test_malformed_login_data_fails_without_authentication(auth, data):
    socket = run_socket({"type": "LOGIN", "request_id": "bad", "data": data})
    if isinstance(data, list):
        assert socket.sent[0]["type"] == "ERROR"
        assert socket.sent[0]["data"]["reason"] == "INVALID_MESSAGE"
    else:
        assert socket.sent[0] == {"type": "LOGIN_RESULT", "request_id": "bad",
                                  "data": {"success": False, "reason": "INVALID_CREDENTIALS"}}
    assert socket.identities == [None]


def test_health_counts_all_connections(auth):
    anonymous, authenticated = FakeSocket([]), FakeSocket([])
    server.manager.active_connections.update({anonymous: None, authenticated: "alice"})
    assert asyncio.run(server.health()) == {
        "status": "ok", "connected_clients": 2,
        "rooms": {"pizza": 0, "football": 0},
    }


def test_authentication_does_not_log_or_return_password_material(auth, capsys):
    socket = run_socket(credentials("SIGNUP"), credentials(request_id="login"))
    output = capsys.readouterr().out
    account = auth.store.get("alice")
    for secret in (PASSWORD, account.password_hash.hex(), account.salt.hex()):
        assert secret not in output
        assert secret not in str(socket.sent)


@pytest.mark.parametrize("data,reason", [
    ({}, "INVALID_MESSAGE"),
    ({"content": ""}, "EMPTY_MESSAGE"),
    ({"content": " \n\t "}, "EMPTY_MESSAGE"),
    ({"content": 123}, "INVALID_MESSAGE"),
    ({"content": []}, "INVALID_MESSAGE"),
    ({"content": None}, "INVALID_MESSAGE"),
    (None, "MISSING_FIELD"),
    ([], "INVALID_MESSAGE"),
    ({"content": "x" * 4097}, "MESSAGE_TOO_LONG"),
])
def test_invalid_chat_stops_delivery_and_socket_can_continue(auth, data, reason):
    auth.signup("alice", PASSWORD)
    socket = run_socket(
        credentials(),
        {"type": "CHAT_MESSAGE", "data": data},
        {"type": "CHAT_MESSAGE", "data": {"content": "next valid message"}},
    )
    assert [message["type"] for message in socket.sent] == [
        "LOGIN_RESULT", "ERROR", "NEW_MESSAGE"
    ]
    assert socket.sent[1]["data"]["reason"] == reason
    assert socket.sent[2]["data"]["content"] == "next valid message"
    assert socket.identities == ["alice", "alice", "alice"]


def test_chat_preserves_content_and_uses_only_server_identity(auth):
    auth.signup("alice", PASSWORD)
    content = "  hello\n  world\t "
    socket = run_socket(credentials(), {"type": "CHAT_MESSAGE", "data": {
        "content": content, "sender": "mallory", "username": "mallory", "user_id": "mallory"
    }})
    assert socket.sent[1] == {"type": "NEW_MESSAGE",
                              "request_id": None,
                              "data": {"sender": "alice", "room": "pizza",
                                       "content": content.strip()}}


def test_spoofed_identity_cannot_authorize_chat_and_delivery_is_not_called(auth, monkeypatch):
    async def forbidden_delivery(usernames, message):
        pytest.fail("Unauthenticated chat reached delivery")

    monkeypatch.setattr(server.manager, "send_to_users", forbidden_delivery)
    socket = run_socket({"type": "CHAT_MESSAGE", "data": {
        "content": "hello", "sender": "alice", "username": "alice", "user_id": "alice"
    }})
    assert socket.sent[0]["type"] == "ERROR"
    assert socket.sent[0]["request_id"] is None
    assert socket.sent[0]["data"]["reason"] == "NOT_AUTHENTICATED"
    assert socket.identities == [None]


def test_authorization_runs_before_semantic_validation(auth, monkeypatch):
    def forbidden_validation(content):
        pytest.fail("Unauthenticated chat reached semantic validation")

    monkeypatch.setattr(server, "validate_chat_message", forbidden_validation)
    socket = run_socket({"type": "CHAT_MESSAGE", "data": {"content": []}})
    assert socket.sent[0]["data"]["reason"] == "NOT_AUTHENTICATED"


def test_dlp_block_prevents_delivery_returns_correlated_result_and_logs_safely(
    auth, monkeypatch, caplog, capsys
):
    marker = "CONTROLLED_SENSITIVE_TEST_CONTENT"
    auth.signup("alice", PASSWORD)
    monkeypatch.setattr(server, "dlp_service", DLPService([lambda text: marker in text]))

    async def forbidden_delivery(usernames, message):
        pytest.fail("DLP-blocked content reached delivery")

    monkeypatch.setattr(server.manager, "send_to_users", forbidden_delivery)
    with caplog.at_level(logging.INFO, logger="tspo.security"):
        socket = run_socket(credentials(), {"type": "CHAT_MESSAGE", "request_id": "blocked-1",
                            "data": {"content": marker, "sender": "mallory"}})
    assert socket.sent[1:] == [{"type": "MESSAGE_RESULT", "request_id": "blocked-1",
                               "data": {"success": False, "decision": "BLOCK",
                                        "reason": "DLP_SENSITIVE_CONTENT", "room": "pizza"}}]
    record, = [r for r in caplog.records if getattr(r, "security_event", None) == "dlp"]
    assert record.decision == "BLOCK"
    assert record.reason == "DLP_SENSITIVE_CONTENT"
    assert record.username == "alice"
    account = auth.store.get("alice")
    captured = capsys.readouterr()
    for secret in (marker, PASSWORD, account.password_hash.hex(), account.salt.hex()):
        assert secret not in caplog.text + captured.out + captured.err
        assert secret not in str(record.__dict__)
        assert secret not in str(socket.sent)


def test_allowed_correlated_chat_broadcasts_then_acknowledges(auth, monkeypatch, caplog):
    auth.signup("alice", PASSWORD)
    seen = []

    def clean_detector(content):
        seen.append(content)
        return False

    monkeypatch.setattr(server, "dlp_service", DLPService([clean_detector]))
    observer = FakeSocket([])
    server.manager.set_username(observer, "bob")
    content = "  original content\n "
    with caplog.at_level(logging.INFO, logger="tspo.security"):
        socket = run_socket(credentials(), {"type": "CHAT_MESSAGE", "request_id": "allowed-1",
                            "data": {"content": content, "sender": "mallory", "username": "mallory"}})
    assert seen == [content.strip()]
    assert socket.sent[1:] == [
        {"type": "NEW_MESSAGE", "request_id": None,
         "data": {"sender": "alice", "room": "pizza", "content": content.strip()}},
        {"type": "MESSAGE_RESULT", "request_id": "allowed-1",
         "data": {"success": True, "room": "pizza", "recipients": 1}},
    ]
    assert observer.sent == []
    record, = [r for r in caplog.records if getattr(r, "security_event", None) == "dlp"]
    assert record.decision == "ALLOW"
    assert record.verdict == "clean"
    assert record.username == "alice"


@pytest.mark.parametrize("data", [None, [], {}, {"content": 123}, {"content": " "}])
def test_semantic_failure_never_reaches_dlp(auth, monkeypatch, data):
    auth.signup("alice", PASSWORD)

    def forbidden_detector(content):
        pytest.fail("Semantic-invalid content reached DLP")

    monkeypatch.setattr(server, "dlp_service", DLPService([forbidden_detector]))
    socket = run_socket(credentials(), {"type": "CHAT_MESSAGE", "request_id": "invalid",
                                       "data": data})
    assert [m["type"] for m in socket.sent] == ["LOGIN_RESULT", "ERROR"]


def test_unauthenticated_message_never_reaches_dlp(auth, monkeypatch):
    def forbidden_detector(content):
        pytest.fail("Unauthenticated content reached DLP")

    monkeypatch.setattr(server, "dlp_service", DLPService([forbidden_detector]))
    socket = run_socket({"type": "CHAT_MESSAGE", "request_id": "anonymous",
                         "data": {"content": "hello", "sender": "alice"}})
    assert socket.sent[0]["data"]["reason"] == "NOT_AUTHENTICATED"


def test_authentication_authorization_validation_dlp_delivery_order(auth, monkeypatch):
    auth.signup("alice", PASSWORD)
    events = []
    original_login = auth.login
    original_authorize = server.authorize
    original_validate = server.validate_chat_message
    original_delivery = server.manager.send_to_users

    def login(username, password):
        result = original_login(username, password)
        assert result["success"]
        events.append("authentication")
        return result

    def authorize(action, username):
        assert username == "alice"
        events.append("authorization")
        return original_authorize(action, username)

    def validate(content):
        events.append("semantic_validation")
        return original_validate(content)

    def detector(content):
        events.append("dlp")
        return False

    async def deliver(usernames, message):
        events.append("delivery")
        return await original_delivery(usernames, message)

    monkeypatch.setattr(auth, "login", login)
    monkeypatch.setattr(server, "authorize", authorize)
    monkeypatch.setattr(server, "validate_chat_message", validate)
    monkeypatch.setattr(server, "dlp_service", DLPService([detector]))
    monkeypatch.setattr(server.manager, "send_to_users", deliver)
    run_socket(credentials(), {"type": "CHAT_MESSAGE", "data": {"content": "hello"}})
    assert events == ["authentication", "authorization", "semantic_validation", "dlp", "delivery"]


def test_blocked_chat_can_be_followed_by_allowed_chat(auth, monkeypatch):
    auth.signup("alice", PASSWORD)
    monkeypatch.setattr(server, "dlp_service", DLPService([lambda text: text == "TEST_MARKER"]))
    socket = run_socket(
        credentials(),
        {"type": "CHAT_MESSAGE", "request_id": "block", "data": {"content": "TEST_MARKER"}},
        {"type": "CHAT_MESSAGE", "request_id": "allow", "data": {"content": "hello"}},
    )
    assert [m["type"] for m in socket.sent] == [
        "LOGIN_RESULT", "MESSAGE_RESULT", "NEW_MESSAGE", "MESSAGE_RESULT"
    ]
    assert socket.sent[1]["data"]["success"] is False
    assert socket.sent[-1] == {"type": "MESSAGE_RESULT", "request_id": "allow",
                               "data": {"success": True, "room": "pizza",
                                        "recipients": 1}}


class SyntheticReputationProvider:
    def __init__(self, malicious):
        self.malicious = malicious
        self.seen = []

    def check(self, address):
        self.seen.append(address)
        return ReputationResult(
            "malicious_address" if self.malicious else "clean_address", self.malicious
        )


class SyntheticURLReputationProvider(SyntheticReputationProvider):
    def __init__(self, malicious):
        super().__init__(False)
        self.url_malicious = malicious
        self.seen_urls = []

    def check_url(self, url):
        self.seen_urls.append(url)
        return ReputationResult(
            "malicious_url" if self.url_malicious else "clean_url",
            self.url_malicious,
        )


def test_malicious_peer_blocks_delivery_and_dlp_using_trusted_address(
    auth, monkeypatch, caplog, capsys
):
    auth.signup("alice", PASSWORD)
    provider = SyntheticReputationProvider(True)
    monkeypatch.setattr(server, "anti_bot_service", AntiBotService(provider))

    def forbidden_detector(content):
        pytest.fail("Malicious peer reached DLP")

    async def forbidden_delivery(usernames, message):
        pytest.fail("Malicious peer reached delivery")

    monkeypatch.setattr(server, "dlp_service", DLPService([forbidden_detector]))
    monkeypatch.setattr(server.manager, "send_to_users", forbidden_delivery)
    content = "SENSITIVE_REPUTATION_TEST_CONTENT"
    with caplog.at_level(logging.INFO, logger="tspo.security"):
        socket = run_socket(credentials(), {
            "type": "CHAT_MESSAGE", "request_id": "reputation-block",
            "ip": "forged-top-level-peer", "address": "forged-top-level-peer",
            "data": {"content": content, "ip": "forged-peer", "address": "forged-peer",
                     "remote_address": "forged-peer", "sender": "mallory", "username": "mallory"},
        }, address="server-peer")
    assert provider.seen == ["server-peer"]
    assert socket.sent[1:] == [{
        "type": "MESSAGE_RESULT", "request_id": "reputation-block",
        "data": {"success": False, "decision": "BLOCK",
                 "reason": "MALICIOUS_ADDRESS", "room": "pizza"},
    }]
    assert all(message["type"] != "NEW_MESSAGE" for message in socket.sent)
    record, = [r for r in caplog.records if r.name == "tspo.security"]
    assert record.security_event == "anti_bot"
    assert record.address == "server-peer"
    assert record.username == "alice"
    assert record.decision == "BLOCK"
    assert record.reason == "MALICIOUS_ADDRESS"
    captured = capsys.readouterr()
    account = auth.store.get("alice")
    for secret in (content, PASSWORD, account.password_hash.hex(), account.salt.hex()):
        assert secret not in caplog.text + captured.out + captured.err
        assert secret not in str(record.__dict__)


def test_malicious_url_blocks_delivery_before_dlp(auth, monkeypatch, caplog):
    auth.signup("alice", PASSWORD)
    provider = SyntheticURLReputationProvider(True)
    monkeypatch.setattr(
        server,
        "anti_bot_service",
        AntiBotService(provider, provider),
    )

    def forbidden_detector(content):
        pytest.fail("Malicious URL reached DLP")

    async def forbidden_delivery(usernames, message):
        pytest.fail("Malicious URL reached delivery")

    monkeypatch.setattr(server, "dlp_service", DLPService([forbidden_detector]))
    monkeypatch.setattr(server.manager, "send_to_users", forbidden_delivery)
    content = "open https://malicious.example/path#fragment"
    with caplog.at_level(logging.INFO, logger="tspo.security"):
        socket = run_socket(
            credentials(),
            {
                "type": "CHAT_MESSAGE",
                "request_id": "malicious-url",
                "data": {"content": content},
            },
            address="server-peer",
        )

    assert provider.seen == ["server-peer"]
    assert provider.seen_urls == ["https://malicious.example/path"]
    assert socket.sent[1:] == [{
        "type": "MESSAGE_RESULT",
        "request_id": "malicious-url",
        "data": {
            "success": False,
            "decision": "BLOCK",
            "reason": "MALICIOUS_ADDRESS",
            "room": "pizza",
        },
    }]
    # caplog also holds plain server.server records, which carry no
    # security_event; only the structured tspo.security ones are asserted here.
    security_records = [
        r for r in caplog.records
        if r.name == "tspo.security" and hasattr(r, "security_event")
    ]
    assert [record.security_event for record in security_records] == [
        "anti_bot",
        "anti_bot_url",
    ]
    assert security_records[1].url_count == 1
    assert content not in caplog.text


def test_clean_peer_reaches_dlp_and_delivery_with_server_identity(auth, monkeypatch, caplog):
    auth.signup("alice", PASSWORD)
    provider = SyntheticReputationProvider(False)
    seen = []

    def detector(content):
        seen.append(content)
        return False

    monkeypatch.setattr(server, "anti_bot_service", AntiBotService(provider))
    monkeypatch.setattr(server, "dlp_service", DLPService([detector]))
    with caplog.at_level(logging.INFO, logger="tspo.security"):
        socket = run_socket(credentials(), {
            "type": "CHAT_MESSAGE", "request_id": "clean-peer",
            "data": {"content": "  hello  ", "address": "forged-peer", "ip": "forged-peer",
                     "username": "mallory", "sender": "mallory", "user_id": "mallory"},
        }, address="server-peer")
    assert provider.seen == ["server-peer"]
    assert seen == ["hello"]
    assert socket.sent[1:] == [
        {"type": "NEW_MESSAGE", "request_id": None,
         "data": {"sender": "alice", "room": "pizza", "content": "hello"}},
        {"type": "MESSAGE_RESULT", "request_id": "clean-peer",
         "data": {"success": True, "room": "pizza", "recipients": 1}},
    ]
    records = [r for r in caplog.records if r.name == "tspo.security"]
    assert [r.security_event for r in records] == ["anti_bot", "dlp"]
    assert records[0].decision == "ALLOW"
    assert records[0].verdict == "clean_address"
    assert records[0].address == "server-peer"


@pytest.mark.parametrize("authenticated,data", [
    (False, {"content": "hello"}), (True, None), (True, []),
    (True, {"content": 123}), (True, {"content": " "}),
])
def test_authorization_and_semantics_stop_before_reputation(auth, monkeypatch, authenticated, data):
    provider = SyntheticReputationProvider(True)
    monkeypatch.setattr(server, "anti_bot_service", AntiBotService(provider))

    def forbidden_detector(content):
        pytest.fail("Invalid request reached DLP")

    monkeypatch.setattr(server, "dlp_service", DLPService([forbidden_detector]))
    messages = []
    if authenticated:
        auth.signup("alice", PASSWORD)
        messages.append(credentials())
    messages.append({"type": "CHAT_MESSAGE", "request_id": "early-stop", "data": data})
    socket = run_socket(*messages, address="server-peer")
    assert provider.seen == []
    assert socket.sent[-1]["type"] == "ERROR"
    assert not any(m["type"] == "NEW_MESSAGE" for m in socket.sent)


def test_missing_connection_address_does_not_fall_back_to_json(auth, monkeypatch, caplog):
    auth.signup("alice", PASSWORD)
    provider = SyntheticReputationProvider(True)
    monkeypatch.setattr(server, "anti_bot_service", AntiBotService(provider))
    with caplog.at_level(logging.INFO, logger="tspo.security"):
        socket = run_socket(credentials(), {"type": "CHAT_MESSAGE", "data": {
            "content": "hello", "address": "forged-peer", "ip": "forged-peer"
        }})
    assert provider.seen == []
    assert socket.sent[-1]["type"] == "NEW_MESSAGE"
    record, = [r for r in caplog.records if getattr(r, "security_event", None) == "anti_bot"]
    assert record.address is None
    assert record.verdict == "address_unavailable"


def test_combined_pipeline_order_and_one_reputation_check_per_chat(auth, monkeypatch):
    auth.signup("alice", PASSWORD)
    events = []
    original_login = auth.login
    original_authorize = server.authorize
    original_validate = server.validate_chat_message
    original_delivery = server.manager.send_to_users

    def login(username, password):
        result = original_login(username, password)
        assert result["success"]
        events.append("authentication")
        return result

    def authorize(action, username):
        assert username == "alice"
        events.append("authorization")
        return original_authorize(action, username)

    def validate(content):
        events.append("validation")
        return original_validate(content)

    def check(address):
        assert address == "server-peer"
        events.append("reputation")
        return ReputationResult("clean_address", False)

    def detector(content):
        events.append("dlp")
        return False

    async def deliver(usernames, message):
        events.append("delivery")
        return await original_delivery(usernames, message)

    monkeypatch.setattr(auth, "login", login)
    monkeypatch.setattr(server, "authorize", authorize)
    monkeypatch.setattr(server, "validate_chat_message", validate)
    monkeypatch.setattr(server, "anti_bot_service", AntiBotService(SimpleNamespace(check=check)))
    monkeypatch.setattr(server, "dlp_service", DLPService([detector]))
    monkeypatch.setattr(server.manager, "send_to_users", deliver)
    run_socket(credentials(),
               {"type": "CHAT_MESSAGE", "data": {"content": "first"}},
               {"type": "CHAT_MESSAGE", "data": {"content": "second"}}, address="server-peer")
    assert events == ["authentication"] + [
        "authorization", "validation", "reputation", "dlp", "delivery"
    ] * 2


@pytest.mark.parametrize("message", [None, [], "not an envelope", 1])
def test_non_object_json_does_not_crash_handler_and_connection_can_continue(auth, message):
    auth.signup("alice", PASSWORD)
    socket = run_socket(
        message,
        credentials(),
        {"type": "CHAT_MESSAGE", "request_id": "after-malformed",
         "data": {"content": "hello"}},
    )
    assert socket.sent[0]["type"] == "ERROR"
    assert socket.sent[0]["request_id"] is None
    assert socket.sent[0]["data"]["reason"] == "INVALID_MESSAGE"
    assert socket.sent[-1] == {
        "type": "MESSAGE_RESULT", "request_id": "after-malformed",
        "data": {"success": True, "room": "pizza", "recipients": 1},
    }


def test_invalid_json_does_not_crash_handler_and_connection_can_continue(auth):
    auth.signup("alice", PASSWORD)
    socket = run_socket(
        ValueError("malformed JSON"),
        credentials(),
        {"type": "CHAT_MESSAGE", "request_id": "after-invalid-json",
         "data": {"content": "hello"}},
    )
    assert socket.sent[0]["type"] == "ERROR"
    assert socket.sent[0]["request_id"] is None
    assert socket.sent[0]["data"]["reason"] == "INVALID_MESSAGE"
    assert socket.sent[-1] == {
        "type": "MESSAGE_RESULT", "request_id": "after-invalid-json",
        "data": {"success": True, "room": "pizza", "recipients": 1},
    }


def test_error_responses_echo_valid_request_id(auth):
    anonymous = run_socket({"type": "CHAT_MESSAGE", "request_id": "anonymous-request",
                            "data": {"content": "hello"}})
    assert anonymous.sent[0]["type"] == "ERROR"
    assert anonymous.sent[0]["request_id"] == "anonymous-request"
    assert anonymous.sent[0]["data"]["reason"] == "NOT_AUTHENTICATED"
    auth.signup("alice", PASSWORD)
    invalid = run_socket(credentials(), {"type": "CHAT_MESSAGE", "request_id": "invalid-request",
                                         "data": {"content": " "}})
    assert invalid.sent[-1]["type"] == "ERROR"
    assert invalid.sent[-1]["request_id"] == "invalid-request"
    assert invalid.sent[-1]["data"]["reason"] == "EMPTY_MESSAGE"


def test_socket_without_client_host_keeps_security_pipeline_available(auth):
    auth.signup("alice", PASSWORD)
    # A real server supplies `client.host`; missing metadata must not crash security.
    socket = FakeSocket([credentials(), {"type": "CHAT_MESSAGE", "request_id": "no-host",
                                          "data": {"room": "pizza", "content": "hello"}}])
    socket.client = object()
    server.rooms["pizza"].add_member("alice")
    asyncio.run(server.websocket_endpoint(socket))
    assert socket.sent[-1] == {
        "type": "MESSAGE_RESULT", "request_id": "no-host",
        "data": {"success": True, "room": "pizza", "recipients": 1}
    }
