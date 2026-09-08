import { config, serverEndpoints } from './config.js';
import { ChatSocket, ConnectionError } from './socket.js';
import { describeError } from './errors.js';

export function setupLive() {
  const byId = (id) => document.getElementById(id);
  let user = null;
  let room = null;
  let generation = 0;
  let actionPending = false;
  let authPending = false;
  const status = byId('live-status');
  const knownRooms = new Map(
    config.rooms.map((name) => [name, { name, members: 0, joined: false }]),
  );
  const socket = new ChatSocket({
    timeoutMs: config.requestTimeoutMs,
    onState(state) {
      byId('socket-state').textContent = { connected: 'WebSocket: מחובר', connecting: 'WebSocket: מתחבר…', disconnected: 'WebSocket: מנותק' }[state];
      byId('connect-server').disabled = state !== 'disconnected';
      byId('disconnect-server').disabled = state === 'disconnected';
      byId('server-url').disabled = state !== 'disconnected';
      if (state === 'disconnected') {
        generation++;
        user = null; room = null;
        byId('live-messages').replaceChildren();
        byId('live-content').value = '';
        update();
      }
    },
    onError(error) { byId('connection-status').textContent = describeError(error); },
    onMessage(message) {
      // Ignore unsolicited messages before login and messages for another room.
      if (!user || message.room !== room) return;
      const article = document.createElement('article');
      article.className = `chat-message${message.sender === user ? ' own-message' : ''}`;
      const sender = document.createElement('strong'); sender.textContent = message.sender;
      const text = document.createElement('p'); text.dir = 'auto'; text.textContent = message.content;
      article.append(sender, text); byId('live-messages').append(article);
      byId('live-messages').scrollTop = byId('live-messages').scrollHeight;
    },
  });
  function update() {
    byId('live-account').textContent = user ? `מחוברים בתור ${user}` : 'נדרשת התחברות לחשבון';
    byId('live-room-controls').hidden = !user || Boolean(room);
    byId('live-conversation').hidden = !user || !room;
    byId('live-room-title').textContent = room || '';
    byId('live-auth-needed').hidden = Boolean(user);
    renderKnownRooms();
  }
  byId('server-url').value = config.serverUrl;
  byId('connect-server').addEventListener('click', () => {
    byId('connection-status').textContent = '';
    try { socket.connect(serverEndpoints(byId('server-url').value).websocket); }
    catch (error) { byId('connection-status').textContent = error.message; }
  });
  byId('disconnect-server').addEventListener('click', () => socket.disconnect());
  byId('check-health').addEventListener('click', async () => {
    const button = byId('check-health'); button.disabled = true;
    byId('health-state').textContent = 'HTTP: בודק…';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), config.requestTimeoutMs);
    try {
      const response = await fetch(serverEndpoints(byId('server-url').value).health, { signal: controller.signal, cache: 'no-store', credentials: 'omit' });
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (data.status !== 'ok' || !Number.isInteger(data.connected_clients) || data.connected_clients < 0) throw new Error();
      // The current backend optionally exposes { rooms: { roomName: memberCount } }.
      // Health still does not grant authentication or room membership.
      if (data.rooms !== undefined) {
        if (!data.rooms || typeof data.rooms !== 'object' || Array.isArray(data.rooms)
          || !Object.entries(data.rooms).every(([name, count]) => name.trim() && Number.isInteger(count) && count >= 0)) {
          throw new Error();
        }
        Object.entries(data.rooms).forEach(([name, members]) => {
          const current = knownRooms.get(name);
          knownRooms.set(name, {
            name,
            members,
            joined: current?.joined || false,
          });
        });
        renderKnownRooms();
      }
      const roomSummary = data.rooms ? ` · ${Object.keys(data.rooms).length} חדרים` : '';
      byId('health-state').textContent = `HTTP: תקין · ${data.connected_clients} חיבורים${roomSummary} (בדיקה אחרונה)`;
    } catch { byId('health-state').textContent = 'HTTP: הבדיקה נכשלה — בדקו שרת, כתובת והרשאת CORS.'; }
    finally { clearTimeout(timer); button.disabled = false; }
  });
  function renderKnownRooms() {
    const list = byId('live-room-list');
    list.replaceChildren();
    let index = 0;
    for (const item of knownRooms.values()) {
      index++;
      const card = document.createElement('article');
      card.className = 'room-card live-room-card';
      const number = document.createElement('span');
      number.className = 'room-number';
      number.textContent = String(index).padStart(2, '0');
      const topic = document.createElement('span');
      topic.className = 'room-topic';
      topic.textContent = `${item.members} ${item.members === 1 ? 'משתמש' : 'משתמשים'} בחדר`;
      const name = document.createElement('span');
      name.className = 'room-name';
      name.dir = 'auto';
      name.textContent = item.name;
      const description = document.createElement('span');
      description.className = 'room-description';
      description.textContent = item.joined
        ? 'השרת מאשר שאתם כבר חברים בחדר הזה.'
        : 'יש לשלוח בקשת הצטרפות לפני הכניסה לשיחה.';
      const footer = document.createElement('span');
      footer.className = 'room-card-footer';
      const membership = document.createElement('span');
      membership.textContent = item.joined ? 'כבר הצטרפתם' : 'עדיין לא הצטרפתם';
      const join = document.createElement('button');
      join.type = 'button';
      join.className = 'secondary';
      join.textContent = item.joined ? 'כניסה לחדר' : 'בקשת הצטרפות';
      join.disabled = actionPending;
      join.addEventListener('click', () => joinRoom(item.name, join, item.joined));
      footer.append(membership, join);
      card.append(number, topic, name, description, footer);
      list.append(card);
    }
    byId('live-room-count').textContent = `${knownRooms.size} חדרים זמינים`;
    byId('live-rooms-empty').hidden = knownRooms.size !== 0;
  }
  renderKnownRooms();
  // LIST_ROOMS is the single room catalog: login and CREATE_ROOM both refresh
  // from it rather than guessing what the server now holds.
  async function loadRoomCatalog() {
    const catalog = await socket.request('LIST_ROOMS', {});
    if (!catalog.rooms.every((item) => item && typeof item.name === 'string'
      && item.name.trim() && Number.isInteger(item.members) && item.members >= 0
      && typeof item.joined === 'boolean')) {
      socket.fail('CONTRACT_MISMATCH');
      throw new ConnectionError('CONTRACT_MISMATCH');
    }
    knownRooms.clear();
    catalog.rooms.forEach((item) => knownRooms.set(item.name, { ...item }));
    renderKnownRooms();
  }
  async function action(button, operation) {
    if (actionPending) return;
    actionPending = true; button.disabled = true; status.textContent = 'ממתין לתשובת השרת…';
    const token = generation;
    try { await operation(token); }
    catch (error) {
      if (['NOT_AUTHENTICATED', 'REQUEST_TIMEOUT'].includes(error.code)) socket.disconnect();
      status.textContent = describeError(error);
    } finally { actionPending = false; button.disabled = false; update(); }
  }
  function joinRoom(target, button, alreadyJoined) {
    action(button, async (token) => {
      if (!user) throw new ConnectionError('NOT_AUTHENTICATED');
      if (!alreadyJoined) {
        const result = await socket.request('JOIN_ROOM', { room: target });
        if (result.room !== target) { socket.fail('CONTRACT_MISMATCH'); throw new ConnectionError('CONTRACT_MISMATCH'); }
      }
      if (token !== generation) return;
      const current = knownRooms.get(target);
      if (current) knownRooms.set(target, {
        ...current,
        members: alreadyJoined ? current.members : current.members + 1,
        joined: true,
      });
      room = target; byId('live-messages').replaceChildren();
      status.textContent = alreadyJoined
        ? 'נכנסתם לחדר שאליו כבר הצטרפתם.'
        : 'השרת אישר את ההצטרפות לחדר.';
    });
  }
  byId('live-create-room').addEventListener('submit', (event) => {
    event.preventDefault();
    const name = byId('live-room-name').value.trim();
    if (!name) { status.textContent = 'יש להזין שם חדר.'; return; }
    action(byId('live-create'), async (token) => {
      if (!user) throw new ConnectionError('NOT_AUTHENTICATED');
      const result = await socket.request('CREATE_ROOM', { name });
      if (result.room !== name) { socket.fail('CONTRACT_MISMATCH'); throw new ConnectionError('CONTRACT_MISMATCH'); }
      if (token !== generation) return;
      // The server decides what exists; re-read the catalog instead of
      // inserting the room locally. Creating does not join it.
      await loadRoomCatalog();
      byId('live-room-name').value = '';
      status.textContent = `השרת אישר את יצירת החדר „${result.room}”. עדיין לא הצטרפתם אליו.`;
    });
  });
  byId('live-leave').addEventListener('click', () => action(byId('live-leave'), async (token) => {
    const target = room;
    const result = await socket.request('LEAVE_ROOM', { room: target });
    if (result.room !== target) { socket.fail('CONTRACT_MISMATCH'); throw new ConnectionError('CONTRACT_MISMATCH'); }
    if (token !== generation) return;
    const current = knownRooms.get(target);
    if (current) knownRooms.set(target, {
      ...current,
      members: Math.max(0, current.members - 1),
      joined: false,
    });
    room = null; byId('live-content').value = ''; byId('live-messages').replaceChildren();
    status.textContent = 'עזיבת החדר אושרה.';
  }));
  byId('live-chat-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const content = byId('live-content').value.trim();
    if (!content) { status.textContent = 'לא ניתן לשלוח הודעה ריקה.'; return; }
    if (config.maxMessageLength && Array.from(content).length > config.maxMessageLength) {
      status.textContent = 'ההודעה ארוכה מהמותר.'; return;
    }
    action(byId('live-send'), async (token) => {
      if (!user || !room) throw new ConnectionError('NOT_IN_ROOM');
      const sentDraft = byId('live-content').value;
      const result = await socket.request('CHAT_MESSAGE', { room, content });
      if (result.decision === 'BLOCK') throw new ConnectionError(result.reason || 'REQUEST_REJECTED');
      if (token !== generation) return;
      if (byId('live-content').value === sentDraft) byId('live-content').value = '';
      status.textContent = 'השרת אישר את ההודעה. אין בכך אישור קריאה של המשתתפים.';
    });
  });
  byId('live-logout').addEventListener('click', () => { socket.disconnect(); location.hash = 'login'; });
  update();
  return {
    async authenticate(type, credentials) {
      if (!config.authenticationReady) throw new ConnectionError('AUTH_NOT_READY');
      if (authPending) throw new ConnectionError('REQUEST_REJECTED');
      authPending = true;
      const token = generation;
      try {
        const result = await socket.request(type, credentials);
        if (token !== generation) throw new ConnectionError('DISCONNECTED');
        if (type === 'LOGIN') {
          if (typeof result.username !== 'string' || !result.username) {
            socket.fail('CONTRACT_MISMATCH');
            throw new ConnectionError('CONTRACT_MISMATCH');
          }
          const confirmedUsername = result.username;
          await loadRoomCatalog();
          user = confirmedUsername;
          renderKnownRooms();
          update();
          location.hash = 'live';
        }
      } catch (error) {
        // A late login must not silently establish a session after UI timeout.
        if (error.code === 'REQUEST_TIMEOUT') socket.disconnect();
        throw error;
      } finally { authPending = false; }
    },
    cancelAuthentication() { if (authPending) socket.disconnect(); },
  };
}
