import asyncio
import json
import uuid

import websockets


SERVER_IP = "172.20.10.2"
PORT = 8000


async def receive_messages(websocket):
    while True:
        try:
            message = await websocket.recv()
            data = json.loads(message)

            message_type = data.get("type")
            payload = data.get("data", {})

            if message_type == "NEW_MESSAGE":
                sender = payload.get("sender")
                content = payload.get("content")

                print(f"\n{sender}: {content}")

            elif message_type == "JOIN_ROOM_RESULT":
                if payload.get("success"):
                    print(f"\nJoined room '{payload.get('room')}'")
                else:
                    print("\nJoin failed:", payload.get("reason"))

            elif message_type == "LEAVE_ROOM_RESULT":
                if payload.get("success"):
                    print(f"\nLeft room '{payload.get('room')}'")
                else:
                    print("\nLeave failed:", payload.get("reason"))

            elif message_type == "LOGIN_RESULT":
                print("Login result:", payload)

            elif message_type == "ERROR":
                print("Server error:", payload.get("reason"))

            else:
                print("Server:", data)

        except websockets.ConnectionClosed:
            print("\nConnection to server closed.")
            break


async def client_program():
    uri = f"ws://{SERVER_IP}:{PORT}/ws"

    username = input("Username: ").strip()

    async with websockets.connect(uri) as websocket:

        # First message: LOGIN
        login_message = {
            "type": "LOGIN",
            "data": {
                "username": username
            }
        }

        await websocket.send(json.dumps(login_message))

        # Separate task keeps listening for server messages
        receiver_task = asyncio.create_task(
            receive_messages(websocket)
        )

        while True:
            message = await asyncio.to_thread(input, "-> ")

            if message.lower().strip() == "bye":
                break

            # /join <room>
            if message.strip().startswith("/join"):
                room = message.strip()[len("/join"):].strip()

                join_message = {
                    "type": "JOIN_ROOM",
                    "request_id": str(uuid.uuid4()),
                    "data": {
                        "room": room
                    }
                }

                await websocket.send(json.dumps(join_message))
                continue

            chat_message = {
                "type": "CHAT_MESSAGE",
                "data": {
                    "content": message
                }
            }

            await websocket.send(
                json.dumps(chat_message)
            )

        receiver_task.cancel()


if __name__ == "__main__":
    asyncio.run(client_program())