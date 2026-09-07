from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from server.accounts import AccountStore
from server.auth import AuthService, ITERATIONS


PASSWORD = "CorrectHorse1!"


@pytest.fixture
def auth(tmp_path):
    return AuthService(AccountStore(tmp_path / "accounts.sqlite3"))


def test_signup_creates_account(auth):
    assert auth.signup(" alice ", PASSWORD) == {"success": True, "username": "alice"}
    assert auth.store.get("alice").username == "alice"


def test_duplicate_signup_preserves_original_password(auth):
    assert auth.signup("alice", PASSWORD)["success"]
    assert auth.signup(" alice ", "AnotherPassword1!") == {
        "success": False, "reason": "USERNAME_ALREADY_EXISTS"
    }
    assert auth.login("alice", PASSWORD)["success"]
    assert not auth.login("alice", "AnotherPassword1!")["success"]


def test_storage_contains_salted_hashes_not_plaintext(auth):
    auth.signup("alice", PASSWORD)
    auth.signup("bob", PASSWORD)
    alice, bob = auth.store.get("alice"), auth.store.get("bob")
    assert alice.password_hash != PASSWORD.encode()
    assert len(alice.password_hash) == 32
    assert len(alice.salt) == 16
    assert alice.iterations == ITERATIONS == 600_000
    assert alice.salt != bob.salt
    assert alice.password_hash != bob.password_hash
    with sqlite3.connect(auth.store.path) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(accounts)")]
    assert columns == ["username", "password_hash", "salt", "iterations"]
    assert PASSWORD.encode() not in auth.store.path.read_bytes()


def test_correct_password_verifies_and_login_succeeds(auth):
    auth.signup("alice", PASSWORD)
    assert auth.login(" alice ", PASSWORD) == {"success": True, "username": "alice"}


def test_wrong_password_and_unknown_user_have_same_failure(auth):
    auth.signup("alice", PASSWORD)
    expected = {"success": False, "reason": "INVALID_CREDENTIALS"}
    assert auth.login("alice", "wrong password") == expected
    assert auth.login("unknown", PASSWORD) == expected


def test_accounts_survive_new_store_and_service(auth):
    auth.signup("alice", PASSWORD)
    restarted = AuthService(AccountStore(auth.store.path))
    assert restarted.login("alice", PASSWORD)["success"]
    assert restarted.signup("alice", PASSWORD)["reason"] == "USERNAME_ALREADY_EXISTS"


@pytest.mark.parametrize("username", [None, 123, "", "   ", "a" * 65, "a\nb"])
def test_signup_rejects_invalid_username(auth, username):
    assert auth.signup(username, PASSWORD) == {
        "success": False, "reason": "INVALID_USERNAME"
    }


@pytest.mark.parametrize("password", [None, 123, "", "short", "x" * 1025, "\ud800"])
def test_signup_rejects_invalid_password(auth, password):
    assert auth.signup("alice", password) == {
        "success": False, "reason": "INVALID_PASSWORD"
    }
    assert auth.store.get("alice") is None


@pytest.mark.parametrize("username,password", [(None, PASSWORD), ("alice", None),
                                                (123, []), ("", ""), ("alice", "short")])
def test_invalid_login_fields_use_generic_error(auth, username, password):
    assert auth.login(username, password) == {
        "success": False, "reason": "INVALID_CREDENTIALS"
    }


def test_password_whitespace_is_preserved(auth):
    password = "  ExactPassword1!  "
    auth.signup("alice", password)
    assert auth.login("alice", password)["success"]
    assert not auth.login("alice", password.strip())["success"]


def test_simultaneous_signup_creates_only_one_account(auth):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: auth.signup("alice", PASSWORD), range(2)))
    assert sum(result["success"] for result in results) == 1
    assert [r["reason"] for r in results if not r["success"]] == ["USERNAME_ALREADY_EXISTS"]
