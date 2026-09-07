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
- Successful login opens the real room catalog returned by the server. The login screen no longer links to the old fictional preview.
- `/#live` requires successful login before room access; entering its URL does not authenticate a user.
- The integrated Python server implements signup, password login, request IDs, the `pizza` and `football` rooms, room routing and message acknowledgments. `authenticationReady` is enabled in `js/config.js`, so the live UI sends credentials according to Contract v1.
- The server settings panel allows independent WebSocket connection and HTTP /health checks. Health is a point-in-time check, not continuous monitoring or proof of a logged-in session.

## Configuration

`js/config.js` contains the default server URL, 10-second request timeout, authentication readiness, optional agreed room names and optional maximum message length. The UI lets users change the server URL while disconnected. HTTPS URLs map to WSS. No URL, password or session is saved in browser storage.

After login the frontend sends `LIST_ROOMS` and validates the correlated `ROOMS_LIST` response. The server returns room names, member counts, and the requesting user's joined state without exposing member usernames. Those real rooms are rendered as cards with a join action. The `/health` room map remains available for a public status check, but it does not grant authentication or membership. The frontend and server both enforce a 4096-character message limit.

The normal integrated setup uses one origin, so CORS is not involved. The backend also allows GET `/health` from `http://127.0.0.1:5500` and `http://localhost:5500` for standalone frontend development. Set `TSPO_FRONTEND_ORIGINS` to a comma-separated origin list when using another development origin.

### VirusTotal address reputation

Anti-Bot can use the VirusTotal API v3 to check both the server-derived public IP address of a WebSocket client and HTTP(S) URLs found in a chat message. Keep the API key outside the repository and export it before starting the server:

```sh
read -rsp "VirusTotal API key: " VIRUSTOTAL_API_KEY; echo
export VIRUSTOTAL_API_KEY
export TSPO_VT_MALICIOUS_THRESHOLD=1
uvicorn server.server:app --host 0.0.0.0 --port 8000
```

The key is sent only in VirusTotal's `x-apikey` header. Results are cached for 15 minutes to conserve the public API quota. Private, loopback and other non-public addresses are skipped, so a local WSL/LAN test normally records `virustotal_non_public_address` for the peer while public URLs in its messages are still checked. A URL report with enough malicious detections blocks the message with the existing `MALICIOUS_ADDRESS` Contract reason before DLP or room delivery. URL text and query parameters are sent to VirusTotal for the report lookup but are never written to the security log.

The chat path reads existing VirusTotal URL reports and does not submit unknown URLs for a new public scan. A missing report, API error, or quota failure is recorded as incomplete evidence and does not falsely label the URL malicious. If `VIRUSTOTAL_API_KEY` is absent, the existing neutral unconfigured verdicts remain in use.

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

Covers out-of-order responses, legacy login rejection, DLP errors, room catalog responses, timeout without retry, disconnect cleanup, stale socket events, malformed payloads, response-type mismatch, URL validation and password boundaries.

For a repeatable browser test, open http://127.0.0.1:8000/tests/integration.html. A red TEST FIXTURE banner identifies the isolated simulated backend. The fixture changes authenticationReady in memory only and makes no real WebSocket connections. Connect using the panel, then use demo / Demo123!, join pizza or team, send text, send BLOCK for a simulated DLP rejection, leave and disconnect. Registration is in-memory only. This fixture verifies UI integration; it does not validate the team's real server. Do not deploy the tests directory as a production application.

Also verify empty fields, mismatched passwords, signup return-to-login, narrow screens, live room cards, literal HTML in messages, room isolation and navigation. The Google font has a local fallback.

## Remaining team work

Run a real multi-laptop test before merging to `main`. Authentication, room isolation, room catalog discovery, and the security pipeline are integrated. Semantic recipe DLP uses the optional `requirements-dlp.txt` model dependency and falls back to an unconfigured detector when it is unavailable. Anti-Bot uses VirusTotal when `VIRUSTOTAL_API_KEY` is configured and otherwise reports neutral unconfigured evidence.
