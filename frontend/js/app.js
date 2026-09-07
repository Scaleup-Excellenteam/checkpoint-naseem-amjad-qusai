import { setupLogin } from './screens/login.js';
import { setupSignup } from './screens/signup.js';
import { setupRooms, previewRooms } from './screens/rooms.js';
import { setupChat } from './screens/chat.js';
import { setupLive } from './live.js';

const live = setupLive();
setupLogin(live.authenticate);
setupSignup(live.authenticate);
const rooms = setupRooms();
const chat = setupChat();

function showScreen(moveFocus = true) {
  live.cancelAuthentication();
  const isLive = location.hash === '#live';
  document.getElementById('live-screen').hidden = !isLive;
  const chatRoute = location.hash.startsWith('#chat-preview/');
  const room = chatRoute ? previewRooms.find((item) => location.hash === `#chat-preview/${item.id}`) : null;
  const isChat = Boolean(room);
  const isRooms = location.hash === '#rooms-preview' || (chatRoute && !room);
  document.querySelector('.layout').classList.toggle('show-rooms', isRooms || isChat || isLive);
  document.querySelector('.story').hidden = isRooms || isChat || isLive;
  document.querySelector('.form-area').hidden = isRooms || isChat || isLive;
  document.getElementById('rooms-screen').hidden = !isRooms;
  document.getElementById('chat-screen').hidden = !isChat;
  if (isChat) chat.open(room);
  else chat.close();
  rooms.reset();
  const screen = location.hash === '#signup' ? 'signup' : 'login';
  for (const name of ['login', 'signup']) {
    document.getElementById(`${name}-screen`).hidden = name !== screen;
    const link = document.getElementById(`${name}-link`);
    if (name === screen) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
    document.getElementById(`${name}-status`).textContent = '';
  }
  // Clear secrets when navigating. No passwords are persisted or logged.
  document.querySelectorAll('.field-error').forEach((error) => { error.textContent = ''; });
  document.querySelectorAll('input[aria-invalid]').forEach((input) => input.removeAttribute('aria-invalid'));
  document.querySelectorAll('[data-password]').forEach((button) => {
    const input = document.getElementById(button.dataset.password);
    input.value = '';
    input.type = 'password';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    button.textContent = 'הצג';
    button.setAttribute('aria-pressed', 'false');
    button.setAttribute('aria-label', input.name === 'confirm' ? 'הצגת אימות סיסמה' : 'הצגת סיסמה');
  });
  document.title = isLive ? 'TSPO — מערכת מחוברת' : isChat ? `TSPO — ${room.name} · דוגמה` : isRooms ? 'TSPO — חדרים לדוגמה' : 'TSPO — הכניסה לחבורה';
  if (moveFocus) document.getElementById(isLive ? 'live-title' : isChat ? 'chat-title' : isRooms ? 'rooms-title' : `${screen}-title`).focus();
}

document.querySelectorAll('[data-password]').forEach((button) => {
  button.addEventListener('click', () => {
    const input = document.getElementById(button.dataset.password);
    const reveal = input.type === 'password';
    input.type = reveal ? 'text' : 'password';
    button.textContent = reveal ? 'הסתר' : 'הצג';
    button.setAttribute('aria-pressed', String(reveal));
    button.setAttribute('aria-label', `${reveal ? 'הסתרת' : 'הצגת'} ${input.name === 'confirm' ? 'אימות סיסמה' : 'סיסמה'}`);
  });
});

window.addEventListener('hashchange', () => showScreen());
showScreen(false);
