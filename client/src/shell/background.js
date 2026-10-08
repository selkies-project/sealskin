/**
 * SealSkin shell background script.
 *
 * Runs as the extension's background (MV3 service worker on Chrome, background
 * page on Firefox) and, in the mobile app and the web app, inside the host
 * window on top of the polyfill in `polyfill.js`. It owns everything privileged:
 *
 *  - the E2EE handshake and encrypted fetch (`secureFetchInBackground`)
 *  - JWT signing with the stored private key
 *  - in the web app, in their place, plain same-origin calls under the sign-in cookie
 *  - context menus, "send next download" interception, badge
 *  - the session-to-tab map and tab focus/close, and the origin a session opens on
 *  - the pending launch context handed to the popup
 *  - the Chrome streaming-download fetch handler
 *
 * Served pages never talk to it directly; the host page (`host.js`) relays
 * bridge requests through `chrome.runtime.sendMessage` (or `window.handleMessage`
 * on mobile). Every handler always answers with `{success, data|error}`.
 */

import { pemToArrayBuffer, arrayBufferToBase64, generateJwtNative } from '../lib/crypto-utils.js';
import { probeSessionOrigin } from '../lib/session-origin.js';
// Context menu titles per language. The build generates this module from
// `background.contextMenu` of src/i18n/*.json (see build.mjs); it is bundled
// because the menus are registered before any page could fetch a language file.
import { contextMenuTitles } from 'sealskin-i18n/context-menu';

/* global __SHELL_TARGET__ */

// The web app signs in with a cookie and calls the plain API of its own origin.
const WEB = typeof __SHELL_TARGET__ !== 'undefined' && __SHELL_TARGET__ === 'ui';
// Thrown where the web app cannot reach a session's own origin; the pages translate it.
const NO_SESSION_ORIGIN = 'noSessionOrigin';

const isServiceWorker = typeof ServiceWorkerGlobalScope !== 'undefined' && self instanceof ServiceWorkerGlobalScope;

/** Pending launch context kept in memory (Firefox, mobile, and File payloads). */
let pendingContext = null;

let session = {
  key: null,
  id: null,
  baseUrl: null
};

/** Storage keys the served pages may read and write through the bridge. */
const STORAGE_ALLOWED_EXACT = new Set(['simple_launch_profile', 'home_layout', 'sealskinPendingConfig']);
const STORAGE_ALLOWED_PREFIX = 'workflow_profile_';

function isAllowedStorageKey(key) {
  return STORAGE_ALLOWED_EXACT.has(key) || key.startsWith(STORAGE_ALLOWED_PREFIX);
}

async function getSessionTabMap() {
  const result = await chrome.storage.local.get('sessionTabMap');
  return result.sessionTabMap || {};
}

async function saveSessionTabMap(map) {
  await chrome.storage.local.set({ sessionTabMap: map });
}

async function importRsaPublicKey(pem) {
  const buffer = pemToArrayBuffer(pem);
  return crypto.subtle.importKey('spki', buffer, {
      name: 'RSA-PSS',
      hash: 'SHA-256'
    }, true, ['verify'])
    .catch(() => crypto.subtle.importKey('spki', buffer, {
      name: 'RSA-OAEP',
      hash: 'SHA-256'
    }, true, ['encrypt']));
}

async function encryptAesGcm(key, data) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encodedData = new TextEncoder().encode(data);
  const ciphertext = await crypto.subtle.encrypt({
    name: 'AES-GCM',
    iv: iv
  }, key, encodedData);
  return {
    iv: arrayBufferToBase64(iv.buffer),
    ciphertext: arrayBufferToBase64(ciphertext)
  };
}

async function decryptAesGcm(key, iv, ciphertext) {
  const ivBuffer = Uint8Array.from(atob(iv), c => c.charCodeAt(0));
  const ciphertextBuffer = Uint8Array.from(atob(ciphertext), c => c.charCodeAt(0));
  const decrypted = await crypto.subtle.decrypt({
    name: 'AES-GCM',
    iv: ivBuffer
  }, key, ciphertextBuffer);
  return new TextDecoder().decode(decrypted);
}

/**
 * Establish the E2EE session. Tries the https session port first and falls
 * back to the plain http API port (Chrome allows E2EE over http for servers
 * with a self-signed certificate that has not been accepted yet).
 */
async function performHandshake(config) {
  console.log('[SealSkin E2EE] Performing handshake...');
  const { serverIp, apiPort, sessionPort, serverPublicKey } = config;

  if (!serverIp || !apiPort || !serverPublicKey) {
    throw new Error('Server IP, API Port, or Server Public Key is not configured.');
  }

  const tryHandshake = async (baseUrl) => {
    const initResponse = await fetch(`${baseUrl}/api/handshake/initiate`, { method: 'POST' });
    if (!initResponse.ok) throw new Error(`Handshake initiation failed: ${await initResponse.text()}`);
    const { nonce, signature } = await initResponse.json();

    const serverPubKey = await importRsaPublicKey(serverPublicKey);
    const nonceBuffer = Uint8Array.from(atob(nonce), c => c.charCodeAt(0));
    const signatureBuffer = Uint8Array.from(atob(signature), c => c.charCodeAt(0));
    const isValid = await crypto.subtle.verify({ name: 'RSA-PSS', saltLength: 32 }, serverPubKey, signatureBuffer, nonceBuffer);

    if (!isValid) throw new Error('Handshake failed: Server signature verification failed.');

    const aesKey = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, true, ['encrypt', 'decrypt']);
    const exportedKey = await crypto.subtle.exportKey('raw', aesKey);
    const serverEncryptKey = await crypto.subtle.importKey('spki', pemToArrayBuffer(serverPublicKey), { name: 'RSA-OAEP', hash: 'SHA-256' }, false, ['encrypt']);
    const encryptedSessionKey = await crypto.subtle.encrypt({ name: 'RSA-OAEP' }, serverEncryptKey, exportedKey);

    const exchangeResponse = await fetch(`${baseUrl}/api/handshake/exchange`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ encrypted_session_key: arrayBufferToBase64(encryptedSessionKey) }),
    });

    if (!exchangeResponse.ok) throw new Error(`Handshake key exchange failed: ${await exchangeResponse.text()}`);
    const { session_id } = await exchangeResponse.json();
    return { key: aesKey, id: session_id, baseUrl };
  };

  const endpoints = [];
  if (sessionPort) endpoints.push(`https://${serverIp}:${sessionPort}`);
  endpoints.push(`http://${serverIp}:${apiPort}`);

  let lastError;
  const failures = [];
  for (const url of endpoints) {
    try {
      session = await tryHandshake(url);
      console.log(`[SealSkin E2EE] Handshake successful using ${url}`);
      return;
    } catch (e) {
      console.warn(`[SealSkin E2EE] Handshake failed on ${url}:`, e);
      lastError = e;
      failures.push(describeNetworkError(url, e));
    }
  }
  if (failures.length) throw new Error(failures.join(' '));
  throw lastError || new Error('Handshake failed on all attempted ports.');
}

async function getConfig() {
  const { sealskinConfig } = await chrome.storage.local.get('sealskinConfig');
  return sealskinConfig || null;
}

async function ensureSession() {
  if (!session.key || !session.id) {
    const sealskinConfig = await getConfig();
    if (!sealskinConfig) throw new Error('Extension is not configured.');
    await performHandshake(sealskinConfig);
  }
  return session;
}

/**
 * Encrypted API call. Adds the user's JWT to every `/api/` request except the
 * handshake (the pages no longer sign anything themselves).
 */
/** Retry schedule (ms) for network-level failures such as ERR_NETWORK_CHANGED. */
const NETWORK_RETRY_DELAYS_MS = [1000, 2000, 3000, 4000];

/**
 * True for errors thrown by fetch() itself (no HTTP response): DNS/TLS
 * failures, connection resets, and Chrome's ERR_NETWORK_CHANGED, which fires
 * on the docker host whenever a session container brings up an interface.
 */
function isNetworkError(error) {
  if (!error) return false;
  if (error instanceof TypeError) return true;
  const msg = String(error.message || error);
  return /Failed to fetch|NetworkError|network changed|ERR_NETWORK|ERR_CONNECTION|Load failed|ECONN|^Could not connect to/i.test(msg);
}

/**
 * Turn an opaque fetch failure into something a user can act on. Browsers do
 * not expose TLS details to scripts, so an https failure is described with
 * the likely causes (expired or untrusted certificate, server down).
 */
function describeNetworkError(baseUrl, error) {
  const msg = String((error && error.message) || error);
  if (!isNetworkError(error)) return msg;
  if (msg.startsWith('Could not connect to')) return msg;
  if (baseUrl && baseUrl.startsWith('https://')) {
    return `Could not connect to ${baseUrl}. The server may be down, or its TLS certificate may be expired, untrusted, or for a different host name. Open ${baseUrl} in a browser tab to check the certificate.`;
  }
  return `Could not connect to ${baseUrl}. The server may be down, the port may not be reachable, or your network changed. (${msg})`;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * The web app's API call: JSON as it is, on this origin, under the sign-in
 * cookie. No `X-Session-ID` goes with it, which is how the server tells it
 * from an encrypted call.
 */
async function plainFetch(url, options = {}) {
  const headers = { ...options.headers };
  // One key per logical request so a retried POST is executed once server-side.
  if ((options.method || 'GET').toUpperCase() !== 'GET') headers['X-Idempotency-Key'] = crypto.randomUUID();
  if (options.body) headers['Content-Type'] = 'application/json';
  for (let attempt = 0; ; attempt++) {
    try {
      const response = await fetch(url, { ...options, headers, credentials: 'same-origin' });
      const text = response.status === 204 ? '' : await response.text();
      if (!response.ok) throw new Error(`HTTP error! status: ${response.status} - ${text}`);
      return text ? JSON.parse(text) : null;
    } catch (error) {
      if (!isNetworkError(error)) throw error;
      if (attempt >= NETWORK_RETRY_DELAYS_MS.length) throw new Error(describeNetworkError(location.origin, error));
      await sleep(NETWORK_RETRY_DELAYS_MS[attempt]);
    }
  }
}

async function secureFetchInBackground(url, options = {}) {
  if (WEB) return plainFetch(url, options);
  // One key per logical request so a retried POST is executed once server-side.
  const idempotencyKey = (options.method || 'GET').toUpperCase() === 'GET' ? null : crypto.randomUUID();
  let sessionRetried = false;
  let networkRetries = 0;
  for (;;) {
    let baseUrlForError = session.baseUrl;
    try {
      const sealskinConfig = await getConfig();
      if (!sealskinConfig || !sealskinConfig.serverIp || !sealskinConfig.apiPort) {
        throw new Error('Extension is not configured.');
      }

      if (url.startsWith('/api/') && !url.startsWith('/api/handshake')) {
        const jwt = await generateJwtNative(sealskinConfig.clientPrivateKey, sealskinConfig.username);
        options.headers = {
          ...options.headers,
          'Authorization': `Bearer ${jwt}`
        };
      }

      const currentSession = await ensureSession();
      baseUrlForError = currentSession.baseUrl;
      const fullUrl = `${currentSession.baseUrl}${url}`;
      const headers = { ...options.headers, 'X-Session-ID': currentSession.id };
      if (idempotencyKey) headers['X-Idempotency-Key'] = idempotencyKey;
      let body = options.body;

      if (body) {
        const encryptedPayload = await encryptAesGcm(currentSession.key, body);
        body = JSON.stringify(encryptedPayload);
        headers['Content-Type'] = 'application/json';
      }

      const response = await fetch(fullUrl, { ...options, headers, body });

      if (response.status === 204 || response.status === 200 && response.headers.get('Content-Length') === '0') {
        return null;
      }

      const responseText = await response.text();

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status} - ${responseText}`);
      }

      const encryptedResponse = JSON.parse(responseText);
      const decryptedData = await decryptAesGcm(currentSession.key, encryptedResponse.iv, encryptedResponse.ciphertext);
      return JSON.parse(decryptedData);

    } catch (error) {
      console.log('[SealSkin SecureFetchInBackground]', error);
      const message = String((error && error.message) || error);
      const isSessionError = message.includes('atob') ||
        message.includes('decryption') ||
        message.includes('HTTP error! status: 400');

      if (isSessionError && !sessionRetried) {
        sessionRetried = true;
        console.log('[SealSkin] Detected session error, resetting session and retrying...');
        session = { key: null, id: null, baseUrl: null };
        continue;
      }
      if (isNetworkError(error) && networkRetries < NETWORK_RETRY_DELAYS_MS.length) {
        const delay = NETWORK_RETRY_DELAYS_MS[networkRetries++];
        console.log(`[SealSkin] Network error, retrying in ${delay}ms (${networkRetries}/${NETWORK_RETRY_DELAYS_MS.length})...`);
        await sleep(delay);
        continue;
      }
      if (isNetworkError(error)) {
        throw new Error(describeNetworkError(baseUrlForError, error));
      }
      throw error;
    }
  }
}

function getSessionUrlBase(config) {
  if (!config.serverIp || !config.sessionPort) return null;
  return `https://${config.serverIp}:${config.sessionPort}`;
}

const SESSION_PATH = /^\/[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\//i;
const ROOM_PATH = /^\/room\/[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}(?:[/?]|$)/i;
// A web sign-in's collaboration room is served on its session's own origin too.
const opensOnOwnOrigin = (sessionUrl) => SESSION_PATH.test(sessionUrl) || (WEB && ROOM_PATH.test(sessionUrl));

// The web app keeps the session domain in memory: pages on its origin can rewrite its storage.
let webSessionDomain = '';
// Whether the server serves a web sign-in's sessions on origins of their own.
let webSessionIsolation = false;

/** Keep the session domain the server's status names, which the probe tries first, and whether sessions have their own origins. */
async function noteSessionDomain(status) {
  if (!status || typeof status.session_domain !== 'string') return;
  if (WEB) {
    webSessionDomain = status.session_domain;
    webSessionIsolation = Boolean(status.session_isolation);
    return;
  }
  const { sealskinSessionDomain } = await chrome.storage.local.get('sealskinSessionDomain');
  if (sealskinSessionDomain !== status.session_domain) await chrome.storage.local.set({ sealskinSessionDomain: status.session_domain });
}

async function sessionDomain() {
  if (WEB) return webSessionDomain;
  const { sealskinSessionDomain } = await chrome.storage.local.get('sealskinSessionDomain');
  return sealskinSessionDomain || '';
}
const sessionOriginProbes = new Map();

/**
 * The name under which this browser reaches a session's own origin,
 * `<session id>.<suffix>` (see `probeSessionOrigin`), probed once per server
 * and session domain; a probe that found none runs again at the next ask. A
 * session opened on its own origin shares no storage, cookies, or service
 * workers with the web app or other sessions, and one opened on the shared
 * origin is served as it always was; the web app opens a session on its own
 * origin or not at all.
 *
 * @param {object} config
 * @returns {Promise<string|null>} The suffix, or null for the shared origin.
 */
async function sessionOriginSuffix(config) {
  const { serverIp: host, sessionPort: port } = config || {};
  if (!host || !port) return null;
  const key = `${host}:${port}|${await sessionDomain()}`;
  if (!sessionOriginProbes.has(key)) {
    const probe = probeSessionOrigin(host, port, key.split('|')[1]);
    sessionOriginProbes.set(key, probe);
    probe.then((suffix) => { if (!suffix) sessionOriginProbes.delete(key); });
  }
  return sessionOriginProbes.get(key);
}

function sessionHref(config, sessionId, sessionUrl, suffix) {
  if (!suffix) return `${getSessionUrlBase(config)}${sessionUrl}`;
  return `https://${sessionId}.${suffix}:${config.sessionPort}${sessionUrl}`;
}

// --- Pending launch context ---------------------------------------------------

/** True when the context can round-trip through chrome.storage (no File). */
function isJsonSafeContext(context) {
  return !context || !(context.file instanceof Blob);
}

async function setPendingContext(context) {
  pendingContext = context || null;
  if (context && isServiceWorker && isJsonSafeContext(context)) {
    await chrome.storage.local.set({ sealskinContext: context });
  } else if (isServiceWorker) {
    await chrome.storage.local.remove('sealskinContext');
  }
}

async function takePendingContext() {
  if (pendingContext) {
    const ctx = pendingContext;
    pendingContext = null;
    if (isServiceWorker) await chrome.storage.local.remove('sealskinContext');
    return ctx;
  }
  const data = await chrome.storage.local.get('sealskinContext');
  if (data.sealskinContext) {
    await chrome.storage.local.remove('sealskinContext');
    return data.sealskinContext;
  }
  return null;
}

// --- Message handling ---------------------------------------------------------

const handlers = {
  async secureFetch({ url, options }) {
    const data = await secureFetchInBackground(url, options);
    if (url === '/api/admin/status') {
      await noteSessionDomain(data);
      // Probed while the user picks an application, the session origin is known by the launch.
      if (!WEB || webSessionIsolation) getConfig().then(sessionOriginSuffix).catch(() => {});
    }
    return data;
  },

  async getUiBase() {
    if (WEB) return location.origin;
    const currentSession = await ensureSession();
    return currentSession.baseUrl;
  },

  // The web app asks ahead of a launch, so the answer is known by the click.
  async sessionOrigin() {
    return sessionOriginSuffix(await getConfig());
  },

  async getPublicConfig() {
    const config = await getConfig();
    if (!config) return null;
    const { clientPrivateKey, ...publicConfig } = config;
    return publicConfig;
  },

  async getFullConfig() {
    const { sealskinConfig, sealskinPendingConfig } = await chrome.storage.local.get(['sealskinConfig', 'sealskinPendingConfig']);
    return { config: sealskinConfig || null, pendingConfig: sealskinPendingConfig || null };
  },

  async saveConfig({ config }) {
    if (!config || typeof config !== 'object') throw new Error('Invalid configuration.');
    await chrome.storage.local.set({ sealskinConfig: config });
    session = { key: null, id: null, baseUrl: null };
    return {};
  },

  async clearConfig() {
    await chrome.storage.local.remove(['sealskinConfig', 'sealskinPendingConfig']);
    session = { key: null, id: null, baseUrl: null };
    return {};
  },

  async updateConfig(partial = {}) {
    const config = await getConfig();
    if (!config) throw new Error('Extension is not configured.');
    if (partial.searchEngineUrl !== undefined) config.searchEngineUrl = partial.searchEngineUrl;
    if (partial.userSettings !== undefined) config.userSettings = partial.userSettings;
    await chrome.storage.local.set({ sealskinConfig: config });
    return {};
  },

  async getContext() {
    return takePendingContext();
  },

  async setContext({ context }) {
    await setPendingContext(context);
    return {};
  },

  async storageGet({ keys }) {
    if (keys === null || keys === undefined) {
      const all = await chrome.storage.local.get(null);
      const result = {};
      for (const key of Object.keys(all)) {
        if (isAllowedStorageKey(key)) result[key] = all[key];
      }
      return result;
    }
    const list = (Array.isArray(keys) ? keys : [keys]).filter(isAllowedStorageKey);
    return list.length ? chrome.storage.local.get(list) : {};
  },

  async storageSet({ items }) {
    const filtered = {};
    for (const key of Object.keys(items || {})) {
      if (isAllowedStorageKey(key)) filtered[key] = items[key];
    }
    if (Object.keys(filtered).length) await chrome.storage.local.set(filtered);
    return {};
  },

  async storageRemove({ keys }) {
    const list = (Array.isArray(keys) ? keys : [keys]).filter(isAllowedStorageKey);
    if (list.length) await chrome.storage.local.remove(list);
    return {};
  },

  async openPopup() {
    if (chrome.action && chrome.action.openPopup) await chrome.action.openPopup();
    return {};
  },

  async createTabAndTrack({ sessionId, session_url }) {
    const config = await getConfig();
    const own = WEB ? webSessionIsolation && opensOnOwnOrigin(session_url) : opensOnOwnOrigin(session_url);
    const suffix = own ? await sessionOriginSuffix(config) : null;
    // The server serves a web sign-in's session on its own origin alone.
    if (WEB && own && !suffix) throw new Error(NO_SESSION_ORIGIN);
    const newTab = await chrome.tabs.create({ url: sessionHref(config, sessionId, session_url, suffix) });
    const map = await getSessionTabMap();
    if (newTab && newTab.id) {
      map[sessionId] = newTab.id;
      await saveSessionTabMap(map);
    }
    return {};
  },

  async focusOrCreateTab({ session: sess }) {
    const map = await getSessionTabMap();
    const tabId = map[sess.session_id];

    if (tabId) {
      try {
        const updatedTab = await chrome.tabs.update(tabId, { active: true });
        if (updatedTab && chrome.windows && chrome.windows.update) {
          await chrome.windows.update(updatedTab.windowId, { focused: true });
        }
        return {};
      } catch (e) {
        console.log(`Tab ${tabId} could not be focused, will create a new one. Error: ${e.message}`);
        delete map[sess.session_id];
        await saveSessionTabMap(map);
      }
    }

    const config = await getConfig();
    const own = WEB ? webSessionIsolation && opensOnOwnOrigin(sess.session_url) : sess.own_origin;
    const probed = own ? await sessionOriginSuffix(config) : null;
    if (WEB && own && !probed) throw new Error(NO_SESSION_ORIGIN);
    const suffix = own ? probed || config.serverIp : null;
    const newTab = await chrome.tabs.create({ url: sessionHref(config, sess.session_id, sess.session_url, suffix) });
    if (newTab && newTab.id) {
      map[sess.session_id] = newTab.id;
      await saveSessionTabMap(map);
    }
    return {};
  },

  async closeSession({ sessionId }) {
    const map = await getSessionTabMap();
    const tabId = map[sessionId];
    if (tabId) {
      try {
        await chrome.tabs.remove(tabId);
      } catch (e) { /* tab already gone */ }
    }
    await secureFetchInBackground(`/api/sessions/${sessionId}`, { method: 'DELETE' });
    delete map[sessionId];
    await saveSessionTabMap(map);
    return {};
  },

  async openExternal({ url }) {
    await chrome.tabs.create({ url });
    return {};
  },
};

/**
 * Dispatch one message. Works both as a `chrome.runtime.onMessage` listener and
 * as `window.handleMessage` on mobile. Always answers.
 */
const handleMessage = (request, sender, sendResponse) => {
  const handler = request && handlers[request.type];
  if (!handler) {
    sendResponse({ success: false, error: `Unknown message type: ${request && request.type}` });
    return false;
  }
  (async () => {
    try {
      const data = await handler(request.payload || {});
      sendResponse({ success: true, data });
    } catch (error) {
      sendResponse({ success: false, error: error.message });
    }
  })();
  return true;
};

if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
  chrome.runtime.onMessage.addListener(handleMessage);
}

if (typeof window !== 'undefined') {
  window.handleMessage = handleMessage;
}

chrome.storage.onChanged.addListener((changes, namespace) => {
  if (namespace === 'local' && changes.sealskinConfig) {
    session = {
      key: null,
      id: null,
      baseUrl: null
    };
  }
});

// --- Context Menu and Download Logic -----------------------------------------

function contextMenuLabel(key) {
  const lang = (chrome.i18n && chrome.i18n.getUILanguage ? chrome.i18n.getUILanguage() : 'en').split('-')[0];
  const table = contextMenuTitles[lang] || contextMenuTitles.en;
  return table[key] || contextMenuTitles.en[key];
}

if (chrome.runtime && chrome.runtime.onInstalled && chrome.contextMenus && chrome.contextMenus.create) {
  chrome.runtime.onInstalled.addListener(() => {
    chrome.contextMenus.create({
      id: 'sealskin-open-url',
      title: contextMenuLabel('openUrl'),
      contexts: ['link']
    });
    chrome.contextMenus.create({
      id: 'sealskin-open-file',
      title: contextMenuLabel('openFile'),
      contexts: ['link']
    });
    chrome.contextMenus.create({
      id: 'sealskin-send-media',
      title: contextMenuLabel('sendMedia'),
      contexts: ['image', 'video', 'audio']
    });
    chrome.contextMenus.create({
      id: 'sealskin-search-selection',
      title: contextMenuLabel('searchText'),
      contexts: ['selection']
    });
    if (isServiceWorker) {
      chrome.contextMenus.create({
        id: 'sealskin-intercept-next-download',
        title: contextMenuLabel('sendDownload'),
        contexts: ['page', 'selection']
      });
    }
  });
}

if (chrome.contextMenus && chrome.contextMenus.onClicked) {
  chrome.contextMenus.onClicked.addListener((info) => {
    const {
      menuItemId,
      linkUrl,
      srcUrl,
      selectionText
    } = info;
    if (menuItemId === 'sealskin-intercept-next-download') {
      chrome.storage.local.set({
        'interceptNextDownload': {
          active: true,
          timestamp: Date.now()
        }
      });
      chrome.action.setBadgeText({
        text: '...'
      });
      return;
    }

    let context = null;

    const getFilenameFromUrl = (url) => {
      try {
        const pathname = new URL(url).pathname;
        return pathname.substring(pathname.lastIndexOf('/') + 1) || 'file_from_url';
      } catch (e) {
        console.warn('Could not parse URL to get filename:', url);
        return 'unknown_file';
      }
    };

    if (menuItemId === 'sealskin-open-url') {
      context = {
        action: 'url',
        targetUrl: linkUrl
      };
    } else if (menuItemId === 'sealskin-open-file') {
      context = {
        action: 'file',
        targetUrl: linkUrl,
        filename: getFilenameFromUrl(linkUrl)
      };
    } else if (menuItemId === 'sealskin-send-media') {
      context = {
        action: 'file',
        targetUrl: srcUrl,
        filename: getFilenameFromUrl(srcUrl)
      };
    } else if (menuItemId === 'sealskin-search-selection') {
      context = {
        action: 'search',
        selectionText: selectionText
      };
    }

    if (context) {
      if (isServiceWorker) {
        chrome.storage.local.set({
          'sealskinContext': context
        }, () => chrome.action.openPopup());
      } else {
        pendingContext = context;
        chrome.action.openPopup();
      }
    }
  });
}

if (chrome.downloads && chrome.downloads.onDeterminingFilename) {
  chrome.downloads.onDeterminingFilename.addListener((downloadItem, suggest) => {
    (async () => {
      const data = await chrome.storage.local.get('interceptNextDownload');
      const interceptConfig = data.interceptNextDownload;
      if (interceptConfig && interceptConfig.active && (Date.now() - interceptConfig.timestamp < 60000)) {
        await chrome.storage.local.remove('interceptNextDownload');
        chrome.action.setBadgeText({
          text: ''
        });
        await chrome.downloads.cancel(downloadItem.id);
        await chrome.storage.local.set({
          'sealskinContext': {
            action: 'file',
            targetUrl: downloadItem.url,
            filename: downloadItem.filename
          }
        });
        chrome.action.openPopup();
      } else suggest();
    })();
    return true;
  });
}

if (isServiceWorker) {
  self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);
    if (url.protocol === 'chrome-extension:' && url.pathname === '/download-stream') {
      event.respondWith(handleStreamingDownload(event.request));
    }
  });
}

async function handleStreamingDownload(request) {
  const url = new URL(request.url);
  const home = url.searchParams.get('home');
  const path = url.searchParams.get('path');
  const filename = url.searchParams.get('filename') || path.split('/').pop();

  try {
    const stream = new ReadableStream({
      async start(controller) {
        let chunkIndex = 0;
        let isLastChunk = false;
        try {
          while (!isLastChunk) {
            const params = new URLSearchParams({ path, chunk_index: chunkIndex });
            const apiUrl = `/api/files/download/chunk/${home}?${params.toString()}`;
            const response = await secureFetchInBackground(apiUrl, { method: 'GET' });

            if (response.chunk_data_b64) {
              const binaryString = atob(response.chunk_data_b64);
              const len = binaryString.length;
              const bytes = new Uint8Array(len);
              for (let i = 0; i < len; i++) {
                bytes[i] = binaryString.charCodeAt(i);
              }
              controller.enqueue(bytes);
            }
            isLastChunk = response.is_last_chunk;
            chunkIndex++;
          }
          controller.close();
        } catch (error) {
          console.error('[SealSkin BG] Streaming download failed:', error);
          controller.error(error);
        }
      }
    });

    return new Response(stream, {
      headers: { 'Content-Disposition': `attachment; filename="${filename}"`, 'Content-Type': 'application/octet-stream' }
    });
  } catch (error) {
    console.error('[SealSkin BG] Failed to create download stream response:', error);
    return new Response(`Streaming download failed: ${error.message}`, { status: 500 });
  }
}
