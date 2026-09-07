"""Persistent room definitions. Membership is NOT stored here.

Membership is live connection state: it belongs to the in-memory Room objects
and must disappear on disconnect or restart. Only the room's existence is
durable.
"""

from pathlib import Path

if __package__:
    from .database import connect
else:
    from database import connect


class RoomStore:
    def __init__(self, path):
        self.path = Path(path)

    def create(self, name: str, created_by: str | None = None) -> bool:
        """Insert a room. Returns False when it already existed."""
        with connect(self.path) as connection:
            cursor = connection.execute(
                "INSERT INTO rooms (name, created_by) VALUES (?, ?) "
                "ON CONFLICT(name) DO NOTHING",
                (name, created_by),
            )
            return cursor.rowcount == 1

    def ensure_defaults(self, names) -> None:
        """Make sure the built-in rooms exist; never overwrites an existing row."""
        with connect(self.path) as connection:
            connection.executemany(
                "INSERT INTO rooms (name, created_by) VALUES (?, NULL) "
                "ON CONFLICT(name) DO NOTHING",
                [(name,) for name in names],
            )

    def all_names(self) -> list:
        with connect(self.path) as connection:
            rows = connection.execute(
                "SELECT name FROM rooms ORDER BY name"
            ).fetchall()
        return [row[0] for row in rows]

    def exists(self, name: str) -> bool:
        with connect(self.path) as connection:
            row = connection.execute(
                "SELECT 1 FROM rooms WHERE name = ?", (name,)
            ).fetchone()
        return row is not None
