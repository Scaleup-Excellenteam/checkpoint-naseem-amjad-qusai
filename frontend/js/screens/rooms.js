// Local fixtures only. The room-list API is not yet defined in Contract v1.
export const previewRooms = [
  { id: 'pizza', name: 'השולחן המרכזי', topic: 'שיחות כלליות', description: 'מקום להכיר את החבורה ולדבר על כל מה שקורה בין סלייס לסלייס.', icon: '01' },
  { id: 'recipes', name: 'סודות מהמטבח', topic: 'מתכונים וטיפים', description: 'בצק אוורירי, רוטב ביתי והתוספת שעושה את ההבדל. כאן משתפים רעיונות מהמטבח.', icon: '02' },
  { id: 'team', name: 'שולחן הצוות', topic: 'הפרויקט שלנו', description: 'מקום לשאלות, לעדכונים ולתיאום העבודה על הפרויקט המשותף.', icon: '03' },
  { id: 'lounge', name: 'עוד משולש אחד', topic: 'הפסקה משותפת', description: 'לוקחים הפסקה, מחליפים המלצות ומדברים על מה שמעניין אתכם.', icon: '04' },
];

export function setupRooms() {
  const search = document.getElementById('room-search');
  const list = document.getElementById('room-list');
  const status = document.getElementById('rooms-status');
  const join = document.getElementById('join-room');
  let selectedRoom = null;

  function selectRoom(room) {
    selectedRoom = room;
    status.textContent = '';
    document.getElementById('room-selection-empty').hidden = Boolean(room);
    document.getElementById('room-selection').hidden = !room;
    document.querySelector('.room-details').setAttribute('aria-labelledby', room ? 'selected-room-name' : 'room-details-title');
    join.disabled = !room;
    document.getElementById('preview-chat').disabled = !room;
    if (room) {
      document.getElementById('selected-room-name').textContent = room.name;
      document.getElementById('selected-room-description').textContent = room.description;
      document.getElementById('selected-room-id').textContent = room.id;
    }
  }

  function render() {
    const query = search.value.trim().toLocaleLowerCase('he');
    const visibleRooms = previewRooms.filter((room) =>
      `${room.name} ${room.topic} ${room.id}`.toLocaleLowerCase('he').includes(query));
    if (selectedRoom && !visibleRooms.includes(selectedRoom)) selectRoom(null);
    list.replaceChildren();
    for (const room of visibleRooms) {
      const label = document.createElement('label');
      label.className = 'room-option';
      const radio = document.createElement('input');
      radio.type = 'radio';
      radio.name = 'room';
      radio.value = room.id;
      radio.checked = selectedRoom === room;
      radio.setAttribute('aria-label', `בחירת החדר ${room.name}`);
      radio.addEventListener('change', () => selectRoom(room));
      const card = document.createElement('span');
      card.className = 'room-card';
      for (const [className, text] of [
        ['room-number', room.icon], ['room-topic', room.topic],
        ['room-name', room.name], ['room-description', room.description],
        ['room-card-footer', 'חדר לדוגמה · בחירה להצגת פרטים'],
      ]) {
        const part = document.createElement('span');
        part.className = className;
        part.textContent = text;
        card.append(part);
      }
      label.append(radio, card);
      list.append(label);
    }
    document.getElementById('room-count').textContent = `${visibleRooms.length} חדרים לדוגמה`;
    document.getElementById('rooms-empty').hidden = visibleRooms.length !== 0;
  }

  search.addEventListener('input', () => { status.textContent = ''; render(); });
  document.getElementById('clear-room-search').addEventListener('click', () => {
    search.value = '';
    render();
    search.focus();
  });
  join.addEventListener('click', () => {
    if (!selectedRoom) return;
    // Never imply membership or send JOIN_ROOM without an authenticated session.
    status.textContent = `לא נשלחה בקשה לחדר „${selectedRoom.name}”. זו תצוגה מקדימה; בקשת הצטרפות אמיתית תהיה זמינה לאחר התחברות וחיבור החדרים לשרת.`;
  });

  document.getElementById('preview-chat').addEventListener('click', () => {
    if (selectedRoom) location.hash = `chat-preview/${selectedRoom.id}`;
  });
  return {
    reset() { search.value = ''; selectRoom(null); render(); },
  };
}
