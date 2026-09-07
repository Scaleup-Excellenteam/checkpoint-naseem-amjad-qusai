"""Persistent chat messages.

Only messages that already passed the full security pipeline reach this
module. A BLOCKED message is never handed to add(): the caller returns before
persistence, so blocked content has no path into the database.
"""

from pathlib import Path

if __package__:
    from .database import connect
else:
    from database import connect


class MessageStore:
    def __init__(self, path):
        self.path = Path(path)

    def add(self, room: str, sender: str, content: str) -> int:
        """Store one allowed message and return its row id."""
        with connect(self.path) as connection:
            cursor = connection.execute(
                "INSERT INTO messages (room, sender, content) VALUES (?, ?, ?)",
                (room, sender, content),
            )
            return cursor.lastrowid

    def get_recent(self, room: str, limit: int = 50) -> list:
        """Most recent messages first. Not wired to any protocol yet."""
        with connect(self.path) as connection:
            rows = connection.execute(
                "SELECT id, room, sender, content, created_at FROM messages "
                "WHERE room = ? ORDER BY id DESC LIMIT ?",
                (room, limit),
            ).fetchall()
        return [
            {"id": row[0], "room": row[1], "sender": row[2],
             "content": row[3], "created_at": row[4]}
            for row in rows
        ]

    def count(self, room: str | None = None) -> int:
        with connect(self.path) as connection:
            if room is None:
                row = connection.execute(
                    "SELECT COUNT(*) FROM messages").fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) FROM messages WHERE room = ?",
                    (room,)).fetchone()
        return row[0]
