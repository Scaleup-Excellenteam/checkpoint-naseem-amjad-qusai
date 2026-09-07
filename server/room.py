class Room:
    """A single chat room.

    Holds room state only (name + members). It knows nothing about
    WebSockets: members are usernames (str), and delivering messages
    is ConnectionManager's job.
    """

    def __init__(self, name: str):
        self.name = name
        # usernames, not websockets -> O(1) lookups, no duplicates
        self.members = set()

    def add_member(self, user: str) -> None:
        """Add a user. Joining twice is a no-op."""
        self.members.add(user)

    def remove_member(self, user: str) -> None:
        """Remove a user. Removing someone who is not a member is a no-op,
        so disconnect cleanup can blindly call this on every room."""
        self.members.discard(user)

    def has_member(self, user: str) -> bool:
        return user in self.members

    def get_members(self) -> set:
        """Return a copy, so callers can iterate while members change
        (e.g. a disconnect during a room broadcast)."""
        return set(self.members)

    def __repr__(self):
        return f"Room(name={self.name!r}, members={len(self.members)})"
