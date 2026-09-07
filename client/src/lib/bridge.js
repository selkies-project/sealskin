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
let helloPromise = null;
let hostOrigin = null;

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
  if (!hostOrigin && event.origin && event.origin !== 'null') hostOrigin = event.origin;
  if (msg.ok) {
    entry.resolve(msg.data);
  } else {
    entry.reject(new Error(msg.error || 'Bridge request failed'));
  }
});

/**
 * Register a pending request and return the promise for its reply.
 *
 * @param {number} id Message id.
 * @param {string} type Request type, used in the timeout error.
 * @param {object} opts Request options (see `request`).
 * @returns {Promise<any>} Resolves with the host's reply data.
 */
function awaitReply(id, type, opts) {
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
  });
}

/**
 * Send the `hello` request. The host's origin is unknown until it answers,
 * so this is the only message posted without a target origin; it carries
 * nothing but protocol and version information.
 *
 * @returns {Promise<object>} HelloInfo from the host.
 */
function sendHello() {
  const id = nextId++;
  const reply = awaitReply(id, 'hello', { timeout: 15000 });
  window.parent.postMessage({
    sealskin: BRIDGE_VERSION,
    id,
    type: 'hello',
    payload: {
      bridge: BRIDGE_VERSION,
      uiVersion: typeof __UI_VERSION__ !== 'undefined' ? __UI_VERSION__ : 'dev',
    },
  }, '*');
  return reply;
}

/**
 * Complete the hello exchange once and cache its result.
 *
 * @returns {Promise<object>} HelloInfo from the host.
 */
function ensureHello() {
  if (helloInfo) return Promise.resolve(helloInfo);
  if (!helloPromise) {
    helloPromise = sendHello().then(
      (info) => {
        helloInfo = info;
        return info;
      },
      (error) => {
        helloPromise = null;
        throw error;
      },
    );
  }
  return helloPromise;
}

/**
 * Post one message to the host at its known origin.
 *
 * @param {object} message Bridge message.
 * @throws {Error} A fixed message when the browser refuses the post, so no
 *   browser-generated text reaches the page.
 */
function post(message) {
  try {
    window.parent.postMessage(message, hostOrigin);
  } catch (e) {
    throw new Error('Could not reach the SealSkin shell.');
  }
}

/**
 * Send one message to the host and return the promise for its reply.
 *
 * @param {string} type Request type from the bridge contract.
 * @param {object} payload Structured-cloneable payload.
 * @param {object} opts Request options (see `request`).
 * @returns {Promise<any>} The host's reply data.
 */
function send(type, payload, opts) {
  const id = nextId++;
  const message = { sealskin: BRIDGE_VERSION, id, type, payload };
  try {
    post(message);
  } catch (e) {
    return Promise.reject(e);
  }
  return opts.fireAndForget ? Promise.resolve() : awaitReply(id, type, opts);
}

/**
 * Send one request to the host and wait for its reply.
 *
 * The first request completes the hello exchange to learn the host's origin;
 * afterwards messages are posted synchronously.
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
  if (hostOrigin) return send(type, payload, opts);
  return ensureHello().then(() => {
    if (!hostOrigin) {
      throw new Error('The SealSkin shell did not report a usable origin.');
    }
    return send(type, payload, opts);
  });
}

export const bridge = {
  /**
   * Announce the page and receive shell information. Cached after the first
   * call so any module can read `bridge.info` synchronously afterwards.
   *
   * @returns {Promise<object>} HelloInfo as described in the architecture doc.
   */
  hello() {
    if (!isFramed()) {
      return Promise.reject(new Error('This page must be opened from the SealSkin extension or app.'));
    }
    return ensureHello();
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
