"""Shared SQLite plumbing for every persistence module.

One database file holds every table. Connections are opened per operation so
worker threads never share a connection, matching the pattern AccountStore
already established.
"""

from contextlib import contextmanager
from pathlib import Path
import sqlite3


DEFAULT_DATABASE_PATH = Path(__file__).with_name("accounts.sqlite3")

# The accounts table is owned by accounts.py; it is repeated here so a fresh
# database is complete before any foreign key referencing it is used. The two
# definitions must stay identical.
SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    username      TEXT PRIMARY KEY NOT NULL,
    password_hash BLOB NOT NULL,
    salt          BLOB NOT NULL,
    iterations    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS rooms (
    name       TEXT PRIMARY KEY NOT NULL,
    created_by TEXT REFERENCES accounts(username),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    room       TEXT NOT NULL REFERENCES rooms(name),
    sender     TEXT NOT NULL REFERENCES accounts(username),
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_room_id
ON messages(room, id DESC);

-- Deliberately has no `content` column: blocked message text must never be
-- storable here, not even by mistake.
CREATE TABLE IF NOT EXISTS security_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT,
    event      TEXT NOT NULL,
    decision   TEXT NOT NULL,
    reason     TEXT,
    verdict    TEXT,
    category   TEXT,
    score      REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_security_events_created
ON security_events(created_at DESC);
"""


@contextmanager
def connect(path):
    """Open one connection, with foreign keys on, wrapped in a transaction."""
    connection = sqlite3.connect(path)
    try:
        # Per-connection pragma: SQLite defaults foreign keys to OFF.
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_schema(path=DEFAULT_DATABASE_PATH) -> None:
    """Create every table and index. Idempotent, and called once at startup
    rather than on each operation."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA)
        connection.commit()
    finally:
        connection.close()
