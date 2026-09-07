"""CLI test client for the TSPO chat server.

Chat-first UX: `join`/`leave`/`use`/`rooms`/`help`/`bye` are commands, and
anything else is sent as a message to the active room.

The active room is *purely client-side convenience*. Every CHAT_MESSAGE
still carries an explicit "room" field, exactly as Message Contract v1
requires, and the server remains the only authority on membership.
"""

import asyncio
import json
import os
import uuid

import websockets


# override for local testing:  CHAT_SERVER_IP=127.0.0.1 python client/client.py
SERVER_IP = os.environ.get("CHAT_SERVER_IP", "172.20.10.2")
PORT = int(os.environ.get("CHAT_SERVER_PORT", "8000"))

PROMPT = "> "

HELP_TEXT = """Commands:
  join <room>      Join a room
  leave <room>     Leave a room
  use <room>       Select active room
  rooms            Show your joined rooms
  help             Show this help
  bye              Quit

Chat:
  After selecting an active room, just type your message normally.

Example:
  join pizza
  use pizza
  hello everyone"""


# commands and how many words they take, including the command itself.
# An input only counts as a command when the word count matches exactly,
# so "join us tomorrow" stays an ordinary chat message.
COMMAND_ARITY = {
    "join": 2,
    "leave": 2,
    "use": 2,
    "rooms": 1,
    "help": 1,
    "bye": 1,
}


def parse_input(text: str):
    """Turn one line of input into ("command", argument) or ("chat", text).

    Returns None for a blank line.
    """
    text = text.strip()

    if not text:
        return None

    words = text.split()

    # "/join pizza" still works as an alias for "join pizza"
    name = words[0].lstrip("/").lower()

    # legacy explicit form: /msg <room> <text>
    if name == "msg" and words[0].startswith("/") and len(words) >= 3:
        room = words[1]
        content = text.split(None, 2)[2]
        return ("msg", (room, content))

    if name in COMMAND_ARITY and len(words) == COMMAND_ARITY[name]:
        argument = words[1] if len(words) == 2 else None
        return (name, argument)

    return ("chat", text)


class ChatClient:
    def __init__(self, websocket, username):
        self.websocket = websocket
        self.username = username

        # ---- client-side UX state only ----
        # the room plain text is sent to
        self.current_room = None
        # rooms the SERVER has confirmed we joined
        self.joined_rooms = set()
        # request_id -> (action, room), so a result that omits the room
        # (e.g. ROOM_NOT_FOUND) can still be reported against it
        self.pending = {}

        self.running = True

    # ------------------------------------------------------------------
    # output
    # ------------------------------------------------------------------

    def show(self, text: str):
        """Print, then redraw the prompt, so server messages arriving while
        the user is typing do not leave them without a prompt."""
        print(f"\n{text}\n{PROMPT}", end="", flush=True)

    # ------------------------------------------------------------------
    # sending
    # ------------------------------------------------------------------

    async def send(self, message_type: str, data: dict,
                   action: str = None, room: str = None):
        """Send one protocol frame and remember what it was for."""
        request_id = str(uuid.uuid4())

        if action is not None:
            self.pending[request_id] = (action, room)

        await self.websocket.send(json.dumps({
            "type": message_type,
            "request_id": request_id,
            "data": data,
        }))

        return request_id

    # ------------------------------------------------------------------
    # input handling
    # ------------------------------------------------------------------

    async def handle_input(self, text: str) -> bool:
        """Process one line of user input. Returns False to quit."""
        parsed = parse_input(text)

        if parsed is None:
            return True

        command, argument = parsed

        if command == "bye":
            return False

        if command == "help":
            self.show(HELP_TEXT)

        elif command == "rooms":
            self.show(self.format_rooms())

        elif command == "join":
            # local state is NOT touched here: it waits for the server's
            # JOIN_ROOM_RESULT
            await self.send(
                "JOIN_ROOM", {"room": argument},
                action="join", room=argument,
            )

        elif command == "leave":
            await self.send(
                "LEAVE_ROOM", {"room": argument},
                action="leave", room=argument,
            )

        elif command == "use":
            self.select_room(argument)

        elif command == "msg":
            room, content = argument
            await self.send_chat(room, content)

        elif command == "chat":
            if self.current_room is None:
                self.show(
                    "No active room selected.\n"
                    "Join a room and select it first.\n"
                    "\n"
                    "For example:\n"
                    "  join pizza\n"
                    "  use pizza"
                )
            else:
                await self.send_chat(self.current_room, argument)

        return True

    async def send_chat(self, room: str, content: str):
        """Contract v1 unchanged: the room is always explicit on the wire."""
        await self.send(
            "CHAT_MESSAGE",
            {"room": room, "content": content},
            action="chat", room=room,
        )

    def select_room(self, room: str):
        """`use <room>` only picks the active room; it never joins."""
        if room not in self.joined_rooms:
            self.show(
                f"You are not joined to '{room}'. Use: join {room}"
            )
            return

        self.current_room = room
        self.show(f"Active room: {room}")

    def format_rooms(self) -> str:
        if not self.joined_rooms:
            return "You are not joined to any rooms."

        lines = ["Joined rooms:"]
        for room in sorted(self.joined_rooms):
            marker = " [active]" if room == self.current_room else ""
            lines.append(f"  * {room}{marker}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # server messages
    # ------------------------------------------------------------------

    def handle_server_message(self, message: dict):
        message_type = message.get("type")
        payload = message.get("data", {})
        request_id = message.get("request_id")

        # what did we send this request for?
        action, requested_room = self.pending.pop(request_id, (None, None))

        if message_type == "NEW_MESSAGE":
            self.show(
                f"[{payload.get('room')}] "
                f"{payload.get('sender')}: {payload.get('content')}"
            )

        elif message_type == "JOIN_ROOM_RESULT":
            self.on_join_result(payload, requested_room)

        elif message_type == "LEAVE_ROOM_RESULT":
            self.on_leave_result(payload, requested_room)

        elif message_type == "MESSAGE_RESULT":
            self.on_message_result(payload, requested_room)

        elif message_type == "ERROR":
            reason = payload.get("reason")
            if action in ("join", "leave"):
                self.show(f"{action.capitalize()} failed: {reason}")
            else:
                self.show(f"Server error: {reason}")

        else:
            self.show(f"Server: {message}")

    def on_join_result(self, payload: dict, requested_room):
        # trust the server's room name; fall back to what we asked for
        room = payload.get("room") or requested_room

        if payload.get("success"):
            self.joined_rooms.add(room)

            lines = [f"Joined room '{room}'"]

            # only auto-select when nothing is active, so an existing
            # active room never changes under the user
            if self.current_room is None:
                self.current_room = room
                lines.append(f"Active room is now '{room}'")

            self.show("\n".join(lines))
            return

        reason = payload.get("reason")

        # the server just told us we ARE a member: resync rather than
        # leaving the user stuck between "already in room" and "not joined"
        if reason == "ALREADY_IN_ROOM" and room:
            self.joined_rooms.add(room)

        self.show(f"Join failed for '{room}': {reason}")

    def on_leave_result(self, payload: dict, requested_room):
        room = payload.get("room") or requested_room

        if payload.get("success"):
            self.joined_rooms.discard(room)

            lines = [f"Left room '{room}'"]

            if self.current_room == room:
                self.current_room = None
                lines.append("No active room selected")

            self.show("\n".join(lines))
            return

        reason = payload.get("reason")

        # the server says we are not a member: resync
        if reason == "NOT_IN_ROOM" and room:
            self.forget_room(room)

        self.show(f"Leave failed for '{room}': {reason}")

    def on_message_result(self, payload: dict, requested_room):
        if payload.get("success"):
            return  # the NEW_MESSAGE echo is the visible confirmation

        room = payload.get("room") or requested_room
        reason = payload.get("reason")

        if reason == "NOT_IN_ROOM" and room:
            self.forget_room(room)

        self.show(f"Message not delivered to '{room}': {reason}")

    def forget_room(self, room: str):
        """Drop a membership the server has denied."""
        self.joined_rooms.discard(room)

        if self.current_room == room:
            self.current_room = None

    # ------------------------------------------------------------------
    # loops
    # ------------------------------------------------------------------

    async def receive_loop(self):
        while True:
            try:
                raw = await self.websocket.recv()
            except websockets.ConnectionClosed:
                self.running = False
                print("\nConnection to server closed.")
                break

            try:
                message = json.loads(raw)
            except ValueError:
                continue

            if isinstance(message, dict):
                self.handle_server_message(message)

    async def run(self):
        receiver_task = asyncio.create_task(self.receive_loop())

        print(HELP_TEXT)
        print()

        try:
            while self.running:
                text = await asyncio.to_thread(input, PROMPT)

                if not self.running:
                    break

                if not await self.handle_input(text):
                    break
        finally:
            receiver_task.cancel()


async def login(websocket) -> str:
    """Ask for a username until the server accepts it."""
    while True:
        username = (await asyncio.to_thread(input, "Username: ")).strip()

        if not username:
            print("Username cannot be empty.")
            continue

        await websocket.send(json.dumps({
            "type": "LOGIN",
            "request_id": str(uuid.uuid4()),
            "data": {"username": username},
        }))

        response = json.loads(await websocket.recv())
        payload = response.get("data", {})

        if response.get("type") == "LOGIN_RESULT" and payload.get("success"):
            print("\nLogin successful.\n")
            return username

        print(f"Login failed: {payload.get('reason')}")


async def client_program():
    uri = f"ws://{SERVER_IP}:{PORT}/ws"

    async with websockets.connect(uri) as websocket:
        username = await login(websocket)

        await ChatClient(websocket, username).run()


if __name__ == "__main__":
    asyncio.run(client_program())
