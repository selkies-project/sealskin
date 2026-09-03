/**
 * Frame host for the SealSkin shells.
 *
 * Owns one iframe and decides what it shows:
 *
 *  1. no configuration      -> the bundled connect page
 *  2. configuration present -> `<served base>/ui/<page>.html`
 *  3. server unreachable    -> the unreachable panel (retry / open server /
 *                              change connection)
 *
 * The served base comes from the background (`getUiBase`), which performs the
 * E2EE handshake trying the https session port first and the http API port
 * second. That keeps the Chrome self-signed certificate workflow: the "Open
 * server" button opens the https origin in a top-level tab so the certificate
 * can be accepted, after which https works for both the API and the iframe.
 *
 * On the extension this module runs on its own (see the bottom); the mobile
 * shell imports `initHost` and passes its native hooks.
 */

import { createHost, callBackground } from '../lib/host-bridge.js';
import { loadTranslator } from '../lib/i18n.js';

/* global __SHELL_TARGET__, __UI_VERSION__ */

const SHELL = typeof __SHELL_TARGET__ !== 'undefined' ? __SHELL_TARGET__ : 'extension';
const SHELL_VERSION = typeof __UI_VERSION__ !== 'undefined' ? __UI_VERSION__ : 'dev';
const READY_TIMEOUT_MS = 8000;
const SERVED_PAGES = new Set(['popup', 'options', 'files', 'upload']);

// English fallbacks; replaced by the shell i18n subset (`shell.host.*`) when it loads.
const STRINGS = {
  loading: 'Connecting to your SealSkin server…',
  unconfiguredTitle: 'Not connected',
  unconfiguredBody: 'SealSkin is not connected to a server yet.',
  setUp: 'Set up connection',
  unreachableTitle: 'Server unreachable',
  unreachableBody: 'The SealSkin server could not be reached or its web interface did not load.',
  selfSigned: 'If the server uses a self-signed certificate, open it once and accept the certificate, then retry.',
  retry: 'Retry',
  openServer: 'Open server',
  changeConnection: 'Change connection',
  mismatchTitle: 'Update required',
  mismatchBody: 'This app and the server web interface speak different bridge versions. Update the extension or app, or update the server, so both match.',
};

/**
 * Translate the host panel strings in place using the bundled i18n subset.
 * Falls back silently to the English defaults if loading fails.
 */
async function localizeStrings() {
  try {
    const t = await loadTranslator(navigator.language);
    for (const key of Object.keys(STRINGS)) {
      const value = t(`shell.host.${key}`);
      if (typeof value === 'string' && value && value !== `shell.host.${key}`) STRINGS[key] = value;
    }
  } catch (e) {
    /* keep English */
  }
}

function extensionTransport(message) {
  return new Promise((resolve, reject) => {
    try {
      chrome.runtime.sendMessage(message, (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
        } else {
          resolve(response);
        }
      });
    } catch (e) {
      reject(e);
    }
  });
}

function mobileTransport(message) {
  return new Promise((resolve, reject) => {
    if (typeof window.handleMessage !== 'function') {
      reject(new Error('Background is not loaded.'));
      return;
    }
    window.handleMessage(message, { id: 'mobile-shell' }, resolve);
  });
}

/**
 * Start the host.
 *
 * @param {object} [overrides]
 * @param {function} [overrides.transport]
 * @param {function} [overrides.saveBlob] mobile native file open
 * @param {function} [overrides.onPageChange] `(page) => void`, mobile back button bookkeeping
 * @returns {{openPage: function(string, object=): void, currentPage: function(): string, boot: function(): Promise<void>}}
 */
export function initHost(overrides = {}) {
  const params = new URLSearchParams(location.search);
  const iframe = document.getElementById('app-frame');
  const panel = document.getElementById('host-panel');
  const transport = overrides.transport || (SHELL === 'mobile' ? mobileTransport : extensionTransport);

  let page = document.body.dataset.page || params.get('page') || 'popup';
  // Extra query parameters (anything except page/tab) are forwarded to the served page.
  let pageParams = {};
  for (const [key, value] of params.entries()) {
    if (key !== 'page' && key !== 'tab') pageParams[key] = value;
  }
  const isPopupWindow = SHELL === 'extension' && document.body.dataset.page === 'popup' && !params.has('tab');
  let framedConnect = false;
  let readyTimer = null;
  let lastBase = null;
  let lastConfig = null;

  const bg = (type, payload) => callBackground(transport, type, payload);

  function showPanel(kind, detail = '') {
    if (readyTimer) { clearTimeout(readyTimer); readyTimer = null; }
    panel.innerHTML = '';
    if (!kind) {
      panel.hidden = true;
      iframe.hidden = false;
      return;
    }
    iframe.hidden = true;
    panel.hidden = false;

    const box = document.createElement('div');
    box.className = 'host-box';
    const logo = document.createElement('img');
    logo.src = 'icons/icon128.png';
    logo.alt = 'SealSkin';
    logo.className = 'host-logo';
    box.appendChild(logo);

    const addText = (tag, text, cls) => {
      const el = document.createElement(tag);
      el.textContent = text;
      if (cls) el.className = cls;
      box.appendChild(el);
      return el;
    };
    const addButton = (label, cls, onClick) => {
      const b = document.createElement('button');
      b.textContent = label;
      b.className = cls;
      b.addEventListener('click', onClick);
      return b;
    };
    const actions = document.createElement('div');
    actions.className = 'host-actions';

    if (kind === 'loading') {
      const spin = document.createElement('div');
      spin.className = 'host-spinner';
      box.appendChild(spin);
      addText('p', STRINGS.loading, 'host-muted');
    } else if (kind === 'unconfigured') {
      addText('h2', STRINGS.unconfiguredTitle);
      addText('p', STRINGS.unconfiguredBody, 'host-muted');
      actions.appendChild(addButton(STRINGS.setUp, 'primary', () => openPage('connect')));
    } else if (kind === 'unreachable') {
      addText('h2', STRINGS.unreachableTitle);
      addText('p', STRINGS.unreachableBody, 'host-muted');
      if (lastConfig && lastConfig.serverIp) {
        addText('code', `${lastConfig.serverIp}:${lastConfig.sessionPort || lastConfig.apiPort}`, 'host-server');
      }
      if (detail) addText('p', detail, 'host-error');
      addText('p', STRINGS.selfSigned, 'host-muted host-small');
      actions.appendChild(addButton(STRINGS.retry, 'primary', () => boot()));
      actions.appendChild(addButton(STRINGS.openServer, 'secondary', () => openServer()));
      actions.appendChild(addButton(STRINGS.changeConnection, 'secondary', () => openPage('connect')));
    } else if (kind === 'mismatch') {
      addText('h2', STRINGS.mismatchTitle);
      addText('p', STRINGS.mismatchBody, 'host-muted');
      if (detail) addText('p', detail, 'host-error');
      actions.appendChild(addButton(STRINGS.retry, 'primary', () => boot()));
      actions.appendChild(addButton(STRINGS.changeConnection, 'secondary', () => openPage('connect')));
    }
    if (actions.childElementCount) box.appendChild(actions);
    panel.appendChild(box);
  }

  function servedOrigin(base) {
    try {
      return new URL(base).origin;
    } catch (e) {
      return base;
    }
  }

  async function openServer() {
    const config = lastConfig || (await bg('getPublicConfig').catch(() => null));
    if (!config || !config.serverIp) return;
    const url = config.sessionPort
      ? `https://${config.serverIp}:${config.sessionPort}/ui/`
      : `http://${config.serverIp}:${config.apiPort}/ui/`;
    try {
      await bg('openExternal', { url });
    } catch (e) {
      window.open(url, '_blank');
    }
  }

  function loadConnect() {
    framedConnect = true;
    host.setExpectedOrigin(location.origin && location.origin !== 'null' ? location.origin : null);
    showPanel(null);
    iframe.src = 'connect.html';
    page = 'connect';
    if (overrides.onPageChange) overrides.onPageChange(page);
  }

  function loadServed(base, target) {
    framedConnect = false;
    lastBase = base;
    host.setExpectedOrigin(servedOrigin(base));
    showPanel('loading');
    iframe.hidden = false;
    const query = new URLSearchParams(pageParams).toString();
    iframe.src = `${base.replace(/\/$/, '')}/ui/${target}.html${query ? `?${query}` : ''}`;
    readyTimer = setTimeout(() => {
      showPanel('unreachable', 'The web interface did not respond in time.');
    }, READY_TIMEOUT_MS);
    page = target;
    if (overrides.onPageChange) overrides.onPageChange(page);
  }

  async function boot() {
    if (page === 'connect') {
      loadConnect();
      return;
    }
    showPanel('loading');
    let config = null;
    try {
      config = await bg('getPublicConfig');
    } catch (e) {
      config = null;
    }
    lastConfig = config;
    if (!config || !config.serverIp || !config.username) {
      if (isPopupWindow) showPanel('unconfigured');
      else loadConnect();
      return;
    }
    let base;
    try {
      base = await bg('getUiBase');
    } catch (e) {
      showPanel('unreachable', e && e.message ? e.message : String(e));
      return;
    }
    loadServed(base, SERVED_PAGES.has(page) ? page : 'popup');
  }

  /**
   * Navigate to another page. From the extension popup, served pages other
   * than the popup open in a tab and the popup closes; everywhere else the
   * iframe is navigated in place.
   */
  function openPage(target, extraParams) {
    const nextParams = extraParams && typeof extraParams === 'object' ? { ...extraParams } : {};
    if (SHELL === 'extension' && isPopupWindow) {
      if (target === 'popup') {
        pageParams = nextParams;
        boot();
        return;
      }
      const query = new URLSearchParams({ page: target, ...nextParams }).toString();
      chrome.tabs.create({ url: chrome.runtime.getURL(`host.html?${query}`) });
      window.close();
      return;
    }
    pageParams = nextParams;
    page = target === 'connect' || SERVED_PAGES.has(target) ? target : 'popup';
    boot();
  }

  const host = createHost({
    iframe,
    transport,
    openPage,
    saveBlob: overrides.saveBlob,
    isConnectPage: () => framedConnect,
    close: () => {
      if (SHELL === 'extension' && isPopupWindow) window.close();
    },
    onReady: () => {
      showPanel(null);
    },
    onHelloMismatch: (payload) => {
      const theirs = payload && payload.bridge !== undefined ? payload.bridge : '?';
      showPanel('mismatch', `App bridge v1 (${SHELL_VERSION}), server UI bridge v${theirs} (${(payload && payload.uiVersion) || '?'}).`);
    },
  });

  document.documentElement.classList.add(`shell-${SHELL}`);
  localizeStrings().finally(() => boot());

  return {
    openPage,
    boot,
    currentPage: () => page,
    servedBase: () => lastBase,
  };
}

if (SHELL !== 'mobile') {
  initHost();
}
