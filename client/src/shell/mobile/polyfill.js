/**
 * Minimal `chrome.*` polyfill for the mobile outer window.
 *
 * Only what `background.js` touches when it runs outside a browser extension:
 * local storage with change events, runtime messaging wired to
 * `window.handleMessage`, tab opening through the Capacitor Browser plugin,
 * `action.openPopup` (shows the served popup in the app frame) and inert stubs
 * for the extension-only APIs. Served pages never see this object; they use
 * the bridge.
 *
 * @param {object} hooks
 * @param {function(string): Promise<void>} hooks.openExternal Opens a URL in a Custom Tab / SFSafariViewController.
 * @param {function(): void} hooks.openPopup Shows the popup page in the app frame.
 */
export function installPolyfill(hooks) {
  const noopListener = { addListener() {}, removeListener() {}, hasListener() { return false; } };
  const storageListeners = new Set();

  const parse = (value) => {
    if (value === null || value === undefined) return undefined;
    try { return JSON.parse(value); } catch (e) { return value; }
  };

  const local = {
    get(keys, cb) {
      const result = {};
      if (keys === null || keys === undefined) {
        for (let i = 0; i < localStorage.length; i++) {
          const k = localStorage.key(i);
          result[k] = parse(localStorage.getItem(k));
        }
      } else {
        const list = Array.isArray(keys) ? keys : (typeof keys === 'object' ? Object.keys(keys) : [keys]);
        list.forEach((k) => {
          const v = parse(localStorage.getItem(k));
          if (v !== undefined) result[k] = v;
        });
      }
      if (cb) cb(result);
      return Promise.resolve(result);
    },
    set(items, cb) {
      const changes = {};
      Object.keys(items).forEach((k) => {
        changes[k] = { oldValue: parse(localStorage.getItem(k)), newValue: items[k] };
        localStorage.setItem(k, JSON.stringify(items[k]));
      });
      storageListeners.forEach((l) => l(changes, 'local'));
      if (cb) cb();
      return Promise.resolve();
    },
    remove(keys, cb) {
      const changes = {};
      (Array.isArray(keys) ? keys : [keys]).forEach((k) => {
        const old = parse(localStorage.getItem(k));
        if (old !== undefined) changes[k] = { oldValue: old, newValue: undefined };
        localStorage.removeItem(k);
      });
      if (Object.keys(changes).length) storageListeners.forEach((l) => l(changes, 'local'));
      if (cb) cb();
      return Promise.resolve();
    },
    clear(cb) {
      localStorage.clear();
      if (cb) cb();
      return Promise.resolve();
    },
  };

  const tabs = {
    async create(props) {
      await hooks.openExternal(props.url);
      return { id: Date.now() };
    },
    async update(id, props) {
      if (props && props.url) await hooks.openExternal(props.url);
      return { id, windowId: 1 };
    },
    async remove() {},
    query: async () => [],
  };

  const chromeLike = {
    runtime: {
      id: 'sealskin-mobile',
      lastError: null,
      getURL: (p) => p,
      getManifest: () => ({ version: 'mobile', manifest_version: 3 }),
      sendMessage(message, options, callback) {
        const cb = typeof options === 'function' ? options : callback;
        if (typeof window.handleMessage === 'function') {
          window.handleMessage(message, { id: 'mobile-shell' }, (response) => { if (cb) cb(response); });
        } else if (cb) {
          cb({ success: false, error: 'Background not loaded' });
        }
      },
      onMessage: {
        addListener(cb) { window.handleMessage = cb; },
        removeListener() { window.handleMessage = null; },
        hasListener() { return typeof window.handleMessage === 'function'; },
      },
      onInstalled: noopListener,
      onStartup: noopListener,
    },
    storage: {
      local,
      onChanged: {
        addListener: (cb) => storageListeners.add(cb),
        removeListener: (cb) => storageListeners.delete(cb),
        hasListener: (cb) => storageListeners.has(cb),
      },
    },
    tabs,
    windows: { update: async () => ({}) },
    action: {
      openPopup: async () => hooks.openPopup(),
      setBadgeText() {},
      setTitle() {},
      setIcon() {},
    },
    contextMenus: { create() {}, removeAll() {}, onClicked: noopListener },
    downloads: null,
    i18n: { getUILanguage: () => navigator.language || 'en-US', getMessage: (m) => m },
  };

  window.chrome = chromeLike;
  return chromeLike;
}
