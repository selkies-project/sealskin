/**
 * Installs the `chrome.*` polyfill for the web app before `background.js`
 * runs (app.js imports this module first). The hooks are late-bound: app.js
 * fills them in once the host exists.
 *
 * Session pages share this origin and can edit its storage, so a stored
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
 * tab up by name would make this page its opener, and session content comes
 * from this origin. Session tabs keep no `opener`. Anything else opens
 * detached.
 *
 * @param {string} url
 */
function openTab(url) {
  const target = new URL(url, location.href);
  const session = target.origin === location.origin && target.pathname.match(SESSION_ID);
  if (!session) {
    window.open(target.href, '_blank', 'noopener');
    return;
  }
  let tab = sessionTabs.get(session[0]);
  if (!tab || tab.closed) {
    tab = reserved && !reserved.closed ? reserved : window.open();
    reserved = null;
    if (!tab) return;
    tab.opener = null;
    tab.location.replace(target.href);
    sessionTabs.set(session[0], tab);
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
    Object.assign(result.sealskinConfig, { serverIp: location.hostname, apiPort: port, sessionPort: port, clientPrivateKey: KEY_REF });
  }
  if (cb) cb(result);
  return result;
};
