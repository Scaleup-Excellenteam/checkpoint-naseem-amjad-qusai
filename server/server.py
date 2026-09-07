import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

try:
    from room import Room
except ImportError:  # when launched as "uvicorn server.server:app"
    from server.room import Room

app = FastAPI()


class ConnectionManager:
    def __init__(self):
        # websocket -> username
        self.active_connections = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[websocket] = None

    def disconnect(self, websocket: WebSocket):
        self.active_connections.pop(websocket, None)

    def set_username(self, websocket: WebSocket, username: str):
        self.active_connections[websocket] = username

    def get_username(self, websocket: WebSocket):
        return self.active_connections.get(websocket)

    def username_exists(self, username: str):
        return username in self.active_connections.values()

    async def send(self, websocket: WebSocket, message: dict):
        await websocket.send_json(message)

    async def send_to_users(self, usernames, message: dict) -> int:
        """Send `message` only to the connections owned by `usernames`.

        This is the room-targeted delivery primitive: Room objects hold
        usernames, so the manager is the only place that has to know which
        WebSocket belongs to whom.

        - unauthenticated connections (username is None) are skipped
        - a recipient whose socket already died is dropped and does not
          break delivery for the remaining recipients
        - iteration happens over a snapshot, because a failed send calls
          disconnect() and mutates active_connections

        Returns how many sockets actually received the message.
        """
        targets = set(usernames)
        delivered = 0

        for websocket, username in list(self.active_connections.items()):
            if username is None or username not in targets:
                continue

            try:
                await websocket.send_json(message)
                delivered += 1
            except Exception:
                # broken socket: clean it up, keep delivering to the rest
                self.disconnect(websocket)

        return delivered

    async def broadcast(self, message: dict):
        """Send to every authenticated connection.

        Kept for server-wide announcements. Normal room chat must use
        send_to_users() so messages never leak across rooms.
        """
        await self.send_to_users(
            [u for u in self.active_connections.values() if u is not None],
            message,
        )


manager = ConnectionManager()

# room name -> Room
rooms = {
    "pizza": Room("pizza"),
    "football": Room("football"),
}


def leave_all_rooms(username: str):
    """Remove the user from every room. Returns the rooms actually left."""
    left = []
    for room in rooms.values():
        if room.has_member(username):
            room.remove_member(username)
            left.append(room.name)
    return left


# ----------------------------------------------------------------------
# protocol helpers
# ----------------------------------------------------------------------

def error_message(request_id, reason: str, message: str = None) -> dict:
    """Build an ERROR frame. request_id is echoed back so the client can
    match the failure to the request that caused it."""
    data = {"reason": reason}
    if message:
        data["message"] = message

    return {
        "type": "ERROR",
        "request_id": request_id,
        "data": data,
    }


def clean_room_name(data: dict):
    """Return the stripped room name, or None if the field is missing,
    not a string, or blank."""
    room_name = data.get("room")

    if not isinstance(room_name, str) or not room_name.strip():
        return None

    return room_name.strip()


# ----------------------------------------------------------------------
# message handlers
# ----------------------------------------------------------------------

async def handle_login(websocket: WebSocket, request_id, data: dict,
                       username: str = None):
    """Associates a username with this WebSocket.

    Public: LOGIN is deliberately absent from AUTH_REQUIRED_TYPES, so the
    `username` argument is whatever this connection had before (normally
    None) and is intentionally unused.

    NOTE: real credential checking / signup persistence belongs to the
    Auth teammate. This only does the connection <-> username binding
    the realtime layer needs.
    """
    username = data.get("username")

    # type check before .strip(): a non-string username must not crash
    if not isinstance(username, str) or not username.strip():
        await manager.send(
            websocket,
            error_message(
                request_id,
                "INVALID_USERNAME",
                "A valid username is required",
            ),
        )
        return

    username = username.strip()

    if manager.username_exists(username):
        await manager.send(
            websocket,
            {
                "type": "LOGIN_RESULT",
                "request_id": request_id,
                "data": {
                    "success": False,
                    "reason": "USERNAME_ALREADY_CONNECTED",
                },
            },
        )
        return

    manager.set_username(websocket, username)

    await manager.send(
        websocket,
        {
            "type": "LOGIN_RESULT",
            "request_id": request_id,
            "data": {
                "success": True,
                "username": username,
            },
        },
    )

    print(f"{username} logged in")


async def handle_join_room(websocket: WebSocket, request_id, data: dict,
                           username: str):
    room_name = clean_room_name(data)

    # missing, wrong type, or blank
    if room_name is None:
        await manager.send(
            websocket,
            error_message(
                request_id,
                "MISSING_FIELD",
                "A valid room name is required",
            ),
        )
        return

    room = rooms.get(room_name)

    if room is None:
        await manager.send(
            websocket,
            {
                "type": "JOIN_ROOM_RESULT",
                "request_id": request_id,
                "data": {
                    "success": False,
                    "reason": "ROOM_NOT_FOUND",
                },
            },
        )
        return

    if room.has_member(username):
        await manager.send(
            websocket,
            {
                "type": "JOIN_ROOM_RESULT",
                "request_id": request_id,
                "data": {
                    "success": False,
                    "reason": "ALREADY_IN_ROOM",
                    "room": room.name,
                },
            },
        )
        return

    room.add_member(username)

    await manager.send(
        websocket,
        {
            "type": "JOIN_ROOM_RESULT",
            "request_id": request_id,
            "data": {
                "success": True,
                "room": room.name,
            },
        },
    )

    print(f"{username} joined room {room.name}")


async def handle_leave_room(websocket: WebSocket, request_id, data: dict,
                            username: str):
    room_name = clean_room_name(data)

    # Missing, wrong type, or blank room name
    if room_name is None:
        await manager.send(
            websocket,
            error_message(
                request_id,
                "MISSING_FIELD",
                "A valid room name is required",
            ),
        )
        return

    room = rooms.get(room_name)

    # Room does not exist
    if room is None:
        await manager.send(
            websocket,
            {
                "type": "LEAVE_ROOM_RESULT",
                "request_id": request_id,
                "data": {
                    "success": False,
                    "reason": "ROOM_NOT_FOUND",
                },
            },
        )
        return

    # User is not a member of this room
    if not room.has_member(username):
        await manager.send(
            websocket,
            {
                "type": "LEAVE_ROOM_RESULT",
                "request_id": request_id,
                "data": {
                    "success": False,
                    "reason": "NOT_IN_ROOM",
                    "room": room.name,
                },
            },
        )
        return

    # Remove the user from this room only. Other memberships are untouched.
    room.remove_member(username)

    await manager.send(
        websocket,
        {
            "type": "LEAVE_ROOM_RESULT",
            "request_id": request_id,
            "data": {
                "success": True,
                "room": room.name,
            },
        },
    )

    print(f"{username} left room {room.name}")


async def handle_chat_message(websocket: WebSocket, request_id, data: dict,
                              username: str):
    """Route one chat message to the members of one room.

    Failure convention follows JOIN_ROOM / LEAVE_ROOM:
      - protocol/auth problems      -> ERROR
      - room-level outcomes         -> MESSAGE_RESULT with success=False
    """
    # 1. `username` came from the auth gate, i.e. from ConnectionManager.
    #    data["sender"] is ignored on purpose.

    # 2. the target room must be present, a string, and not blank
    room_name = clean_room_name(data)

    if room_name is None:
        await manager.send(
            websocket,
            error_message(
                request_id,
                "MISSING_FIELD",
                "A valid room name is required",
            ),
        )
        return

    room = rooms.get(room_name)

    if room is None:
        await manager.send(
            websocket,
            {
                "type": "MESSAGE_RESULT",
                "request_id": request_id,
                "data": {
                    "success": False,
                    "reason": "ROOM_NOT_FOUND",
                    "room": room_name,
                },
            },
        )
        return

    # 3. you may only talk in a room you actually joined
    if not room.has_member(username):
        await manager.send(
            websocket,
            {
                "type": "MESSAGE_RESULT",
                "request_id": request_id,
                "data": {
                    "success": False,
                    "reason": "NOT_IN_ROOM",
                    "room": room.name,
                },
            },
        )
        print(
            f"{username} tried to send to room {room.name} "
            f"without being a member"
        )
        return

    # 4. content: type-check BEFORE calling .strip()
    content = data.get("content")

    if not isinstance(content, str) or not content.strip():
        await manager.send(
            websocket,
            error_message(
                request_id,
                "EMPTY_MESSAGE",
                "Message content must be a non-empty string",
            ),
        )
        return

    content = content.strip()

    # ------------------------------------------------------------------
    # SECURITY INTEGRATION POINT  (owned by the Auth / Security teammate)
    #
    # At this line the message is fully validated and the sender is
    # authenticated, and NOTHING has been delivered yet. The DLP /
    # Anti-Bot / URL-reputation ALLOW-BLOCK check goes exactly here,
    # e.g.:
    #
    #     decision = security.inspect(sender=username,
    #                                 room=room.name,
    #                                 content=content)
    #     if not decision.allowed:
    #         -> MESSAGE_RESULT success=False, reason=decision.reason
    #         return
    #
    # No security component exists in the repository yet, so no check is
    # performed and the server does NOT claim this message was scanned.
    # ------------------------------------------------------------------

    # 5. build the server-authored frame. The sender is the server's value.
    outgoing_message = {
        "type": "NEW_MESSAGE",
        "request_id": None,
        "data": {
            "sender": username,
            "room": room.name,
            "content": content,
        },
    }

    # 6. recipients = this room's members only (a copy, so a disconnect
    #    during delivery cannot mutate what we are iterating)
    recipients = room.get_members()

    # 7. targeted delivery. The sender is a member, so they get their own
    #    message back: the server is the source of truth for the chat log.
    delivered = await manager.send_to_users(recipients, outgoing_message)

    print(
        f"{username} sent message to room {room.name} "
        f"({delivered} recipient(s))"
    )

    await manager.send(
        websocket,
        {
            "type": "MESSAGE_RESULT",
            "request_id": request_id,
            "data": {
                "success": True,
                "room": room.name,
                "recipients": delivered,
            },
        },
    )


async def handle_list_rooms(websocket: WebSocket, request_id, data: dict,
                            username: str):
    """Report the rooms the server knows about.

    The server is the source of truth here: it walks its own `rooms`
    dictionary rather than echoing anything the client believes.

    Deliberately NOT exposed:
      - member usernames (only the count leaves the server)
      - any "active room" flag; the active room is a client-side UX
        concept and the server does not store one
    """
    room_list = [
        {
            "name": room.name,
            "members": len(room.members),
            "joined": room.has_member(username),
        }
        # alphabetical, so the output is stable between calls
        for room in sorted(rooms.values(), key=lambda r: r.name)
    ]

    await manager.send(
        websocket,
        {
            "type": "ROOMS_LIST",
            "request_id": request_id,
            "data": {
                "rooms": room_list,
            },
        },
    )

    print(f"{username} listed {len(room_list)} room(s)")


HANDLERS = {
    "LOGIN": handle_login,
    "JOIN_ROOM": handle_join_room,
    "LEAVE_ROOM": handle_leave_room,
    "CHAT_MESSAGE": handle_chat_message,
    "LIST_ROOMS": handle_list_rooms,
}

# Message types that may only be used by a logged-in connection. Anything
# not listed here is public -- LOGIN above all, which must stay reachable
# or nobody could ever authenticate.
AUTH_REQUIRED_TYPES = {
    "JOIN_ROOM",
    "LEAVE_ROOM",
    "CHAT_MESSAGE",
    "LIST_ROOMS",
}


# ----------------------------------------------------------------------
# endpoints
# ----------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "connected_clients": len(manager.active_connections),
        "rooms": {name: len(room.members) for name, room in rooms.items()},
    }


def cleanup_connection(websocket: WebSocket):
    """Drop a connection and every room membership it owned.

    Runs in a finally block so it also covers an unexpected error, not
    only a clean WebSocketDisconnect.
    """
    username = manager.get_username(websocket)

    manager.disconnect(websocket)

    if username is None:
        print("Unknown client disconnected")
        return

    left = leave_all_rooms(username)

    if left:
        print(f"{username} removed from rooms: {', '.join(left)}")

    print(f"{username} disconnected")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)

    print("New WebSocket connection")

    try:
        while True:
            # --- receive: a malformed frame must never kill the server ---
            try:
                raw = await websocket.receive_text()
            except (KeyError, TypeError):
                # a non-text frame (e.g. binary) is not part of protocol V1
                await manager.send(
                    websocket,
                    error_message(
                        None,
                        "INVALID_MESSAGE",
                        "Only JSON text frames are supported",
                    ),
                )
                print("Rejected a non-text frame")
                continue

            try:
                message = json.loads(raw)
            except ValueError:
                await manager.send(
                    websocket,
                    error_message(None, "INVALID_MESSAGE", "Malformed JSON"),
                )
                print("Rejected malformed JSON")
                continue

            # --- envelope validation ---
            if not isinstance(message, dict):
                await manager.send(
                    websocket,
                    error_message(
                        None,
                        "INVALID_MESSAGE",
                        "A message must be a JSON object",
                    ),
                )
                print("Rejected a non-object message")
                continue

            request_id = message.get("request_id")
            message_type = message.get("type")
            data = message.get("data")

            if not isinstance(message_type, str) or not message_type.strip():
                await manager.send(
                    websocket,
                    error_message(
                        request_id,
                        "INVALID_MESSAGE",
                        "Field 'type' is required and must be a string",
                    ),
                )
                print("Rejected a message with a missing/invalid type")
                continue

            message_type = message_type.strip()

            # "data" is optional, but if present it must be an object
            if data is None:
                data = {}

            if not isinstance(data, dict):
                await manager.send(
                    websocket,
                    error_message(
                        request_id,
                        "INVALID_MESSAGE",
                        "Field 'data' must be a JSON object",
                    ),
                )
                print(f"Rejected {message_type} with a non-object data field")
                continue

            # --- dispatch ---
            handler = HANDLERS.get(message_type)

            if handler is None:
                await manager.send(
                    websocket,
                    error_message(
                        request_id,
                        "UNKNOWN_MESSAGE_TYPE",
                        f"Unsupported message type: {message_type}",
                    ),
                )
                print(f"Unknown message type received: {message_type}")
                continue

            # ---- one authentication gate for every protected type ----
            # Resolved once, from ConnectionManager only, and handed to
            # the handler so it never has to look it up again.
            username = manager.get_username(websocket)

            if message_type in AUTH_REQUIRED_TYPES and username is None:
                await manager.send(
                    websocket,
                    error_message(
                        request_id,
                        "NOT_AUTHENTICATED",
                        f"You must be logged in to use {message_type}",
                    ),
                )
                print(
                    f"Rejected {message_type} from an "
                    f"unauthenticated connection"
                )
                continue

            await handler(websocket, request_id, data, username)

    except WebSocketDisconnect:
        pass
    finally:
        cleanup_connection(websocket)
