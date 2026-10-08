/**
 * Installs the `chrome.*` polyfill for the web app before `background.js`
 * runs (app.js imports this module first). The hooks are late-bound: app.js
 * fills them in once the host exists.
 *
 * A session of a key-file user that the browser cannot open on its own origin
 * is served from this one, and its pages can edit this origin's storage, so a
 * stored configuration never decides where to connect: reads name this origin
 * as the server.
 */
import { installPolyfill } from '../shell/polyfill.js';

const SESSION_ID = /[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}/i;

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

// Tabs showing the launching page, by launch id, until their session is known.
const launchTabs = new Map();

/**
 * Open the launching page in a tab while the click that launches a session
 * still allows one. The page takes itself to the session.
 *
 * @param {string} id The launch id.
 * @param {string} url The launching page's address.
 * @returns {boolean} False when the browser blocked the tab.
 */
export function openLaunchTab(id, url) {
  const tab = window.open(url);
  if (!tab) return false;
  tab.opener = null;
  launchTabs.set(id, tab);
  return true;
}

/**
 * Keep a launch's tab as its session's, to focus and close it later.
 *
 * @param {string} id The launch id.
 * @param {string} sessionId
 * @returns {boolean} False when the launch has no open tab.
 */
export function adoptLaunchTab(id, sessionId) {
  const tab = launchTabs.get(id);
  launchTabs.delete(id);
  if (!tab || tab.closed) return false;
  sessionTabs.set(sessionId, tab);
  return true;
}

/**
 * Open a session: bring forward the tab showing it, fill the tab taken for
 * it at the click, or else take the browser there in this tab, in the web
 * app's place.
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
    tab = reserved && !reserved.closed ? reserved : null;
    reserved = null;
    if (!tab) {
      location.assign(target.href);
      return;
    }
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

const { get } = chrome.storage.local;
chrome.storage.local.get = async (keys, cb) => {
  const result = await get(keys);
  if (result.sealskinConfig) {
    const port = location.port || '443';
    Object.assign(result.sealskinConfig, { serverIp: location.hostname, apiPort: port, sessionPort: port });
  }
  if (cb) cb(result);
  return result;
};
