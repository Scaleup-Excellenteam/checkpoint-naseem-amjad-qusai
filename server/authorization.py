"""Action authorization only; no socket, account-store, or delivery access.

Backend must supply authenticated_username from its server-side connection mapping,
never from message data. An allowed result does not imply room membership.
"""

from dataclasses import dataclass

if __package__:
    from . import reason_codes as reasons
else:
    import reason_codes as reasons


@dataclass(frozen=True)
class AuthorizationResult:
    allowed: bool
    reason: str | None = None


def authorize(action: str, authenticated_username: str | None) -> AuthorizationResult:
    if action in ("SIGNUP", "LOGIN"):
        if authenticated_username is not None:
            return AuthorizationResult(False, reasons.ALREADY_AUTHENTICATED)
        return AuthorizationResult(True)
    if action in ("JOIN_ROOM", "LEAVE_ROOM", "CHAT_MESSAGE", "LIST_ROOMS"):
        if authenticated_username is None:
            return AuthorizationResult(False, reasons.NOT_AUTHENTICATED)
        return AuthorizationResult(True)
    return AuthorizationResult(False, reasons.UNKNOWN_MESSAGE_TYPE)
