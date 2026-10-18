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
import { detectPlatform } from './platform.js';

export const BRIDGE_VERSION = 1;

/* global __SHELL_TARGET__, __UI_VERSION__ */

const SHELL = typeof __SHELL_TARGET__ !== 'undefined' ? __SHELL_TARGET__ : 'extension';
const SHELL_VERSION = typeof __UI_VERSION__ !== 'undefined' ? __UI_VERSION__ : 'dev';

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
 * @param {'extension'|'mobile'|'web'} [options.shell] defaults to the build target
 * @param {HTMLIFrameElement} options.iframe The single framed page.
 * @param {function} options.transport Sends a message to the background, resolves its reply.
 * @param {function} [options.onReady] Called with the page's hello payload the first time it arrives.
 * @param {function} [options.onHelloMismatch] Called when the page's bridge version differs.
 * @param {function} options.openPage `(page, params?) => void` implemented by the host page.
 * @param {function} [options.saveBlob] `(blob, filename) => Promise` for mobile native open.
 * @param {function} [options.scanCode] `(instructions) => Promise<string>`, mobile: reads a QR code with the camera, empty when closed.
 * @param {function} [options.streamDownload] `(home, path, filename) => Promise`, the web app's streamed download.
 * @param {function} [options.reserveTab] `(reserve, launch?) => void`, web app tab reservation; with
 *   `launch` (`{id, app, logo, room}`) the tab shows the launching page, and a throw refuses the launch.
 * @param {function} [options.adoptLaunch] `(launchId, sessionId) => boolean`, web app: the launch's tab is the session's.
 * @param {function} [options.onLeave] `(leaving) => void`, web app: the framed page is growing a session over itself, or gave up.
 * @param {function} [options.close] Closes the popup (extension) or no-op.
 * @param {function} [options.isConnectPage] `() => boolean`, true while the bundled connect page is framed.
 * @returns {{setExpectedOrigin: function(string): void, destroy: function(): void}}
 */
export function createHost(options) {
  const { iframe, transport } = options;
  const shell = options.shell || SHELL;
  const platform = detectPlatform(shell);
  let expectedOrigin = null;
  let helloSeen = false;
  // The files the user handed the shell by address (a link, media, or a download it took);
  // fetchBlob reads these alone with the shell's host permissions, never one a page names.
  const handedFiles = new Set();
  const onConnectPage = () => Boolean(options.isConnectPage && options.isConnectPage());
  // The connect page's draft holds the private key, which a served page never sees.
  const DRAFT_KEY = 'sealskinPendingConfig';
  const namesDraft = (keys) => [].concat(keys || []).includes(DRAFT_KEY);

  const capabilities = {
    streamDownload: Boolean(options.streamDownload) || (shell === 'extension' && platform === 'chrome'),
    nativeFileOpen: shell === 'mobile',
    scanCode: Boolean(options.scanCode),
    contextMenus: shell === 'extension',
    tabs: shell === 'extension',
  };

  const handlers = {
    async hello(payload) {
      const config = await callBackground(transport, 'getPublicConfig');
      const info = {
        bridge: BRIDGE_VERSION,
        shell,
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

    // The web app's background answers this with a plain call under the sign-in cookie.
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
      if (context && context.action === 'file' && context.targetUrl) handedFiles.add(context.targetUrl);
      return context;
    },

    async setContext({ context, openPopup = true }) {
      if (context && context.action === 'file' && context.targetUrl) {
        throw new Error('A page hands over a file itself, not its address.');
      }
      let toStore = context;
      if (context && context.file instanceof Blob) {
        await storePendingFile(context.file);
        const { file, ...rest } = context;
        toStore = { ...rest, hasFile: true };
      }
      await callBackground(transport, 'setContext', { context: toStore });
      if (openPopup) {
        if (shell !== 'extension') {
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
      if (!handedFiles.has(url)) throw new Error('Only a file handed to the shell can be fetched.');
      const response = await fetch(url);
      if (!response.ok) throw new Error(`Failed to fetch file data: ${response.statusText}`);
      return response.blob();
    },

    openSession({ sessionId, sessionUrl, launchId }) {
      // The web app's launching page takes its own tab to the session.
      if (launchId && options.adoptLaunch && options.adoptLaunch(launchId, sessionId)) return {};
      return callBackground(transport, 'createTabAndTrack', { sessionId, session_url: sessionUrl });
    },

    focusSession({ session }) {
      return callBackground(transport, 'focusOrCreateTab', { session });
    },

    async leaving({ leaving }) {
      if (options.onLeave) options.onLeave(leaving !== false);
      return {};
    },

    closeSession({ sessionId }) {
      return callBackground(transport, 'closeSession', { sessionId });
    },

    async reserveTab({ reserve, launch }) {
      if (options.reserveTab) await options.reserveTab(reserve !== false, launch);
      return {};
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
      if (options.streamDownload) {
        await options.streamDownload(home, path, filename);
        return {};
      }
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

    // Connect page only: the camera reads the application key the web app shows.
    async scanCode({ instructions }) {
      if (!options.scanCode) throw new Error('Scanning is not available in this shell.');
      if (!onConnectPage()) throw new Error('scanCode is only accepted from the connect page.');
      return { text: await options.scanCode(typeof instructions === 'string' ? instructions : '') };
    },

    async storageGet({ keys }) {
      const items = await callBackground(transport, 'storageGet', { keys: keys === undefined ? null : keys });
      if (items && !onConnectPage()) delete items[DRAFT_KEY];
      return items;
    },

    storageSet({ items }) {
      if (!onConnectPage() && namesDraft(Object.keys(items || {}))) throw new Error('Only the connect page keeps a draft.');
      return callBackground(transport, 'storageSet', { items });
    },

    storageRemove({ keys }) {
      if (!onConnectPage() && namesDraft(keys)) throw new Error('Only the connect page keeps a draft.');
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
