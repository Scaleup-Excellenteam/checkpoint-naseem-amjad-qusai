import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

if __package__:
    from .accounts import AccountStore
    from .auth import AuthService
    from .authorization import authorize
    from .validation import validate_chat_message
    from .dlp import DLPService
    from .security_decision import Decision
    from .anti_bot import AntiBotService
    from .security_pipeline import SecurityPipeline
    from . import reason_codes as reasons
else:
    from accounts import AccountStore
    from auth import AuthService
    from authorization import authorize
    from validation import validate_chat_message
    from dlp import DLPService
    from security_decision import Decision
    from anti_bot import AntiBotService
    from security_pipeline import SecurityPipeline
    import reason_codes as reasons

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

    async def send(self, websocket: WebSocket, message: dict):
        await websocket.send_json(message)

    async def broadcast(self, message: dict):
        for websocket, username in list(self.active_connections.items()):
            if username is not None:
                try:
                    await websocket.send_json(message)
                except Exception:
                    self.disconnect(websocket)


manager = ConnectionManager()
auth_service = AuthService(AccountStore(Path(__file__).with_name("accounts.sqlite3")))
# No production detectors until the Day-2 DLP rules are supplied.
dlp_service = DLPService()
# Neutral until production reputation requirements are supplied.
anti_bot_service = AntiBotService()


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "connected_clients": len(manager.active_connections)
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)

    print("New WebSocket connection")

    try:
        while True:
            try:
                message = await websocket.receive_json()
            except ValueError:
                # Invalid JSON must not terminate an otherwise usable connection.
                await manager.send(websocket, {
                    "type": "ERROR",
                    "request_id": None,
                    "data": {"reason": reasons.INVALID_MESSAGE},
                })
                continue

            # Backend structural guard: semantic/security layers only see values
            # from a mapping-shaped envelope. Malformed JSON values must not end
            # the connection loop.
            if not isinstance(message, dict):
                await manager.send(websocket, {
                    "type": "ERROR",
                    "request_id": None,
                    "data": {"reason": reasons.INVALID_MESSAGE},
                })
                continue

            message_type = message.get("type")
            data = message.get("data", {})
            request_id = message.get("request_id")
            if not isinstance(request_id, str) or not request_id.strip():
                request_id = None

            # Authentication owns accounts; this handler owns socket identity/delivery.
            if message_type in ("SIGNUP", "LOGIN"):
                if request_id is None:
                    result = {"success": False, "reason": reasons.INVALID_REQUEST_ID}
                    request_id = None
                elif manager.get_username(websocket) is not None:
                    result = {"success": False, "reason": reasons.ALREADY_AUTHENTICATED}
                else:
                    credentials = data if isinstance(data, dict) else {}
                    operation = (auth_service.signup if message_type == "SIGNUP"
                                 else auth_service.login)
                    result = await asyncio.to_thread(
                        operation, credentials.get("username"), credentials.get("password")
                    )
                    if message_type == "LOGIN" and result["success"]:
                        manager.set_username(websocket, result["username"])

                await manager.send(websocket, {
                    "type": f"{message_type}_RESULT",
                    "request_id": request_id,
                    "data": result,
                })

            # CHAT
            elif message_type == "CHAT_MESSAGE":

                username = manager.get_username(websocket)
                authorization = authorize(message_type, username)

                if not authorization.allowed:
                    await manager.send(
                        websocket,
                        {
                            "type": "ERROR",
                            "request_id": request_id,
                            "data": {
                                "reason": authorization.reason
                            }
                        }
                    )
                    continue

                # Backend extracts values; security checks semantics, not envelopes.
                content = data.get("content") if isinstance(data, dict) else None
                validation = validate_chat_message(content)

                if not validation.valid:
                    await manager.send(
                        websocket,
                        {
                            "type": "ERROR",
                            "request_id": request_id,
                            "data": {
                                "reason": validation.reason
                            }
                        }
                    )
                    continue

                # Connection metadata belongs to Backend, never to client JSON.
                client = getattr(websocket, "client", None)
                address = getattr(client, "host", None)
                security_result = SecurityPipeline(anti_bot_service, dlp_service).evaluate(
                    content, address, username
                )
                if security_result.decision is Decision.BLOCK:
                    await manager.send(websocket, {
                        "type": "MESSAGE_RESULT",
                        "request_id": request_id,
                        "data": {"success": False, "reason": security_result.reason},
                    })
                    continue

                # Server adds the sender.
                # We do NOT trust a sender field from the client.
                outgoing_message = {
                    "type": "NEW_MESSAGE",
                    "data": {
                        "sender": username,
                        "content": content
                    }
                }

                await manager.broadcast(outgoing_message)
                if request_id is not None:
                    await manager.send(websocket, {
                        "type": "MESSAGE_RESULT",
                        "request_id": request_id,
                        "data": {"success": True},
                    })

            else:
                await manager.send(
                    websocket,
                    {
                        "type": "ERROR",
                        "request_id": request_id,
                        "data": {
                            "reason": reasons.UNKNOWN_MESSAGE_TYPE
                        }
                    }
                )

    except WebSocketDisconnect:
        username = manager.get_username(websocket)

        manager.disconnect(websocket)

        if username:
            print(f"{username} disconnected")
        else:
            print("Unknown client disconnected")
