"""Persistent security-decision metadata.

Safe metadata only. The table has no `content` column, so raw message text,
credentials, API keys and tokens cannot be stored here even by accident.
"""

from pathlib import Path

if __package__:
    from .database import connect
else:
    from database import connect


class SecurityEventStore:
    def __init__(self, path):
        self.path = Path(path)

    def record(self, event: str, decision: str, username: str | None = None,
               reason: str | None = None, verdict: str | None = None,
               category: str | None = None, score: float | None = None) -> int:
        """Store one decision. Every field is metadata produced by the
        security pipeline; none of them carry message content."""
        with connect(self.path) as connection:
            cursor = connection.execute(
                "INSERT INTO security_events "
                "(username, event, decision, reason, verdict, category, score) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (username, event, decision, reason, verdict, category, score),
            )
            return cursor.lastrowid

    def recent(self, limit: int = 50) -> list:
        with connect(self.path) as connection:
            rows = connection.execute(
                "SELECT id, username, event, decision, reason, verdict, "
                "category, score, created_at FROM security_events "
                "ORDER BY id DESC LIMIT ?", (limit,),
            ).fetchall()
        return [
            {"id": r[0], "username": r[1], "event": r[2], "decision": r[3],
             "reason": r[4], "verdict": r[5], "category": r[6], "score": r[7],
             "created_at": r[8]}
            for r in rows
        ]

    def count(self) -> int:
        with connect(self.path) as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM security_events").fetchone()[0]

