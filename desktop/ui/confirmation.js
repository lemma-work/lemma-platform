/* Only the shell supplies this payload; textContent keeps supplied copy inert. */
const prompt = window.__LEMMA_CONFIRMATION__;
const dialog = document.querySelector('dialog');
const cancel = document.querySelector('#cancel');
const confirm = document.querySelector('#confirm');
let submitting = false;
async function answer(confirmed) {
  if (submitting) return;
  submitting = true;
  cancel.disabled = true;
  confirm.disabled = true;
  try {
    await window.__TAURI__.core.invoke('resolve_confirmation', { id: prompt.id, confirmed });
  } catch (error) {
    const output = document.querySelector('#error');
    output.textContent = String(error);
    output.hidden = false;
    cancel.disabled = false;
    confirm.disabled = false;
    submitting = false;
    cancel.focus();
  }
}
if (prompt && typeof prompt.id === 'string' && typeof prompt.title === 'string' && typeof prompt.message === 'string' && typeof prompt.confirmLabel === 'string') {
  document.querySelector('#title').textContent = prompt.title;
  document.querySelector('#message').textContent = prompt.message;
  confirm.textContent = prompt.confirmLabel;
  cancel.hidden = prompt.cancelable === false;
  cancel.addEventListener('click', () => answer(false));
  confirm.addEventListener('click', () => answer(true));
  dialog.addEventListener('cancel', (event) => { event.preventDefault(); void answer(false); });
  dialog.addEventListener('keydown', (event) => {
    if (event.key !== 'Tab') return;
    event.preventDefault();
    if (cancel.hidden) confirm.focus();
    else (document.activeElement === cancel ? confirm : cancel).focus();
  });
  dialog.showModal();
  (cancel.hidden ? confirm : cancel).focus();
} else {
  document.querySelector('#title').textContent = 'Confirmation unavailable';
  document.querySelector('#message').textContent = 'Close this window and retry. No action has been approved.';
  confirm.hidden = true;
  cancel.hidden = true;
  dialog.showModal();
}
