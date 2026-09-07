# TSPO Frontend

Hebrew RTL interface in HTML, CSS and JavaScript. No build step or npm installation is required for normal use.

## Run

From the repository root, activate the virtual environment and run the integrated server:

```sh
uvicorn server.server:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. FastAPI serves the website, `/health`, and `/ws` from the same address. No separate frontend server is needed. Use HTTP, not file://, because scripts use native JavaScript modules.

## Modes and backend compatibility

- Login and signup validate input and are wired to Contract v1 requests.
- Public preview links use only fictional rooms, members and local messages. They do not grant authentication or membership.
- `/#live` is the real connection view. It requires successful login before room access; entering its URL does not authenticate a user.
- The integrated Python server implements signup, password login, request IDs, the `pizza` and `football` rooms, room routing and message acknowledgments. `authenticationReady` is enabled in `js/config.js`, so the live UI sends credentials according to Contract v1.
- The server settings panel allows independent WebSocket connection and HTTP /health checks. Health is a point-in-time check, not continuous monitoring or proof of a logged-in session.

## Configuration

`js/config.js` contains the default server URL, 10-second request timeout, authentication readiness, optional agreed room names and optional maximum message length. The UI lets users change the server URL while disconnected. HTTPS URLs map to WSS. No URL, password or session is saved in browser storage.

Finalize member-list messages and updates with the team. No new WebSocket protocol types have been invented. Until then, the live view accepts a known room name and optionally suggests names from config.rooms. The current `/health` response also exposes a room/count map; a successful health check adds its validated room names as suggestions. This discovery grants no authentication or membership. Member lists remain unavailable in live mode. The demo catalog is never treated as the server catalog. The frontend and server both enforce a 4096-character message limit.

The normal integrated setup uses one origin, so CORS is not involved. The backend also allows GET `/health` from `http://127.0.0.1:5500` and `http://localhost:5500` for standalone frontend development. Set `TSPO_FRONTEND_ORIGINS` to a comma-separated origin list when using another development origin.

## Real request flow

Open server settings and connect, then sign up or log in. Signup success returns to login. Login success opens the live room view. Join and leave wait for matching successful server replies. Only NEW_MESSAGE for the active room is rendered; sender comes from the server and all content is inserted as text.

CHAT_MESSAGE expects a MESSAGE_RESULT for success or rejection, while NEW_MESSAGE is the separate broadcast event. The team must confirm success acknowledgment semantics and that JOIN_ROOM_RESULT is sent before room broadcasts. No delivery/read guarantee is inferred from success.

Pending buttons prevent duplicate actions. Timeout never triggers resubmission. Membership-changing or chat request timeout disconnects to clear uncertain session state; the operation may already have happened on the server. Reconnection is explicit, followed by login and rejoining. Passwords are not retained or replayed. Disconnect clears authenticated user, active room and live messages. A request without request_id or an incompatible response fails closed.

## Password policy

At least 8 Unicode code points, one uppercase English letter, one lowercase English letter, one digit and one ASCII punctuation character. Both signup fields must match. The backend must enforce the same policy. Username rules and exact maximum lengths still need agreement.

## Files

- index.html / css/styles.css: screens and responsive layout.
- js/app.js: navigation and preview/live separation.
- js/socket.js: WebSocket lifetime, request correlation, validation, timeouts.
- js/live.js: account, membership, messaging and health UI.
- js/errors.js: readable reason codes.
- js/screens/: authentication forms and local room/chat previews.

## Tests

With Node.js installed:

```sh
node --test frontend/tests/socket.test.mjs
```

Covers out-of-order responses, legacy login rejection, DLP errors, timeout without retry, disconnect cleanup, stale socket events, malformed payloads, response-type mismatch, URL validation and password boundaries.

For a repeatable browser test, open http://127.0.0.1:5500/tests/integration.html. A red TEST FIXTURE banner identifies the isolated simulated backend. The fixture changes authenticationReady in memory only and makes no real WebSocket connections. Connect using the panel, then use demo / Demo123!, join pizza or team, send text, send BLOCK for a simulated DLP rejection, leave and disconnect. Registration is in-memory only. This fixture verifies UI integration; it does not validate the team's real server. Do not deploy the tests directory as a production application.

Also verify empty fields, mismatched passwords, signup return-to-login, narrow screens, preview room filtering, literal HTML in messages, room isolation and navigation. The Google font has a local fallback.

## Remaining team work

Run a real multi-laptop test before merging to `main`. Authentication, room isolation and the security pipeline are integrated. The default DLP detector list and reputation provider are deliberately unconfigured; the security owner still needs to connect the agreed production detectors/provider. Room member discovery is not part of Contract v1 and remains unavailable in the live UI.
