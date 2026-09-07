"""Persistent account records. This layer knows nothing about WebSockets."""

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class Account:
    username: str
    password_hash: bytes
    salt: bytes
    iterations: int


class AccountStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def _connection(self):
        # Open per operation so worker threads never share a SQLite connection.
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS accounts (
                        username TEXT PRIMARY KEY NOT NULL,
                        password_hash BLOB NOT NULL,
                        salt BLOB NOT NULL,
                        iterations INTEGER NOT NULL
                    )
                """)
                yield connection
        finally:
            connection.close()

    def create(self, account: Account) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """INSERT INTO accounts (username, password_hash, salt, iterations)
                   VALUES (?, ?, ?, ?) ON CONFLICT(username) DO NOTHING""",
                (account.username, account.password_hash, account.salt,
                 account.iterations),
            )
            return cursor.rowcount == 1

    def get(self, username: str):
        with self._connection() as connection:
            row = connection.execute(
                "SELECT username, password_hash, salt, iterations "
                "FROM accounts WHERE username = ?", (username,),
            ).fetchone()
        return Account(*row) if row else None
