import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

try:
    from room import Room
    from accounts import AccountStore
    from auth import AuthService
    from authorization import authorize
    from validation import validate_chat_message
    from dlp import DLPService
    from security_decision import Decision
    from anti_bot import AntiBotService
    from security_pipeline import SecurityPipeline
    from embedding_dlp import RecipeEmbeddingDetector
    import reason_codes as reasons

except ImportError:  # when launched as "uvicorn server.server:app"
    from server.room import Room
    from server.accounts import AccountStore
    from server.auth import AuthService
    from server.authorization import authorize
    from server.validation import validate_chat_message
    from server.dlp import DLPService
    from server.security_decision import Decision
    from server.anti_bot import AntiBotService
    from server.security_pipeline import SecurityPipeline
    from server.embedding_dlp import RecipeEmbeddingDetector
    from server import reason_codes as reasons

app = FastAPI()
frontend_origins = [
    origin.strip()
    for origin in os.environ.get(
        "TSPO_FRONTEND_ORIGINS",
        "http://127.0.0.1:5500,http://localhost:5500",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_methods=["GET"],
    allow_headers=[],
)


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
auth_service = AuthService(AccountStore(Path(__file__).with_name("accounts.sqlite3")))
try:
    dlp_service = DLPService([RecipeEmbeddingDetector()])
except RuntimeError:
    # Keep the server importable if the embedding dependency/model is unavailable.
    dlp_service = DLPService()
anti_bot_service = AntiBotService()

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

async def handle_authentication(
    websocket: WebSocket, request_id, data: dict, message_type: str
):
    if not isinstance(request_id, str) or not request_id.strip():
        result = {"success": False, "reason": reasons.INVALID_REQUEST_ID}
    elif manager.get_username(websocket) is not None:
        result = {"success": False, "reason": reasons.ALREADY_AUTHENTICATED}
    else:
        operation = auth_service.signup if message_type == "SIGNUP" else auth_service.login
        result = await asyncio.to_thread(
            operation,
            data.get("username"),
            data.get("password"),
        )
        if message_type == "LOGIN" and result["success"]:
            username = result["username"]
            if manager.username_exists(username):
                result = {
                    "success": False,
                    "reason": "USERNAME_ALREADY_CONNECTED",
                }
            else:
                manager.set_username(websocket, username)
                print(f"{username} logged in")

    await manager.send(
        websocket,
        {
            "type": f"{message_type}_RESULT",
            "request_id": request_id if isinstance(request_id, str) else None,
            "data": result,
        },
    )


async def handle_signup(websocket: WebSocket, request_id, data: dict):
    await handle_authentication(websocket, request_id, data, "SIGNUP")


async def handle_login(websocket: WebSocket, request_id, data: dict):
    await handle_authentication(websocket, request_id, data, "LOGIN")


async def handle_join_room(websocket: WebSocket, request_id, data: dict):
    username = manager.get_username(websocket)

    authorization = authorize("JOIN_ROOM", username)
    if not authorization.allowed:
        await manager.send(
            websocket,
            error_message(
                request_id,
                authorization.reason,
                "User must be logged in before joining a room",
            ),
        )
        print("Rejected JOIN_ROOM from an unauthenticated connection")
        return

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


async def handle_leave_room(websocket: WebSocket, request_id, data: dict):
    username = manager.get_username(websocket)

    authorization = authorize("LEAVE_ROOM", username)
    if not authorization.allowed:
        await manager.send(
            websocket,
            error_message(
                request_id,
                authorization.reason,
                "User must be logged in before leaving a room",
            ),
        )
        print("Rejected LEAVE_ROOM from an unauthenticated connection")
        return

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


async def handle_chat_message(websocket: WebSocket, request_id, data: dict):
    """Route one chat message to the members of one room.

    Failure convention follows JOIN_ROOM / LEAVE_ROOM:
      - protocol/auth problems      -> ERROR
      - room-level outcomes         -> MESSAGE_RESULT with success=False
    """
    # 1. the sender is decided by the server, never by the client.
    #    data["sender"] is ignored on purpose.
    username = manager.get_username(websocket)

    authorization = authorize("CHAT_MESSAGE", username)
    if not authorization.allowed:
        await manager.send(
            websocket,
            error_message(
                request_id,
                authorization.reason,
                "User must be logged in before sending a message",
            ),
        )
        print("Rejected CHAT_MESSAGE from an unauthenticated connection")
        return

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

    # 4. Validate content before any security decision or delivery.
    content = data.get("content")
    validation = validate_chat_message(content)
    if not validation.valid:
        await manager.send(
            websocket,
            error_message(
                request_id,
                validation.reason,
                "Message content is invalid",
            ),
        )
        return

    content = content.strip()
    client = getattr(websocket, "client", None)
    address = getattr(client, "host", None)
    security_result = SecurityPipeline(anti_bot_service, dlp_service).evaluate(
        content, address, username
    )
    if security_result.decision is Decision.BLOCK:
        await manager.send(
            websocket,
            {
                "type": "MESSAGE_RESULT",
                "request_id": request_id,
                "data": {
                    "success": False,
                    "decision": "BLOCK",
                    "reason": security_result.reason,
                    "room": room.name,
                },
            },
        )
        return

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

    if request_id is not None:
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


HANDLERS = {
    "SIGNUP": handle_signup,
    "LOGIN": handle_login,
    "JOIN_ROOM": handle_join_room,
    "LEAVE_ROOM": handle_leave_room,
    "CHAT_MESSAGE": handle_chat_message,
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
                message = await websocket.receive_json()
            except (ValueError, KeyError, TypeError):
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

            await handler(websocket, request_id, data)

    except WebSocketDisconnect:
        pass
    finally:
        cleanup_connection(websocket)
