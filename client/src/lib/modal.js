/**
 * Confirmation and notice dialogs for the served pages and the shells.
 *
 * One `.modal` the page's stylesheet already lays out (`css/options.css`, or
 * the connect page's copy of it) is built on first use and reused, so no page
 * falls back to the browser's own alert, confirm, and prompt boxes. Each call
 * resolves when the dialog closes: `confirmDialog` with whether the action was
 * confirmed, `noticeDialog` once it was read.
 */

let dialog = null;
let close = null;

function build() {
  dialog = document.createElement('div');
  dialog.className = 'modal dialog-modal';
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');
  dialog.setAttribute('aria-labelledby', 'dialog-modal-title');
  dialog.innerHTML = `
    <div class="modal-content" style="max-width: 460px;">
      <div class="modal-header">
        <h3 id="dialog-modal-title"></h3>
        <span class="close-button" data-role="dismiss">&times;</span>
      </div>
      <p id="dialog-modal-message" style="white-space: pre-line;"></p>
      <input id="dialog-modal-value" type="text" readonly hidden style="width: 100%; font-family: var(--font-mono); font-size: 0.85rem;">
      <div class="button-group">
        <button type="button" class="secondary" data-role="dismiss"></button>
        <button type="button" data-role="confirm"></button>
      </div>
    </div>`;
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog || event.target.closest('[data-role="dismiss"]')) close(false);
    else if (event.target.closest('[data-role="confirm"]')) close(true);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && close) close(false);
  });
  document.body.appendChild(dialog);
}

/**
 * Show the dialog and resolve with the button pressed.
 *
 * @param {function} t Translator, for the Cancel label.
 * @param {object} options
 * @param {string} options.title
 * @param {string} options.message
 * @param {string} options.confirm Label of the confirming button.
 * @param {boolean} [options.danger] Style the confirming button as destructive and focus Cancel.
 * @param {boolean} [options.cancel] Offer a Cancel button.
 * @param {string} [options.value] Text shown in a read-only field, selected for copying.
 * @returns {Promise<boolean>}
 */
function open(t, { title, message, confirm, danger = false, cancel = true, value = '' }) {
  if (!dialog) build();
  if (close) close(false);
  const confirmButton = dialog.querySelector('[data-role="confirm"]');
  const cancelButton = dialog.querySelector('button[data-role="dismiss"]');
  const field = dialog.querySelector('#dialog-modal-value');
  dialog.querySelector('#dialog-modal-title').textContent = title;
  dialog.querySelector('#dialog-modal-message').textContent = message;
  confirmButton.textContent = confirm;
  confirmButton.className = danger ? 'danger' : 'primary';
  cancelButton.textContent = t('common.cancel');
  cancelButton.hidden = !cancel;
  field.hidden = !value;
  field.value = value;
  const previous = document.activeElement;
  dialog.style.display = 'block';
  if (value) field.select();
  else (danger && cancel ? cancelButton : confirmButton).focus();
  return new Promise((resolve) => {
    close = (confirmed) => {
      close = null;
      dialog.style.display = 'none';
      if (previous && previous.focus) previous.focus();
      resolve(confirmed);
    };
  });
}

/**
 * Ask before an action; resolves true when the action is confirmed.
 *
 * @param {function} t Translator.
 * @param {{title: string, message: string, confirm: string, danger?: boolean}} options
 * @returns {Promise<boolean>}
 */
export function confirmDialog(t, options) {
  return open(t, options);
}

/**
 * Tell the user something; resolves once dismissed.
 *
 * @param {function} t Translator.
 * @param {{title: string, message: string, value?: string}} options
 * @returns {Promise<void>}
 */
export async function noticeDialog(t, options) {
  await open(t, { ...options, confirm: t('common.ok'), cancel: false });
}
