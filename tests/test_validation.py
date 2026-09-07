import pytest

from server.validation import (
    MAX_MESSAGE_LENGTH, MAX_ROOM_LENGTH, validate_chat_message,
    validate_login, validate_room, validate_signup,
)


@pytest.mark.parametrize("content,reason", [
    (None, "INVALID_MESSAGE"), (123, "INVALID_MESSAGE"),
    ([], "INVALID_MESSAGE"), ({}, "INVALID_MESSAGE"),
    ("", "EMPTY_MESSAGE"), (" \t\n ", "EMPTY_MESSAGE"),
    ("x" * (MAX_MESSAGE_LENGTH + 1), "MESSAGE_TOO_LONG"),
    (" " + "x" * MAX_MESSAGE_LENGTH, "MESSAGE_TOO_LONG"),
])
def test_invalid_chat_values(content, reason):
    result = validate_chat_message(content)
    assert result.valid is False
    assert result.reason == reason


@pytest.mark.parametrize("content", ["hello", "  hello\nworld  ", "שלום", "x" * MAX_MESSAGE_LENGTH])
def test_valid_chat_values(content):
    result = validate_chat_message(content)
    assert result.valid is True
    assert result.reason is None


@pytest.mark.parametrize("room", [None, 123, [], {}, "", " \t\n", "r" * (MAX_ROOM_LENGTH + 1)])
def test_invalid_room_values(room):
    result = validate_room(room)
    assert result.valid is False
    assert result.reason == "INVALID_ROOM"


@pytest.mark.parametrize("room", ["general", "חדר", "r" * MAX_ROOM_LENGTH])
def test_valid_room_values(room):
    result = validate_room(room)
    assert result.valid is True
    assert result.reason is None


@pytest.mark.parametrize("username,password,reason", [
    (None, "ValidPassword1!", "INVALID_USERNAME"),
    (" ", "ValidPassword1!", "INVALID_USERNAME"),
    ("x" * 65, "ValidPassword1!", "INVALID_USERNAME"),
    ("alice", None, "INVALID_PASSWORD"),
    ("alice", 123, "INVALID_PASSWORD"),
    ("alice", "short", "INVALID_PASSWORD"),
    ("alice", "x" * 1025, "INVALID_PASSWORD"),
])
def test_signup_reuses_existing_credential_policy(username, password, reason):
    result = validate_signup(username, password)
    assert result.valid is False
    assert result.reason == reason


@pytest.mark.parametrize("username,password", [
    (None, "valid password"), (" ", "valid password"),
    ("alice", None), ("alice", []), ("alice", "short"),
])
def test_invalid_login_semantics_always_use_generic_error(username, password):
    result = validate_login(username, password)
    assert result.valid is False
    assert result.reason == "INVALID_CREDENTIALS"


@pytest.mark.parametrize("validator", [validate_signup, validate_login])
def test_valid_credential_semantics_do_not_require_account_lookup(validator):
    result = validator(" alice ", "ValidPassword1!")
    assert result.valid is True
    assert result.reason is None
