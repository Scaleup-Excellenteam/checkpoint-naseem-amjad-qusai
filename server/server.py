from fastapi import FastAPI, WebSocket, WebSocketDisconnect

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

    async def broadcast(self, message: dict):
        for websocket, username in list(self.active_connections.items()):
            if username is not None:
                try:
                    await websocket.send_json(message)
                except Exception:
                    self.disconnect(websocket)


manager = ConnectionManager()


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
            message = await websocket.receive_json()

            message_type = message.get("type")
            data = message.get("data", {})

            # LOGIN
            if message_type == "LOGIN":
                username = data.get("username", "").strip()

                if not username:
                    await manager.send(
                        websocket,
                        {
                            "type": "ERROR",
                            "data": {
                                "reason": "INVALID_USERNAME"
                            }
                        }
                    )
                    continue

                if manager.username_exists(username):
                    await manager.send(
                        websocket,
                        {
                            "type": "LOGIN_RESULT",
                            "data": {
                                "success": False,
                                "reason": "USERNAME_ALREADY_CONNECTED"
                            }
                        }
                    )
                    continue

                manager.set_username(websocket, username)

                await manager.send(
                    websocket,
                    {
                        "type": "LOGIN_RESULT",
                        "data": {
                            "success": True,
                            "username": username
                        }
                    }
                )

                print(f"{username} logged in")

            # CHAT
            elif message_type == "CHAT_MESSAGE":

                username = manager.get_username(websocket)

                if username is None:
                    await manager.send(
                        websocket,
                        {
                            "type": "ERROR",
                            "data": {
                                "reason": "NOT_AUTHENTICATED"
                            }
                        }
                    )
                    continue

                content = data.get("content", "").strip()

                if not content:
                    await manager.send(
                        websocket,
                        {
                            "type": "ERROR",
                            "data": {
                                "reason": "EMPTY_MESSAGE"
                            }
                        }
                    )
                    continue

                print(f"{username}: {content}")

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

            else:
                await manager.send(
                    websocket,
                    {
                        "type": "ERROR",
                        "data": {
                            "reason": "UNKNOWN_MESSAGE_TYPE"
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