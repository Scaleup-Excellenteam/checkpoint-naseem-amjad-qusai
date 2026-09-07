export function setupChat() {
  const form = document.getElementById('chat-form');
  const input = document.getElementById('chat-content');
  const log = document.getElementById('chat-messages');
  const error = document.getElementById('chat-error');
  const status = document.getElementById('chat-status');
  const security = document.getElementById('chat-security-status');
  // Separate in-memory transcripts. No storage, authentication or network requests.
  const transcripts = new Map();
  let activeRoom = null;

  function renderMessage(message) {
    const article = document.createElement('article');
    article.className = `chat-message${message.local ? ' own-message' : ''}`;
    const metadata = document.createElement('div');
    metadata.className = 'message-meta';
    const sender = document.createElement('strong');
    sender.textContent = message.sender;
    const badge = document.createElement('span');
    badge.textContent = message.local ? 'מקומי בלבד · לא נשלח לשרת' : 'הודעה לדוגמה';
    metadata.append(sender, badge);
    const content = document.createElement('p');
    content.dir = 'auto';
    // User input is displayed as text, never interpreted as HTML.
    content.textContent = message.content;
    article.append(metadata, content);
    log.append(article);
  }
  function clearComposer() {
    input.value = '';
    input.removeAttribute('aria-invalid');
    error.textContent = '';
    status.textContent = '';
    security.textContent = '';
  }
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!activeRoom) return;
    const content = input.value.trim();
    if (!content) {
      error.textContent = 'יש לכתוב הודעה לפני השליחה.';
      input.setAttribute('aria-invalid', 'true');
      input.focus();
      return;
    }
    const message = { sender: 'אתם', content, local: true };
    transcripts.get(activeRoom.id).push(message);
    renderMessage(message);
    clearComposer();
    status.textContent = 'ההודעה נוספה לתצוגה המקומית בלבד.';
    log.scrollTop = log.scrollHeight;
    input.focus();
  });
  input.addEventListener('input', () => {
    error.textContent = '';
    input.removeAttribute('aria-invalid');
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  document.querySelectorAll('[data-security]').forEach((button) => {
    button.addEventListener('click', () => {
      security.textContent = button.dataset.security === 'DLP'
        ? 'דוגמה בלבד: ההודעה נחסמה עקב מידע רגיש (DLP_SENSITIVE_CONTENT). לא בוצעה בדיקת אבטחה בפועל.'
        : 'דוגמה בלבד: ההודעה נחסמה עקב כתובת זדונית (MALICIOUS_ADDRESS). לא בוצעה בדיקת מוניטין בפועל.';
    });
  });
  return {
    open(room) {
      activeRoom = room;
      clearComposer();
      document.getElementById('chat-title').textContent = room.name;
      document.getElementById('chat-topic').textContent = room.topic;
      if (!transcripts.has(room.id)) transcripts.set(room.id, [
        { sender: 'נועם', content: `ברוכים הבאים לתצוגת „${room.name}”! איזה כיף שיש מקום לשיחה.` },
        { sender: 'דנה', content: 'אפשר לנסות לכתוב למטה ולראות איך ההודעה שלכם נראית. זו תצוגה מקומית בלבד.' },
      ]);
      log.replaceChildren();
      transcripts.get(room.id).forEach(renderMessage);
      log.scrollTop = log.scrollHeight;
    },
    close() { activeRoom = null; clearComposer(); },
  };
}
