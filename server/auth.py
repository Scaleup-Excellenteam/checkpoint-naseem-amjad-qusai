"""Credential validation and PBKDF2-HMAC-SHA256; no socket or routing access."""

import hashlib
import hmac
import secrets
import string

if __package__:
    from .accounts import Account
    from . import reason_codes as reasons
else:  # Support uvicorn server:app when launched from the server directory.
    from accounts import Account
    import reason_codes as reasons


ITERATIONS = 600_000
MIN_PASSWORD_BYTES = 8
MAX_PASSWORD_BYTES = 1024


def normalize_username(username):
    # Preserve existing case-sensitive names and surrounding-whitespace trimming.
    if not isinstance(username, str):
        return None
    username = username.strip()
    if not 1 <= len(username) <= 64 or not username.isprintable():
        return None
    return username


def password_bytes(password):
    if not isinstance(password, str) or len(password) > MAX_PASSWORD_BYTES:
        return None
    try:
        encoded = password.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if len(password) < 8 or not MIN_PASSWORD_BYTES <= len(encoded) <= MAX_PASSWORD_BYTES:
        return None
    return encoded


def password_meets_signup_policy(password: object) -> bool:
    encoded = password_bytes(password)
    return encoded is not None and all((
        any("A" <= char <= "Z" for char in password),
        any("a" <= char <= "z" for char in password),
        any("0" <= char <= "9" for char in password),
        any(char in string.punctuation for char in password),
    ))


def derive_password(password: bytes, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password, salt, iterations)


class AuthService:
    def __init__(self, store):
        self.store = store
        self._dummy_salt = secrets.token_bytes(16)
        self._dummy_hash = derive_password(
            secrets.token_bytes(32), self._dummy_salt, ITERATIONS
        )

    def signup(self, username, password):
        username = normalize_username(username)
        if username is None:
            return {"success": False, "reason": reasons.INVALID_USERNAME}
        encoded = password_bytes(password)
        if not password_meets_signup_policy(password):
            return {"success": False, "reason": reasons.INVALID_PASSWORD}

        salt = secrets.token_bytes(16)
        account = Account(
            username, derive_password(encoded, salt, ITERATIONS), salt, ITERATIONS
        )
        # SQLite's unique key also arbitrates simultaneous signup attempts.
        if not self.store.create(account):
            return {"success": False, "reason": reasons.USERNAME_ALREADY_EXISTS}
        return {"success": True, "username": username}

    def login(self, username, password):
        username = normalize_username(username)
        encoded = password_bytes(password)
        account = self.store.get(username) if username is not None else None
        salt = account.salt if account else self._dummy_salt
        iterations = account.iterations if account else ITERATIONS
        expected = account.password_hash if account else self._dummy_hash
        # Unknown accounts still perform a KDF and constant-time comparison.
        actual = derive_password(encoded if encoded is not None else b"", salt, iterations)
        matches = hmac.compare_digest(actual, expected)
        if account is None or encoded is None or not matches:
            return {"success": False, "reason": reasons.INVALID_CREDENTIALS}
        return {"success": True, "username": account.username}
