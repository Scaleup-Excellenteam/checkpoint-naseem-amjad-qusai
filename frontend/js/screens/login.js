import { describeError } from '../errors.js';
export function showFieldError(input, message) {
  input.setAttribute('aria-invalid', String(Boolean(message)));
  document.getElementById(`${input.id}-error`).textContent = message;
}

export function setupLogin(authenticate) {
  const form = document.getElementById('login-form');
  const status = document.getElementById('login-status');
  let pending = false;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (pending) return;
    status.textContent = '';
    const username = form.elements.username;
    const password = form.elements.password;
    showFieldError(username, username.value.trim() ? '' : 'יש להזין שם משתמש.');
    showFieldError(password, password.value ? '' : 'יש להזין סיסמה.');
    const invalid = form.querySelector('[aria-invalid="true"]');
    if (invalid) return invalid.focus();
    pending = true;
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    status.textContent = 'ממתין לתשובת השרת…';
    try {
      await authenticate('LOGIN', { username: username.value.trim(), password: password.value });
      status.textContent = 'ההתחברות אושרה.';
    } catch (error) { status.textContent = describeError(error); }
    finally {
      pending = false; button.disabled = false;
      password.value = '';
      
    }
  });
  form.addEventListener('input', (event) => {
    if (event.target.matches('input')) showFieldError(event.target, '');
    status.textContent = '';
  });
}
