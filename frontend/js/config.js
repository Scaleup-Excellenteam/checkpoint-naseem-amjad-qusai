// Enable authentication only after the backend verifies passwords and supports Contract v1.
export const config = {
  serverUrl: 'http://127.0.0.1:8000',
  authenticationReady: false,
  requestTimeoutMs: 10000,
  // The team must supply an agreed real room catalog; no room-list request is invented.
  rooms: [],
  maxMessageLength: null,
};

export function serverEndpoints(baseUrl) {
  const base = new URL(baseUrl);
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password) {
    throw new Error('כתובת השרת חייבת להיות HTTP או HTTPS וללא פרטי כניסה.');
  }
  const health = new URL('/health', base);
  const websocket = new URL('/ws', base);
  websocket.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:';
  return { health: health.href, websocket: websocket.href };
}
