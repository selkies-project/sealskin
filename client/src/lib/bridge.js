/**
 * Page side of the shell bridge (bridge protocol version 1).
 *
 * Every served page runs inside an iframe owned by a shell host: the browser
 * extension's host page or the mobile app's outer window. The page never
 * touches `chrome.*` or `window.parent.*` directly; it sends requests through
 * this module and the host answers. See docs/content/architecture.md ("The bridge") for
 * the full contract.
 *
 * Usage:
 *   import { bridge } from '../lib/bridge.js';
 *   const info = await bridge.hello();
 *   const apps = await bridge.secureFetch('/api/applications', { method: 'POST', body: '{}' });
 */

export const BRIDGE_VERSION = 1;

const REQUEST_TIMEOUT_MS = 120000;

let nextId = 1;
const pending = new Map();
let helloInfo = null;

function isFramed() {
  try {
    return window.parent && window.parent !== window;
  } catch (e) {
    return true;
  }
}

window.addEventListener('message', (event) => {
  const msg = event.data;
  if (!msg || msg.sealskin !== BRIDGE_VERSION || typeof msg.id !== 'number') return;
  if (isFramed() && event.source !== window.parent) return;
  const entry = pending.get(msg.id);
  if (!entry) return;
  pending.delete(msg.id);
  if (entry.timer) clearTimeout(entry.timer);
  if (msg.ok) {
    entry.resolve(msg.data);
  } else {
    entry.reject(new Error(msg.error || 'Bridge request failed'));
  }
});

/**
 * Send one request to the host and wait for its reply.
 *
 * @param {string} type Request type from the bridge contract.
 * @param {object} [payload] Structured-cloneable payload.
 * @param {object} [opts]
 * @param {number} [opts.timeout] Milliseconds before the request rejects.
 * @param {boolean} [opts.fireAndForget] Resolve immediately without waiting.
 * @returns {Promise<any>} The host's reply data.
 */
export function request(type, payload = {}, opts = {}) {
  if (!isFramed()) {
    return Promise.reject(new Error('This page must be opened from the SealSkin extension or app.'));
  }
  const id = nextId++;
  const message = { sealskin: BRIDGE_VERSION, id, type, payload };
  if (opts.fireAndForget) {
    window.parent.postMessage(message, '*');
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    // A timeout of 0 disables the timer (long transfers such as blobs).
    const timeoutMs = opts.timeout === undefined ? REQUEST_TIMEOUT_MS : opts.timeout;
    const timer = timeoutMs > 0
      ? setTimeout(() => {
          pending.delete(id);
          reject(new Error(`Bridge request '${type}' timed out`));
        }, timeoutMs)
      : null;
    pending.set(id, { resolve, reject, timer });
    window.parent.postMessage(message, '*');
  });
}

export const bridge = {
  /**
   * Announce the page and receive shell information. Cached after the first
   * call so any module can read `bridge.info` synchronously afterwards.
   *
   * @returns {Promise<object>} HelloInfo as described in the architecture doc.
   */
  async hello() {
    if (helloInfo) return helloInfo;
    helloInfo = await request('hello', {
      bridge: BRIDGE_VERSION,
      uiVersion: typeof __UI_VERSION__ !== 'undefined' ? __UI_VERSION__ : 'dev',
    }, { timeout: 15000 });
    return helloInfo;
  },

  /** @returns {object|null} The cached HelloInfo, or null before hello(). */
  get info() {
    return helloInfo;
  },

  /**
   * Encrypted API call through the host. The host adds the JWT where the API
   * needs it and handles the E2EE session.
   *
   * @param {string} url Path beginning with `/api/`.
   * @param {object} [options] fetch-like options: method, headers, body (string).
   * @returns {Promise<any>} Decrypted JSON body, or null for empty responses.
   */
  secureFetch(url, options = {}) {
    return request('secureFetch', { url, options });
  },

  /** @returns {Promise<object|null>} Pending launch context; cleared on read. */
  getContext() {
    return request('getContext');
  },

  /**
   * Store a launch context in the shell and optionally open the popup.
   *
   * @param {object} context Launch context (may contain a File under `file`).
   * @param {boolean} [openPopup=true]
   */
  setContext(context, openPopup = true) {
    return request('setContext', { context, openPopup });
  },

  /**
   * Fetch a URL with the shell's privileges and return its body as a Blob.
   *
   * @param {string} url
   * @returns {Promise<Blob>}
   */
  fetchBlob(url) {
    return request('fetchBlob', { url }, { timeout: 0 });
  },

  openSession(sessionId, sessionUrl) {
    return request('openSession', { sessionId, sessionUrl });
  },

  focusSession(session) {
    return request('focusSession', { session });
  },

  closeSession(sessionId) {
    return request('closeSession', { sessionId });
  },

  /** @param {'popup'|'options'|'files'|'upload'|'connect'} page */
  openPage(page) {
    return request('openPage', { page });
  },

  openExternal(url) {
    return request('openExternal', { url });
  },

  downloadFile(home, path, filename) {
    return request('downloadFile', { home, path, filename });
  },

  saveBlob(blob, filename) {
    return request('saveBlob', { blob, filename }, { timeout: 0 });
  },

  storageGet(keys) {
    return request('storageGet', { keys });
  },

  storageSet(items) {
    return request('storageSet', { items });
  },

  storageRemove(keys) {
    return request('storageRemove', { keys });
  },

  updateConfig(partial) {
    return request('updateConfig', partial);
  },

  close() {
    return request('close', {}, { fireAndForget: true });
  },
};
