import asyncio
import logging
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

try:
    from room import Room
    import database
    from room_store import RoomStore
    from message_store import MessageStore
    from security_store import SecurityEventStore
    from accounts import AccountStore
    from auth import AuthService
    from authorization import authorize
    from validation import validate_chat_message
    from dlp import DLPService
    from security_decision import Decision
    from anti_bot import AntiBotService
    from security_pipeline import SecurityPipeline
    from embedding_dlp import RecipeEmbeddingDetector
    from virustotal import VirusTotalReputationProvider
    import reason_codes as reasons

except ImportError:  # when launched as "uvicorn server.server:app"
    from server.room import Room
    from server import database
    from server.room_store import RoomStore
    from server.message_store import MessageStore
    from server.security_store import SecurityEventStore
    from server.accounts import AccountStore
    from server.auth import AuthService
    from server.authorization import authorize
    from server.validation import validate_chat_message
    from server.dlp import DLPService
    from server.security_decision import Decision
    from server.anti_bot import AntiBotService
    from server.security_pipeline import SecurityPipeline
    from server.embedding_dlp import RecipeEmbeddingDetector
    from server.virustotal import VirusTotalReputationProvider
    from server import reason_codes as reasons

LOG_LEVEL = os.environ.get("TSPO_LOG_LEVEL", "INFO").upper()


def configure_logging(level: str = LOG_LEVEL) -> None:
    """Configure logging once, at application startup.

    Without this the root logger has no handler, so its default WARNING
    level silently discards every INFO record - including the ALLOW
    decisions emitted by security_logging.
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
    )
    # basicConfig() is a no-op once a handler exists (uvicorn or pytest may
    # install one first), so set the level explicitly either way.
    logging.getLogger().setLevel(level)


configure_logging()

logger = logging.getLogger(__name__)

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
                # Broken socket: clean it up, keep delivering to the rest.
                # Metadata only - `message` carries chat content and must
                # never reach the logs.
                logger.warning(
                    "Delivery failed, dropping connection for user=%s",
                    username, exc_info=True,
                )
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

# One SQLite file holds accounts, rooms, messages and security events.
# The schema is created once here, not on every database operation.
# TSPO_DATABASE_PATH lets the test suite (and any throwaway run) point at a
# different file, so nothing but a real server touches the developer's copy.
DATABASE_PATH = Path(
    os.environ.get("TSPO_DATABASE_PATH")
    or Path(__file__).with_name("accounts.sqlite3")
)
database.initialize_schema(DATABASE_PATH)

auth_service = AuthService(AccountStore(DATABASE_PATH))
room_store = RoomStore(DATABASE_PATH)
message_store = MessageStore(DATABASE_PATH)
security_store = SecurityEventStore(DATABASE_PATH)

try:
    dlp_service = DLPService([RecipeEmbeddingDetector()])
except RuntimeError:
    # Keep the server importable if the embedding dependency/model is unavailable.
    dlp_service = DLPService()
virustotal_api_key = os.environ.get("VIRUSTOTAL_API_KEY")
if virustotal_api_key:
    virustotal_threshold = int(os.environ.get("TSPO_VT_MALICIOUS_THRESHOLD", "1"))
    virustotal_provider = VirusTotalReputationProvider(
        virustotal_api_key,
        malicious_threshold=virustotal_threshold,
    )
    anti_bot_service = AntiBotService(virustotal_provider, virustotal_provider)
else:
    anti_bot_service = AntiBotService()

DEFAULT_ROOMS = ("pizza", "football")


def load_rooms(store) -> dict:
    """Rebuild the in-memory room registry from persistent storage.

    Only room *definitions* are loaded. Membership is live connection state,
    so every room starts empty on boot and after a restart.
    """
    return {name: Room(name) for name in store.all_names()}


# The built-in rooms are persisted once, then the runtime dict is loaded from
# the database. `rooms` remains the live state the handlers work with.
room_store.ensure_defaults(DEFAULT_ROOMS)
rooms = load_rooms(room_store)


def record_security_event(event, result, username, address=None):
    """Persist one security decision.

    SecurityPipeline calls this for every stage, so persistence happens
    whether or not a log record is emitted: it does not depend on the
    logger's level. Metadata only - message content is never passed in, and
    security_events has no column for it. A storage failure is logged and
    swallowed so it can never change a security outcome.
    """
    try:
        security_store.record(
            event=event,
            decision=result.decision.value,
            username=username,
            reason=result.reason,
            verdict=result.verdict,
            category=result.category,
            score=None if result.score is None else round(result.score, 6),
        )
    except Exception:
        logger.warning(
            "Could not persist security event event=%s user=%s",
            event, username, exc_info=True,
        )


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
                logger.info("%s logged in", username)
        elif message_type == "SIGNUP" and result["success"]:
            logger.info("Account created for %s", result["username"])

    if not result["success"]:
        # The reason code only. The client-supplied username is not echoed
        # into the log on failure.
        logger.warning("%s rejected reason=%s", message_type,
                       result.get("reason"))

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
        logger.warning("Rejected JOIN_ROOM from an unauthenticated connection")
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

    logger.info("%s joined room %s", username, room.name)


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
        logger.warning("Rejected LEAVE_ROOM from an unauthenticated connection")
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

    logger.info("%s left room %s", username, room.name)


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
        logger.warning("Rejected CHAT_MESSAGE from an unauthenticated connection")
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
        logger.warning(
            "%s tried to send to room %s without being a member",
            username, room.name,
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
    security_result = await asyncio.to_thread(
        SecurityPipeline(
            anti_bot_service, dlp_service, on_decision=record_security_event
        ).evaluate,
        content,
        address,
        username,
    )
    if security_result.decision is Decision.BLOCK:
        # Metadata only. security_logging already records the full decision;
        # this adds the room, which that layer does not receive.
        logger.warning(
            "Security BLOCK user=%s room=%s reason=%s",
            username, room.name, security_result.reason,
        )
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

    # 5. persist. Reached only after Decision.ALLOW: the BLOCK branch above
    #    returned, so blocked content has no path into the database. Storage
    #    happens before delivery and gates it: if the archive rejects the
    #    message, nobody receives it.
    try:
        await asyncio.to_thread(message_store.add, room.name, username, content)
    except Exception:
        # Traceback and safe metadata for us; the client is told only that
        # the server failed. Delivery is abandoned rather than sending a
        # message the archive never recorded.
        logger.exception(
            "Could not persist message user=%s room=%s", username, room.name,
        )
        await manager.send(
            websocket,
            error_message(
                request_id,
                reasons.INTERNAL_SERVER_ERROR,
                "The message could not be stored and was not delivered",
            ),
        )
        return

    # 6. build the server-authored frame. The sender is the server's value.
    outgoing_message = {
        "type": "NEW_MESSAGE",
        "request_id": None,
        "data": {
            "sender": username,
            "room": room.name,
            "content": content,
        },
    }

    # 7. recipients = this room's members only (a copy, so a disconnect
    #    during delivery cannot mutate what we are iterating)
    recipients = room.get_members()

    # 8. targeted delivery. The sender is a member, so they get their own
    #    message back: the server is the source of truth for the chat log.
    delivered = await manager.send_to_users(recipients, outgoing_message)

    # Routing metadata only: sender, room and recipient count. The
    # message content is deliberately absent.
    logger.info(
        "Message routed user=%s room=%s recipients=%d",
        username, room.name, delivered,
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


async def handle_list_rooms(websocket: WebSocket, request_id, data: dict):
    """Return the server room catalog without exposing member identities."""
    username = manager.get_username(websocket)
    authorization = authorize("LIST_ROOMS", username)
    if not authorization.allowed:
        await manager.send(
            websocket,
            error_message(
                request_id,
                authorization.reason,
                "User must be logged in before listing rooms",
            ),
        )
        return

    room_list = [
        {
            "name": room.name,
            "members": len(room.members),
            "joined": room.has_member(username),
        }
        for room in sorted(rooms.values(), key=lambda candidate: candidate.name)
    ]
    await manager.send(
        websocket,
        {
            "type": "ROOMS_LIST",
            "request_id": request_id,
            "data": {"rooms": room_list},
        },
    )
    logger.info("%s listed %d room(s)", username, len(room_list))


HANDLERS = {
    "SIGNUP": handle_signup,
    "LOGIN": handle_login,
    "JOIN_ROOM": handle_join_room,
    "LEAVE_ROOM": handle_leave_room,
    "CHAT_MESSAGE": handle_chat_message,
    "LIST_ROOMS": handle_list_rooms,
}

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
        logger.info("Unknown client disconnected")
        return

    left = leave_all_rooms(username)

    if left:
        logger.info("%s removed from rooms: %s", username, ", ".join(left))

    logger.info("%s disconnected", username)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)

    logger.info("New WebSocket connection")

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
                logger.warning("Rejected malformed JSON")
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
                logger.warning("Rejected a non-object message")
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
                logger.warning("Rejected a message with a missing/invalid type")
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
                logger.warning("Rejected %s with a non-object data field", message_type)
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
                logger.warning("Unknown message type received: %s", message_type)
                continue

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
                logger.warning(
                    "Rejected %s from an unauthenticated connection",
                    message_type,
                )
                continue

            await handler(websocket, request_id, data)

    except WebSocketDisconnect:
        pass
    except Exception:
        # Make an unexpected failure visible, then propagate exactly as
        # before. Tracebacks do not include local variables, so no message
        # content is exposed.
        logger.exception(
            "Unhandled error on WebSocket connection for user=%s",
            manager.get_username(websocket),
        )
        raise
    finally:
        cleanup_connection(websocket)


# Keep this mount last so API and WebSocket routes take precedence. Serving the
# frontend from FastAPI gives local and LAN users one address for the whole app.
frontend_directory = Path(__file__).resolve().parent.parent / "frontend"
app.mount(
    "/",
    StaticFiles(directory=frontend_directory, html=True),
    name="frontend",
)
