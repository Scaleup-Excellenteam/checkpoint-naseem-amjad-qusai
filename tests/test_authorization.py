import pytest

from server.authorization import authorize


@pytest.mark.parametrize("action", ["CHAT_MESSAGE", "JOIN_ROOM", "LEAVE_ROOM"])
def test_protected_actions_require_authenticated_identity(action):
    result = authorize(action, None)
    assert result.allowed is False
    assert result.reason == "NOT_AUTHENTICATED"


@pytest.mark.parametrize("action", ["CHAT_MESSAGE", "JOIN_ROOM", "LEAVE_ROOM"])
def test_protected_actions_allow_authenticated_identity(action):
    result = authorize(action, "alice")
    assert result.allowed is True
    assert result.reason is None


@pytest.mark.parametrize("action", ["SIGNUP", "LOGIN"])
def test_authentication_actions_allow_anonymous_connection(action):
    result = authorize(action, None)
    assert result.allowed is True
    assert result.reason is None


@pytest.mark.parametrize("action", ["SIGNUP", "LOGIN"])
def test_repeat_authentication_denied(action):
    result = authorize(action, "alice")
    assert result.allowed is False
    assert result.reason == "ALREADY_AUTHENTICATED"


def test_unknown_action_does_not_receive_authorization():
    result = authorize("DELETE_ACCOUNT", "alice")
    assert result.allowed is False
    assert result.reason == "UNKNOWN_MESSAGE_TYPE"
