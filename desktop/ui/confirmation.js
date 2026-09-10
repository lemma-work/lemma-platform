/* Only the shell supplies this payload; textContent keeps supplied copy inert. */
const prompt = window.__LEMMA_CONFIRMATION__;
const dialog = document.querySelector('dialog');
const cancel = document.querySelector('#cancel');
const confirm = document.querySelector('#confirm');
const discard = document.querySelector('#discard');
let submitting = false;
async function answer(decision) {
  if (submitting) return;
  submitting = true;
  cancel.disabled = true;
  confirm.disabled = true;
  discard.disabled = true;
  try {
    await window.__TAURI__.core.invoke('resolve_confirmation', { id: prompt.id, decision });
  } catch (error) {
    const output = document.querySelector('#error');
    output.textContent = String(error);
    output.hidden = false;
    cancel.disabled = false;
    confirm.disabled = false;
    discard.disabled = false;
    submitting = false;
    cancel.focus();
  }
}
if (prompt && typeof prompt.id === 'string' && typeof prompt.title === 'string' && typeof prompt.message === 'string' && typeof prompt.confirmLabel === 'string') {
  document.querySelector('#title').textContent = prompt.title;
  document.querySelector('#message').textContent = prompt.message;
  confirm.textContent = prompt.confirmLabel;
  cancel.hidden = prompt.cancelable === false;
  discard.hidden = prompt.allowDiscard !== true;
  cancel.addEventListener('click', () => answer('cancel'));
  discard.addEventListener('click', () => answer('discard'));
  confirm.addEventListener('click', () => answer('confirm'));
  dialog.addEventListener('cancel', (event) => { event.preventDefault(); void answer('cancel'); });
  dialog.addEventListener('keydown', (event) => {
    if (event.key !== 'Tab') return;
    event.preventDefault();
    const buttons = [cancel, discard, confirm].filter(button => !button.hidden && !button.disabled);
    if (!buttons.length) return;
    const current = buttons.indexOf(document.activeElement);
    const next = (current + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length;
    buttons[next].focus();
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
