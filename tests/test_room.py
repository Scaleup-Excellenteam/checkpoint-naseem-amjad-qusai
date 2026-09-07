"""ROOM MODEL — the Room class is pure state, no WebSockets involved."""
from server.room import Room


def test_add_member():
    room = Room("pizza")
    room.add_member("qusai")

    assert room.has_member("qusai")
    assert room.get_members() == {"qusai"}


def test_duplicate_add_is_a_noop():
    room = Room("pizza")
    room.add_member("qusai")
    room.add_member("qusai")

    assert room.get_members() == {"qusai"}


def test_remove_member():
    room = Room("pizza")
    room.add_member("qusai")
    room.remove_member("qusai")

    assert not room.has_member("qusai")
    assert room.get_members() == set()


def test_remove_nonexistent_member_does_not_raise():
    room = Room("pizza")
    room.remove_member("nobody")          # must not raise: disconnect
    room.add_member("qusai")              # cleanup calls this blindly
    room.remove_member("amjad")

    assert room.get_members() == {"qusai"}


def test_has_member():
    room = Room("pizza")
    room.add_member("qusai")

    assert room.has_member("qusai") is True
    assert room.has_member("amjad") is False


def test_get_members_returns_a_copy():
    room = Room("pizza")
    room.add_member("qusai")

    members = room.get_members()
    members.add("intruder")

    assert room.get_members() == {"qusai"}
    assert not room.has_member("intruder")
