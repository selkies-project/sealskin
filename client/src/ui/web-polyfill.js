/**
 * Installs the `chrome.*` polyfill for the web app before `background.js`
 * runs (app.js imports this module first). The hooks are late-bound: app.js
 * fills them in once the host exists.
 *
 * A session the browser cannot open on its own origin is served from this
 * one, and its pages can edit this origin's storage, so a stored
 * configuration never decides where to connect or what to sign with: reads
 * name this origin as the server and the sealed key as the client key.
 */
import { installPolyfill } from '../shell/polyfill.js';

const SESSION_ID = /[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}/i;
// Stored in place of the PEM; `setSigningKey` maps it to the unwrapped key.
export const KEY_REF = 'sealed-key';

export const hooks = {
  openPopup: () => {},
};

const sessionTabs = new Map();
let reserved = null;

/**
 * Take a blank tab while the click that launches a session still allows one,
 * or close it when the launch fails.
 *
 * @param {boolean} reserve
 */
export function reserveTab(reserve) {
  if (reserve) {
    if (!reserved || reserved.closed) reserved = window.open();
    if (reserved) reserved.opener = null;
  } else if (reserved) {
    reserved.close();
    reserved = null;
  }
}

/**
 * Open a session in a tab of its own, or bring forward the tab showing it.
 *
 * This page opens every session tab itself and keeps its handle: looking a
 * tab up by name would make this page its opener, and session content may
 * come from this origin. A session is on this origin or on its own, whose
 * name starts with the session's id. Session tabs keep no `opener`. Anything
 * else opens detached.
 *
 * @param {string} url
 */
function openTab(url) {
  const target = new URL(url, location.href);
  const id = (target.pathname.match(SESSION_ID) || [])[0];
  const session = id && (target.origin === location.origin || target.hostname.startsWith(`${id}.`)) && id;
  if (!session) {
    window.open(target.href, '_blank', 'noopener');
    return;
  }
  let tab = sessionTabs.get(session);
  if (!tab || tab.closed) {
    tab = reserved && !reserved.closed ? reserved : window.open();
    reserved = null;
    if (!tab) return;
    tab.opener = null;
    tab.location.replace(target.href);
    sessionTabs.set(session, tab);
  }
  tab.focus();
}

installPolyfill({
  openExternal: async (url) => openTab(url),
  closeExternal: (url) => {
    const session = new URL(url, location.href).pathname.match(SESSION_ID);
    if (session && sessionTabs.has(session[0])) {
      sessionTabs.get(session[0]).close();
      sessionTabs.delete(session[0]);
    }
  },
  openPopup: () => hooks.openPopup(),
});

// The configurations, which stay in this page alone while the user keeps no key in this browser.
const CONFIGS = ['sealskinConfig', 'sealskinPendingConfig'];
let memory = null;

/**
 * Keep the configurations in this page's memory and none in storage, or go
 * back to storage for the next one saved. While they are in memory, one a
 * session page writes into storage is never read.
 *
 * @param {boolean} on
 */
export function keepInMemory(on) {
  memory = on ? memory || {} : null;
  if (on) CONFIGS.forEach((key) => localStorage.removeItem(key));
}

const { get, set, remove } = chrome.storage.local;
chrome.storage.local.set = (items, cb) => {
  const rest = { ...items };
  CONFIGS.forEach((key) => {
    if (memory && key in rest) {
      memory[key] = rest[key];
      delete rest[key];
    }
  });
  return set(rest, cb);
};
chrome.storage.local.remove = (keys, cb) => {
  if (memory) [].concat(keys).forEach((key) => delete memory[key]);
  return remove(keys, cb);
};
chrome.storage.local.get = async (keys, cb) => {
  const result = await get(keys);
  if (memory) {
    CONFIGS.filter((key) => keys == null || [].concat(keys).includes(key)).forEach((key) => {
      if (key in memory) result[key] = memory[key];
      else delete result[key];
    });
  }
  if (result.sealskinConfig) {
    const port = location.port || '443';
    Object.assign(result.sealskinConfig, { serverIp: location.hostname, apiPort: port, sessionPort: port, clientPrivateKey: KEY_REF });
  }
  if (cb) cb(result);
  return result;
};
