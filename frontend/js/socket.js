const replies = {
  SIGNUP: 'SIGNUP_RESULT', LOGIN: 'LOGIN_RESULT', JOIN_ROOM: 'JOIN_ROOM_RESULT',
  LEAVE_ROOM: 'LEAVE_ROOM_RESULT', CHAT_MESSAGE: 'MESSAGE_RESULT',
  LIST_ROOMS: 'ROOMS_LIST', CREATE_ROOM: 'CREATE_ROOM_RESULT',
};
const record = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

export class ConnectionError extends Error {
  constructor(code) { super(code); this.code = code; }
}

export class ChatSocket {
  constructor({ timeoutMs = 10000, WebSocketClass = globalThis.WebSocket,
    onState = () => {}, onMessage = () => {}, onError = () => {} } = {}) {
    Object.assign(this, { timeoutMs, WebSocketClass, onState, onMessage, onError });
    this.pending = new Map();
    this.socket = null;
    this.sequence = 0;
    this.session = 0;
    this.state = 'disconnected';
  }
  setState(state) { this.state = state; this.onState(state); }
  connect(url) {
    this.disconnect();
    const socket = new this.WebSocketClass(url);
    this.socket = socket;
    this.session += 1;
    this.setState('connecting');
    this.connectionTimer = setTimeout(() => {
      if (this.socket === socket) this.fail('CONNECT_TIMEOUT');
    }, this.timeoutMs);
    socket.onopen = () => {
      if (this.socket !== socket) return;
      clearTimeout(this.connectionTimer);
      this.setState('connected');
    };
    socket.onmessage = (event) => {
      if (this.socket === socket) this.receive(event.data);
    };
    socket.onerror = () => { if (this.socket === socket) this.fail('CONNECTION_FAILED'); };
    socket.onclose = () => { if (this.socket === socket) this.disconnect(); };
  }
  fail(code) {
    this.onError(new ConnectionError(code));
    this.disconnect(code);
  }
  disconnect(code = 'DISCONNECTED') {
    clearTimeout(this.connectionTimer);
    const socket = this.socket;
    this.socket = null;
    for (const item of this.pending.values()) {
      clearTimeout(item.timer);
      item.reject(new ConnectionError(code));
    }
    this.pending.clear();
    if (socket) socket.close();
    this.setState('disconnected');
  }
  request(type, data) {
    if (!replies[type] || !record(data)) return Promise.reject(new ConnectionError('INVALID_MESSAGE'));
    if (this.state !== 'connected') return Promise.reject(new ConnectionError('DISCONNECTED'));
    const request_id = `${this.session}-${++this.sequence}`;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(request_id);
        reject(new ConnectionError('REQUEST_TIMEOUT'));
      }, this.timeoutMs);
      this.pending.set(request_id, { resolve, reject, timer, expected: replies[type] });
      try { this.socket.send(JSON.stringify({ type, request_id, data })); }
      catch { this.fail('CONNECTION_FAILED'); }
    });
  }
  receive(raw) {
    let message;
    try { message = JSON.parse(raw); } catch { return this.fail('CONTRACT_MISMATCH'); }
    if (!record(message) || typeof message.type !== 'string' || !record(message.data)
      || !(message.request_id === null || typeof message.request_id === 'string')) {
      return this.fail('CONTRACT_MISMATCH');
    }
    if (message.type === 'NEW_MESSAGE') {
      if (message.request_id !== null || !['sender', 'room', 'content'].every((key) => typeof message.data[key] === 'string')) {
        return this.fail('CONTRACT_MISMATCH');
      }
      this.onMessage(message.data);
      return;
    }
    if (message.type === 'ERROR' && message.request_id === null) {
      this.onError(new ConnectionError(message.data.reason || 'INTERNAL_SERVER_ERROR'));
      return;
    }
    const item = this.pending.get(message.request_id);
    // Ignore late replies; timed-out actions are never automatically retried.
    if (!item) return;
    if (message.type !== 'ERROR') {
      const validResult = item.expected === 'ROOMS_LIST'
        ? Array.isArray(message.data.rooms)
        : typeof message.data.success === 'boolean';
      if (message.type !== item.expected || !validResult) return this.fail('CONTRACT_MISMATCH');
    }
    clearTimeout(item.timer);
    this.pending.delete(message.request_id);
    if (message.type === 'ERROR' || message.data.success === false) {
      item.reject(new ConnectionError(message.data.reason || 'REQUEST_REJECTED'));
    } else item.resolve(message.data);
  }
}
