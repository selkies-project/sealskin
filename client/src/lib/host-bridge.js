/**
 * Host side of the shell bridge (protocol version 1).
 *
 * A host page (extension popup/options/host page, or the mobile outer window)
 * owns one iframe showing either the bundled connect page or a served page.
 * The page sends `{sealskin: 1, id, type, payload}` through `postMessage`; this
 * module validates the sender, dispatches to a handler and replies with
 * `{sealskin: 1, id, ok: true, data}` or `{sealskin: 1, id, ok: false, error}`.
 *
 * Privileged work is relayed to the background through `transport(message)`,
 * which resolves with the background's `{success, data|error}` reply:
 *   - extension: `chrome.runtime.sendMessage`
 *   - mobile:    `window.handleMessage` (background runs in the outer window)
 *
 * See docs/content/architecture.md ("The bridge") for the contract.
 */

import { storePendingFile, takePendingFile } from './context-store.js';

export const BRIDGE_VERSION = 1;

/* global __SHELL_TARGET__, __UI_VERSION__ */

const SHELL = typeof __SHELL_TARGET__ !== 'undefined' ? __SHELL_TARGET__ : 'extension';
const SHELL_VERSION = typeof __UI_VERSION__ !== 'undefined' ? __UI_VERSION__ : 'dev';

/**
 * Detect the platform the host runs on.
 *
 * @returns {'chrome'|'firefox'|'android'|'ios'|'web'}
 */
function detectPlatform() {
  const cap = typeof window !== 'undefined' && window.Capacitor;
  if (cap && typeof cap.getPlatform === 'function') {
    const p = cap.getPlatform();
    if (p === 'android' || p === 'ios') return p;
  }
  const ua = navigator.userAgent || '';
  if (/Android/i.test(ua) && SHELL === 'mobile') return 'android';
  if (/iPhone|iPad|iPod/i.test(ua) && SHELL === 'mobile') return 'ios';
  if (typeof browser !== 'undefined' && /Firefox/.test(ua)) return 'firefox';
  if (/Firefox/.test(ua)) return 'firefox';
  if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.id) return 'chrome';
  return 'web';
}

/**
 * Call the background through the transport and unwrap its reply.
 *
 * @param {function} transport
 * @param {string} type
 * @param {object} [payload]
 */
export async function callBackground(transport, type, payload = {}) {
  const reply = await transport({ type, payload });
  if (!reply) throw new Error(`No response from background for '${type}'`);
  if (reply.success === false) throw new Error(reply.error || `Background request '${type}' failed`);
  return reply.data;
}

/**
 * Create the host bridge.
 *
 * @param {object} options
 * @param {HTMLIFrameElement} options.iframe The single framed page.
 * @param {function} options.transport Sends a message to the background, resolves its reply.
 * @param {function} [options.onReady] Called with the page's hello payload the first time it arrives.
 * @param {function} [options.onHelloMismatch] Called when the page's bridge version differs.
 * @param {function} options.openPage `(page, params?) => void` implemented by the host page.
 * @param {function} [options.saveBlob] `(blob, filename) => Promise` for mobile native open.
 * @param {function} [options.close] Closes the popup (extension) or no-op.
 * @param {function} [options.isConnectPage] `() => boolean`, true while the bundled connect page is framed.
 * @returns {{setExpectedOrigin: function(string): void, destroy: function(): void}}
 */
export function createHost(options) {
  const { iframe, transport } = options;
  const platform = detectPlatform();
  let expectedOrigin = null;
  let helloSeen = false;

  const capabilities = {
    streamDownload: SHELL === 'extension' && platform === 'chrome',
    nativeFileOpen: SHELL === 'mobile',
    contextMenus: SHELL === 'extension',
    tabs: SHELL === 'extension',
  };

  const handlers = {
    async hello(payload) {
      const config = await callBackground(transport, 'getPublicConfig');
      const info = {
        bridge: BRIDGE_VERSION,
        shell: SHELL,
        platform,
        shellVersion: SHELL_VERSION,
        locale: navigator.language || 'en-US',
        capabilities,
        config: config ? {
          serverIp: config.serverIp,
          apiPort: config.apiPort,
          sessionPort: config.sessionPort,
          username: config.username,
          searchEngineUrl: config.searchEngineUrl,
          userSettings: config.userSettings,
        } : null,
      };
      if (!helloSeen) {
        helloSeen = true;
        if (payload && payload.bridge !== BRIDGE_VERSION && options.onHelloMismatch) {
          options.onHelloMismatch(payload);
        } else if (options.onReady) {
          options.onReady(payload);
        }
      }
      return info;
    },

    secureFetch({ url, options: fetchOptions }) {
      return callBackground(transport, 'secureFetch', { url, options: fetchOptions || {} });
    },

    async getContext() {
      const context = await callBackground(transport, 'getContext');
      if (context && context.hasFile) {
        const file = await takePendingFile();
        delete context.hasFile;
        if (file) context.file = file;
      }
      return context;
    },

    async setContext({ context, openPopup = true }) {
      let toStore = context;
      if (context && context.file instanceof Blob) {
        await storePendingFile(context.file);
        const { file, ...rest } = context;
        toStore = { ...rest, hasFile: true };
      }
      await callBackground(transport, 'setContext', { context: toStore });
      if (openPopup) {
        if (SHELL === 'mobile') {
          options.openPage('popup');
        } else {
          await callBackground(transport, 'openPopup');
        }
      }
      return {};
    },

    async fetchBlob({ url }) {
      // Host pages carry the extension's host permissions, so cross-origin
      // fetches of link targets and media work here (not in the served page).
      const response = await fetch(url);
      if (!response.ok) throw new Error(`Failed to fetch file data: ${response.statusText}`);
      return response.blob();
    },

    openSession({ sessionId, sessionUrl }) {
      return callBackground(transport, 'createTabAndTrack', { sessionId, session_url: sessionUrl });
    },

    focusSession({ session }) {
      return callBackground(transport, 'focusOrCreateTab', { session });
    },

    closeSession({ sessionId }) {
      return callBackground(transport, 'closeSession', { sessionId });
    },

    async openPage({ page, params }) {
      options.openPage(page, params && typeof params === 'object' ? params : undefined);
      return {};
    },

    openExternal({ url }) {
      return callBackground(transport, 'openExternal', { url });
    },

    async downloadFile({ home, path, filename }) {
      if (!capabilities.streamDownload) throw new Error('Streaming download is not available in this shell.');
      const params = new URLSearchParams({ home, path, filename });
      const a = document.createElement('a');
      a.href = chrome.runtime.getURL(`/download-stream?${params.toString()}`);
      document.body.appendChild(a);
      a.click();
      a.remove();
      return {};
    },

    async saveBlob({ blob, filename }) {
      if (!options.saveBlob) throw new Error('Native file open is not available in this shell.');
      await options.saveBlob(blob, filename);
      return {};
    },

    storageGet({ keys }) {
      return callBackground(transport, 'storageGet', { keys: keys === undefined ? null : keys });
    },

    storageSet({ items }) {
      return callBackground(transport, 'storageSet', { items });
    },

    storageRemove({ keys }) {
      return callBackground(transport, 'storageRemove', { keys });
    },

    updateConfig(partial) {
      return callBackground(transport, 'updateConfig', partial || {});
    },

    // Connect page only: the full config including the private key.
    saveConfig({ config }) {
      if (!(options.isConnectPage && options.isConnectPage())) {
        throw new Error('saveConfig is only accepted from the connect page.');
      }
      return callBackground(transport, 'saveConfig', { config });
    },

    getConnectConfig() {
      if (!(options.isConnectPage && options.isConnectPage())) {
        throw new Error('getConnectConfig is only accepted from the connect page.');
      }
      return callBackground(transport, 'getFullConfig');
    },

    clearConfig() {
      if (!(options.isConnectPage && options.isConnectPage())) {
        throw new Error('clearConfig is only accepted from the connect page.');
      }
      return callBackground(transport, 'clearConfig');
    },

    async close() {
      if (options.close) options.close();
      return {};
    },
  };

  function reply(msg, body) {
    if (!iframe.contentWindow) return;
    iframe.contentWindow.postMessage({ sealskin: BRIDGE_VERSION, id: msg.id, ...body }, expectedOrigin || '*');
  }

  async function onMessage(event) {
    const msg = event.data;
    if (!msg || msg.sealskin !== BRIDGE_VERSION || typeof msg.id !== 'number' || !msg.type) return;
    if (event.source !== iframe.contentWindow) return;
    if (expectedOrigin && event.origin !== expectedOrigin) return;
    const handler = handlers[msg.type];
    if (!handler) {
      reply(msg, { ok: false, error: `Unknown bridge request '${msg.type}'` });
      return;
    }
    try {
      const data = await handler(msg.payload || {});
      reply(msg, { ok: true, data });
    } catch (error) {
      reply(msg, { ok: false, error: error && error.message ? error.message : String(error) });
    }
  }

  window.addEventListener('message', onMessage);

  return {
    /**
     * Set the origin the framed page is expected to come from. `null` accepts
     * only the extension/app origin (used for the bundled connect page).
     */
    setExpectedOrigin(origin) {
      expectedOrigin = origin;
      helloSeen = false;
    },
    destroy() {
      window.removeEventListener('message', onMessage);
    },
  };
}
