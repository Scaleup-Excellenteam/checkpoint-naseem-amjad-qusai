"""Action-value semantics, called after Backend's structural contract checks.

This module does not inspect envelopes, request IDs, or dispatch messages. Defensive
value-type checks keep callers safe while the shared contract layer is incomplete.
Credential policies reuse Checkpoint 1 helpers; credential verification stays in
AuthService. Room validation accepts an extracted identifier, leaving the choice of
wire field and all membership/routing behavior to Backend.
"""

from dataclasses import dataclass
import re

if __package__:
    from . import reason_codes as reasons
    from .auth import normalize_username, password_bytes, password_meets_signup_policy
else:
    import reason_codes as reasons
    from auth import normalize_username, password_bytes, password_meets_signup_policy


MAX_MESSAGE_LENGTH = 4096  # Python string characters, including whitespace.
MAX_ROOM_LENGTH = 128

# Creation-time naming policy, stricter than MAX_ROOM_LENGTH: a name a user
# invents becomes a durable identifier, so it stays short and unambiguous.
MAX_ROOM_NAME_LENGTH = 30
# ASCII letters, digits, underscore and hyphen only - no spaces, no
# punctuation, and nothing that could read as markup or a path segment.
ROOM_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


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


def validate_room_name(name: object) -> ValidationResult:
    """Naming policy for a room the user creates.

    Call it with the already-stripped value: stripping is the only
    transformation CREATE_ROOM performs, and the caller keeps the result it
    validated so the stored name is exactly the one that passed here.
    """
    if not isinstance(name, str):
        return ValidationResult(False, reasons.INVALID_ROOM_NAME)
    # fullmatch on a "+" pattern also rejects the empty string.
    if len(name) > MAX_ROOM_NAME_LENGTH or not ROOM_NAME_PATTERN.fullmatch(name):
        return ValidationResult(False, reasons.INVALID_ROOM_NAME)
    return ValidationResult(True)
