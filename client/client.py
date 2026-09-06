import asyncio
import json
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