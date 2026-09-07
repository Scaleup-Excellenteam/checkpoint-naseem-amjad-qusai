import test from 'node:test';
import assert from 'node:assert/strict';
import { ChatSocket } from '../js/socket.js';
import { serverEndpoints } from '../js/config.js';
import { passwordChecks } from '../js/screens/signup.js';

class FakeSocket {
  constructor() { this.sent = []; FakeSocket.latest = this; }
  send(raw) { this.sent.push(JSON.parse(raw)); }
  close() { this.onclose?.(); }
  reply(message) { this.onmessage({ data: JSON.stringify(message) }); }
}
function connected(options = {}) {
  const client = new ChatSocket({ WebSocketClass: FakeSocket, timeoutMs: 100, ...options });
  client.connect('ws://test/ws');
  const wire = FakeSocket.latest; wire.onopen();
  return { client, wire };
}

test('out-of-order responses resolve the matching request, not the oldest', async () => {
  const { client, wire } = connected();
  const a = client.request('JOIN_ROOM', { room: 'a' });
  const b = client.request('JOIN_ROOM', { room: 'b' });
  assert.notEqual(wire.sent[0].request_id, wire.sent[1].request_id);
  wire.reply({ type: 'JOIN_ROOM_RESULT', request_id: wire.sent[1].request_id, data: { success: true, room: 'b' } });
  wire.reply({ type: 'JOIN_ROOM_RESULT', request_id: wire.sent[0].request_id, data: { success: true, room: 'a' } });
  assert.equal((await a).room, 'a'); assert.equal((await b).room, 'b'); client.disconnect();
});
test('legacy login without request_id never grants success', async () => {
  const { client, wire } = connected();
  const result = client.request('LOGIN', { username: 'test', password: 'Test123!' });
  wire.reply({ type: 'LOGIN_RESULT', data: { success: true } });
  await assert.rejects(result, { code: 'CONTRACT_MISMATCH' });
  assert.equal(client.state, 'disconnected');
});
test('security block rejects the request with its reason', async () => {
  const { client, wire } = connected();
  const result = client.request('CHAT_MESSAGE', { room: 'a', content: 'test' });
  wire.reply({ type: 'MESSAGE_RESULT', request_id: wire.sent[0].request_id, data: { success: false, reason: 'DLP_SENSITIVE_CONTENT' } });
  await assert.rejects(result, { code: 'DLP_SENSITIVE_CONTENT' }); client.disconnect();
});
test('timeout does not resend; late reply does not resolve another action', async () => {
  const { client, wire } = connected({ timeoutMs: 10 });
  await assert.rejects(client.request('CHAT_MESSAGE', { room: 'a', content: 'x' }), { code: 'REQUEST_TIMEOUT' });
  wire.reply({ type: 'MESSAGE_RESULT', request_id: wire.sent[0].request_id, data: { success: true } });
  assert.equal(wire.sent.length, 1); assert.equal(client.pending.size, 0); client.disconnect();
});
test('disconnect rejects pending requests and ignores old connection events', async () => {
  const { client, wire } = connected();
  const result = client.request('LOGIN', {});
  client.disconnect(); await assert.rejects(result, { code: 'DISCONNECTED' });
  client.connect('ws://test/ws'); FakeSocket.latest.onopen(); wire.onclose();
  assert.equal(client.state, 'connected'); client.disconnect();
});
test('malformed event closes connection; well-formed broadcast is delivered', () => {
  const received = [];
  const { client, wire } = connected({ onMessage: (data) => received.push(data) });
  wire.reply({ type: 'NEW_MESSAGE', request_id: null, data: { room: 'a', sender: 'b', content: '<b>text</b>' } });
  assert.equal(received.length, 1);
  wire.onmessage({ data: '{bad' }); assert.equal(client.state, 'disconnected');
});
test('response type mismatch rejects instead of accepting unrelated success', async () => {
  const { client, wire } = connected();
  const result = client.request('LOGIN', {});
  wire.reply({ type: 'SIGNUP_RESULT', request_id: wire.sent[0].request_id, data: { success: true } });
  await assert.rejects(result, { code: 'CONTRACT_MISMATCH' });
});
test('https uses wss and URLs with embedded credentials are rejected', () => {
  assert.equal(serverEndpoints('https://example.org').websocket, 'wss://example.org/ws');
  assert.throws(() => serverEndpoints('http://user:secret@example.org'));
});
test('password boundary and composition rules', () => {
  assert.equal(passwordChecks('Abc12!x').length, false);
  assert.equal(Object.values(passwordChecks('Abc12!xy')).every(Boolean), true);
  assert.equal(Object.values(passwordChecks('abcdefgh')).every(Boolean), false);
});

test('room catalog response resolves LIST_ROOMS without a success field', async () => {
  const { client, wire } = connected();
  const result = client.request('LIST_ROOMS', {});
  wire.reply({
    type: 'ROOMS_LIST', request_id: wire.sent[0].request_id,
    data: { rooms: [{ name: 'pizza', members: 2, joined: true }] },
  });
  assert.deepEqual(await result, { rooms: [{ name: 'pizza', members: 2, joined: true }] });
  client.disconnect();
});
