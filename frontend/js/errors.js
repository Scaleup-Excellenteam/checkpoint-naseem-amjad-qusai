const messages = {
  DISCONNECTED: 'חיבור הצ׳אט מנותק. התחברו לשרת ונסו שוב.',
  CONNECTION_FAILED: 'החיבור נכשל. בדקו את כתובת השרת ואת הפעלתו.',
  CONNECT_TIMEOUT: 'השרת לא פתח חיבור בזמן. אפשר לנסות להתחבר מחדש.',
  REQUEST_TIMEOUT: 'לא התקבלה תשובה בזמן. ייתכן שהפעולה בוצעה בשרת; היא לא תישלח שוב אוטומטית.',
  CONTRACT_MISMATCH: 'תשובת השרת אינה תואמת לחוזה v1. החיבור נסגר כדי למנוע מצב כניסה שגוי.',
  AUTH_NOT_READY: 'השרת הנוכחי עדיין אינו מאמת סיסמאות. חיבור החשבונות יופעל לאחר עדכון השרת וההגדרה authenticationReady.',
  NOT_AUTHENTICATED: 'נדרשת התחברות מחדש לחשבון.',
  USERNAME_ALREADY_EXISTS: 'שם המשתמש כבר קיים. בחרו שם אחר.',
  INVALID_CREDENTIALS: 'שם המשתמש או הסיסמה שגויים.',
  INVALID_USERNAME: 'שם המשתמש אינו תקין.',
  INVALID_PASSWORD: 'הסיסמה אינה עומדת בדרישות השרת.',
  ROOM_NOT_FOUND: 'החדר לא נמצא.', ALREADY_IN_ROOM: 'אתם כבר חברים בחדר.',
  NOT_IN_ROOM: 'אינכם חברים בחדר הזה.', EMPTY_MESSAGE: 'לא ניתן לשלוח הודעה ריקה.',
  MESSAGE_TOO_LONG: 'ההודעה ארוכה מהמותר בשרת.',
  DLP_SENSITIVE_CONTENT: 'ההודעה נחסמה בגלל מידע רגיש.',
  MALICIOUS_ADDRESS: 'ההודעה נחסמה בגלל כתובת שסומנה כזדונית.',
  UNKNOWN_MESSAGE_TYPE: 'השרת אינו תומך בפעולה הזאת.',
  INTERNAL_SERVER_ERROR: 'אירעה שגיאה בשרת.',
};
export function describeError(error) {
  const code = error.code || 'INTERNAL_SERVER_ERROR';
  return `${messages[code] || 'השרת דחה את הבקשה.'} (${code})`;
}
