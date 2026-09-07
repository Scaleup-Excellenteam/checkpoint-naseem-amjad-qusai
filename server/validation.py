"""Action-value semantics, called after Backend's structural contract checks.

This module does not inspect envelopes, request IDs, or dispatch messages. Defensive
value-type checks keep callers safe while the shared contract layer is incomplete.
Credential policies reuse Checkpoint 1 helpers; credential verification stays in
AuthService. Room validation accepts an extracted identifier, leaving the choice of
wire field and all membership/routing behavior to Backend.
"""

from dataclasses import dataclass

if __package__:
    from . import reason_codes as reasons
    from .auth import normalize_username, password_bytes, password_meets_signup_policy
else:
    import reason_codes as reasons
    from auth import normalize_username, password_bytes, password_meets_signup_policy


MAX_MESSAGE_LENGTH = 4096  # Python string characters, including whitespace.
MAX_ROOM_LENGTH = 128


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reason: str | None = None


def validate_signup(username: object, password: object) -> ValidationResult:
    if normalize_username(username) is None:
        return ValidationResult(False, reasons.INVALID_USERNAME)
    if not password_meets_signup_policy(password):
        return ValidationResult(False, reasons.INVALID_PASSWORD)
    return ValidationResult(True)


def validate_login(username: object, password: object) -> ValidationResult:
    # All invalid login values use the same public error as credential failures.
    if normalize_username(username) is None or password_bytes(password) is None:
        return ValidationResult(False, reasons.INVALID_CREDENTIALS)
    return ValidationResult(True)


def validate_chat_message(content: object) -> ValidationResult:
    if not isinstance(content, str):
        return ValidationResult(False, reasons.INVALID_MESSAGE)
    if not content.strip():
        return ValidationResult(False, reasons.EMPTY_MESSAGE)
    if len(content) > MAX_MESSAGE_LENGTH:
        return ValidationResult(False, reasons.MESSAGE_TOO_LONG)
    return ValidationResult(True)


def validate_room(room: object) -> ValidationResult:
    """Shared value validation for JOIN_ROOM and LEAVE_ROOM; does not normalize."""
    if not isinstance(room, str) or not room.strip() or len(room) > MAX_ROOM_LENGTH:
        return ValidationResult(False, reasons.INVALID_ROOM)
    return ValidationResult(True)
