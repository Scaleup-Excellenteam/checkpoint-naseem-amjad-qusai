import { describeError } from '../errors.js';
import { showFieldError } from './login.js';

// Keep these UI rules aligned with server-side validation.
export function passwordChecks(password) {
  return {
    length: Array.from(password).length >= 8,
    uppercase: /[A-Z]/.test(password),
    lowercase: /[a-z]/.test(password),
    number: /[0-9]/.test(password),
    symbol: /[!"#$%&'()*+,\-./:;<=>?@[\]\\^_`{|}~]/.test(password),
  };
}

export function setupSignup(authenticate) {
  const form = document.getElementById('signup-form');
  const status = document.getElementById('signup-status');
  form.addEventListener('input', (event) => {
    if (event.target.matches('input')) showFieldError(event.target, '');
    status.textContent = '';
    for (const [rule, passed] of Object.entries(passwordChecks(form.elements.password.value))) {
      document.querySelector(`[data-rule="${rule}"]`).classList.toggle('passed', passed);
    }
  });
  let pending = false;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (pending) return;
    status.textContent = '';
    const { username, password, confirm } = form.elements;
    showFieldError(username, username.value.trim() ? '' : 'יש להזין שם משתמש.');
    showFieldError(password, Object.values(passwordChecks(password.value)).every(Boolean) ? '' : 'הסיסמה צריכה לעמוד בכל חמש הדרישות.');
    showFieldError(confirm, !confirm.value ? 'יש להזין את הסיסמה שוב.' : confirm.value !== password.value ? 'הסיסמאות אינן זהות.' : '');
    const invalid = form.querySelector('[aria-invalid="true"]');
    if (invalid) return invalid.focus();
    pending = true;
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    status.textContent = 'ממתין לתשובת השרת…';
    try {
      await authenticate('SIGNUP', { username: username.value.trim(), password: password.value });
      window.addEventListener('hashchange', () => {
        document.getElementById('login-status').textContent = 'החשבון נוצר בהצלחה. אפשר להתחבר.';
      }, { once: true });
      location.hash = 'login';
    } catch (error) { status.textContent = describeError(error); }
    finally {
      pending = false; button.disabled = false;
      password.value = '';
      confirm.value = "";
      document.querySelectorAll('[data-rule]').forEach((rule) => rule.classList.remove('passed'));
    }
  });
}
